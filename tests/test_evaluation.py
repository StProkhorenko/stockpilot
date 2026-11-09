import numpy as np
import pandas as pd
import pytest
from pandas.testing import assert_frame_equal

from scripts.evaluate import features, metrics


@pytest.mark.parametrize("enhanced", [False, True])
def test_features_exclude_sales_and_prices_from_forecast_period(enhanced):
    day_count = 100
    calendar = (
        pd.Series(pd.date_range("2025-01-01", periods=day_count)),
        np.arange(day_count) % 7, np.ones(day_count), np.zeros(day_count),
        np.zeros((2, day_count)),
    )
    sales = np.tile(np.arange(day_count), (2, 1))
    prices = sales.astype(np.float32)
    expected = features(sales, prices, calendar, [65], enhanced)
    sales[:, 65:] = 1000
    prices[:, 65:] = 1000
    assert_frame_equal(expected, features(sales, prices, calendar, [65], enhanced))
    assert len(expected) == 28
    assert expected.iloc[0]["lag_1"] == 64
    assert expected.iloc[0]["last_price"] == 64
    if enhanced:
        assert expected.iloc[0]["weekday_mean_2"] == (58 + 51) / 2
        assert expected.iloc[1]["weekday_mean_2"] == (59 + 52) / 2


def test_wape_is_weighted_and_bias_preserves_direction():
    score = metrics(np.array([1, 9]), np.array([3, 8]))
    assert score == {"wape": 0.3, "bias": 0.1, "mae": 1.5, "samples": 2, "units": 10}
    assert metrics(np.zeros(2), np.ones(2))["wape"] is None
