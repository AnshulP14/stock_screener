"""screener.trends — pure classifiers behind historical_trends.*.trend fields.

Values below are worked by hand against the classifier's documented rule, not
derived by re-running the code under test.
"""

import pytest

from screener.transform import (
    GrowthTrend,
    LeverageBand,
    MarginDirection,
    average_roe,
    cagr,
    classify_growth,
    classify_leverage,
    classify_margin_direction,
    yoy,
)


def test_classify_growth_consistently_growing():
    # 1<2<3<4: every consecutive pair increases.
    assert classify_growth([1, 2, 3, 4]) == GrowthTrend.CONSISTENTLY_GROWING


def test_classify_growth_declining():
    # 4>3>2>1: every consecutive pair decreases, zero ups.
    assert classify_growth([4, 3, 2, 1]) == GrowthTrend.DECLINING


def test_classify_growth_insufficient_data_below_three_points():
    assert classify_growth([1, 2]) == GrowthTrend.INSUFFICIENT_DATA


def test_classify_growth_mostly_growing():
    # pairs: 1<2 up, 2<3 up, 3<2 down, 2<4 up -> 3 ups of 4 possible (75% >= 60%).
    assert classify_growth([1, 2, 3, 2, 4]) == GrowthTrend.MOSTLY_GROWING


def test_classify_growth_volatile():
    # pairs: 1<2 up, 2<1 down, 1<2 up, 2<1 down -> 2 ups of 4 possible (50% < 60%).
    assert classify_growth([1, 2, 1, 2, 1]) == GrowthTrend.VOLATILE


def test_classify_margin_direction_expanding():
    # 0.20 - 0.10 = +0.10, over the +0.02 threshold.
    assert classify_margin_direction([0.10, 0.15, 0.20]) == MarginDirection.EXPANDING


def test_classify_margin_direction_contracting():
    # 0.05 - 0.20 = -0.15, under the -0.02 threshold.
    assert classify_margin_direction([0.20, 0.10, 0.05]) == MarginDirection.CONTRACTING


def test_classify_margin_direction_stable():
    # 0.11 - 0.10 = +0.01, within the +-0.02 band.
    assert classify_margin_direction([0.10, 0.105, 0.11]) == MarginDirection.STABLE


def test_classify_margin_direction_insufficient_data():
    assert classify_margin_direction([0.10]) == MarginDirection.INSUFFICIENT_DATA


def test_classify_leverage_debt_free_below_005():
    # D/E of 0.02 is essentially no debt.
    assert classify_leverage([0.10, 0.02]) == LeverageBand.DEBT_FREE


def test_classify_leverage_low():
    assert classify_leverage([1.0, 0.30]) == LeverageBand.LOW


def test_classify_leverage_moderate():
    assert classify_leverage([0.30, 0.80]) == LeverageBand.MODERATE


def test_classify_leverage_high():
    assert classify_leverage([0.80, 2.0]) == LeverageBand.HIGH


def test_classify_leverage_insufficient_data_when_all_null():
    assert classify_leverage([None, None]) == LeverageBand.INSUFFICIENT_DATA


def test_classify_leverage_uses_latest_not_first():
    # First value alone would read HIGH; must classify off the trailing value.
    assert classify_leverage([2.0, 0.02]) == LeverageBand.DEBT_FREE


def test_average_roe_over_trailing_window():
    # Trailing 3 of [0.10, 0.20, 0.30, 0.40]: (0.20+0.30+0.40)/3 = 0.30.
    assert average_roe([0.10, 0.20, 0.30, 0.40], window=3) == 0.30


def test_average_roe_skips_nulls_within_window():
    # Trailing 3 of [0.10, None, 0.30, 0.50]: only 0.30 and 0.50 count -> 0.40.
    assert average_roe([0.10, None, 0.30, 0.50], window=3) == 0.40


def test_average_roe_none_when_window_all_null():
    assert average_roe([0.10, None, None], window=2) is None


def test_yoy_first_entry_is_none():
    # 100 -> 110 is +10%; the first year has no prior year to compare against.
    assert yoy([100, 110]) == [None, 0.10]


def test_cagr_two_points():
    # 100 -> 121 over 2 periods: (121/100)**(1/2) - 1 = 0.10.
    assert cagr([100, 110, 121]) == pytest.approx(0.10)
