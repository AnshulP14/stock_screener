"""screener.freshness — the single staleness definition shared by both market
pipelines and the enrichment batch (previously five/seven duplicated,
inconsistent implementations across nse.py/us.py/enrich.py).

Quarter-boundary dates below are worked by hand against the documented 45-day
post-quarter-end lag, not derived by re-running the code under test:
  - Dec 31 quarter-end clears its lag on Feb 14 (45 days later).
  - Mar 31 quarter-end clears its lag on May 15 (45 days later).
"""

import json
from datetime import date

import pytest

from screener.freshness import AgeDays, Market, QuarterLag, expected_latest_quarter, stale_symbols


# ── expected_latest_quarter ─────────────────────────────────────────

def test_quarter_lag_not_yet_cleared_on_feb_1():
    # Dec 2025 quarter-end (Dec 31) needs until Feb 14 to clear a 45-day lag.
    assert expected_latest_quarter(Market.NSE, today=date(2026, 2, 1)) == "Sep 2025"


def test_quarter_lag_day_before_boundary_still_prior_quarter():
    assert expected_latest_quarter(Market.NSE, today=date(2026, 2, 13)) == "Sep 2025"


def test_quarter_lag_clears_exactly_on_day_45():
    assert expected_latest_quarter(Market.NSE, today=date(2026, 2, 14)) == "Dec 2025"


def test_quarter_lag_cleared_on_may_16():
    # Mar 2026 quarter-end (Mar 31) clears its lag on May 15; May 16 is past it.
    assert expected_latest_quarter(Market.NSE, today=date(2026, 5, 16)) == "Mar 2026"


def test_quarter_lag_day_before_may_boundary_still_prior_quarter():
    assert expected_latest_quarter(Market.NSE, today=date(2026, 5, 14)) == "Dec 2025"


# ── stale_symbols: QuarterLag policy ────────────────────────────────

def _quarter_policy():
    return QuarterLag(field=("shareholding", "quarters", -1), market=Market.NSE)


def test_missing_company_file_is_stale_under_quarter_policy(tmp_path):
    assert stale_symbols(tmp_path, _quarter_policy(), symbols=["RELIANCE"]) == ["RELIANCE"]


def test_up_to_date_shareholding_is_not_stale(tmp_path):
    latest_q = expected_latest_quarter(Market.NSE, today=date(2026, 7, 27))
    (tmp_path / "RELIANCE.json").write_text(json.dumps({
        "shareholding": {"quarters": ["Jun 2020", latest_q]}
    }))
    assert stale_symbols(tmp_path, _quarter_policy(), today=date(2026, 7, 27)) == []


def test_outdated_shareholding_is_stale(tmp_path):
    (tmp_path / "RELIANCE.json").write_text(json.dumps({
        "shareholding": {"quarters": ["Jun 2020"]}
    }))
    assert stale_symbols(tmp_path, _quarter_policy(), today=date(2026, 7, 27)) == ["RELIANCE"]


def test_explicit_shareholding_null_is_stale(tmp_path):
    # shareholding is explicitly `null` (not a missing key) on a fresh fetch.
    (tmp_path / "RELIANCE.json").write_text(json.dumps({"shareholding": None}))
    assert stale_symbols(tmp_path, _quarter_policy(), today=date(2026, 7, 27)) == ["RELIANCE"]


def test_corrupt_json_counts_as_stale(tmp_path):
    (tmp_path / "RELIANCE.json").write_text("{not valid json")
    assert stale_symbols(tmp_path, _quarter_policy(), symbols=["RELIANCE"]) == ["RELIANCE"]


# ── stale_symbols: AgeDays policy ───────────────────────────────────

def _age_policy(days=7):
    return AgeDays(field=("current_snapshot", "as_of"), days=days)


def test_age_policy_exactly_at_threshold_is_not_stale(tmp_path):
    # Strict `>` semantics: exactly `days` old is NOT yet stale.
    (tmp_path / "TSLA.json").write_text(json.dumps({
        "current_snapshot": {"as_of": "2026-07-20"}
    }))
    assert stale_symbols(tmp_path, _age_policy(7), today=date(2026, 7, 27)) == []


def test_age_policy_one_day_past_threshold_is_stale(tmp_path):
    (tmp_path / "TSLA.json").write_text(json.dumps({
        "current_snapshot": {"as_of": "2026-07-19"}
    }))
    assert stale_symbols(tmp_path, _age_policy(7), today=date(2026, 7, 27)) == ["TSLA"]


def test_age_policy_missing_field_is_stale(tmp_path):
    (tmp_path / "TSLA.json").write_text(json.dumps({"current_snapshot": {}}))
    assert stale_symbols(tmp_path, _age_policy(7), today=date(2026, 7, 27)) == ["TSLA"]


def test_age_policy_missing_file_is_stale(tmp_path):
    assert stale_symbols(tmp_path, _age_policy(7), symbols=["TSLA"], today=date(2026, 7, 27)) == ["TSLA"]


# ── stale_symbols: explicit `symbols` universe catches never-fetched ─

def test_symbols_param_flags_never_fetched_alongside_existing_files(tmp_path):
    latest_q = expected_latest_quarter(Market.NSE, today=date(2026, 7, 27))
    (tmp_path / "RELIANCE.json").write_text(json.dumps({
        "shareholding": {"quarters": [latest_q]}
    }))
    result = stale_symbols(
        tmp_path, _quarter_policy(), symbols=["RELIANCE", "NEWLISTING"], today=date(2026, 7, 27)
    )
    assert result == ["NEWLISTING"]


def test_no_symbols_param_globs_directory(tmp_path):
    (tmp_path / "RELIANCE.json").write_text(json.dumps({
        "shareholding": {"quarters": ["Jun 2020"]}
    }))
    # No `symbols` given: falls back to globbing companies_dir/*.json.
    assert stale_symbols(tmp_path, _quarter_policy(), today=date(2026, 7, 27)) == ["RELIANCE"]
