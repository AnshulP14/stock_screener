"""Rebuild and query data/screener.db."""

import json
import re
import sys
from datetime import datetime
from pathlib import Path

import duckdb
import pandas as pd

from .config import BUILD_DB_DB_PATH
from .index import update_manifest
from .market import NSE, SNP, MarketConfig
from .summary import COLUMN_DESCRIPTIONS

# ── DuckDB query ────────────────────────────────────────────────────

def query(sql: str, csv: bool = False, market: str | None = None) -> None:
    """Execute SQL against the screener database and print results."""
    conn = duckdb.connect(str(BUILD_DB_DB_PATH), read_only=True)

    # Add prefix for unqualified tables
    if market:
        sql = sql.replace("FROM nse", f"FROM {market}")
        sql = sql.replace("FROM snp", f"FROM {market}")

    try:
        result = conn.execute(sql).fetchdf()
        if re.fullmatch(r"describe(?: table)? (?:nse|snp);?", sql.strip(), re.IGNORECASE):
            result["description"] = result["column_name"].map(COLUMN_DESCRIPTIONS).fillna("")
        if csv:
            result.to_csv(sys.stdout, index=False)
        else:
            print(result.to_string(index=False))
    except Exception as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)
    finally:
        conn.close()


# ── DB rebuild ──────────────────────────────────────────────────────

def rebuild(market: str | MarketConfig = "all") -> dict:
    """Rebuild one or both markets and return their table row counts."""
    results = {}
    if isinstance(market, MarketConfig):
        targets = [market]
    elif market == "all":
        targets = [NSE, SNP]
    else:
        targets = {"nse": NSE, "snp": SNP}[market]
        targets = [targets]

    for mc in targets:
        if not mc.indices_dir.exists():
            print(f"  No {mc.label} screening_summary.json found.", file=sys.stderr)
            drop_market_tables(mc.id)
        else:
            results[mc.id] = rebuild_market_db(
                market=mc.id,
                companies_dir=mc.companies_dir,
                indices_dir=mc.indices_dir,
            )

    return results


def rebuild_market_db(
    *,
    market: str,
    companies_dir: Path,
    indices_dir: Path,
) -> dict:
    """Rebuild one market's DuckDB tables and return their row counts."""
    summary_path = indices_dir / "screening_summary.json"
    companies_glob = str(companies_dir / "*.json")
    stats_path = indices_dir / "industry_stats.json"
    prefix = market.lower()

    with open(summary_path) as f:
        summary = json.load(f)
    companies = summary.get("companies", [])

    con = duckdb.connect(str(BUILD_DB_DB_PATH))
    try:
        con.register("_summary_rows", pd.DataFrame(companies))
        con.execute(f"CREATE OR REPLACE TABLE {prefix} AS SELECT * FROM _summary_rows")

        con.execute(f"""
            CREATE OR REPLACE TABLE {prefix}_companies AS
            SELECT * FROM read_json_auto(?, union_by_name=true)
        """, [companies_glob])
        count_row = con.execute(f"SELECT count(*) FROM {prefix}_companies").fetchone()
        company_count = count_row[0] if count_row else 0

        stats_count = 0
        if stats_path.exists():
            with open(stats_path) as f:
                stats = json.load(f)
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
    prefix = market.lower()
    con = duckdb.connect(str(BUILD_DB_DB_PATH))
    try:
        for suffix in ("", "_companies", "_industry_stats"):
            con.execute(f"DROP TABLE IF EXISTS {prefix}{suffix}")
    finally:
        con.close()
