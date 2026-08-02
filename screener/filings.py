"""Navigable index over long 10-K filings.

The iXBRL `.htm` filings on disk are ~2.5MB each (~150k tokens). Reading one whole
is wasteful and usually impossible. Instead we build a small, durable *map* per
filing once — a section tree with character offsets over a normalized text copy —
and expose a handful of addressed slice/search primitives the agent walks
iteratively (outline -> grep/read_section -> expand window), PageIndex-style.

Section detection keys on **rendered style, not text**. A 10-K's real item headers
("Item 1A. Risk Factors") are styled as headings (bold / larger font); the *same
string* also appears in the table of contents (with a trailing page number) and in
inline cross-references ("as discussed in Item 1A"), which inherit body style. So we
parse the HTML node tree, keep only item-pattern text that sits in a heading-styled
element, and drop TOC rows by their trailing page number. This is the core of what
libraries like edgartools do; the residual tail (class-based styling, split headers)
is why some filings come back `degraded` — for those the flat-text grep/read tools
still work, they just lack per-section boundaries.

Two derived files sit next to each `{SYM}_10K_{YEAR}.htm`:
  {SYM}_10K_{YEAR}.txt         normalized plain text (offsets address THIS file)
  {SYM}_10K_{YEAR}.index.json  {symbol, fy, char_len, quality, sections:[...]}

Rebuild both with `scripts/filings.py index`; nothing here re-downloads.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from lxml import html as LH

_YEAR_RE = re.compile(r"10K_(\d{4})")
_WS_RE = re.compile(r"\s+")

# An item header: "Item 1A." / "ITEM 7" at the start of a heading element's text.
_HDR_RE = re.compile(r"^\s*ITEM\s+(\d+[A-Z]?)\b[\.\s]", re.IGNORECASE)
# A trailing page number marks a table-of-contents row, not a body header.
_TOC_TAIL_RE = re.compile(r"\d+\s*$")

_ITEM_ORDER = [
    "1", "1A", "1B", "1C", "2", "3", "4",
    "5", "6", "7", "7A", "8", "9", "9A", "9B", "9C",
    "10", "11", "12", "13", "14", "15", "16",
]
_ITEM_RANK = {item: i for i, item in enumerate(_ITEM_ORDER)}

_ITEM_LABELS = {
    "1": "Business", "1A": "Risk Factors", "1B": "Unresolved Staff Comments",
    "1C": "Cybersecurity", "2": "Properties", "3": "Legal Proceedings",
    "4": "Mine Safety Disclosures", "5": "Market for Registrant's Common Equity",
    "6": "Selected Financial Data / [Reserved]", "7": "Management's Discussion and Analysis",
    "7A": "Quantitative and Qualitative Disclosures About Market Risk",
    "8": "Financial Statements", "9": "Changes in and Disagreements with Accountants",
    "9A": "Controls and Procedures", "9B": "Other Information",
    "9C": "Disclosure Regarding Foreign Jurisdictions", "10": "Directors and Officers",
    "11": "Executive Compensation", "12": "Security Ownership",
    "13": "Certain Relationships and Related Transactions",
    "14": "Principal Accountant Fees and Services",
    "15": "Exhibits and Financial Statement Schedules", "16": "Form 10-K Summary",
}

# The sections an analyst needs; a filing missing any is quality "degraded".
_CORE_ITEMS = {"1", "1A", "7", "7A", "8"}
# Core items that are always substantial — used to tell a real header slice from a
# TOC scrap. 7A and 8 are deliberately excluded: both are frequently one-line
# pointers ("information required by Item 7A is in Item 7"; Item 8's actual financial
# statements are often presented after Item 15 as F-pages), so they're legitimately
# short and a size floor would wrongly flag them. Detection still requires them present.
_SIZED_CORE = {"1", "1A", "7"}
_MIN_CORE_WORDS = 200

_BOLD_WEIGHTS = ("font-weight:bold", "font-weight:700", "font-weight:800", "font-weight:900")
# font-size >= 10pt (body text is usually 8-9pt in these filings).
_BIG_FONT_RE = re.compile(r"font-size:\s*(1[0-9]|[2-9][0-9])(\.\d+)?pt")


def year_of(path: Path) -> int | None:
    m = _YEAR_RE.search(Path(path).name)
    return int(m.group(1)) if m else None


# --- style-aware parsing -------------------------------------------------

def _in_href_anchor(el) -> bool:
    """True if the element sits inside a hyperlink (`<a href=...>`).

    Real section headers are plain text; the identical strings in the table of
    contents and in inline cross-references are hyperlinks. Named-target anchors
    (`<a name=/id=>` with no href) wrap some real headers, so those don't count.
    """
    node = el
    for _ in range(5):
        if node is None:
            break
        if isinstance(node.tag, str) and node.tag == "a" and node.get("href"):
            return True
        node = node.getparent()
    return False


def _is_heading_styled(el) -> bool:
    """True if the element (or a near ancestor) is rendered bold or in a large font.

    Walks a few ancestors to approximate the CSS cascade cheaply — these Workiva
    filings carry heading styling as inline `style=` attributes, so a shallow walk
    catches the common case. (Class-stylesheet filers are the residual `degraded` tail.)
    """
    node = el
    for _ in range(4):
        if node is None:
            break
        style = (node.get("style") or "").lower()
        if any(w in style for w in _BOLD_WEIGHTS) or _BIG_FONT_RE.search(style):
            return True
        node = node.getparent()
    return False


def parse_filing(htm_path: Path) -> tuple[str, list[dict]]:
    """Parse a filing into (normalized_text, sections).

    Single DOM walk builds the whitespace-collapsed text AND records the character
    offset where each detected item header begins, so section offsets always address
    the returned text exactly. Sections are the first heading-styled occurrence of
    each item, kept in strict document order.
    """
    root = LH.parse(str(htm_path)).getroot()

    # 1. Detect heading elements and MARK them with an attribute. (lxml re-wraps C
    #    nodes on each traversal, so Python object identity is unstable across passes;
    #    an attribute set on the node persists and is what the walk below reads.)
    for el in root.iter():
        if not isinstance(el.tag, str):     # skip comments / processing instructions
            continue
        txt = " ".join(el.text_content().split())
        if not (4 <= len(txt) <= 140):
            continue
        m = _HDR_RE.match(txt)
        if not m or m.group(1).upper() not in _ITEM_RANK:
            continue
        if _TOC_TAIL_RE.search(txt):            # table-of-contents row (trailing page no.)
            continue
        if _in_href_anchor(el):                 # TOC link or inline cross-reference
            continue
        # Real header: either rendered as a heading (bold/large) or shouted UPPERCASE
        # (some filers style headers via CSS classes the inline-style gate can't see).
        if not (_is_heading_styled(el) or txt == txt.upper()):
            continue
        el.set("_hdrmark", m.group(1).upper())

    # 2. Build collapsed text; record offset when a marked heading is entered.
    buf: list[str] = []
    total = 0
    raw_spans: list[tuple[str, int]] = []

    def emit(s: str) -> None:
        nonlocal total
        if not s:
            return
        s = _WS_RE.sub(" ", s)
        buf.append(s)
        total += len(s)

    def walk(el) -> None:
        if not isinstance(el.tag, str):     # comment / PI: no text; tail emitted by caller
            return
        mark = el.get("_hdrmark")
        if mark:
            raw_spans.append((mark, total))
        emit(el.text or "")
        for child in el:
            walk(child)
            emit(child.tail or "")

    walk(root)
    text = "".join(buf)

    # 3. Assemble sections. A body header can collide with an out-of-order duplicate
    #    (a plain-text uppercase TOC that our anchor/page-number filters miss, or a
    #    late uppercase cross-reference). "First occurrence" wins for filers whose
    #    stray match is late; "last occurrence" wins for those whose stray match is an
    #    early TOC. We build both and keep whichever yields more plausibly-sized core
    #    sections — no single global rule fits every filer.
    first_sections = _assemble(raw_spans, text, prefer_last=False)
    last_sections = _assemble(raw_spans, text, prefer_last=True)
    return text, max(first_sections, last_sections, key=_core_size_score)


def _assemble(raw_spans, text, prefer_last: bool) -> list[dict]:
    pick: dict[str, int] = {}
    for item, pos in raw_spans:
        if prefer_last:
            pick[item] = pos
        else:
            pick.setdefault(item, pos)
    kept: list[tuple[str, int]] = []
    for item, pos in sorted(pick.items(), key=lambda kv: _ITEM_RANK[kv[0]]):
        while kept and pos <= kept[-1][1]:       # enforce strictly increasing offsets
            kept.pop()
        kept.append((item, pos))
    sections = []
    for i, (item, start) in enumerate(kept):
        end = kept[i + 1][1] if i + 1 < len(kept) else len(text)
        sections.append({
            "id": item, "title": _ITEM_LABELS.get(item, f"Item {item}"),
            "start": start, "end": end, "word_count": len(text[start:end].split()),
        })
    return sections


def _core_size_score(sections: list[dict]) -> int:
    """How many size-bearing core items look real (a header slice, not a TOC scrap)."""
    words = {s["id"]: s["word_count"] for s in sections}
    return sum(1 for item in _SIZED_CORE if words.get(item, 0) >= _MIN_CORE_WORDS)


# --- index build/load ----------------------------------------------------

def txt_path(htm_path: Path) -> Path:
    return Path(htm_path).with_suffix(".txt")


def index_path(htm_path: Path) -> Path:
    return Path(htm_path).with_suffix(".index.json")


def build_index(htm_path: Path) -> dict:
    """Parse `htm_path`, write the `.txt` + `.index.json`, return the index."""
    htm_path = Path(htm_path)
    text, sections = parse_filing(htm_path)
    words = {s["id"]: s["word_count"] for s in sections}
    # "Present" means detected AND, for the substantial items, a plausible size —
    # a 3-word Item 1A is a mis-slice, not a section.
    def present(item: str) -> bool:
        if item not in words:
            return False
        return words[item] >= _MIN_CORE_WORDS if item in _SIZED_CORE else True
    missing = sorted((c for c in _CORE_ITEMS if not present(c)), key=lambda x: _ITEM_RANK[x])
    quality = "ok" if not missing else ("degraded" if sections else "failed")
    index = {
        "symbol": htm_path.name.split("_")[0],
        "fy": year_of(htm_path),
        "source": htm_path.name,
        "char_len": len(text),
        "quality": quality,
        "missing_core": missing,
        "sections": sections,
    }
    txt_path(htm_path).write_text(text, encoding="utf-8")
    index_path(htm_path).write_text(json.dumps(index, indent=2), encoding="utf-8")
    return index


def load_index(htm_path: Path) -> dict:
    return json.loads(index_path(htm_path).read_text(encoding="utf-8"))


def load_text(htm_path: Path) -> str:
    tp = txt_path(htm_path)
    if tp.exists():
        return tp.read_text(encoding="utf-8")
    return parse_filing(htm_path)[0]


# --- navigation primitives (serve-time) ----------------------------------
#
# Every result is *addressed* (item id + character offset) so the agent can feed a
# hit straight back into read_section next turn. Loop: outline() -> pick items ->
# grep()/read_section() -> expand window where signal is. grep and read_section work
# on the flat text, so they still function on `degraded` filings that lack a clean
# section map — the agent just navigates by keyword instead of by item.

try:
    from screener.config import SNP_ANNUAL_REPORTS_DIR as _SNP_DIR
except Exception:  # pragma: no cover - config always present in-repo
    _SNP_DIR = Path("data/raw/snp/annual_reports")


def list_filings(symbol: str, base_dir: Path | None = None) -> list[Path]:
    """Return every `{SYM}_10K_{YEAR}.htm` for a symbol, oldest year first."""
    base = Path(base_dir or _SNP_DIR) / symbol.upper()
    if not base.is_dir():
        return []
    return sorted(base.glob(f"{symbol.upper()}_10K_*.htm"), key=lambda p: year_of(p) or 0)


def resolve(symbol: str, fy: int | None = None, base_dir: Path | None = None) -> Path:
    """Resolve a symbol (+ optional fiscal year) to a filing path (latest if no fy)."""
    filings = list_filings(symbol, base_dir)
    if not filings:
        raise FileNotFoundError(
            f"No 10-K filings on disk for {symbol.upper()}. "
            f"Fetch: python scripts/fetch_annual_reports_snp.py --symbol {symbol.upper()}"
        )
    if fy is None:
        return filings[-1]
    for p in filings:
        if year_of(p) == fy:
            return p
    have = ", ".join(str(year_of(p)) for p in filings)
    raise FileNotFoundError(f"{symbol.upper()} has no FY{fy} filing. On disk: {have}")


def _ensure_index(htm_path: Path) -> dict:
    if index_path(htm_path).exists():
        return load_index(htm_path)
    return build_index(htm_path)


def outline(symbol: str, fy: int | None = None, base_dir: Path | None = None) -> dict:
    """Bird's-eye view: the section tree with word counts. Always the entry point."""
    htm = resolve(symbol, fy, base_dir)
    idx = _ensure_index(htm)
    return {
        "symbol": idx["symbol"], "fy": idx["fy"], "char_len": idx["char_len"],
        "quality": idx["quality"], "missing_core": idx.get("missing_core", []),
        "sections": [
            {"id": s["id"], "title": s["title"], "words": s["word_count"]}
            for s in idx["sections"]
        ],
    }


def read_section(
    symbol: str, item_id: str, fy: int | None = None,
    offset: int = 0, limit: int = 6000, base_dir: Path | None = None,
) -> dict:
    """Windowed read of one item. Pages via `offset`; footer says how much remains."""
    htm = resolve(symbol, fy, base_dir)
    idx = _ensure_index(htm)
    text = load_text(htm)
    sec = next((s for s in idx["sections"] if s["id"].upper() == item_id.upper()), None)
    if sec is None:
        have = ", ".join(s["id"] for s in idx["sections"]) or "(none detected)"
        raise KeyError(f"{symbol.upper()} FY{idx['fy']} has no Item {item_id}. Items: {have}")
    body = text[sec["start"]:sec["end"]]
    window = body[offset:offset + limit]
    remaining = max(0, len(body) - (offset + limit))
    return {
        "symbol": idx["symbol"], "fy": idx["fy"], "item": sec["id"], "title": sec["title"],
        "offset": offset, "returned": len(window), "remaining": remaining,
        "next_offset": offset + limit if remaining else None, "text": window,
    }


def grep(
    symbol: str, pattern: str, fy: int | None = None, item_id: str | None = None,
    context: int = 160, max_hits: int = 40, flags: int = re.IGNORECASE,
    base_dir: Path | None = None,
) -> dict:
    """Regex search returning addressed hits (item id + offset) to drill into.

    Works even when the section map is empty/degraded: with no sections it searches
    the whole normalized text and reports offsets against the document.
    """
    htm = resolve(symbol, fy, base_dir)
    idx = _ensure_index(htm)
    text = load_text(htm)
    sections = idx["sections"] or [{"id": "-", "start": 0, "end": len(text)}]
    if item_id:
        sections = [s for s in sections if s["id"].upper() == item_id.upper()]

    rx = re.compile(pattern, flags)
    hits = []
    for s in sections:
        for m in rx.finditer(text, s["start"], s["end"]):
            lo = max(s["start"], m.start() - context)
            hi = min(s["end"], m.end() + context)
            hits.append({"item": s["id"], "offset": m.start() - s["start"],
                         "excerpt": text[lo:hi]})
            if len(hits) >= max_hits:
                break
        if len(hits) >= max_hits:
            break
    return {"symbol": idx["symbol"], "fy": idx["fy"], "pattern": pattern,
            "hit_count": len(hits), "hits": hits}


def compare_section(
    symbol: str, item_id: str, fys: list[int] | None = None, base_dir: Path | None = None,
) -> dict:
    """Align one item across years for the same company (size + offsets per year)."""
    filings = list_filings(symbol, base_dir)
    years = fys or [year_of(p) for p in filings]
    out = []
    for fy in years:
        try:
            htm = resolve(symbol, fy, base_dir)
        except FileNotFoundError:
            continue
        idx = _ensure_index(htm)
        sec = next((s for s in idx["sections"] if s["id"].upper() == item_id.upper()), None)
        if sec:
            out.append({"fy": fy, "words": sec["word_count"],
                        "start": sec["start"], "end": sec["end"]})
    return {"symbol": symbol.upper(), "item": item_id.upper(), "years": out}
