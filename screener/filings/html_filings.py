"""Parse S&P 10-K HTML filings for navigation."""

from __future__ import annotations

import re
from pathlib import Path

from lxml import html as LH

from ..config import DATA_DIR
from .backend import ALPHA, MAX_TITLE, MIN_ALPHA, FilingBackend, norm_heading

REPORTS_DIR = DATA_DIR / "raw" / "snp" / "annual_reports"

_WS = re.compile(r"\s+")
_SIZE = re.compile(r"font-size:\s*([\d.]+)\s*(pt|px|em|rem)", re.IGNORECASE)
_BOLD = re.compile(r"font-weight:\s*(bold|[6-9]00)", re.IGNORECASE)
_BREAKE = re.compile(r"page-break-(before|after)\s*:\s*(always|page)", re.IGNORECASE)
_XBRL_TAG = re.compile(r"^(xbrl|ix|xbrldi|xbrli|link|xlink)[:.]")

INDEX_VERSION = 1


# ----------------------------------------------------------------------- helpers

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
    """Strip scripts and styles while preserving inline XBRL text."""
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


def doc_pages(htm_path: Path) -> list[list[tuple[str, float | None, bool]]]:
    """Split HTML into pages of `(text, size, bold)` tuples."""
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
    """Return the font size and weight with the greatest character mass."""
    mass: dict[tuple[float, bool], int] = {}
    for lines in pages:
        for text, size, bold in lines:
            if size is not None:
                key = (round(size, 1), bold)
                mass[key] = mass.get(key, 0) + len(text)
    if not mass:
        return (10.0, False)  # default
    return max(mass, key=lambda k: mass[k])


# ----------------------------------------------------------------------- headings

def _page_heading(lines: list[tuple[str, float | None, bool]],
                  body_size: float, body_bold: bool) -> tuple[str, float, bool] | None:
    """Return the strongest line that differs from the body style."""
    best: tuple[str, float, bool] | None = None
    for text, size, bold in lines:
        if size is None:
            continue
        if len(text) > MAX_TITLE:
            continue
        if len(ALPHA.findall(text)) <= MIN_ALPHA:
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
    title = norm_heading(best[0])
    if not title or len(ALPHA.findall(title)) <= MIN_ALPHA:
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
    """Return full text, page offsets, sections, and metadata for a 10-K."""
    pages = doc_pages(htm_path)
    body_style = _body_style(pages) if pages else (10.0, False)

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


def _count_pages_in_html(html: str) -> int:
    """Count page-break markers in HTML text."""
    count = 0
    # page-break CSS markers
    for m in _BREAKE.finditer(html):
        count += 1
    # bare <hr> elements (not hidden)
    for m in re.finditer(r'<hr[^>]*>', html, re.IGNORECASE):
        attr = m.group(0).lower()
        if 'display:none' not in attr.replace(' ', ''):
            count += 1
    return max(count, 1)  # at least 1


def _quick_page_count(htm_path: Path) -> int:
    return _count_pages_in_html(htm_path.read_text(encoding="utf-8"))


_BACKEND = FilingBackend(
    reports_dir=REPORTS_DIR,
    index_version=INDEX_VERSION,
    glob_suffix="_10K_*.htm",
    fy_regex=re.compile(r"10K_(\d{4})"),
    parse=parse_filing,
    quick_page_count=_quick_page_count,
)

build_index = _BACKEND.build_index
filings = _BACKEND.filings
outline = _BACKEND.outline
read_page = _BACKEND.read_page
grep = _BACKEND.grep
