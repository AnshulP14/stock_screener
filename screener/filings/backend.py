"""Shared navigation engine for annual report filings (NSE PDF, S&P HTML).

Both formats reduce to the same shape: build a normalized `.txt` + page-offset
index once, then serve resolve/filings/outline/read_page/grep off it. Only
parsing (`parse`) and a cheap page-count fallback (`quick_page_count`) are
format-specific -- see pdf_filings.py / html_filings.py.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

WS = re.compile(r"\s+")
# leading enumeration ("3.", "(iv)", "b)") is detail within a section, not its name
ENUM = re.compile(r"^\s*(\(?[0-9]{1,3}[.)]|\(?[ivxIVX]{1,5}[.)]|\(?[a-zA-Z][.)])\s+")
# a continuation marker varies between pages of one section without changing its identity
CONTD = re.compile(r"\s*\((cont|contd)[^)]*\)\s*", re.IGNORECASE)
ALPHA = re.compile(r"[A-Za-z]")

MAX_TITLE = 90  # longer than this is a paragraph set large, not a heading
MIN_ALPHA = 3   # a heading needs more than this many letters


def norm_heading(text: str) -> str:
    """A heading's identity: enumeration and continuation markers stripped."""
    return WS.sub(" ", CONTD.sub(" ", ENUM.sub("", text))).strip(" .:-–—|")


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


ParseFn = Callable[[Path], tuple[str, list[int], list[dict], dict]]
QuickPageCountFn = Callable[[Path], int]


@dataclass
class FilingBackend:
    """Navigation engine shared by pdf_filings.py and html_filings.py. Each format
    supplies its own `parse` (full parse -> text/offsets/sections/meta) and
    `quick_page_count` (page count without a full parse); everything else --
    resolve, filings, outline, read_page, grep, and the on-disk index -- is identical.
    """

    reports_dir: Path
    index_version: int
    glob_suffix: str  # e.g. "_AR_*.pdf" or "_10K_*.htm"
    fy_regex: re.Pattern
    parse: ParseFn
    quick_page_count: QuickPageCountFn

    def txt_path(self, path: Path) -> Path:
        return path.with_suffix(".txt")

    def index_path(self, path: Path) -> Path:
        return path.with_suffix(".index.json")

    def fy_of(self, path: Path) -> int | None:
        m = self.fy_regex.search(path.stem)
        return int(m.group(1)) if m else None

    def reports(self, symbol: str, base_dir: Path | None = None) -> list[Path]:
        base = Path(base_dir or self.reports_dir) / symbol.upper()
        if not base.is_dir():
            return []
        return sorted(base.glob(f"{symbol.upper()}{self.glob_suffix}"),
                      key=lambda p: self.fy_of(p) or 0)

    def list_filings(self, symbol: str, base_dir: Path | None = None) -> list[Path]:
        return self.reports(symbol, base_dir)

    def resolve(self, symbol: str, fy: int | None = None, base_dir: Path | None = None) -> Path:
        """The filing for `fy`, or the most recent one on disk."""
        files = self.reports(symbol, base_dir)
        if not files:
            raise FileNotFoundError(f"No filings on disk for {symbol.upper()}.")
        if fy is None:
            return max(files, key=lambda p: self.fy_of(p) or 0)
        for f in files:
            if self.fy_of(f) == fy:
                return f
        have = ", ".join(str(self.fy_of(f)) for f in files)
        raise FileNotFoundError(f"{symbol.upper()} has no FY{fy} filing. On disk: {have}")

    def build_index(self, path: Path) -> dict:
        """Parse `path`, write the `.txt` + `.index.json`, return the index."""
        text, offsets, sections, meta = self.parse(path)
        index = {
            "index_version": self.index_version,
            "symbol": path.parent.name,
            "fy": self.fy_of(path),
            "pages": meta["pages"],
            "section_count": len(sections),
            "page_offsets": offsets,
            "sections": sections,
        }
        self.txt_path(path).write_text(text, encoding="utf-8")
        self.index_path(path).write_text(json.dumps(index, indent=1), encoding="utf-8")
        return index

    def read_index(self, path: Path) -> dict | None:
        """The index on disk, or None if it is missing or written by an older detector."""
        p = self.index_path(path)
        if not p.exists():
            return None
        try:
            idx = json.loads(p.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return None
        if idx.get("index_version") != self.index_version or not self.txt_path(path).exists():
            return None
        return idx

    def load_index(self, path: Path) -> dict:
        return self.read_index(path) or self.build_index(path)

    def filings(self, symbol: str, base_dir: Path | None = None) -> dict:
        """Which reports are on disk for a symbol, newest first. Never builds an
        index -- an unindexed report reports `sections: null` from a cheap page
        count rather than a full parse."""
        rows: list[dict] = []
        for path in self.reports(symbol, base_dir):
            idx = self.read_index(path)
            if idx:
                rows.append({"fy": idx["fy"], "pages": idx["pages"],
                             "sections": idx["section_count"]})
            else:
                rows.append({"fy": self.fy_of(path), "pages": self.quick_page_count(path),
                             "sections": None})
        rows.sort(key=lambda r: r["fy"] or 0, reverse=True)
        return {"symbol": symbol.upper(), "count": len(rows), "filings": rows}

    def outline(self, symbol: str, fy: int | None = None, base_dir: Path | None = None) -> dict:
        """The sections this document actually has — titles verbatim, with page ranges."""
        idx = self.load_index(self.resolve(symbol, fy, base_dir))
        return {
            "symbol": idx["symbol"], "fy": idx["fy"], "pages": idx["pages"],
            "section_count": idx["section_count"],
            "sections": [{k: s[k] for k in ("title", "start_page", "end_page", "pages")}
                         for s in idx["sections"]],
        }

    def read_page(self, symbol: str, page: int, fy: int | None = None,
                  base_dir: Path | None = None) -> dict:
        """Exactly one page of the filing -- ~800 tokens, and an agent almost
        always needs just one of the several a window would have returned."""
        path = self.resolve(symbol, fy, base_dir)
        idx = self.load_index(path)
        total = idx["pages"]
        if not 1 <= page <= total:
            raise ValueError(f"{idx['symbol']} FY{idx['fy']} has pages 1-{total}; asked for {page}")

        offsets = idx["page_offsets"]
        text = self.txt_path(path).read_text(encoding="utf-8")
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

    def grep(self, symbol: str, pattern: str, fy: int | None = None, offset: int = 0,
             limit: int = 10, context: int = 140, base_dir: Path | None = None,
             start_page: int | None = None, end_page: int | None = None) -> dict:
        """Search the filing; every hit carries the page it landed on. `start_page`/
        `end_page` narrow the search range; `hit_count` always reports the true total,
        independent of `offset`/`limit` pagination."""
        path = self.resolve(symbol, fy, base_dir)
        idx = self.load_index(path)
        text = self.txt_path(path).read_text(encoding="utf-8")
        offsets = idx["page_offsets"]

        lo = offsets[max(0, (start_page or 1) - 1)]
        hi_page = min(end_page, idx["pages"]) if end_page else idx["pages"]
        hi = offsets[hi_page] if hi_page < idx["pages"] else len(text)

        positions = [lo + m.start() for m in re.finditer(pattern, text[lo:hi], re.IGNORECASE)]
        window = positions[offset:offset + limit]
        hits = [{"page": _page_of(offsets, pos),
                 "excerpt": WS.sub(" ", text[max(0, pos - context):pos + context]).strip()}
                for pos in window]
        shown = offset + len(hits)
        return {
            "symbol": idx["symbol"], "fy": idx["fy"], "pattern": pattern,
            "hit_count": len(positions), "offset": offset, "returned": len(hits),
            "next_offset": shown if shown < len(positions) else None,
            "hits": hits,
        }
