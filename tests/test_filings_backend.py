"""Contract tests for shared filing navigation."""

import json
import re

import pytest

from screener.filings.backend import FilingBackend


def _backend(tmp_path):
    calls = []

    def parse(path):
        calls.append(path)
        first = "first page risk"
        second = "second page risk"
        return (
            first + second,
            [0, len(first)],
            [{"title": "Business", "start_page": 1, "end_page": 2, "pages": 2, "size": 12.0}],
            {"pages": 2},
        )

    backend = FilingBackend(
        reports_dir=tmp_path,
        index_version=1,
        glob_suffix="_AR_*.fake",
        fy_regex=re.compile(r"_AR_(\d{4})"),
        parse=parse,
        quick_page_count=lambda path: 2,
    )
    symbol_dir = tmp_path / "ACME"
    symbol_dir.mkdir()
    for year in (2023, 2024):
        (symbol_dir / f"ACME_AR_{year}.fake").write_text("source")
    return backend, calls


def test_navigation_builds_one_reusable_index_and_defaults_to_newest(tmp_path):
    backend, calls = _backend(tmp_path)
    assert [row["fy"] for row in backend.filings("ACME")["filings"]] == [2024, 2023]
    assert backend.filings("ACME")["filings"][0]["sections"] is None

    assert backend.outline("ACME")["fy"] == 2024
    assert backend.outline("ACME")["sections"][0]["title"] == "Business"
    assert len(calls) == 1

    page = backend.read_page("ACME", 2)
    assert page["text"] == "second page risk"
    assert page["prev_page"] == 1 and page["next_page"] is None

    hits = backend.grep("ACME", "risk", limit=1)
    assert hits["hit_count"] == 2
    assert hits["returned"] == 1
    assert hits["next_offset"] == 1


def test_navigation_rejects_missing_year_and_out_of_range_page(tmp_path):
    backend, _ = _backend(tmp_path)
    with pytest.raises(FileNotFoundError, match="FY2099"):
        backend.outline("ACME", fy=2099)
    with pytest.raises(ValueError, match="pages 1-2"):
        backend.read_page("ACME", 3)


def test_stale_index_is_rebuilt(tmp_path):
    backend, calls = _backend(tmp_path)
    backend.outline("ACME", fy=2024)
    index_path = tmp_path / "ACME" / "ACME_AR_2024.index.json"
    index = json.loads(index_path.read_text())
    index["index_version"] = 0
    index_path.write_text(json.dumps(index))

    backend.outline("ACME", fy=2024)

    assert len(calls) == 2
