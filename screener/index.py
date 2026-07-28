"""Build indices: screening_summary + industry_stats + DuckDB."""

import json
import math
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


def _safe_float(v):
    if v is None or (isinstance(v, float) and (math.isnan(v) or math.isinf(v))):
        return None
    try:
        f = float(v)
        return f if not (math.isnan(f) or math.isinf(f)) else None
    except (ValueError, TypeError):
        return None


def _cagr(values):
    valid = [(i, v) for i, v in enumerate(values) if v is not None and v > 0]
    if len(valid) < 2:
        return None
    return (valid[-1][1] / valid[0][1]) ** (1 / (valid[-1][0] - valid[0][0])) - 1


def _percentile(value, sorted_values):
    if value is None or not sorted_values:
        return None
    count = sum(1 for v in sorted_values if v < value)
    return min(99, max(0, int(100 * count / len(sorted_values))))


# ── Index building ──────────────────────────────────────────────────

METRICS_FOR_PERCENTILE = [
    ("price_metrics", "trailing_pe", "pe"),
    ("price_metrics", "forward_pe", "forward_pe"),
    ("price_metrics", "price_to_book", "price_to_book"),
    ("profitability", "profit_margin", "margin"),
    ("profitability", "operating_margin", "operating_margin"),
    ("profitability", "return_on_equity", "roe"),
    ("profitability", "return_on_assets", "roa"),
    ("financial_health", "debt_to_equity", "debt_to_equity"),
    ("price_metrics", "enterprise_to_ebitda", "ev_to_ebitda"),
    ("historical_trends.revenue", "cagr_3yr", "revenue_cagr_3yr"),
    ("historical_trends.eps", "cagr_3yr", "eps_cagr_3yr"),
]


def _metric_value(c: dict, group: str, field: str):
    """Look up a metric value from a company dict via its current_snapshot
    sub-struct (e.g. "profitability") or a historical_trends.<series> path."""
    if group.startswith("historical_trends."):
        sub = c.get("historical_trends", {}).get(group.split(".", 1)[1], {})
    else:
        sub = c.get("current_snapshot", {}).get(group, {})
    return _safe_float(sub.get(field))

def build_indices(
    *,
    companies_dir: Path = COMPANIES_DIR,
    indices_dir: Path = INDICES_DIR,
) -> dict:
    """Build screening_summary.json and industry_stats.json from company JSONs.

    Args:
        companies_dir: directory containing company JSON files
        indices_dir: directory to write screening_summary.json and industry_stats.json

    Returns:
        dict with summary count, industry count, and company count
    """
    print("  Building indices...")
    all_companies = []
    for p in sorted(companies_dir.glob("*.json")):
        try:
            all_companies.append(json.load(open(p)))
        except Exception:
            pass

    if not all_companies:
        print("  No companies found. Nothing to build.")
        return

    # ── Industry stats ──
    groups: dict[str, list] = {}
    for c in all_companies:
        ind = c.get("industry", "Unknown")
        if ind not in groups:
            groups[ind] = []
        groups[ind].append(c)

    industry_stats = {}
    for ind, companies in groups.items():
        metrics_stats = {}
        for group, field, key in METRICS_FOR_PERCENTILE:
            vals = [_metric_value(c, group, field) for c in companies]
            vals = [v for v in vals if v is not None]
            if len(vals) >= 2:
                sorted_vals = sorted(vals)
                n = len(sorted_vals)
                median = sorted_vals[n // 2] if n % 2 == 1 else (sorted_vals[n // 2 - 1] + sorted_vals[n // 2]) / 2
                metrics_stats[key] = {
                    "median": median,
                    "mean": sum(vals) / n,
                    "std": (sum((v - sum(vals) / n) ** 2 for v in vals) / n) ** 0.5,
                    "p25": sorted_vals[n // 4],
                    "p75": sorted_vals[3 * n // 4],
                    "min": sorted_vals[0],
                    "max": sorted_vals[-1],
                    "count": n,
                    "_values": sorted_vals,
                }
            else:
                metrics_stats[key] = None
        industry_stats[ind] = {
            "company_count": len(companies),
            "metrics": metrics_stats,
        }

    # ── Summary entries (with percentiles) ──
    summary = []
    for c in all_companies:
        ind = c.get("industry", "Unknown")
        snap = c.get("current_snapshot", {})
        price = snap.get("price_metrics", {})
        prof = snap.get("profitability", {})
        health = snap.get("financial_health", {})
        size = snap.get("size", {})
        trends = c.get("historical_trends", {})
        ind_stats = industry_stats.get(ind, {}).get("metrics", {})

        entry = {
            "symbol": c.get("symbol", ""),
            "company_name": c.get("company_name", ""),
            "sector": c.get("sector", ""),
            "industry": ind,
            "market_cap": _safe_float(size.get("market_cap")),
            "currency": c.get("currency"),
            "trailing_pe": _safe_float(price.get("trailing_pe")),
            "forward_pe": _safe_float(price.get("forward_pe")),
            "price_to_book": _safe_float(price.get("price_to_book")),
            "roe": _safe_float(prof.get("return_on_equity")),
            "profit_margin": _safe_float(prof.get("profit_margin")),
            "debt_to_equity": _safe_float(health.get("debt_to_equity")),
            "beta": _safe_float(health.get("beta")),
            "revenue_cagr_3yr": trends.get("revenue", {}).get("cagr_3yr"),
            "net_income_cagr_3yr": trends.get("net_income", {}).get("cagr_3yr"),
            "eps_cagr_3yr": trends.get("eps", {}).get("cagr_3yr"),
        }

        for group, field, key in METRICS_FOR_PERCENTILE:
            val = _metric_value(c, group, field)
            stat = ind_stats.get(key)
            entry[f"{key}_percentile"] = _percentile(val, stat.get("_values")) if stat else None

        # Shareholding latest/trend (always present, null if missing). S&P profiles
        # always carry an explicit `"shareholding": null` key, so `.get(k, {})`
        # alone won't fall back — `or {}` is needed to catch the None value too.
        sh = c.get("shareholding") or {}
        sh_trends = sh.get("trends") or {}
        for holder in ("promoter", "fii", "dii", "public"):
            vals = sh.get(holder, [])
            entry[f"{holder}_latest"] = vals[-1] if vals else None
            entry[f"{holder}_trend"] = sh_trends.get(holder)

        summary.append(entry)

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

    print(f"  screening_summary.json: {len(summary)} companies")
    print(f"  industry_stats.json: {len(industry_stats)} industries")
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
