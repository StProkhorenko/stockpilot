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

DEV = (1744, 1772, 1800)
TEST = (1829, 1857, 1885, 1913)
FINAL = 1941
WINDOW = 28
HISTORY = 56
STORES = ("CA_1", "TX_1", "WI_1")
DEPARTMENTS = ("FOODS_1", "HOBBIES_1", "HOUSEHOLD_1")
NAIVE = ("last_week", "four_week")
MODEL = "lightgbm"
V1 = "v1"
CANDIDATES = (V1, "seasonal_features", "tweedie", "blend")


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


def append_final(path, meta, sales):
    final_cols = [f"d_{day}" for day in range(1942, 1970)]
    keys = ["store_id", "item_id"]
    with ZipFile(path) as archive, archive.open("sales_test_evaluation.csv") as stream:
        final = pd.read_csv(stream, usecols=[*keys, *final_cols]).set_index(keys)
    selected = final.loc[[(row["store_id"], row["item_id"]) for row in meta], final_cols]
    return np.concatenate((sales, selected.to_numpy(dtype=np.int16)), axis=1)


def features(y, prices, cal, origins, enhanced=False):
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
    if enhanced:
        weekday = np.arange(HORIZON) % REVIEW_DAYS
        aligned = history[:, :, 6 - weekday[None, :] + 7 * np.arange(8)[:, None]]
        for weeks in (2, 4, 8):
            columns[f"weekday_mean_{weeks}"] = aligned[:, :, :weeks].mean(axis=2).ravel()
        for width in (14, 28, HISTORY):
            deviation = history[:, :, :width].std(axis=2)
            columns[f"std_{width}"] = np.repeat(deviation.ravel(), HORIZON)
        columns["recent_trend"] = np.repeat((past["mean_14"] - past["mean_56"]).ravel(), HORIZON)
    return pd.DataFrame(columns)


def fit_predict(y, prices, cal, start, review, variant):
    enhanced = variant != V1
    history_days = 1050 if enhanced else 730
    origins = np.arange(max(HISTORY, start - history_days), start - HORIZON + 1, 4)
    target = origins[:, None] + np.arange(HORIZON)
    train_x = features(y, prices, cal, origins, enhanced)
    training = lgb.Dataset(train_x, label=y[:, target].ravel(), categorical_feature=["series"])
    model = lgb.train({
        "objective": "tweedie" if variant == "tweedie" else "poisson",
        "learning_rate": 0.055, "num_leaves": 27,
        "min_child_samples": 60, "lambda_l2": 1.0, "num_threads": 4, "seed": 42,
        "deterministic": True, "force_col_wise": True, "verbosity": -1,
    }, training, num_boost_round=190)
    return model.predict(features(y, prices, cal, review, enhanced), num_threads=4).reshape(len(y), len(review), HORIZON)


def forecast_fold(y, prices, cal, start, candidates):
    # Weekly forecasts use newly observed history. Only issue complete forecast horizons.
    review = np.arange(start, min(start + WINDOW, len(cal[0]) - HORIZON + 1), REVIEW_DAYS)
    weekday = np.arange(HORIZON) % REVIEW_DAYS
    past_days = review[:, None, None] + weekday[None, None, :] - REVIEW_DAYS * np.arange(1, 5)[None, :, None]
    past = y[:, past_days]
    forecasts = {"last_week": past[:, :, 0], "four_week": past.mean(axis=2)}
    for variant in candidates:
        if variant != "blend":
            forecasts[variant] = fit_predict(y, prices, cal, start, review, variant)
    if "blend" in candidates:
        if "seasonal_features" not in forecasts:
            forecasts["seasonal_features"] = fit_predict(y, prices, cal, start, review, "seasonal_features")
        forecasts["blend"] = .8 * forecasts["seasonal_features"] + .2 * forecasts["four_week"]
    return forecasts


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


def scored_forecast(values):
    # Nonoverlapping 14-day forecasts issued on days 1 and 15.
    return values[:, [0, 2]].reshape(len(values), WINDOW)


def score_windows(y, windows):
    actual = np.concatenate([y[:, start:start + WINDOW] for start, _ in windows], axis=1)
    predictions = {
        name: np.concatenate([scored_forecast(values[name]) for _, values in windows], axis=1)
        for name in windows[0][1]
    }
    return actual, predictions


def summarize_forecasts(actual, predictions):
    weekly = actual.reshape(len(actual), -1, REVIEW_DAYS).sum(axis=2)
    scores = {}
    for name, forecast in predictions.items():
        scores[name] = {
            **metrics(actual, forecast),
            "weekly_wape": metrics(weekly, forecast.reshape(len(actual), -1, REVIEW_DAYS).sum(axis=2))["wape"],
        }
    return scores


def fold_view(y, forecasts, start, dates, baseline):
    actual, predictions = score_windows(y, [(start, forecasts)])
    return {
        "start": dates[start], "end": dates[start + WINDOW - 1],
        "inventory_days": len(forecasts[baseline][0]) * REVIEW_DAYS,
        "metrics": summarize_forecasts(actual, predictions),
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
    tuning = []
    for start in DEV:
        tuning.append((start, forecast_fold(y, price, cal, start, CANDIDATES)))
        print(f"Tuning {dates[start]} complete", flush=True)
    dev = summarize_forecasts(*score_windows(y, tuning))
    baseline = min(NAIVE, key=lambda name: dev[name]["wape"])
    eligible = [name for name in CANDIDATES if abs(dev[name]["bias"]) <= .10]
    selected = min(eligible or CANDIDATES, key=lambda name: dev[name]["wape"])
    print(json.dumps({"selected": selected, "baseline": baseline, "tuning": dev}), flush=True)
    y = append_final(args.archive, meta, y)
    pairs = [{**row, "id": f"{row['store_id']}:{row['item_id']}", "folds": []} for row in meta]
    views = []
    windows = []
    for start in (*TEST, FINAL):
        forecasts = forecast_fold(y, price, cal, start, dict.fromkeys((V1, selected)))
        forecasts = {**{name: forecasts[name] for name in (*NAIVE, V1)}, MODEL: forecasts[selected]}
        views.append(fold_view(y, forecasts, start, dates, baseline))
        windows.append((start, forecasts))
        for index, pair in enumerate(pairs):
            pair["folds"].append({
                "history_dates": dates[start - 28:start],
                "history": y[index, start - 28:start].tolist(),
                "dates": dates[start:start + WINDOW],
                "actual": y[index, start:start + WINDOW].tolist(),
                "baseline": forecasts[baseline][index].round(5).tolist(),
                "previous": forecasts[V1][index].round(5).tolist(),
                "model": forecasts[MODEL][index].round(5).tolist(),
            })
        print(dates[start], views[-1]["metrics"], flush=True)

    actual, prediction = score_windows(y, windows[:-1])
    totals = summarize_forecasts(actual, prediction)
    by_department = {
        dept: summarize_forecasts(actual[indices], {name: values[indices] for name, values in prediction.items()})
        for dept in DEPARTMENTS
        for indices in [np.array([row["dept_id"] == dept for row in meta])]
    }
    scenario = Scenario()
    runs = [simulate_pair(pair, scenario) for pair in pairs]
    inventory = summarize_inventory([fold for run in runs for fold in run[:-1]])
    final_inventory = summarize_inventory([run[-1] for run in runs])
    tradeoffs = []
    for buffer in (0, 1, 2, 3):
        setting = Scenario(buffer_days=buffer)
        results = [fold for pair in pairs for fold in simulate_pair(pair, setting)[:-1]]
        tradeoffs.append({"buffer_days": buffer, "metrics": summarize_inventory(results)})
    report = {
        "series": len(pairs), "dev_start": dates[DEV[0]], "dev_end": dates[DEV[-1] + WINDOW - 1],
        "baseline": baseline, "selected": selected, "dev": dev, "test": views, "totals": totals,
        "final": views[-1], "final_inventory": final_inventory,
        "by_department": by_department, "inventory": inventory, "tradeoffs": tradeoffs,
        "scenario": vars(scenario),
    }
    args.output.mkdir(parents=True, exist_ok=True)
    json_write(args.output / "report.json", report)
    json_write(args.output / "snapshot.json", {"report": report, "pairs": pairs})
    print(json.dumps({"forecast": totals, "inventory": inventory}, indent=2), flush=True)


if __name__ == "__main__":
    main()
