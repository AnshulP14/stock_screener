"""Navigation index for S&P 10-K HTML filings.

American 10-K filings are inline XBRL (iXBRL) wrapped in ~2.5 MB `.htm` files.
They have no native page breaks — page boundaries come from CSS
`page-break-before/after` and bare `<hr>` elements that filers emit for print
rendering.  They do have font sizes, but unlike PDFs the size is often declared
on a parent element and inherited down the tree.  Boldness is also inherited
and serves as the second axis by which headings stand apart from body text.

The navigation outline is built from one rule per page: **the line that is set
apart from the body by (size, bold) is that page's heading**.  Nothing about
10-K structure is encoded — the dominant (size, bold) pair is measured per
document by character mass, and a heading is any line that deviates from it.

Three filters keep that rule honest, and no more:

* a line over 90 characters is a paragraph set large, not a heading;
* a heading must contain more than three letters, which drops page numbers,
  rules and standalone figures without knowing what any section is called;
* consecutive pages carrying the same heading are one section, merged into a
  page range.

The same heading appearing in two separate parts of the document is kept twice,
on purpose: a 10-K may repeat a section title across years or in exhibit
references, and collapsing the two loses the second occurrence entirely.

Page ranges are the addressing unit; ``.txt`` offsets exist only so ``grep``
can report the page a hit landed on.

Degenerate case: ~2 % of filings have no page-break markers at all.  In that
case the entire document is treated as a single page and the "outline" is just
the document-level heading candidates (useful for a quick table of contents).
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from lxml import html as LH

from screener.config import DATA_DIR

REPORTS_DIR = DATA_DIR / "raw" / "snp" / "annual_reports"

_WS = re.compile(r"\s+")
_ENUM = re.compile(r"^\s*(\(?[0-9]{1,3}[.)]|\(?[ivxIVX]{1,5}[.)]|\(?[a-zA-Z][.)])\s+")
_CONTD = re.compile(r"\s*\((cont|contd)[^)]*\)\s*", re.I)
_ALPHA = re.compile(r"[A-Za-z]")
_SIZE = re.compile(r"font-size:\s*([\d.]+)\s*(pt|px|em|rem)", re.I)
_BOLD = re.compile(r"font-weight:\s*(bold|[6-9]00)", re.I)
_BREAKE = re.compile(r"page-break-(before|after)\s*:\s*(always|page)", re.I)
_XBRL_TAG = re.compile(r"^(xbrl|ix|xbrldi|xbrli|link|xlink)[:.]")

_MAX_TITLE = 90
_MIN_ALPHA = 3

INDEX_VERSION = 1


# ----------------------------------------------------------------------- helpers

def _norm(text: str) -> str:
    """A heading's identity: enumeration and continuation markers stripped."""
    return _WS.sub(" ", _CONTD.sub(" ", _ENUM.sub("", text))).strip(" .:-–—|")


def _size_of(el) -> float | None:
    """Resolved font size in pt: walk ancestors for nearest ``font-size``."""
    node = el
    while node is not None:
        style = (node.get("style") or "").lower()
        m = _SIZE.search(style)
        if m:
            v, unit = float(m.group(1)), m.group(2).lower()
            return v * {"pt": 1.0, "px": 0.75, "em": 10.0, "rem": 10.0}[unit]
        node = node.getparent()
    return None


def _is_bold(el) -> bool:
    """Check if element or nearest ancestor declares bold font-weight."""
    node = el
    while node is not None:
        style = (node.get("style") or "").lower()
        if _BOLD.search(style):
            return True
        if node.tag in ("b", "strong"):
            return True
        node = node.getparent()
    return False


def _is_break(el) -> bool:
    """Does this element mark a page boundary?"""
    style = (el.get("style") or "").replace(" ", "")
    if _BREAKE.search(el.get("style") or ""):
        return True
    return el.tag == "hr" and "display:none" not in style


def _strip_xbrl(root) -> None:
    """Remove inline XBRL/iXBRL elements that clutter the text.

    These elements carry data but not prose: ``<ix:nonnumeric>``,
    ``<xbrli:unit>``, etc.  Also strip ``<script>`` and ``<style>``.
    """
    # Collect all nodes to remove first (can't modify while iterating)
    to_remove: list = []
    for tag_name in ("script", "style"):
        to_remove.extend(root.xpath(f"//{tag_name}"))
    for el in root.xpath("//*"):
        if not isinstance(el.tag, str):
            continue
        if _XBRL_TAG.match(el.tag):
            to_remove.append(el)

    # Remove in reverse order (deeper nodes first) to avoid shifting indices
    for el in reversed(to_remove):
        parent = el.getparent()
        if parent is not None:
            # For script/style: discard element text (it's code, not prose)
            # For XBRL: preserve element text (it's data, not markup)
            tag = (el.tag or "").lower()
            if tag not in ("script", "style") and el.text:
                if parent.text is None:
                    parent.text = el.text
                else:
                    parent.text += el.text
            # Preserve tail on previous sibling or parent
            if el.tail:
                prev = el.getprevious()
                if prev is not None:
                    if prev.tail is None:
                        prev.tail = el.tail
                    else:
                        prev.tail += el.tail
                else:
                    if parent.text is None:
                        parent.text = el.tail
                    else:
                        parent.text += el.tail
            parent.remove(el)


# ----------------------------------------------------------------------- page split

def _page_lines(page_data: list) -> list[tuple[str, float | None, bool]]:
    """Extract (text, size, bold) from one page's DOM elements."""
    lines = []
    for text, size, bold in page_data:
        text = _WS.sub(" ", text).strip()
        if text:
            lines.append((text, size, bold))
    return lines


def doc_pages(htm_path: Path) -> list[list[tuple[str, float | None, bool]]]:
    """Split the document into pages.

    Each page is a list of ``(text, size_in_pt, is_bold)`` tuples.
    Pages are delimited by ``page-break-***: always`` CSS and bare ``<hr>``.
    When no page-break markers exist, the whole document becomes one page.
    """
    tree = LH.parse(str(htm_path)).getroot()
    _strip_xbrl(tree)

    pages_list: list[list[tuple[str, float | None, bool]]] = []
    current: list[tuple[str, float | None, bool]] = []

    for el in tree.iter():
        if not isinstance(el.tag, str):
            continue
        if _is_break(el):
            if current:
                pages_list.append(current)
                current = []
            continue
        # Extract text from this element (text + children + tail)
        text_parts = []
        if el.text:
            text_parts.append(el.text)
        for child in el:
            if child.tail:
                text_parts.append(child.tail)
        raw_text = "".join(text_parts)
        text = _WS.sub(" ", raw_text).strip()
        if text:
            size = _size_of(el)
            bold = _is_bold(el)
            current.append((text, size, bold))

    if current:
        pages_list.append(current)

    return [p for p in pages_list if p]


# ----------------------------------------------------------------------- body style

def _body_style(pages: list[list[tuple[str, float | None, bool]]]) -> tuple[float, bool]:
    """The dominant (size, bold) pair by character mass.

    Returns ``(body_size_pt, is_body_bold)`` — the style that accounts for the
    most characters when you measure size classes and bold vs non-bold.
    """
    mass: dict[tuple[float | None, bool], int] = {}
    for lines in pages:
        for text, size, bold in lines:
            if size is not None:
                key = (round(size, 1), bold)
                mass[key] = mass.get(key, 0) + len(text)
    if not mass:
        return (10.0, False)  # default
    return max(mass, key=mass.get)


# ----------------------------------------------------------------------- headings

def _page_heading(lines: list[tuple[str, float | None, bool]],
                  body_size: float, body_bold: bool) -> tuple[str, float, bool] | None:
    """The page's heading: the line set apart from body style.

    A heading is any sized line (size > body, or same size but bold when body
    is not) that passes the length and letter-count filters.
    """
    best: tuple[str, float, bool] | None = None
    for text, size, bold in lines:
        if size is None:
            continue
        if len(text) > _MAX_TITLE:
            continue
        if len(_ALPHA.findall(text)) <= _MIN_ALPHA:
            continue
        # A line is a heading candidate if:
        #   larger than body, OR
        #   same size but bold (and body is not bold)
        is_heading = (size > body_size + 0.01 or
                      (bold and not body_bold and size >= body_size - 0.01))
        if not is_heading:
            continue
        if best is None or (size, bold) > (best[1], best[2]):
            best = (text, size, bold)
    if best is None:
        return None
    title = _norm(best[0])
    if not title or len(_ALPHA.findall(title)) <= _MIN_ALPHA:
        return None
    return (title, best[1], best[2])


def _sections_from_pages(pages: list[list[tuple[str, float | None, bool]]],
                         body_style: tuple[float, bool]) -> list[dict]:
    """Build sections from one-section-per-run-of-consecutive-pages."""
    body_size, body_bold = body_style
    sections: list[dict] = []

    for i, page_lines in enumerate(pages):
        found = _page_heading(page_lines, body_size, body_bold)
        if found is None:
            continue
        title, _size, _bold = found
        prev = sections[-1] if sections else None
        if prev and prev["title"].lower() == title.lower() and prev["end_page"] == i:
            prev["end_page"] = i + 1
        else:
            sections.append({"title": title, "start_page": i + 1, "end_page": i + 1,
                             "size": round(_size, 1)})

    for s in sections:
        s["pages"] = s["end_page"] - s["start_page"] + 1
    return sections


# ----------------------------------------------------------------------- public API

def parse_filing(htm_path: Path) -> tuple[str, list[int], list[dict], dict]:
    """Parse a 10-K filing -> (full text, per-page offsets, sections, meta).

    Single pass: split into pages, measure body style, find headings per page,
    build sections, extract text per page.
    """
    pages = doc_pages(htm_path)
    body_style = _body_style(pages) if pages else (10.0, False)
    body_size, body_bold = body_style

    if not pages:
        return ("", [], [], {"pages": 0})

    sections = _sections_from_pages(pages, body_style)

    # Build full text and per-page offsets
    chunks: list[str] = []
    offsets: list[int] = []
    pos = 0

    for page_lines in pages:
        offsets.append(pos)
        page_text = " ".join(t for t, _s, _b in page_lines)
        chunks.append(page_text)
        pos += len(page_text)

    meta: dict = {"pages": len(pages)}
    return "".join(chunks), offsets, sections, meta


# ----------------------------------------------------------------------- index

def build_index(htm_path: Path) -> dict:
    """Parse ``htm_path``, write the ``.txt`` + ``.index.json``, return the index."""
    text, offsets, sections, meta = parse_filing(htm_path)
    index = {
        "index_version": INDEX_VERSION,
        "symbol": htm_path.name.split("_")[0],
        "fy": _fy_of(htm_path),
        "pages": meta["pages"],
        "section_count": len(sections),
        "page_offsets": offsets,
        "sections": sections,
    }
    _txt_path(htm_path).write_text(text, encoding="utf-8")
    _index_path(htm_path).write_text(json.dumps(index, indent=1), encoding="utf-8")
    return index


def _read_index(htm_path: Path) -> dict | None:
    """The index on disk, or None if missing or stale."""
    p = _index_path(htm_path)
    if not p.exists():
        return None
    try:
        idx = json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None
    if idx.get("index_version") != INDEX_VERSION or not _txt_path(htm_path).exists():
        return None
    return idx


def load_index(htm_path: Path) -> dict:
    """Read index from disk, or rebuild if missing/stale."""
    return _read_index(htm_path) or build_index(htm_path)


# ----------------------------------------------------------------------- paths

def _txt_path(htm_path: Path) -> Path:
    return htm_path.with_suffix(".txt")


def _index_path(htm_path: Path) -> Path:
    return htm_path.with_suffix(".index.json")


def _reports(symbol: str, base_dir: Path | None = None) -> list[Path]:
    base = Path(base_dir or REPORTS_DIR) / symbol.upper()
    if not base.is_dir():
        return []
    return sorted(base.glob(f"{symbol.upper()}_10K_*.htm"),
                  key=lambda p: _fy_of(p) or 0)


def _fy_of(htm_path: Path) -> int | None:
    m = _YEAR_RE.search(htm_path.stem)
    return int(m.group(1)) if m else None


_YEAR_RE = re.compile(r"10K_(\d{4})")


def resolve(symbol: str, fy: int | None = None,
            base_dir: Path | None = None) -> Path:
    """The filing for ``fy``, or the most recent one on disk."""
    files = _reports(symbol, base_dir)
    if not files:
        raise FileNotFoundError(
            f"No 10-K filings on disk for {symbol.upper()}. "
            f"Fetch: python scripts/fetch_annual_reports_snp.py --symbol {symbol.upper()}")
    if fy is None:
        return max(files, key=lambda p: _fy_of(p) or 0)
    for f in files:
        if _fy_of(f) == fy:
            return f
    have = ", ".join(str(_fy_of(f)) for f in files)
    raise FileNotFoundError(f"{symbol.upper()} has no FY{fy} filing. On disk: {have}")


# ----------------------------------------------------------------------- navigation

def filings(symbol: str, base_dir: Path | None = None) -> dict:
    """Which reports are on disk for a symbol, newest first.

    Never builds an index -- opens each ``.htm`` for page-break count only,
    which is instant even for multi-megabyte filings.
    """
    rows: list[dict] = []
    for htm in _reports(symbol, base_dir):
        idx = _read_index(htm)
        if idx:
            rows.append({"fy": idx["fy"], "pages": idx["pages"],
                         "sections": idx["section_count"]})
        else:
            # page count by counting page-break markers in the raw HTML
            raw = htm.read_text(encoding="utf-8")
            page_count = _count_pages_in_html(raw)
            rows.append({"fy": _fy_of(htm), "pages": page_count, "sections": None})
    rows.sort(key=lambda r: r["fy"] or 0, reverse=True)
    return {"symbol": symbol.upper(), "count": len(rows), "filings": rows}


def _count_pages_in_html(html: str) -> int:
    """Count page-break markers in HTML text."""
    count = 0
    # page-break CSS markers
    for m in _BREAKE.finditer(html):
        count += 1
    # bare <hr> elements (not hidden)
    for m in re.finditer(r'<hr[^>]*>', html, re.I):
        attr = m.group(0).lower()
        if 'display:none' not in attr.replace(' ', ''):
            count += 1
    return max(count, 1)  # at least 1


def outline(symbol: str, fy: int | None = None,
            base_dir: Path | None = None) -> dict:
    """The sections this document actually has — titles verbatim, with page ranges."""
    htm = resolve(symbol, fy, base_dir)
    idx = load_index(htm)
    return {
        "symbol": idx["symbol"], "fy": idx["fy"], "pages": idx["pages"],
        "section_count": idx["section_count"],
        "sections": [{k: s[k] for k in ("title", "start_page", "end_page", "pages")}
                     for s in idx["sections"]],
    }


def _page_of(offsets: list[int], pos: int) -> int:
    """Binary search: which page does a character offset fall on?"""
    lo, hi = 0, len(offsets) - 1
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if offsets[mid] <= pos:
            lo = mid
        else:
            hi = mid - 1
    return lo + 1


def read_page(symbol: str, page: int, fy: int | None = None,
              base_dir: Path | None = None) -> dict:
    """Exactly one page of the filing.

    One page and no more, because a page is ~800 tokens and an agent almost
    always needs one of the four a window would have returned.
    """
    htm = resolve(symbol, fy, base_dir)
    idx = load_index(htm)
    total = idx["pages"]
    if not 1 <= page <= total:
        raise ValueError(
            f"{idx['symbol']} FY{idx['fy']} has pages 1-{total}; asked for {page}")

    offsets = idx["page_offsets"]
    text = _txt_path(htm).read_text(encoding="utf-8")
    end = offsets[page] if page < total else len(text)
    section = next((s["title"] for s in idx["sections"]
                    if s["start_page"] <= page <= s["end_page"]), None)
    return {
        "symbol": idx["symbol"], "fy": idx["fy"], "page": page, "pages": total,
        "section": section,
        "prev_page": page - 1 if page > 1 else None,
        "next_page": page + 1 if page < total else None,
        "text": text[offsets[page - 1]:end],
    }


def grep(symbol: str, pattern: str, fy: int | None = None, offset: int = 0,
         limit: int = 10, context: int = 140,
         page_min: int | None = None, page_max: int | None = None,
         base_dir: Path | None = None) -> dict:
    """Search the filing; every hit carries the page it landed on.

    ``page_min`` / ``page_max`` filter the search range to specific pages.
    Results are paginated with ``offset`` / ``limit``; ``hit_count`` always
    reports the true total.
    """
    htm = resolve(symbol, fy, base_dir)
    idx = load_index(htm)
    text = _txt_path(htm).read_text(encoding="utf-8")
    offsets = idx["page_offsets"]

    # Determine character range for page_min/page_max filtering
    start_char = 0
    end_char = len(text)
    if page_min is not None:
        start_char = offsets[page_min - 1] if page_min > 0 else 0
    if page_max is not None:
        end_char = offsets[page_max] if page_max < len(offsets) else len(text)

    positions = [m.start() for m in re.finditer(pattern, text[start_char:end_char], re.I)]
    # Adjust positions to global offsets
    positions = [start_char + p for p in positions]

    window = positions[offset:offset + limit]
    hits = [{"page": _page_of(offsets, pos),
             "excerpt": _WS.sub(" ", text[max(0, pos - context):pos + context]).strip()}
            for pos in window]
    shown = offset + len(hits)
    return {
        "symbol": idx["symbol"], "fy": idx["fy"], "pattern": pattern,
        "hit_count": len(positions), "offset": offset, "returned": len(hits),
        "next_offset": shown if shown < len(positions) else None,
        "hits": hits,
    }


def list_filings(symbol: str, base_dir: Path | None = None) -> list[Path]:
    """Return every ``{SYM}_10K_{YEAR}.htm`` for a symbol, oldest first."""
    return _reports(symbol, base_dir)
