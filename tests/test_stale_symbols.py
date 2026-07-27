"""_get_stale_symbols_incomplete carried an nse_metadata parameter it never
read; Phase 0 dropped it. This pins the one-argument signature and the
staleness behavior it still needs to preserve."""

import json

from screener.markets import nse


def test_missing_company_file_is_stale(tmp_path, monkeypatch):
    monkeypatch.setattr(nse, "COMPANIES_DIR", tmp_path)
    assert nse._get_stale_symbols_incomplete(["RELIANCE"]) == ["RELIANCE"]


def test_up_to_date_shareholding_is_not_stale(tmp_path, monkeypatch):
    monkeypatch.setattr(nse, "COMPANIES_DIR", tmp_path)
    latest_q = nse._expected_latest_quarter()
    (tmp_path / "RELIANCE.json").write_text(json.dumps({
        "shareholding": {"quarters": ["Jun 2020", latest_q]}
    }))
    assert nse._get_stale_symbols_incomplete(["RELIANCE"]) == []


def test_outdated_shareholding_is_stale(tmp_path, monkeypatch):
    monkeypatch.setattr(nse, "COMPANIES_DIR", tmp_path)
    (tmp_path / "RELIANCE.json").write_text(json.dumps({
        "shareholding": {"quarters": ["Jun 2020"]}
    }))
    assert nse._get_stale_symbols_incomplete(["RELIANCE"]) == ["RELIANCE"]
