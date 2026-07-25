#!/usr/bin/env python3
"""
DuckDB SQL query utility against data/screener.db.

Usage:
    python scripts/query.py "SELECT * FROM nse WHERE trailing_pe < 15 ORDER BY roe DESC LIMIT 10"
    python scripts/query.py --csv "SELECT symbol, sector, trailing_pe FROM nse"
    python scripts/query.py --market snp --csv "SELECT * FROM snp WHERE pe_forward < 20"
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import argparse

import duckdb
import pandas as pd

from core.config import BUILD_DB_DB_PATH


def query(sql: str, csv: bool = False, market: str | None = None) -> None:
    """Execute SQL against the screener database and print results."""
    db_path = str(BUILD_DB_DB_PATH)
    conn = duckdb.connect(db_path)

    # Add prefix for unqualified tables
    if market:
        sql = sql.replace("FROM nse", f"FROM {market}")
        sql = sql.replace("FROM snp", f"FROM {market}")

    try:
        result = conn.execute(sql).fetchdf()
        if csv:
            result.to_csv(sys.stdout, index=False)
        else:
            print(result.to_string(index=False))
    except Exception as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)
    finally:
        conn.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Query data/screener.db with SQL")
    parser.add_argument("sql", help="SQL query")
    parser.add_argument("--csv", action="store_true", help="Output as CSV")
    parser.add_argument("--market", choices=["nse", "snp"], help="Prefix tables with market")
    args = parser.parse_args()
    query(args.sql, csv=args.csv, market=args.market)
