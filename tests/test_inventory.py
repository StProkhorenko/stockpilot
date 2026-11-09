import pytest

from stockpilot.inventory import Scenario, simulate, summarize_inventory


def test_lead_and_inventory_conservation():
    demand = [3] * 14
    predicted = [[4] * 14, [4] * 14]
    result = simulate(demand, predicted, 2, Scenario(lead_days=3, buffer_days=0))
    rows = result["daily"]
    assert rows[0] == {"stock": 0, "lost": 1, "order": 38, "received": 0}
    assert rows[1]["received"] == 0 and rows[2]["received"] == 0
    assert rows[3]["received"] == 38
    assert result["lost"] == 7
    assert rows[-1]["stock"] == 2 + sum(row["received"] for row in rows) - (sum(demand) - result["lost"])


def test_fill_rate_weights_by_demand_and_no_demand_is_undefined():
    empty = simulate([0] * 7, [[0] * 14], 0, Scenario())
    half = simulate([2] * 7, [[0] * 14], 7, Scenario())
    assert empty["fill_rate"] is None
    assert summarize_inventory([{"model": empty, "baseline": half}])["baseline"]["fill_rate"] == 0.5


@pytest.mark.parametrize("lead", [0, 8])
def test_reject_unsupported_lead(lead):
    with pytest.raises(ValueError, match="Lead time"):
        simulate([1] * 7, [[1] * 14], 1, Scenario(lead_days=lead))
