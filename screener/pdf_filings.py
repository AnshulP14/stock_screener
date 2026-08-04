"""Navigation index for NSE annual report PDFs.

Indian annual reports have no statutory section numbering (unlike a 10-K's "Item 1A"),
and no section name is used by even 40% of companies -- so this module never matches
against a list of expected section names. It reads structure only, and reports whatever
titles the document itself uses.

The outline is built from one rule: **the largest-font line on a page is that page's
heading**. Nothing else survived testing. Running headers track branding rather than
structure, absolute font sizes do not transfer between reports (body text runs 8-10pt in
one and 11pt in another), and a layout model returns every heading in the document --
1000+ of them, flat, with no hierarchy to rank them by. Taking the largest line per page
yields at most one row per page, which bounds the outline by the length of the document
instead of by how densely it is typeset.

Three filters keep that rule honest, and no more:

* a line over 90 characters is a paragraph set large, not a heading;
* a heading must contain more than three letters, which drops page numbers, rules and
  standalone figures without knowing what any section is called;
* consecutive pages carrying the same heading are one section, merged into a page range.

The same heading appearing in two separate parts of the document is kept twice, on
purpose: an annual report presents its accounts standalone and then again consolidated,
and collapsing the two loses the second audit opinion entirely.

Titles are reported verbatim, so "Consolidated Balance Sheet" and "Standalone Balance
Sheet" stay distinct with no special handling -- a distinction that matters because the
two report different numbers for the same company.

Page ranges are the addressing unit; `.txt` offsets exist only so `grep` can report the
page a hit landed on.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import fitz

from screener.config import DATA_DIR

REPORTS_DIR = DATA_DIR / "raw" / "nse" / "annual_reports"

_WS = re.compile(r"\s+")
# leading enumeration ("3.", "(iv)", "b)") is detail within a section, not its name
_ENUM = re.compile(r"^\s*(\(?[0-9]{1,3}[.)]|\(?[ivxIVX]{1,5}[.)]|\(?[a-zA-Z][.)])\s+")
# a continuation marker varies between pages of one section without changing its identity
_CONTD = re.compile(r"\s*\((cont|contd)[^)]*\)\s*", re.I)
_ALPHA = re.compile(r"[A-Za-z]")

_MAX_TITLE = 90       # longer than this is a paragraph set large, not a heading
_MIN_ALPHA = 3        # a heading needs more than this many letters

# Bumped whenever the extraction rule changes. An index written by an older rule holds
# sections this module would no longer produce, and silently serving them is worse than
# reparsing: the page ranges look valid but point at a different notion of "section".
INDEX_VERSION = 2


def _norm(text: str) -> str:
    """A heading's identity: enumeration and continuation markers stripped."""
    return _WS.sub(" ", _CONTD.sub(" ", _ENUM.sub("", text))).strip(" .:-–—|")


def _page_items(page) -> list[tuple[float, str, float]]:
    """(y, text, font_size) for every line on the page, in reading order."""
    out = []
    for block in page.get_text("dict")["blocks"]:
        for line in block.get("lines", []):
            text = "".join(s["text"] for s in line["spans"]).strip()
            if not text:
                continue
            size = max((s["size"] for s in line["spans"]), default=0.0)
            out.append((line["bbox"][1], text, size))
    return out


def _page_heading(page) -> tuple[str, float] | None:
    """The page's heading: its largest line, or nothing if the page has no heading."""
    best = None
    for _y, text, size in _page_items(page):
        if len(text) > _MAX_TITLE:
            continue
        if best is None or size > best[1]:
            best = (text, size)
    if best is None:
        return None
    title = _norm(best[0])
    if not title or len(_ALPHA.findall(title)) <= _MIN_ALPHA:
        return None
    return title, best[1]


def _sections(doc) -> list[dict]:
    """One section per run of consecutive pages sharing a heading."""
    sections: list[dict] = []
    for i in range(doc.page_count):
        found = _page_heading(doc[i])
        if found is None:
            continue
        title, size = found
        prev = sections[-1] if sections else None
        # only *consecutive* repeats merge; the accounts are presented twice per report,
        # and a distant repeat is the second presentation, not the same section
        if prev and prev["title"].lower() == title.lower() and prev["end_page"] == i:
            prev["end_page"] = i + 1
        else:
            sections.append({"title": title, "start_page": i + 1, "end_page": i + 1,
                             "size": round(size, 1)})
    for s in sections:
        s["pages"] = s["end_page"] - s["start_page"] + 1
    return sections


def parse_pdf(pdf_path: Path) -> tuple[str, list[int], list[dict], dict]:
    """-> (full text, per-page start offsets, sections, meta)."""
    doc = fitz.open(pdf_path)
    try:
        sections = _sections(doc)
        chunks, offsets, pos = [], [], 0
        for i in range(doc.page_count):
            offsets.append(pos)
            page_text = doc[i].get_text()
            chunks.append(page_text)
            pos += len(page_text)
        meta = {"pages": doc.page_count}
    finally:
        doc.close()
    return "".join(chunks), offsets, sections, meta


# --------------------------------------------------------------------------- paths

def txt_path(pdf_path: Path) -> Path:
    return pdf_path.with_suffix(".txt")


def index_path(pdf_path: Path) -> Path:
    return pdf_path.with_suffix(".index.json")


def _reports(symbol: str, base_dir: Path | None = None) -> list[Path]:
    base = base_dir or REPORTS_DIR
    return sorted((base / symbol).glob(f"{symbol}_AR_*.pdf"))


def _fy_of(pdf_path: Path) -> int | None:
    m = re.search(r"_AR_(\d{4})", pdf_path.stem)
    return int(m.group(1)) if m else None


def resolve(symbol: str, fy: int | None = None, base_dir: Path | None = None) -> Path:
    """The report for `fy`, or the most recent one on disk."""
    files = _reports(symbol, base_dir)
    if not files:
        raise FileNotFoundError(f"no annual report PDFs for {symbol}")
    if fy is None:
        return max(files, key=lambda p: _fy_of(p) or 0)
    for f in files:
        if _fy_of(f) == fy:
            return f
    have = ", ".join(str(_fy_of(f)) for f in files)
    raise FileNotFoundError(f"no {symbol} report for FY{fy} (have: {have})")


# --------------------------------------------------------------------------- index

def build_index(pdf_path: Path) -> dict:
    """Parse `pdf_path`, write the `.txt` + `.index.json`, return the index."""
    text, offsets, sections, meta = parse_pdf(pdf_path)
    index = {
        "index_version": INDEX_VERSION,
        "symbol": pdf_path.parent.name,
        "fy": _fy_of(pdf_path),
        "pages": meta["pages"],
        "section_count": len(sections),
        "page_offsets": offsets,
        "sections": sections,
    }
    txt_path(pdf_path).write_text(text, encoding="utf-8")
    index_path(pdf_path).write_text(json.dumps(index, indent=1), encoding="utf-8")
    return index


def _read_index(pdf_path: Path) -> dict | None:
    """The index on disk, or None if it is missing or written by an older detector."""
    p = index_path(pdf_path)
    if not p.exists():
        return None
    try:
        idx = json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None
    if idx.get("index_version") != INDEX_VERSION or not txt_path(pdf_path).exists():
        return None
    return idx


def load_index(pdf_path: Path) -> dict:
    return _read_index(pdf_path) or build_index(pdf_path)


# ---------------------------------------------------------------------- navigation

def filings(symbol: str, base_dir: Path | None = None) -> dict:
    """Which reports are on disk for a symbol, newest first.

    Deliberately does not return the outlines: one outline is ~1,700 tokens at the
    median, so returning every year's would spend more context answering "which years
    exist?" than the whole rest of the session.

    Never builds an index either -- an unindexed report reports `sections: null` rather
    than parsing five 300-page PDFs to answer a question about what is on disk.
    """
    rows = []
    for pdf in _reports(symbol, base_dir):
        idx = _read_index(pdf)
        if idx:
            rows.append({"fy": idx["fy"], "pages": idx["pages"],
                         "sections": idx["section_count"]})
        else:
            doc = fitz.open(pdf)                       # page count only: instant
            rows.append({"fy": _fy_of(pdf), "pages": doc.page_count, "sections": None})
            doc.close()
    rows.sort(key=lambda r: r["fy"] or 0, reverse=True)
    return {"symbol": symbol, "count": len(rows), "filings": rows}


def outline(symbol: str, fy: int | None = None, base_dir: Path | None = None) -> dict:
    """The sections this document actually has — titles verbatim, with page ranges."""
    idx = load_index(resolve(symbol, fy, base_dir))
    return {
        "symbol": idx["symbol"], "fy": idx["fy"], "pages": idx["pages"],
        "section_count": idx["section_count"],
        "sections": [{k: s[k] for k in ("title", "start_page", "end_page", "pages")}
                     for s in idx["sections"]],
    }


def _page_of(offsets: list[int], pos: int) -> int:
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
    """Exactly one page of the report.

    One page and no more, because a page is already ~800 tokens (p90 ~1,400) and an
    agent almost always needs one of the four a window would have returned. Paginating
    with `next_page` costs a call; reading three pages that go unused costs context that
    cannot be reclaimed -- and having read a page, the agent is usually better placed to
    jump elsewhere than to continue.
    """
    pdf = resolve(symbol, fy, base_dir)
    idx = load_index(pdf)
    total = idx["pages"]
    if not 1 <= page <= total:
        raise ValueError(f"{symbol} FY{idx['fy']} has pages 1-{total}; asked for {page}")

    offsets = idx["page_offsets"]
    text = txt_path(pdf).read_text(encoding="utf-8")
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
         limit: int = 10, context: int = 140, base_dir: Path | None = None,
         start_page: int | None = None, end_page: int | None = None) -> dict:
    """Search the report; every hit carries the page it landed on, so it can be read.

    Results are paginated like a page read, for the same reason: a common term matches
    hundreds of times, and `hit_count` always reports the true total so a query that
    needs narrowing is visible rather than silently truncated. `start_page`/`end_page`
    narrow the search to a page range -- typically one read off the outline -- rather
    than searching the whole report.
    """
    pdf = resolve(symbol, fy, base_dir)
    idx = load_index(pdf)
    text = txt_path(pdf).read_text(encoding="utf-8")
    offsets = idx["page_offsets"]

    lo = offsets[max(0, (start_page or 1) - 1)]
    hi_page = min(end_page, idx["pages"]) if end_page else idx["pages"]
    hi = offsets[hi_page] if hi_page < idx["pages"] else len(text)

    positions = [m.start() + lo for m in re.finditer(pattern, text[lo:hi], re.I)]
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
