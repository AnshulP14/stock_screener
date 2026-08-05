#!/usr/bin/env python3
"""Query data/screener.db with SQL."""

import argparse

from screener.db import query

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Query data/screener.db with SQL")
    parser.add_argument("sql", help="SQL query")
    parser.add_argument("--csv", action="store_true", help="Output as CSV")
    parser.add_argument("--market", choices=["nse", "snp"], help="Prefix tables with market")
    args = parser.parse_args()
    query(args.sql, csv=args.csv, market=args.market)
