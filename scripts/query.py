#!/usr/bin/env python3
"""
DuckDB SQL query utility against data/screener.db — thin wrapper over screener.query.

Usage:
    python scripts/query.py "SELECT * FROM nse WHERE trailing_pe < 15 ORDER BY roe DESC LIMIT 10"
    python scripts/query.py --csv "SELECT symbol, sector, trailing_pe FROM nse"
    python scripts/query.py --market snp --csv "SELECT * FROM snp WHERE pe_forward < 20"
"""

import argparse

from screener.db import query

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Query data/screener.db with SQL")
    parser.add_argument("sql", help="SQL query")
    parser.add_argument("--csv", action="store_true", help="Output as CSV")
    parser.add_argument("--market", choices=["nse", "snp"], help="Prefix tables with market")
    args = parser.parse_args()
    query(args.sql, csv=args.csv, market=args.market)
