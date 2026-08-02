"""Section detection in 10-K HTML keys on rendered style, not text, because the
same "Item 1A. Risk Factors" string appears as the real header, in the table of
contents (a hyperlink with a trailing page number), and in inline cross-references
(hyperlinks in body prose). The detector must keep only the real body header.

These cases are built so a naive text-only splitter (the original approach) would
mis-slice: it would match the TOC row and the cross-reference too, and — matching
case-insensitively — would treat the lowercase cross-reference as a section start.
"""

from pathlib import Path

import pytest

from screener import filings


# A minimal filing exercising every discriminator:
#  - a TOC where each item is an <a href> link with a trailing page number
#  - real body headers in mixed styles: bold-inline (1A), UPPERCASE div (7),
#    title-case (1, 7A, 8)
#  - an inline cross-reference to Item 1A inside the MD&A body (an <a href> link)
_FILING_HTML = """
<html><body>
<div>
  <table>
    <tr><td><a href="#b">Item 1. Business</a></td><td>1</td></tr>
    <tr><td><a href="#r">Item 1A. Risk Factors</a></td><td>2</td></tr>
    <tr><td><a href="#m">Item 7. MD&amp;A</a></td><td>3</td></tr>
    <tr><td><a href="#q">Item 7A. Market Risk</a></td><td>4</td></tr>
    <tr><td><a href="#f">Item 8. Financials</a></td><td>5</td></tr>
  </table>
</div>
<div id="b" style="font-weight:bold">Item 1. Business</div>
<p>We make widgets and gadgets for the whole world. {biz}</p>
<div id="r" style="font-weight:700">Item 1A. Risk Factors</div>
<p>Tariffs and supply chain shocks could hurt us materially. {risk}</p>
<div id="m"><span style="font-weight:bold">ITEM 7. MANAGEMENT'S DISCUSSION</span></div>
<p>Revenue rose. As discussed in <a href="#r">Item 1A. Risk Factors</a> above, risks remain. {mda}</p>
<div id="q" style="font-weight:bold">Item 7A. Market Risk</div>
<p>We hold foreign currency exposure.</p>
<div id="f" style="font-weight:bold">Item 8. Financial Statements</div>
<p>Net income was up sharply this year. {fin}</p>
</body></html>
""".format(
    biz="Our operations span many regions and product lines. " * 45,
    risk="Regulatory, currency, and competitive pressures persist. " * 45,
    mda="Margins improved as costs fell and demand grew steadily. " * 45,
    fin="Assets, liabilities, and cash flows are detailed in the notes. " * 45,
)


@pytest.fixture
def filing(tmp_path: Path) -> Path:
    """Nested as {base}/WIDG/WIDG_10K_2024.htm so the resolve()/grep() API works."""
    d = tmp_path / "WIDG"
    d.mkdir()
    p = d / "WIDG_10K_2024.htm"
    p.write_text(_FILING_HTML, encoding="utf-8")
    return p


def test_detects_all_core_items_once(filing: Path):
    _text, sections = filings.parse_filing(filing)
    ids = [s["id"] for s in sections]
    assert ids == ["1", "1A", "7", "7A", "8"]  # ordered, one each — no TOC/xref dupes


def test_offsets_land_on_body_not_toc(filing: Path):
    text, sections = filings.parse_filing(filing)
    business = next(s for s in sections if s["id"] == "1")
    # The section body, not the TOC row, so the widget sentence is inside it.
    assert "widgets and gadgets" in text[business["start"]:business["end"]]


def test_crossreference_does_not_start_a_section(filing: Path):
    # The MD&A body links to Item 1A, but that must not create a second Item 1A
    # section starting inside Item 7.
    _text, sections = filings.parse_filing(filing)
    risk = [s for s in sections if s["id"] == "1A"]
    assert len(risk) == 1
    mda = next(s for s in sections if s["id"] == "7")
    assert risk[0]["start"] < mda["start"]


def test_build_index_quality(filing: Path):
    idx = filings.build_index(filing)
    assert idx["quality"] == "ok"
    assert idx["missing_core"] == []
    assert filings.txt_path(filing).exists()


def test_grep_is_section_scoped_and_addressed(filing: Path):
    base = filing.parent.parent
    hits = filings.grep("WIDG", "tariffs", item_id="1A", base_dir=base)
    assert hits["hit_count"] == 1
    assert hits["hits"][0]["item"] == "1A"
    # A term only in Item 8 must not show up when scoped to Item 1A.
    assert filings.grep("WIDG", "net income", item_id="1A", base_dir=base)["hit_count"] == 0
    assert filings.grep("WIDG", "net income", item_id="8", base_dir=base)["hit_count"] == 1


def test_read_section_windows(filing: Path):
    base = filing.parent.parent
    r = filings.read_section("WIDG", "1", base_dir=base, limit=10)
    assert r["item"] == "1" and r["offset"] == 0
    assert r["returned"] == 10 and r["remaining"] > 0 and r["next_offset"] == 10
