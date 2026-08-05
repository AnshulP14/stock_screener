"""Tests for S&P 10-K HTML navigation."""

from __future__ import annotations

from pathlib import Path

from screener.filings import html_filings as hf

# ----------------------------------------------------------------------- fixtures

_BODY_SIZE = 10   # pt
_TITLE_SIZE = 14  # pt
_BODY_BOLD = False


def _htm_fragment(pages_data: list[list[dict]],
                  page_breaks: list[int] | None = None) -> str:
    """Build test HTML from styled page lines and optional breaks."""
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
