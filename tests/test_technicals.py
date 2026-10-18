from datetime import UTC, datetime

import pandas as pd
import pytest

from screener.technicals import indicators, snapshot, wilder


def history(count=300, start="2024-01-01"):
    index = pd.bdate_range(start, periods=count, tz="Asia/Kolkata")
    close = pd.Series([100.0 + i for i in range(count)], index=index)
    return pd.DataFrame(
        {
            "Open": close,
            "High": close + 1,
            "Low": close - 1,
            "Close": close,
            "Adj Close": close,
            "Volume": 1000.0,
        },
        index=index,
    )


def after(frame):
    return (frame.index[-1] + pd.Timedelta(days=1)).to_pydatetime()


def test_known_indicators_and_wilder_seed():
    f = history()
    m = indicators(f)
    assert m["sma_20"] == pytest.approx(389.5)
    assert m["sma_200"] == pytest.approx(299.5)
    assert m["rsi_14"] == 100
    assert m["atr_14"] == pytest.approx(2)
    assert m["volume_ratio_20"] == 1
    assert m["return_63"] == pytest.approx(399 / 336 - 1)
    smooth = wilder(pd.Series([1.0, 2.0, 3.0, 4.0]), 3)
    assert pd.isna(smooth.iloc[1])
    assert smooth.iloc[2] == 2
    assert smooth.iloc[3] == pytest.approx(8 / 3)


def test_flat_rsi_and_zero_volume_are_finite():
    f = history()
    for c in ("Open", "High", "Low", "Close", "Adj Close"):
        f[c] = 100.0
    f["Volume"] = 0
    m = indicators(f)
    assert m["rsi_14"] == 50
    assert m["volume_ratio_20"] is None


def test_excludes_today_and_retains_raw_mark():
    f = history()
    today = f.index[-1].to_pydatetime().replace(hour=12)
    result = snapshot("nse", "INFY", f, now=today)
    assert result["completed_bars"] == 299
    assert result["metrics"]["close"] == 398
    assert result["mark"]["price"] == 399
    assert result["mark"]["potentially_incomplete"]
    assert "uptrend" in result["signals"]


def test_adjustment_benchmark_and_stale_suppression():
    f = history()
    f["Adj Close"] = f["Close"] / 2
    result = snapshot("nse", "INFY", f, now=after(f), benchmark=f)
    assert result["metrics"]["close"] == 199.5
    assert result["mark"]["price"] == 399
    assert result["metrics"]["excess_return_63"] == pytest.approx(0)
    old = snapshot("nse", "INFY", f, now=datetime(2026, 1, 1, tzinfo=UTC))
    assert old["stale"] and old["signals"] == []


@pytest.mark.parametrize("bad", ["duplicate", "nan", "ohlc", "volume", "short"])
def test_bad_history_rejected(bad):
    f = history()
    now = after(f)
    if bad == "duplicate":
        f = pd.concat([f, f.tail(1)])
    elif bad == "nan":
        f.iloc[0, 0] = float("nan")
    elif bad == "ohlc":
        f.iloc[0, f.columns.get_loc("High")] = 1
    elif bad == "volume":
        f["Volume"] = -1
    elif bad == "short":
        f = f.tail(10)
    with pytest.raises(ValueError):
        snapshot("nse", "INFY", f, now=now)
