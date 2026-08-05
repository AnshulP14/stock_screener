"""Tests for annual-report discovery and staleness."""

import os
import time

from bs4 import BeautifulSoup

from screener.annual_reports import is_report_stale, parse_annual_report_links


def test_parse_annual_report_links_handles_label_and_url_years():
    soup = BeautifulSoup("""
      <div class="annual-reports">
        <a href="https://cdn.example/acme-2025.pdf">Annual Report</a>
        <a href="https://cdn.example/report.pdf">Annual Report 2023-24</a>
        <a href="https://cdn.example/readme.txt">Not a report</a>
      </div>
    """, "html.parser")

    assert parse_annual_report_links(soup) == [
        {"year": "2025", "url": "https://cdn.example/acme-2025.pdf", "label": "Annual Report"},
        {"year": "2023-24", "url": "https://cdn.example/report.pdf", "label": "Annual Report 2023-24"},
    ]


def test_report_staleness_uses_newest_matching_file(tmp_path):
    assert is_report_stale(tmp_path, "*.pdf", max_age_days=10) is True
    old = tmp_path / "old.pdf"
    fresh = tmp_path / "fresh.pdf"
    old.touch()
    fresh.touch()
    os.utime(old, (time.time() - 20 * 86400,) * 2)
    os.utime(fresh, (time.time() - 2 * 86400,) * 2)

    assert is_report_stale(tmp_path, "*.pdf", max_age_days=10) is False

