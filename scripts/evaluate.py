"""Rebuild StockPilot's backtest and dashboard snapshot from the pinned M5 ZIP."""

import argparse
import json
from pathlib import Path
from zipfile import ZipFile

import lightgbm as lgb
import numpy as np
import pandas as pd

from scripts.fetch_m5 import verify
from stockpilot.inventory import HORIZON, REVIEW_DAYS, Scenario, simulate_pair, summarize_inventory

DEV = 1800
TEST = (1829, 1857, 1885, 1913)
WINDOW = 28
HISTORY = 56
STORES = ("CA_1", "TX_1", "WI_1")
DEPARTMENTS = ("FOODS_1", "HOBBIES_1", "HOUSEHOLD_1")
NAIVE = ("last_week", "four_week")
MODEL = "lightgbm"


def load_data(path):
    day_cols = [f"d_{day}" for day in range(1, 1942)]
    use = ["item_id", "store_id", "dept_id", *day_cols]
    with ZipFile(path) as archive:
        with archive.open("sales_train_evaluation.csv") as stream:
            sales = pd.read_csv(stream, usecols=use, dtype=dict.fromkeys(day_cols, "int16"))
        sales = sales.loc[sales.store_id.isin(STORES) & sales.dept_id.isin(DEPARTMENTS)].copy()
        sales["volume"] = sales.loc[:, "d_1351":"d_1650"].mean(axis=1)
        cohorts = []
        for _, group in sales.groupby(["store_id", "dept_id"], sort=True):
            active = group.loc[group.volume >= 0.5].sort_values(["volume", "item_id"])
            positions = np.rint(np.linspace(.12, .92, 6) * (len(active) - 1)).astype(int)
            cohorts.append(active.iloc[positions])
        selected = pd.concat(cohorts, ignore_index=True)
        meta = selected[["item_id", "store_id", "dept_id"]].to_dict("records")
        y = selected[day_cols].to_numpy()
        with archive.open("calendar.csv") as stream:
            calendar = pd.read_csv(stream)
        pairs = {(row["store_id"], row["item_id"]) for row in meta}
        # Filter chunks to avoid materializing the entire price table.
        with archive.open("sell_prices.csv") as stream:
            prices = pd.concat([
                chunk.loc[pd.MultiIndex.from_frame(chunk[["store_id", "item_id"]]).isin(pairs)]
                for chunk in pd.read_csv(stream, chunksize=200_000)
            ])
    weekly = prices.pivot(index=["store_id", "item_id"], columns="wm_yr_wk", values="sell_price")
    price = weekly.reindex(
        index=[(row["store_id"], row["item_id"]) for row in meta], columns=calendar.wm_yr_wk
    ).ffill(axis=1).to_numpy(dtype=np.float32)
    dates = pd.to_datetime(calendar.date)
    events = calendar[["event_name_1", "event_name_2"]].notna().any(axis=1).to_numpy()
    snap = np.array([calendar[f"snap_{row['store_id'][:2]}"] for row in meta], dtype=np.int8)
    cal = (dates, dates.dt.day_of_week.to_numpy(), dates.dt.month.to_numpy(), events, snap)
    return meta, y, price, cal


def features(y, prices, cal, origins):
    """Each row has only sales/prices before origin and target calendar data."""
    origins = np.asarray(origins, dtype=int)
    series_count = len(y)
    target = origins[:, None] + np.arange(HORIZON)
    history = y[:, origins[:, None] - np.arange(1, HISTORY + 1)]
    price_now = prices[:, origins - 1]
    price_old = prices[:, origins - 29]
    ratio = np.divide(price_now, price_old, out=np.ones_like(price_now), where=price_old > 0)
    columns = {
        "series": np.repeat(np.arange(series_count), len(origins) * HORIZON),
        "horizon": np.tile(np.arange(1, HORIZON + 1), series_count * len(origins)),
        "weekday": np.tile(cal[1][target].ravel(), series_count),
        "month": np.tile(cal[2][target].ravel(), series_count),
        "event": np.tile(cal[3][target].ravel(), series_count),
        "snap": cal[4][:, target].ravel(),
    }
    past = {f"lag_{lag}": history[:, :, lag - 1] for lag in (1, 2, 7, 14, 21, 28)}
    for width in (7, 14, 28, HISTORY):
        past[f"mean_{width}"] = history[:, :, :width].mean(axis=2)
    past["zero_share"] = (history[:, :, :28] == 0).mean(axis=2)
    past["last_price"] = price_now
    past["price_ratio"] = ratio
    columns.update({key: np.repeat(value.ravel(), HORIZON) for key, value in past.items()})
    return pd.DataFrame(columns)


def forecast_fold(y, prices, cal, start):
    # Freeze model at the beginning of each fold. Weekly predictions use newly observed history.
    origins = np.arange(max(HISTORY, start - 730), start - HORIZON + 1, 4)
    target = origins[:, None] + np.arange(HORIZON)
    train_x = features(y, prices, cal, origins)
    training = lgb.Dataset(train_x, label=y[:, target].ravel(), categorical_feature=["series"])
    model = lgb.train({
        "objective": "poisson", "learning_rate": 0.055, "num_leaves": 27,
        "min_child_samples": 60, "lambda_l2": 1.0, "num_threads": 4, "seed": 42,
        "deterministic": True, "force_col_wise": True, "verbosity": -1,
    }, training, num_boost_round=190)
    review = start + np.arange(0, WINDOW, REVIEW_DAYS)
    forecast = model.predict(features(y, prices, cal, review), num_threads=4).reshape(len(y), len(review), HORIZON)
    weekday = np.arange(HORIZON) % REVIEW_DAYS
    past_days = review[:, None, None] + weekday[None, None, :] - REVIEW_DAYS * np.arange(1, 5)[None, :, None]
    past = y[:, past_days]
    return {"last_week": past[:, :, 0], "four_week": past.mean(axis=2), MODEL: forecast}


def metrics(actual, prediction):
    delta = prediction - actual
    total = float(actual.sum())
    return {
        "wape": float(np.abs(delta).sum() / total) if total else None,
        "bias": float(delta.sum() / total) if total else None,
        "mae": float(np.abs(delta).mean()),
        "samples": int(actual.size),
        "units": int(total),
    }


def forecast_scores(y, start, forecasts, indices=slice(None)):
    actual = y[indices, start:start + WINDOW]
    # Nonoverlapping 14-day forecasts issued on days 1 and 15.
    return {
        name: metrics(actual, values[indices, ::2].reshape(len(actual), WINDOW))
        for name, values in forecasts.items()
    }


def json_write(path, data):
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(data, ensure_ascii=False, allow_nan=False, separators=(",", ":")), encoding="utf8")
    temporary.replace(path)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive", type=Path, default=Path("artifacts/m5.zip"))
    parser.add_argument("--output", type=Path, default=Path("artifacts"))
    args = parser.parse_args()
    verify(args.archive)
    meta, y, price, cal = load_data(args.archive)
    dates = cal[0].dt.strftime("%Y-%m-%d").tolist()
    dev = forecast_scores(y, DEV, forecast_fold(y, price, cal, DEV))
    baseline = min(NAIVE, key=lambda name: dev[name]["wape"])
    print(f"Dev baseline: {baseline} {dev[baseline]['wape']:.4f}", flush=True)
    pairs = [{**row, "id": f"{row['store_id']}:{row['item_id']}", "folds": []} for row in meta]
    test = []
    evaluations = {method: [] for method in (*NAIVE, MODEL)}
    actuals = []
    for start in TEST:
        forecasts = forecast_fold(y, price, cal, start)
        fold = forecast_scores(y, start, forecasts)
        test.append({"start": dates[start], "end": dates[start + WINDOW - 1], "metrics": fold})
        actuals.append(y[:, start:start + WINDOW])
        for name, values in forecasts.items():
            evaluations[name].append(values[:, ::2].reshape(len(y), WINDOW))
        for index, pair in enumerate(pairs):
            pair["folds"].append({
                "history_dates": dates[start - 28:start],
                "history": y[index, start - 28:start].tolist(),
                "dates": dates[start:start + WINDOW],
                "actual": y[index, start:start + WINDOW].tolist(),
                "baseline": forecasts[baseline][index].round(5).tolist(),
                "model": forecasts[MODEL][index].round(5).tolist(),
            })
        print(dates[start], f"baseline {fold[baseline]['wape']:.4f}", f"model {fold[MODEL]['wape']:.4f}", flush=True)

    actual = np.concatenate(actuals, axis=1)
    prediction = {name: np.concatenate(values, axis=1) for name, values in evaluations.items()}
    totals = {name: metrics(actual, values) for name, values in prediction.items()}
    by_department = {
        dept: {name: metrics(actual[indices], values[indices]) for name, values in prediction.items()}
        for dept in DEPARTMENTS
        for indices in [np.array([row["dept_id"] == dept for row in meta])]
    }
    scenario = Scenario()
    inventory = summarize_inventory([fold for pair in pairs for fold in simulate_pair(pair, scenario)])
    tradeoffs = []
    for buffer in (0, 1, 2, 3):
        setting = Scenario(buffer_days=buffer)
        results = [fold for pair in pairs for fold in simulate_pair(pair, setting)]
        tradeoffs.append({"buffer_days": buffer, "metrics": summarize_inventory(results)})
    report = {
        "series": len(pairs), "dev_start": dates[DEV], "dev_end": dates[DEV + WINDOW - 1],
        "baseline": baseline, "dev": dev, "test": test, "totals": totals,
        "by_department": by_department, "inventory": inventory, "tradeoffs": tradeoffs,
        "scenario": vars(scenario),
    }
    args.output.mkdir(parents=True, exist_ok=True)
    json_write(args.output / "report.json", report)
    json_write(args.output / "snapshot.json", {"report": report, "pairs": pairs})
    print(json.dumps({"forecast": totals, "inventory": inventory}, indent=2), flush=True)


if __name__ == "__main__":
    main()
