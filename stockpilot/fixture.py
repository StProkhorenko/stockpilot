"""Small deterministic synthetic snapshot for a clean checkout."""

from datetime import date, timedelta

from stockpilot.inventory import HORIZON, Scenario, simulate_pair, summarize_inventory


def sample_snapshot():
    start = date(2026, 1, 5)
    dates = lambda offset, size: [(start + timedelta(days=offset + i)).isoformat() for i in range(size)]
    pairs = []
    for index, (item, store, dept) in enumerate((
        ("DEMO_001", "CA_1", "FOODS_1"),
        ("DEMO_002", "TX_1", "HOUSEHOLD_1"),
        ("DEMO_003", "WI_1", "HOBBIES_1"),
    )):
        normal = [4 + index, 6, 5, 7, 8, 12, 10]
        actual = [normal[i % 7] + (i // 7) for i in range(28)]
        predictions = [[normal[i % 7] for i in range(HORIZON)] for _ in range(4)]
        improved = [[normal[i % 7] + k for i in range(HORIZON)] for k in range(4)]
        pairs.append({
            "id": f"{store}:{item}", "item_id": item, "store_id": store, "dept_id": dept,
            "folds": [{"history": normal * 4, "history_dates": dates(-28, 28),
                       "dates": dates(0, 28), "actual": actual, "baseline": predictions, "model": improved}],
        })
    scenario = Scenario()
    return {
        "pairs": pairs,
        "report": {
            "source": "synthetic", "series": len(pairs), "baseline": "four_week",
            "test": [{"start": dates(0, 1)[0], "end": dates(27, 1)[0]}],
            "totals": {}, "by_department": {},
            "inventory": summarize_inventory([fold for pair in pairs for fold in simulate_pair(pair, scenario)]),
            "scenario": vars(scenario),
        },
    }
