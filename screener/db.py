"""Rebuild data/screener.db from curated JSON — the logic behind scripts/build_db.py."""

import sys

from .config import COMPANIES_DIR, INDICES_DIR, SNP_COMPANIES_DIR, SNP_INDICES_DIR
from .index import drop_market_tables, rebuild_market_db


def rebuild(market: str = "all") -> dict:
    """
    Rebuild screener.db for one or both markets.

    Args:
        market: "nse", "snp", or "all"

    Returns:
        dict with table names and row counts (only for markets actually rebuilt)
    """
    results = {}

    if market in ("nse", "all"):
        if not INDICES_DIR.exists():
            print("  No NSE screening_summary.json found.", file=sys.stderr)
            drop_market_tables("nse")
        else:
            results["nse"] = rebuild_market_db(
                market="nse",
                companies_dir=COMPANIES_DIR,
                indices_dir=INDICES_DIR,
            )

    if market in ("snp", "all"):
        if not SNP_INDICES_DIR.exists():
            print("  No SNP screening_summary.json found.", file=sys.stderr)
            drop_market_tables("snp")
        else:
            results["snp"] = rebuild_market_db(
                market="snp",
                companies_dir=SNP_COMPANIES_DIR,
                indices_dir=SNP_INDICES_DIR,
            )

    return results
