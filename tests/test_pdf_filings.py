"""Section detection in NSE annual report PDFs is freeform: no list of expected section
names exists anywhere in the module, because no section name is used by even 40% of
companies. These tests therefore assert on structure -- that the largest line on a page
becomes that page's heading, that consecutive repeats merge into one range while distant
repeats stay separate, and that the four navigation tools stay addressed by page.

The fixture is built with fitz so it exercises the real font-size logic rather than a
stubbed parser.
"""

from pathlib import Path

import fitz
import pytest

from screener import pdf_filings as pf


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


def test_index_writes_text_and_page_offsets(report: Path):
    idx = pf.build_index(report)
    assert idx["section_count"] == len(idx["sections"])
    assert len(idx["page_offsets"]) == idx["pages"]
    assert pf.txt_path(report).exists()


def test_filings_lists_years_without_outlines(report: Path):
    """`filings` answers "which years exist" -- returning outlines too would cost more
    context than the rest of the session."""
    base = report.parent.parent
    # unindexed: answers from the PDF's page count alone rather than parsing it
    assert pf.filings("WIDG", base_dir=base)["filings"] == [
        {"fy": 2024, "pages": 11, "sections": None}]
    pf.build_index(report)
    row = pf.filings("WIDG", base_dir=base)["filings"][0]
    assert isinstance(row["sections"], int) and row["sections"] > 0


def test_grep_hits_are_page_addressed(report: Path):
    hits = pf.grep("WIDG", "ordinary body copy", base_dir=report.parent.parent)
    assert hits["hit_count"] > 0
    assert all(1 <= h["page"] <= 11 for h in hits["hits"])
    assert all(h["excerpt"] for h in hits["hits"])


def test_grep_paginates_and_reports_the_true_total(report: Path):
    """A truncated result set must be visible as truncated, or the agent concludes it
    has seen every match when it has seen ten."""
    base = report.parent.parent
    first = pf.grep("WIDG", "ordinary", base_dir=base, limit=3)
    assert first["returned"] == 3
    assert first["hit_count"] > 3
    assert first["next_offset"] == 3
    second = pf.grep("WIDG", "ordinary", base_dir=base, offset=3, limit=3)
    assert second["hits"] != first["hits"]

    tail = pf.grep("WIDG", "ordinary", base_dir=base, offset=0, limit=999)
    assert tail["next_offset"] is None


def test_read_page_returns_one_page_and_its_neighbours(report: Path):
    r = pf.read_page("WIDG", 3, base_dir=report.parent.parent)
    assert r["page"] == 3 and r["prev_page"] == 2 and r["next_page"] == 4
    assert r["section"] == "Board's Report"
    assert "Ordinary body copy" in r["text"]


def test_read_page_rejects_a_page_outside_the_document(report: Path):
    with pytest.raises(ValueError, match="pages 1-11"):
        pf.read_page("WIDG", 999, base_dir=report.parent.parent)


def test_resolve_defaults_to_the_newest_year(tmp_path: Path):
    d = tmp_path / "WIDG"
    d.mkdir()
    for fy in (2023, 2025, 2024):
        doc = fitz.open()
        _page(doc, title="Board's Report")
        doc.save(d / f"WIDG_AR_{fy}.pdf")
        doc.close()
    assert pf._fy_of(pf.resolve("WIDG", base_dir=tmp_path)) == 2025
