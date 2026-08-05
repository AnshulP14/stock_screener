"""Tests for NSE annual-report PDF navigation."""

from pathlib import Path

import fitz
import pytest

from screener.filings import pdf_filings as pf

BODY = 9
TITLE = 16


def _page(doc, title=None, body_lines=6, long_line=None, width=460):
    # wide enough that a long line is not clipped at the page edge -- clipping would
    # shorten it back under the limit and quietly stop exercising the filter
    page = doc.new_page(width=width, height=620)
    y = 90
    if title:
        page.insert_text((40, y), title, fontsize=TITLE)
        y += 34
    if long_line:                                    # a paragraph set in heading type
        page.insert_text((40, y), long_line, fontsize=TITLE + 4)
        y += 22
    for i in range(body_lines):
        page.insert_text((40, y), f"Ordinary body copy line {i} with enough text to weigh.",
                         fontsize=BODY)
        y += 16
    return page


@pytest.fixture
def report(tmp_path: Path) -> Path:
    """A miniature annual report: a marketing page whose largest line is prose, a
    statutory section running over several pages, and two sets of accounts."""
    d = tmp_path / "WIDG"
    d.mkdir()
    doc = fitz.open()
    _page(doc, width=1800,
          long_line="We have spent a century brewing the finest products for patrons"
                    " everywhere, and we continue to do so today with great pride.")
    for _ in range(4):                               # one section spanning four pages
        _page(doc, title="Board's Report")
    _page(doc, title="12.")                          # no letters: not a heading
    # accounts, presented twice: the same auditor's report heads each set
    _page(doc, title="Independent Auditors' Report")
    _page(doc, title="Standalone Balance Sheet")
    _page(doc, title="Standalone Statement of Cash Flow for the year ended March 31, 2025")
    _page(doc, title="Independent Auditors' Report")
    _page(doc, title="Consolidated Balance Sheet")
    p = d / "WIDG_AR_2024.pdf"
    doc.save(p)
    doc.close()
    return p


def test_titles_are_verbatim_from_the_document(report: Path):
    _text, _offsets, sections, _meta = pf.parse_pdf(report)
    assert "Board's Report" in [s["title"] for s in sections]


def test_standalone_and_consolidated_stay_distinct(report: Path):
    """Both sets of accounts exist in every report; collapsing them would silently
    return one company's numbers for the other."""
    _text, _offsets, sections, _meta = pf.parse_pdf(report)
    titles = [s["title"] for s in sections]
    assert "Standalone Balance Sheet" in titles
    assert "Consolidated Balance Sheet" in titles


def test_consecutive_repeats_merge_into_one_range(report: Path):
    _text, _offsets, sections, _meta = pf.parse_pdf(report)
    board = next(s for s in sections if s["title"] == "Board's Report")
    assert board["pages"] == 4
    assert board["end_page"] - board["start_page"] == 3


def test_a_title_used_twice_far_apart_is_kept_twice(report: Path):
    """The auditor's report appears once for the standalone accounts and once for the
    consolidated ones. Merging every repeat would lose the consolidated audit opinion."""
    _text, _offsets, sections, _meta = pf.parse_pdf(report)
    audits = [s for s in sections if "Auditors' Report" in s["title"]]
    assert len(audits) == 2
    assert audits[0]["start_page"] < audits[1]["start_page"]


def test_statement_title_with_period_clause_is_kept(report: Path):
    _text, _offsets, sections, _meta = pf.parse_pdf(report)
    assert any("Cash Flow" in s["title"] for s in sections)


def test_long_line_is_not_a_heading(report: Path):
    """The largest type on a marketing page is a pull-quote, not a section name."""
    _text, _offsets, sections, _meta = pf.parse_pdf(report)
    assert not any("century brewing" in s["title"] for s in sections)


def test_heading_needs_more_than_three_letters(report: Path):
    _text, _offsets, sections, _meta = pf.parse_pdf(report)
    assert not any(s["title"].startswith("12") for s in sections)


def test_at_most_one_heading_per_page(report: Path):
    """The bound that makes the outline affordable: it grows with the document's length,
    not with how densely it is typeset."""
    _text, _offsets, sections, meta = pf.parse_pdf(report)
    assert sum(s["pages"] for s in sections) <= meta["pages"]
