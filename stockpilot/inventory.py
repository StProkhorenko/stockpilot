"""Periodic-review inventory simulator with deterministic lead times."""

from dataclasses import dataclass
from math import ceil

REVIEW_DAYS = 7
HORIZON = 14


@dataclass(frozen=True)
class Scenario:
    lead_days: int = 3
    buffer_days: float = 1
    holding_cost: float = 0.05
    shortage_cost: float = 2


def simulate(demand, forecasts, initial_stock, scenario):
    """Receive, review/order, fulfill sales, and charge end-of-day stock."""
    if len(demand) % REVIEW_DAYS or len(forecasts) != len(demand) // REVIEW_DAYS:
        raise ValueError("One forecast is required per complete review period")
    protection = scenario.lead_days + REVIEW_DAYS
    if not 1 <= scenario.lead_days <= HORIZON - REVIEW_DAYS:
        raise ValueError("Lead time must be between 1 and 7 days")

    inventory = initial_stock
    pipeline = {}
    rows = []
    for day, requested in enumerate(demand):
        received = pipeline.pop(day, 0)
        inventory += received
        order = 0
        if day % REVIEW_DAYS == 0:
            forecast = forecasts[day // REVIEW_DAYS]
            if len(forecast) < protection:
                raise ValueError("Forecast does not cover the protection period")
            expected = sum(forecast[:protection])
            target = ceil(expected * (1 + scenario.buffer_days / protection))
            order = max(0, target - inventory - sum(pipeline.values()))
            pipeline[day + scenario.lead_days] = order

        served = min(inventory, requested)
        inventory -= served
        rows.append(dict(stock=inventory, lost=requested - served, order=order, received=received))

    units = sum(demand)
    lost = sum(row["lost"] for row in rows)
    stock_days = sum(row["stock"] for row in rows)
    return {
        "daily": rows,
        "lost": lost,
        "units": units,
        "fill_rate": (units - lost) / units if units else None,
        "mean_stock": stock_days / len(rows) if rows else 0,
        "stockout_days": sum(row["lost"] > 0 for row in rows),
        "cost": stock_days * scenario.holding_cost + lost * scenario.shortage_cost,
    }


def simulate_pair(pair, scenario):
    results = []
    for fold in pair["folds"]:
        initial = ceil(sum(fold["history"][-28:]) / 28 * (scenario.lead_days + REVIEW_DAYS))
        observed = fold["actual"][:len(fold["model"]) * REVIEW_DAYS]
        run = {
            "start": fold["dates"][0],
            "initial": initial,
            "baseline": simulate(observed, fold["baseline"], initial, scenario),
            "model": simulate(observed, fold["model"], initial, scenario),
        }
        if "previous" in fold:
            run["previous"] = simulate(observed, fold["previous"], initial, scenario)
        results.append(run)
    return results


def summarize_inventory(results):
    summary = {}
    methods = ("baseline", "model", "previous") if all("previous" in fold for fold in results) else ("baseline", "model")
    for method in methods:
        runs = [fold[method] for fold in results]
        units = sum(run["units"] for run in runs)
        lost = sum(run["lost"] for run in runs)
        days = sum(len(run["daily"]) for run in runs)
        summary[method] = {
            "lost": lost,
            "units": units,
            "fill_rate": (units - lost) / units if units else None,
            "mean_stock": sum(run["mean_stock"] * len(run["daily"]) for run in runs) / days,
            "cost": sum(run["cost"] for run in runs),
            "stockout_days": sum(run["stockout_days"] for run in runs),
        }
    return summary
