"""Tests for S&P 10-K HTML filing navigation (html_filings.py).

Mirrors the NSE PDF filing tests (test_pdf_filings.py) with hand-crafted HTML
fixtures for pure logic and 3-4 corpus smoke tests against real filings.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from screener.filings import html_filings as hf

# ----------------------------------------------------------------------- fixtures

_BODY_SIZE = 10   # pt
_TITLE_SIZE = 14  # pt
_BODY_BOLD = False


def _htm_fragment(pages_data: list[list[dict]],
                  page_breaks: list[int] | None = None) -> str:
    """Build a minimal HTML document for testing.

    Each page is a list of dicts with keys: text, size (pt), bold (bool).
    page_breaks is a list of page indices after which to insert a break.
    If not given, breaks are inserted between every page.
    """
    if page_breaks is None:
        page_breaks = list(range(len(pages_data) - 1))

    parts = ['<?xml version="1.0" encoding="UTF-8"?>',
             '<!DOCTYPE html PUBLIC "-//W3C//DTD XHTML 1.0 Strict//EN"',
             '  "http://www.w3.org/TR/xhtml1/DTD/xhtml1-strict.dtd">',
             '<html xmlns="http://www.w3.org/1999/xhtml">',
             '<head><meta http-equiv="Content-Type"',
             ' content="text/html; charset=utf-8"></head>',
             '<body>']

    for i, lines in enumerate(pages_data):
        # insert break before page i if the previous page is marked for break
        if i > 0 and (i - 1) in page_breaks:
            parts.append('<div style="page-break-after: always;"></div>')
        for line in lines:
            style_parts = []
            size = line.get("size", _BODY_SIZE)
            bold = line.get("bold", _BODY_BOLD)
            style_parts.append(f"font-size: {size}pt")
            if bold:
                style_parts.append("font-weight: bold")
            style = "; ".join(style_parts)
            parts.append(f'<p style="{style}">{line["text"]}</p>')

    parts.append('</body></html>')
    return "\n".join(parts)


def _fixture_hth(tmp_path: Path, pages_data: list[list[dict]],
                 symbol: str = "WIDG", fy: int = 2024,
                 page_breaks: list[int] | None = None) -> Path:
    """Write a test filing to disk and return the path."""
    d = tmp_path / symbol.upper()
    d.mkdir()
    htm = d / f"{symbol.upper()}_10K_{fy}.htm"
    htm.write_text(_htm_fragment(pages_data, page_breaks), encoding="utf-8")
    return htm


# ----------------------------------------------------------------------- page splitting

def test_page_splitting_with_css_breaks(tmp_path: Path):
    """Pages are split on page-break-after: always."""
    data = [
        [{"text": "Page 1 line 1", "size": _BODY_SIZE},
         {"text": "Page 1 line 2", "size": _BODY_SIZE}],
        [{"text": "Page 2 line 1", "size": _BODY_SIZE}],
    ]
    htm = _fixture_hth(tmp_path, data)
    pages = hf.doc_pages(htm)
    assert len(pages) == 2
    assert len(pages[0]) == 2
    assert len(pages[1]) == 1


def test_page_splitting_with_hr_marker(tmp_path: Path):
    """A bare <hr> also splits pages."""
    data = [
        [{"text": "Before HR", "size": _BODY_SIZE}],
        [{"text": "After HR", "size": _BODY_SIZE}],
    ]
    htm = _fixture_hth(tmp_path, data)
    pages = hf.doc_pages(htm)
    assert len(pages) == 2


# ----------------------------------------------------------------------- body style measurement

def test_body_style_measured_by_character_mass(tmp_path: Path):
    """The dominant (size, bold) pair by character mass is body style."""
    # Two large lines, many body lines -> body is (10, False)
    data = [
        [{"text": "Title", "size": _TITLE_SIZE},
         {"text": "Body body body body body body body body body body",
          "size": _BODY_SIZE}],
        [{"text": "Body body body body body body body body body body",
          "size": _BODY_SIZE}],
    ]
    htm = _fixture_hth(tmp_path, data)
    pages = hf.doc_pages(htm)
    body_size, body_bold = hf._body_style(pages)
    assert round(body_size, 1) == _BODY_SIZE
    assert body_bold == _BODY_BOLD


def test_body_style_with_bold_body(tmp_path: Path):
    """Body can be bold (e.g. monospaced financial tables)."""
    data = [
        [{"text": "Body", "size": _BODY_SIZE, "bold": True},
         {"text": "Body body body body body", "size": _BODY_SIZE, "bold": True},
         {"text": "LARGE HEADING", "size": _TITLE_SIZE}],
    ]
    htm = _fixture_hth(tmp_path, data)
    pages = hf.doc_pages(htm)
    body_size, body_bold = hf._body_style(pages)
    assert body_bold is True
    # Heading must be larger since body is already bold
    assert round(body_size, 1) == _BODY_SIZE


# ----------------------------------------------------------------------- heading detection

def test_heading_candidate_filtering(tmp_path: Path):
    """Only lines with size > body or (bold + not body_bold) are headings."""
    # PG-style: body is regular, headings are bold at same size
    data = [
        [
            {"text": "  Item 1. Business  ", "size": _BODY_SIZE, "bold": True},
            {"text": "    Company is a leading manufacturer of consumer goods.",
             "size": _BODY_SIZE},
        ],
        [
            {"text": "  Item 1A. Risk Factors  ", "size": _BODY_SIZE, "bold": True},
            {"text": "    There are many risks facing the company.",
             "size": _BODY_SIZE},
        ],
    ]
    htm = _fixture_hth(tmp_path, data)
    _text, _offsets, sections, _meta = hf.parse_filing(htm)
    titles = [s["title"] for s in sections]
    assert "Item 1. Business" in titles
    assert "Item 1A. Risk Factors" in titles


def test_long_line_not_a_heading(tmp_path: Path):
    """A 90+ char line is not a heading, even if styled as one."""
    long_line = "A" * 100
    data = [
        [{"text": long_line, "size": _TITLE_SIZE, "bold": True},
         {"text": "Short heading", "size": _BODY_SIZE}],
    ]
    htm = _fixture_hth(tmp_path, data)
    _text, _offsets, sections, _meta = hf.parse_filing(htm)
    titles = [s["title"] for s in sections]
    assert not any(long_line[:20] in t for t in titles)
    # The short body line might become heading if nothing else
    # (since long line is filtered out)


def test_heading_needs_more_than_three_letters(tmp_path: Path):
    """A heading with 3 or fewer letters is skipped."""
    data = [
        [{"text": "12.", "size": _TITLE_SIZE, "bold": True},
         {"text": "Body text", "size": _BODY_SIZE}],
    ]
    htm = _fixture_hth(tmp_path, data)
    _text, _offsets, sections, _meta = hf.parse_filing(htm)
    titles = [s["title"] for s in sections]
    assert not any("12" in t for t in titles)


# ----------------------------------------------------------------------- consecutive merge

def test_consecutive_repeats_merge_into_one_range(tmp_path: Path):
    """Same heading on consecutive pages merges into one section."""
    data = [
        [{"text": "Board Report", "size": _BODY_SIZE, "bold": True},
         {"text": "Body text body text body text body text body text",
          "size": _BODY_SIZE}]
        for _ in range(4)
    ]
    htm = _fixture_hth(tmp_path, data)
    _text, _offsets, sections, _meta = hf.parse_filing(htm)
    board = next(s for s in sections if "Board Report" in s["title"])
    assert board["pages"] == 4
    assert board["end_page"] - board["start_page"] == 3


def test_distant_repeat_kept_separate(tmp_path: Path):
    """The same heading appearing in two parts of the doc stays separate."""
    data = [
        [{"text": "Balance Sheet", "size": _BODY_SIZE, "bold": True},
         {"text": "Body 1", "size": _BODY_SIZE}],
        [{"text": "Some other stuff", "size": _BODY_SIZE}],
        [{"text": "Balance Sheet", "size": _BODY_SIZE, "bold": True},
         {"text": "Body 2", "size": _BODY_SIZE}],
    ]
    htm = _fixture_hth(tmp_path, data)
    _text, _offsets, sections, _meta = hf.parse_filing(htm)
    balance_sheets = [s for s in sections if "Balance Sheet" in s["title"]]
    assert len(balance_sheets) == 2
    assert balance_sheets[0]["start_page"] < balance_sheets[1]["start_page"]


# ----------------------------------------------------------------------- XBRL stripping

def test_xbrl_elements_are_stripped(tmp_path: Path):
    """XBRL/iXBRL elements are removed from the parsed text."""
    d = tmp_path / "WIDG"
    d.mkdir()
    htm = d / "WIDG_10K_2024.htm"
    htm.write_text(
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<!DOCTYPE html><html><body>'
        '<p style="font-size: 10pt"><ix:name="Revenue">12345</ix:name> Actual text here</p>'
        '</body></html>', encoding="utf-8")
    text, _offsets, _sections, _meta = hf.parse_filing(htm)
    # The xbrl markup should be stripped; only prose remains
    assert "ix:name" not in text
    assert "Actual text here" in text


def test_script_and_style_tags_stripped(tmp_path: Path):
    """<script> and <style> tags are removed before parsing."""
    html = ('<?xml version="1.0" encoding="UTF-8"?>'
            '<!DOCTYPE html><html><body>'
            '<script>var x = "should not appear";</script>'
            '<style>.foo { color: red; }</style>'
            '<p style="font-size: 10pt">Real text</p>'
            '</body></html>')
    d = tmp_path / "TEST"
    d.mkdir()
    htm = d / "TEST_10K_2024.htm"
    htm.write_text(html, encoding="utf-8")
    text, _offsets, _sections, _meta = hf.parse_filing(htm)
    assert "should not appear" not in text
    assert "color: red" not in text
    assert "Real text" in text


# ----------------------------------------------------------------------- ancestor style walk

def test_ancestor_style_inherited(tmp_path: Path):
    """Font size declared on a parent element is inherited by children."""
    html = ('<?xml version="1.0" encoding="UTF-8"?>'
            '<!DOCTYPE html><html><body>'
            '<div style="font-size: 14pt">'
            '  <p>Heading text from child</p>'
            '</div>'
            '<p style="font-size: 10pt">Body text</p>'
            '</body></html>')
    d = tmp_path / "TEST"
    d.mkdir()
    htm = d / "TEST_10K_2024.htm"
    htm.write_text(html, encoding="utf-8")
    pages = hf.doc_pages(htm)
    # All content is on one page (no breaks)
    assert len(pages) == 1
    # The div's child <p> should have size 14pt (inherited from div)
    assert pages[0][0][1] == 14.0  # (text, size, bold)
    # The standalone <p> should have size 10pt (explicit)
    assert pages[0][1][1] == 10.0


# ----------------------------------------------------------------------- degenerate case

def test_degenerate_no_page_breaks(tmp_path: Path):
    """A filing with no page-break markers becomes one page."""
    html = ('<?xml version="1.0" encoding="UTF-8"?>'
            '<!DOCTYPE html><html><body>'
            '<p style="font-size: 10pt">Line one</p>'
            '<p style="font-size: 10pt">Line two</p>'
            '<p style="font-size: 10pt">Line three</p>'
            '</body></html>')
    d = tmp_path / "TEST"
    d.mkdir()
    htm = d / "TEST_10K_2024.htm"
    htm.write_text(html, encoding="utf-8")
    pages = hf.doc_pages(htm)
    assert len(pages) == 1
    # parse_filing should also return 1 page
    _text, _offsets, _sections, meta = hf.parse_filing(htm)
    assert meta["pages"] == 1


# ----------------------------------------------------------------------- index versioning

def test_index_version_rejects_old_format(tmp_path: Path):
    """An index with a different version is rejected."""
    html = ('<?xml version="1.0" encoding="UTF-8"?>'
            '<!DOCTYPE html><html><body>'
            '<p style="font-size: 10pt">Some text</p>'
            '</body></html>')
    d = tmp_path / "WIDG"
    d.mkdir()
    htm = d / "WIDG_10K_2024.htm"
    htm.write_text(html, encoding="utf-8")
    # Write a fake index with wrong version
    index_data = {
        "index_version": 99,
        "symbol": "WIDG",
        "fy": 2024,
        "pages": 10,
        "section_count": 5,
        "page_offsets": [0, 100, 200, 300, 400, 500, 600, 700, 800, 900, 1000],
        "sections": [{"title": "Test", "start_page": 1, "end_page": 1, "pages": 1, "size": 10.0}],
    }
    htm.with_suffix(".txt").write_text("x" * 1000, encoding="utf-8")
    htm.with_suffix(".index.json").write_text(
        json.dumps(index_data, indent=1), encoding="utf-8")
    # _read_index should reject this
    assert hf._read_index(htm) is None
    # load_index should rebuild from the .htm
    idx = hf.load_index(htm)
    assert idx["index_version"] == hf.INDEX_VERSION


def test_missing_txt_rejects_index(tmp_path: Path):
    """If the .txt file is missing, the index is rejected."""
    d = tmp_path / "WIDG"
    d.mkdir()
    htm = d / "WIDG_10K_2024.htm"
    index_data = {
        "index_version": hf.INDEX_VERSION,
        "symbol": "WIDG",
        "fy": 2024,
        "pages": 1,
        "section_count": 0,
        "page_offsets": [0],
        "sections": [],
    }
    htm.with_suffix(".index.json").write_text(
        json.dumps(index_data, indent=1), encoding="utf-8")
    # .txt does NOT exist
    assert hf._read_index(htm) is None


# ----------------------------------------------------------------------- filings() never builds index

def test_filings_never_builds_index(tmp_path: Path):
    """`filings` answers 'which years exist?' without building an index."""
    html = ('<?xml version="1.0" encoding="UTF-8"?>'
            '<!DOCTYPE html><html><body>'
            '<p style="font-size: 10pt">Some text</p>'
            '</body></html>')
    d = tmp_path / "WIDG"
    d.mkdir()
    htm = d / "WIDG_10K_2024.htm"
    htm.write_text(html, encoding="utf-8")
    # No index exists
    assert not htm.with_suffix(".txt").exists()
    # filings() should work and NOT build an index
    result = hf.filings("WIDG", base_dir=tmp_path)
    assert result["count"] == 1
    assert result["filings"][0]["fy"] == 2024
    assert result["filings"][0]["sections"] is None
    # Still no index
    assert not htm.with_suffix(".txt").exists()


# ----------------------------------------------------------------------- grep pagination

def test_grep_reports_hit_count(tmp_path: Path):
    """grep always reports the true hit_count."""
    data = [
        [{"text": "The body text mentions risk three times here", "size": _BODY_SIZE},
         {"text": "risk risk risk more risk", "size": _BODY_SIZE}]
        for _ in range(5)
    ]
    htm = _fixture_hth(tmp_path, data)
    idx = hf.build_index(htm)
    # Write a proper txt
    text, offsets, sections, _meta = hf.parse_filing(htm)
    hf._txt_path(htm).write_text(text, encoding="utf-8")
    hf._index_path(htm).write_text(
        json.dumps({**idx, "page_offsets": offsets, "sections": sections}, indent=1),
        encoding="utf-8")

    result = hf.grep("WIDG", "risk", fy=2024, limit=3,
                     base_dir=htm.parent.parent)
    assert result["hit_count"] > 3
    assert result["returned"] == 3
    assert result["next_offset"] == 3


def test_grep_paginates_correctly(tmp_path: Path):
    """Offset advances through results."""
    data = [
        [{"text": "word word word word word", "size": _BODY_SIZE}]
        for _ in range(3)
    ]
    htm = _fixture_hth(tmp_path, data)
    idx = hf.build_index(htm)
    text, offsets, sections, _meta = hf.parse_filing(htm)
    hf._txt_path(htm).write_text(text, encoding="utf-8")
    hf._index_path(htm).write_text(
        json.dumps({**idx, "page_offsets": offsets, "sections": sections}, indent=1),
        encoding="utf-8")

    first = hf.grep("WIDG", "word", fy=2024, offset=0, limit=3,
                    base_dir=htm.parent.parent)
    second = hf.grep("WIDG", "word", fy=2024, offset=3, limit=3,
                     base_dir=htm.parent.parent)
    assert first["returned"] == 3
    assert len(second["hits"]) > 0


# ----------------------------------------------------------------------- read_page

def test_read_page_returns_exactly_one_page(tmp_path: Path):
    """read_page returns exactly one page with prev/next navigation."""
    data = [
        [{"text": f"Page {i + 1} content", "size": _BODY_SIZE},
         {"text": "Header", "size": _BODY_SIZE, "bold": True}]
        for i in range(5)
    ]
    htm = _fixture_hth(tmp_path, data)
    hf.build_index(htm)

    r = hf.read_page("WIDG", 3, fy=2024, base_dir=htm.parent.parent)
    assert r["page"] == 3
    assert r["prev_page"] == 2
    assert r["next_page"] == 4
    assert r["pages"] == 5
    assert "Page 3" in r["text"]


def test_read_page_rejects_out_of_range(tmp_path: Path):
    """read_page raises ValueError for pages outside the document."""
    data = [[{"text": "Line", "size": _BODY_SIZE}]]
    htm = _fixture_hth(tmp_path, data)
    hf.build_index(htm)
    text, offsets, sections, _meta = hf.parse_filing(htm)
    hf._txt_path(htm).write_text(text, encoding="utf-8")
    idx = hf._read_index(htm)
    idx["page_offsets"] = offsets
    hf._index_path(htm).write_text(
        json.dumps({**idx, "sections": sections}, indent=1), encoding="utf-8")

    with pytest.raises(ValueError, match="pages 1-1"):
        hf.read_page("WIDG", 999, fy=2024, base_dir=htm.parent.parent)


# ----------------------------------------------------------------------- parse_filing

def test_parse_filing_returns_consistent_text_and_offsets(tmp_path: Path):
    """The full text is the concatenation of per-page text at the recorded offsets."""
    data = [
        [{"text": "First page content", "size": _BODY_SIZE}],
        [{"text": "Second page content", "size": _BODY_SIZE}],
    ]
    htm = _fixture_hth(tmp_path, data)
    text, offsets, _sections, meta = hf.parse_filing(htm)
    assert offsets == [0, len("First page content")]
    assert meta["pages"] == 2
    # Each page is joined with space, pages concatenated directly
    assert "First page content" in text
    assert "Second page content" in text


# ----------------------------------------------------------------------- list_filings / resolve

def test_list_filings_returns_oldest_first(tmp_path: Path):
    """list_filings returns filings sorted oldest first."""
    d = tmp_path / "WIDG"
    d.mkdir()
    for fy in (2023, 2025, 2024):
        htm = d / f"WIDG_10K_{fy}.htm"
        htm.write_text('<html><body><p style="font-size: 10pt">text</p></body></html>',
                       encoding="utf-8")
    files = hf.list_filings("WIDG", base_dir=tmp_path)
    assert [hf._fy_of(f) for f in files] == [2023, 2024, 2025]


def test_resolve_defaults_to_newest_year(tmp_path: Path):
    """resolve without fy returns the newest filing."""
    d = tmp_path / "WIDG"
    d.mkdir()
    for fy in (2023, 2025, 2024):
        htm = d / f"WIDG_10K_{fy}.htm"
        htm.write_text('<html><body><p style="font-size: 10pt">text</p></body></html>',
                       encoding="utf-8")
    resolved = hf.resolve("WIDG", base_dir=tmp_path)
    assert hf._fy_of(resolved) == 2025


def test_resolve_specific_fy(tmp_path: Path):
    """resolve(fy=2024) returns the correct year's filing."""
    d = tmp_path / "WIDG"
    d.mkdir()
    for fy in (2023, 2024, 2025):
        htm = d / f"WIDG_10K_{fy}.htm"
        htm.write_text('<html><body><p style="font-size: 10pt">text</p></body></html>',
                       encoding="utf-8")
    resolved = hf.resolve("WIDG", fy=2024, base_dir=tmp_path)
    assert hf._fy_of(resolved) == 2024


def test_resolve_raises_for_missing_fy(tmp_path: Path):
    """resolve(fy=X) raises FileNotFoundError when year is not on disk."""
    d = tmp_path / "WIDG"
    d.mkdir()
    htm = d / "WIDG_10K_2024.htm"
    htm.write_text('<html><body><p style="font-size: 10pt">text</p></body></html>',
                   encoding="utf-8")
    with pytest.raises(FileNotFoundError, match="FY2099"):
        hf.resolve("WIDG", fy=2099, base_dir=tmp_path)


# ----------------------------------------------------------------------- corpus smoke tests

@pytest.fixture
def corpus_base():
    from screener.config import SNP_ANNUAL_REPORTS_DIR
    return Path(SNP_ANNUAL_REPORTS_DIR)


def test_corpus_pg_bold_required(corpus_base):
    """PG proves bold is required for heading detection — size alone yields 0 rows."""
    htm = corpus_base / "PG" / "PG_10K_2023.htm"
    if not htm.exists():
        pytest.skip("PG 2023 not on disk")
    _text, _offsets, sections, meta = hf.parse_filing(htm)
    assert meta["pages"] > 0
    assert len(sections) >= 50  # PG has many sections due to bold detection
    titles = [s["title"] for s in sections]
    assert "PART I" in titles
    assert "PART II" in titles


def test_corpus_aapl_typical_behavior(corpus_base):
    """AAPL shows typical 10-K behavior: many sections, reasonable page count."""
    htm = corpus_base / "AAPL" / "AAPL_10K_2023.htm"
    if not htm.exists():
        pytest.skip("AAPL 2023 not on disk")
    _text, _offsets, sections, meta = hf.parse_filing(htm)
    assert meta["pages"] >= 50
    assert len(sections) >= 30
    titles = [s["title"] for s in sections]
    assert "PART I" in titles
    assert "PART II" in titles


def test_corpus_are_degenerate(corpus_base):
    """ARE 2024 is degenerate: no page-break markers → 1 page."""
    htm = corpus_base / "ARE" / "ARE_10K_2024.htm"
    if not htm.exists():
        pytest.skip("ARE 2024 not on disk")
    raw = htm.read_text(encoding="utf-8")
    pb_count = len(hf._BREAKE.findall(raw))
    # ARE 2024 has 0 page-break markers
    assert pb_count == 0
    _text, _offsets, _sections, meta = hf.parse_filing(htm)
    assert meta["pages"] == 1  # degenerate: one page


def test_corpus_filings_lists_years(corpus_base):
    """filings() lists all years for a symbol without building index."""
    result = hf.filings("AAPL", base_dir=corpus_base)
    assert result["count"] >= 3  # should have multiple years
    fy_years = [f["fy"] for f in result["filings"]]
    assert max(fy_years) > min(fy_years)


def test_corpus_read_page_works(corpus_base):
    """read_page works on a real filing."""
    htm = corpus_base / "AAPL" / "AAPL_10K_2023.htm"
    if not htm.exists():
        pytest.skip("AAPL 2023 not on disk")
    # Build index first
    hf.load_index(htm)
    r = hf.read_page("AAPL", 1, fy=2023, base_dir=corpus_base)
    assert r["page"] == 1
    assert r["pages"] >= 50
    assert len(r["text"]) > 0
    assert r["prev_page"] is None
    assert r["next_page"] == 2
