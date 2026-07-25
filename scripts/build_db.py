#!/usr/bin/env python3
"""
Rebuild data/screener.db from curated JSON files.

Usage:
    python scripts/build_db.py
    python scripts/build_db.py --market nse
    python scripts/build_db.py --market snp
    python scripts/build_db.py --market all
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from core.index import rebuild_market_db
from core.config import (
    COMPANIES_DIR, INDICES_DIR,
    SNP_COMPANIES_DIR, SNP_INDICES_DIR,
)


def rebuild(market: str = "all") -> dict:
    """
    Rebuild screener.db for one or both markets.

    Args:
        market: "nse", "snp", or "all"

    Returns:
        dict with table names and row counts
    """
    results = {}

    if market in ("nse", "all"):
        if not INDICES_DIR.exists():
            print("  No NSE screening_summary.json found.", file=sys.stderr)
        else:
            results["nse"] = rebuild_market_db(
                market="nse",
                companies_dir=COMPANIES_DIR,
                indices_dir=INDICES_DIR,
            )

    if market in ("snp", "all"):
        if not SNP_INDICES_DIR.exists():
            print("  No SNP screening_summary.json found.", file=sys.stderr)
        else:
            results["snp"] = rebuild_market_db(
                market="snp",
                companies_dir=SNP_COMPANIES_DIR,
                indices_dir=SNP_INDICES_DIR,
            )

    return results


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Rebuild screener.db from curated JSON")
    parser.add_argument(
        "--market",
        choices=["nse", "snp", "all"],
        default="all",
        help="Which market to rebuild (default: all)",
    )
    args = parser.parse_args()

    result = rebuild(args.market)
    for m, r in result.items():
        print(f"\n  {m} tables rebuilt at {r['rebuilt_at']}:")
        for tbl, count in r["tables"].items():
            print(f"    {tbl}: {count} rows")
