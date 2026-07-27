"""build_current_snapshot's field list previously omitted "beta", so
financial_health.beta was always null even though yfinance's `info` blob
carries it and the DB schema already had a column for it. Regression test
for that fix."""

from screener.transform import build_current_snapshot


def test_beta_is_extracted_from_info():
    data = {
        "symbol": "RELIANCE.NS",
        "info": {"beta": 1.23, "trailingPE": 25.0, "sector": "Energy"},
        "fetch_time": "2026-01-01",
        "error": None,
    }
    snapshot = build_current_snapshot(data)
    assert snapshot["financial_health"]["beta"] == 1.23


def test_beta_absent_from_info_is_null_not_missing():
    data = {
        "symbol": "RELIANCE.NS",
        "info": {"trailingPE": 25.0},
        "fetch_time": "2026-01-01",
        "error": None,
    }
    snapshot = build_current_snapshot(data)
    assert snapshot["financial_health"]["beta"] is None
