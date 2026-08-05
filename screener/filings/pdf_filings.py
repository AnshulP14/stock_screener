"""Parse NSE annual-report PDFs for navigation."""

from __future__ import annotations

import re
from pathlib import Path

import fitz

from ..config import DATA_DIR
from .backend import ALPHA, MAX_TITLE, MIN_ALPHA, FilingBackend, norm_heading

REPORTS_DIR = DATA_DIR / "raw" / "nse" / "annual_reports"

# Bump when extraction rules change so stale indexes are rebuilt.
INDEX_VERSION = 2


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
        if len(text) > MAX_TITLE:
            continue
        if best is None or size > best[1]:
            best = (text, size)
    if best is None:
        return None
    title = norm_heading(best[0])
    if not title or len(ALPHA.findall(title)) <= MIN_ALPHA:
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


def _quick_page_count(pdf_path: Path) -> int:
    doc = fitz.open(pdf_path)
    try:
        return doc.page_count
    finally:
        doc.close()


_BACKEND = FilingBackend(
    reports_dir=REPORTS_DIR,
    index_version=INDEX_VERSION,
    glob_suffix="_AR_*.pdf",
    fy_regex=re.compile(r"_AR_(\d{4})"),
    parse=parse_pdf,
    quick_page_count=_quick_page_count,
)

build_index = _BACKEND.build_index
filings = _BACKEND.filings
outline = _BACKEND.outline
read_page = _BACKEND.read_page
grep = _BACKEND.grep
