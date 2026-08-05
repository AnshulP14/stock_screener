"""Tests for SEC EDGAR access helpers."""

import json

from screener import edgar


def test_user_agent_priority(monkeypatch, tmp_path):
    contact_file = tmp_path / "contact.txt"
    contact_file.write_text("file@example.com\n")
    monkeypatch.setattr(edgar, "EDGAR_CONTACT_FILE", contact_file)
    monkeypatch.setenv("SEC_EDGAR_CONTACT", "env@example.com")
    assert edgar._user_agent() == "sp500-screener-bot (env@example.com)"

    monkeypatch.delenv("SEC_EDGAR_CONTACT")
    assert edgar._user_agent() == "sp500-screener-bot (file@example.com)"

    contact_file.unlink()
    assert edgar._user_agent() == edgar.YFINANCE_USER_AGENT


def test_get_10k_filings_filters_sorts_and_builds_archive_urls():
    submissions = {
        "cik": "320193",
        "filings": {"recent": {
            "form": ["10-K", "10-Q", "10-K"],
            "accessionNumber": ["0001-24-001", "0001-25-002", "0001-25-003"],
            "filingDate": ["2024-11-01", "2025-05-01", "2025-10-31"],
            "reportDate": ["2024-09-28", "2025-03-29", "2025-09-27"],
            "primaryDocument": ["aapl-20240928.htm", "aapl-q2.htm", "aapl-20250927.htm"],
        }},
    }

    filings = edgar.get_10k_filings(submissions, max_reports=1)

    assert [filing["year"] for filing in filings] == ["2025"]
    assert filings[0]["url"].endswith("/320193/000125003/aapl-20250927.htm")


def test_fetch_facts_uses_fresh_cache_without_network(monkeypatch, tmp_path):
    cache = tmp_path / "AAPL.json"
    cache.write_text(json.dumps({"entityName": "Apple Inc."}))
    monkeypatch.setattr(edgar, "EDGAR_CACHE_DIR", tmp_path)
    monkeypatch.setattr(edgar, "_get", lambda *args, **kwargs: (_ for _ in ()).throw(
        AssertionError("network should not be called")
    ))

    assert edgar.fetch_facts("AAPL", 320193) == {"entityName": "Apple Inc."}

