#!/usr/bin/env python3
"""
Rebuild data/screener.db from curated JSON files — thin wrapper over screener.db.

Usage:
    python scripts/build_db.py
    python scripts/build_db.py --market nse
    python scripts/build_db.py --market snp
    python scripts/build_db.py --market all
"""

import argparse

from screener.db import rebuild

if __name__ == "__main__":
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
