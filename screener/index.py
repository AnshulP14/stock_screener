"""Build indices: screening_summary + industry_stats + DuckDB."""

import json
from datetime import datetime
from pathlib import Path

import pandas as pd

from .config import (
    BUILD_DB_DB_PATH,
    COMPANIES_DIR,
    INDICES_DIR,
    SNP_COMPANIES_DIR,
    SNP_INDICES_DIR,
    MANIFEST_PATH,
)
from .summary import compute_industry_comparison, compute_industry_stats, compute_summary_row


# ── Index building ──────────────────────────────────────────────────

def build_indices(
    *,
    companies_dir: Path = COMPANIES_DIR,
    indices_dir: Path = INDICES_DIR,
) -> dict:
    """Build screening_summary.json and industry_stats.json from company
    JSONs, and write each company's own industry_comparison back onto its
    file -- that needs every company loaded first (see
    screener.summary.compute_industry_comparison), so it can't happen at
    per-symbol fetch time the way the rest of the company JSON is built.

    Args:
        companies_dir: directory containing company JSON files
        indices_dir: directory to write screening_summary.json and industry_stats.json

    Returns:
        dict with summary count, industry count, and company count
    """
    print("  Building indices...")
    loaded = []
    for p in sorted(companies_dir.glob("*.json")):
        try:
            loaded.append((p, json.load(open(p))))
        except Exception:
            pass

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
        tmp = path.parent / f".{path.name}.tmp"
        tmp.write_text(json.dumps(company, indent=2))
        tmp.replace(path)  # atomic: no torn files
        updated += 1

    print(f"  screening_summary.json: {len(summary)} companies")
    print(f"  industry_stats.json: {len(industry_stats)} industries")
    if updated:
        print(f"  industry_comparison: updated {updated} company file(s)")
    return {"summary": len(summary), "industries": len(industry_stats), "companies": len(all_companies)}


def rebuild_market_db(
    *,
    market: str,
    companies_dir: Path,
    indices_dir: Path,
) -> dict:
    """Rebuild DuckDB tables for a single market.

    Args:
        market: "nse" or "snp"
        companies_dir: directory containing company JSON files
        indices_dir: directory containing screening_summary.json and industry_stats.json

    Returns:
        dict with table counts
    """
    import duckdb

    summary_path = indices_dir / "screening_summary.json"
    companies_glob = str(companies_dir / "*.json")
    stats_path = indices_dir / "industry_stats.json"
    prefix = market.lower()

    summary = json.load(open(summary_path))
    companies = summary.get("companies", [])

    con = duckdb.connect(str(BUILD_DB_DB_PATH))
    try:
        con.register("_summary_rows", pd.DataFrame(companies))
        con.execute(f"CREATE OR REPLACE TABLE {prefix} AS SELECT * FROM _summary_rows")

        con.execute(f"""
            CREATE OR REPLACE TABLE {prefix}_companies AS
            SELECT * FROM read_json_auto(?, union_by_name=true)
        """, [companies_glob])
        company_count = con.execute(f"SELECT count(*) FROM {prefix}_companies").fetchone()[0]

        stats_count = 0
        if stats_path.exists():
            stats = json.load(open(stats_path))
            rows = [{"industry": k, **v} for k, v in stats.items()]
            con.register("_stats_rows", pd.DataFrame(rows))
            con.execute(
                f"CREATE OR REPLACE TABLE {prefix}_industry_stats AS SELECT * FROM _stats_rows"
            )
            stats_count = len(rows)
    finally:
        con.close()

    result = {
        "rebuilt_at": datetime.now().isoformat(),
        "tables": {
            f"{prefix}": len(companies),
            f"{prefix}_companies": company_count,
            f"{prefix}_industry_stats": stats_count,
        },
    }
    update_manifest(market, {"db": result}, touch_generated_at=False)
    return result


def drop_market_tables(market: str) -> None:
    """Drop a market's tables so a market whose curated JSON no longer exists
    doesn't leave stale rows behind in screener.db (rebuild_market_db only
    replaces tables for markets it actually rebuilds)."""
    import duckdb

    prefix = market.lower()
    con = duckdb.connect(str(BUILD_DB_DB_PATH))
    try:
        for suffix in ("", "_companies", "_industry_stats"):
            con.execute(f"DROP TABLE IF EXISTS {prefix}{suffix}")
    finally:
        con.close()


def update_manifest(market: str, entry: dict, *, touch_generated_at: bool = True) -> None:
    """Merge `entry` into data/manifest.json under `market`, preserving existing
    keys (e.g. a `db` rebuild shouldn't wipe out `total_companies` written by the
    last fetch run, and vice versa). `generated_at` tracks data freshness — pass
    touch_generated_at=False for updates (like a DB rebuild) that aren't a data
    refresh, so it keeps reflecting the last real fetch/transform run."""
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
