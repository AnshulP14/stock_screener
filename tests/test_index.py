"""Tests for company storage and curated index generation."""

import json
import threading

from screener.index import (
    build_indices,
    delete_company,
    iter_companies,
    list_symbols,
    load_company,
    merge_company,
    save_company,
)


def _company(symbol: str, market_cap: float, pe: float) -> dict:
    return {
        "symbol": symbol,
        "company_name": symbol,
        "sector": "Technology",
        "industry": "Software",
        "currency": "USD",
        "current_snapshot": {
            "size": {"market_cap": market_cap},
            "price_metrics": {"trailing_pe": pe},
        },
        "historical_trends": {},
    }


def test_save_load_list_iter_and_delete(tmp_path):
    save_company(tmp_path, "MSFT", _company("MSFT", 2e12, 30.0))
    save_company(tmp_path, "AAPL", _company("AAPL", 3e12, 20.0))

    assert load_company(tmp_path, "AAPL")["symbol"] == "AAPL"
    assert list_symbols(tmp_path) == ["AAPL", "MSFT"]
    assert [data["symbol"] for _, data in iter_companies(tmp_path)] == ["AAPL", "MSFT"]

    delete_company(tmp_path, "AAPL")
    delete_company(tmp_path, "MISSING")
    assert list_symbols(tmp_path) == ["MSFT"]


def test_iter_companies_skips_bad_json(tmp_path):
    (tmp_path / "BROKEN.json").write_text("{not valid")
    assert list(iter_companies(tmp_path)) == []


def test_concurrent_merge_preserves_both_updates(tmp_path):
    save_company(tmp_path, "AAPL", {"symbol": "AAPL"})
    barrier = threading.Barrier(3)

    def merge(update):
        barrier.wait()
        merge_company(tmp_path, "AAPL", update)

    threads = [
        threading.Thread(target=merge, args=({"shareholding": {"ok": True}},)),
        threading.Thread(target=merge, args=({"current_snapshot": {"ok": True}},)),
    ]
    for thread in threads:
        thread.start()
    barrier.wait()
    for thread in threads:
        thread.join()

    company = load_company(tmp_path, "AAPL")
    assert company["shareholding"] == {"ok": True}
    assert company["current_snapshot"] == {"ok": True}


def test_build_indices_writes_summary_stats_and_company_comparison(tmp_path):
    companies_dir = tmp_path / "companies"
    indices_dir = tmp_path / "indices"
    for symbol, market_cap, pe in (("AAPL", 3e12, 20.0), ("MSFT", 2e12, 30.0)):
        save_company(companies_dir, symbol, _company(symbol, market_cap, pe))

    result = build_indices(companies_dir=companies_dir, indices_dir=indices_dir, market="snp")

    summary = json.loads((indices_dir / "screening_summary.json").read_text())
    aapl = next(company for company in summary["companies"] if company["symbol"] == "AAPL")
    assert result == {"summary": 2, "industries": 1, "companies": 2}
    assert aapl["market_cap"] == 3e12
    assert aapl["currency"] == "USD"
    assert "market_cap_inr" not in aapl and "market_cap_usd" not in aapl

    stored = load_company(companies_dir, "AAPL")
    assert stored["industry_comparison"]["peer_count"] == 1
    assert stored["industry_comparison"]["metrics"]["trailing_pe"]["value"] == 20.0


def test_build_indices_with_no_companies_is_a_noop(tmp_path):
    assert build_indices(companies_dir=tmp_path / "missing", indices_dir=tmp_path / "indices", market="snp") is None
    assert not (tmp_path / "indices").exists()
