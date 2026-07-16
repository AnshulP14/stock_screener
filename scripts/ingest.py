#!/usr/bin/env python3
"""
NSE500 Data Ingestion Pipeline

Fetch and update NSE500 fundamental stock data from yfinance.
Run this script to populate the data directory or refresh stale data.

Modes:
  (default)         Incremental — refresh stocks older than --days-old (default 7)
  --quick           Top 50 stocks by market cap (fast, ~5 min)
  --full            All 500 stocks (slow, ~60–90 min)
  --symbols SYM...  Specific tickers only (e.g. --symbols RELIANCE TCS INFY)
  --sync-universe   Remove delisted stocks, fetch newly added ones
  --rebuild         Rebuild JSON indices from existing CSVs (no network fetch)
  --dry-run         Show what would be fetched, without fetching

Examples:
    python scripts/ingest.py                      # Incremental update
    python scripts/ingest.py --quick              # Quick top-50 refresh
    python scripts/ingest.py --full               # Full 500-stock refresh
    python scripts/ingest.py --symbols RELIANCE   # Single stock
    python scripts/ingest.py --sync-universe      # Sync delisted/new stocks
    python scripts/ingest.py --rebuild            # Rebuild index only
    python scripts/ingest.py --dry-run            # Preview what's stale
"""

import argparse
import sys
from pathlib import Path

# Ensure scripts dir is importable
sys.path.insert(0, str(Path(__file__).parent))


def main() -> None:
    parser = argparse.ArgumentParser(
        description="NSE500 data ingestion pipeline",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )

    mode_group = parser.add_mutually_exclusive_group()
    mode_group.add_argument(
        "--quick",
        action="store_true",
        help="Refresh top 50 stocks by market cap (~5 min)",
    )
    mode_group.add_argument(
        "--full",
        action="store_true",
        help="Full refresh — all 500 stocks (60–90 min)",
    )
    mode_group.add_argument(
        "--symbols",
        nargs="+",
        metavar="SYMBOL",
        help="Refresh specific tickers, e.g. --symbols RELIANCE TCS",
    )
    mode_group.add_argument(
        "--sync-universe",
        action="store_true",
        help="Remove delisted stocks from index, fetch newly added ones",
    )
    mode_group.add_argument(
        "--rebuild",
        action="store_true",
        help="Rebuild JSON indices from existing CSVs only (no fetch)",
    )

    parser.add_argument(
        "--days-old",
        type=int,
        default=7,
        metavar="N",
        help="Incremental: re-fetch stocks older than N days (default: 7)",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=3,
        metavar="N",
        help="Parallel fetch workers (default: 3)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Preview what would be fetched without fetching",
    )

    args = parser.parse_args()

    # Map ingest.py flags → update_data.py flags
    from update_data import main as _run

    # Rebuild update_data's argv
    argv = []
    if args.full:
        argv.append("--full")
    elif args.symbols:
        argv.extend(["--symbols"] + args.symbols)
    elif args.rebuild:
        argv.append("--transform-only")
    elif args.quick:
        argv.append("--quick")
    elif args.sync_universe:
        argv.append("--sync-universe")
    # else: incremental (default, no flag needed)

    argv.extend(["--days-old", str(args.days_old)])
    argv.extend(["--workers", str(args.workers)])
    if args.dry_run:
        argv.append("--dry-run")

    sys.argv = [sys.argv[0]] + argv
    _run()


if __name__ == "__main__":
    main()
