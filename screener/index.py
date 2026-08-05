"""Manage company JSON, indices, and the data manifest."""

import json
import threading
from collections.abc import Iterator
from datetime import datetime
from pathlib import Path

from .config import INDICES_DIR, MANIFEST_PATH
from .summary import compute_industry_comparison, compute_industry_stats, compute_summary_row

# ── Store helpers (former store.py) ────────────────────────────────

_locks_guard = threading.Lock()
_locks: dict[str, threading.Lock] = {}


def _lock_for(symbol: str) -> threading.Lock:
    """One lock per symbol, shared by every caller in this process -- so two
    stages merging into the same company file at once serialize instead of
    racing (last-write-wins would silently drop one stage's fields)."""
    with _locks_guard:
        return _locks.setdefault(symbol, threading.Lock())


def _company_path(dir_path: Path, symbol: str) -> Path:
    return dir_path / f"{symbol}.json"


def load_company(dir_path: Path, symbol: str) -> dict:
    """Load a company JSON. Raises FileNotFoundError if missing."""
    with open(_company_path(dir_path, symbol)) as f:
        return json.load(f)


def save_company(dir_path: Path, symbol: str, data: dict) -> None:
    """Atomically write a company JSON (tmp + rename)."""
    dir_path.mkdir(parents=True, exist_ok=True)
    tmp = dir_path / f".{symbol}.json.tmp"
    tmp.write_text(json.dumps(data, indent=2))
    tmp.replace(_company_path(dir_path, symbol))


def merge_company(dir_path: Path, symbol: str, updates: dict) -> None:
    """Atomically shallow-merge updates into a company JSON."""
    with _lock_for(symbol):
        try:
            existing = load_company(dir_path, symbol)
        except FileNotFoundError:
            existing = {}
        existing.update(updates)
        save_company(dir_path, symbol, existing)


def delete_company(dir_path: Path, symbol: str) -> None:
    """Remove a company file. Silently no-ops if missing."""
    _company_path(dir_path, symbol).unlink(missing_ok=True)


def list_symbols(dir_path: Path) -> list[str]:
    """Sorted list of company symbols (file stems) on disk."""
    return sorted(p.stem for p in dir_path.glob("*.json"))


def iter_companies(dir_path: Path) -> Iterator[tuple[Path, dict]]:
    """Yield readable `(path, company)` pairs."""
    for path in sorted(dir_path.glob("*.json")):
        try:
            yield path, json.loads(path.read_text())
        except (OSError, json.JSONDecodeError):
            pass


# ── Index building ──────────────────────────────────────────────────

def build_indices(
    *,
    companies_dir: Path,
    indices_dir: Path = INDICES_DIR,
) -> dict | None:
    """Build summary and industry indices, then update company comparisons."""
    print("  Building indices...")
    loaded = list(iter_companies(companies_dir))

    if not loaded:
        print("  No companies found. Nothing to build.")
        return

    all_companies = [c for _, c in loaded]
    industry_stats = compute_industry_stats(all_companies)
    summary = [compute_summary_row(c, industry_stats) for c in all_companies]

    # ── Write screening_summary ──
    summary_json = {
        "generated_at": datetime.now().isoformat(),
        "total_companies": len(summary),
        "companies": summary,
    }
    indices_dir.mkdir(parents=True, exist_ok=True)
    with open(indices_dir / "screening_summary.json", "w") as f:
        json.dump(summary_json, f, indent=2)

    # ── Write industry_stats (strip internal _values) ──
    industry_out = {
        ind: {
            "company_count": stats["company_count"],
            "metrics": {
                k: {x: round(v, 4) if isinstance(v, float) else v
                    for x, v in (m or {}).items() if x != "_values"}
                for k, m in stats["metrics"].items()
            },
        }
        for ind, stats in industry_stats.items()
    }
    with open(indices_dir / "industry_stats.json", "w") as f:
        json.dump(industry_out, f, indent=2)

    # ── Write each company's own industry_comparison back onto its file ──
    updated = 0
    for path, company in loaded:
        comparison = compute_industry_comparison(company, industry_stats)
        if company.get("industry_comparison") == comparison:
            continue
        company["industry_comparison"] = comparison
        save_company(companies_dir, path.stem, company)
        updated += 1

    print(f"  screening_summary.json: {len(summary)} companies")
    print(f"  industry_stats.json: {len(industry_stats)} industries")
    if updated:
        print(f"  industry_comparison: updated {updated} company file(s)")
    return {"summary": len(summary), "industries": len(industry_stats), "companies": len(all_companies)}


def update_manifest(market: str, entry: dict, *, touch_generated_at: bool = True) -> None:
    """Merge a market manifest entry, optionally updating its generation time."""
    manifest: dict = {}
    if MANIFEST_PATH.exists():
        try:
            with open(MANIFEST_PATH) as f:
                manifest = json.load(f)
        except Exception:
            manifest = {}
    merged = {**manifest.get(market, {}), **entry}
    if touch_generated_at:
        merged["generated_at"] = datetime.now().isoformat()
    manifest[market] = merged
    MANIFEST_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(MANIFEST_PATH, "w") as f:
        json.dump(manifest, f, indent=2)
