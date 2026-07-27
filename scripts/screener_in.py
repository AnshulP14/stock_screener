#!/usr/bin/env python3
"""
Screener.in shareholding patterns and credit ratings scrapers.

Usage:
    python scripts/screener_in.py shareholding RELIANCE TCS
    python scripts/screener_in.py credit_ratings RELIANCE TCS
    python scripts/screener_in.py shareholding --stale
    python scripts/screener_in.py credit_ratings --stale
"""

import argparse
import sys

from screener.enrich import process_symbols, get_stale_symbols

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Fetch shareholding patterns and credit ratings from Screener.in")
    parser.add_argument(
        "dataset",
        choices=["shareholding", "credit_ratings"],
        help="Dataset to fetch",
    )
    parser.add_argument("--stale", action="store_true", help="Find and fetch stale companies")
    parser.add_argument(
        "--symbols",
        nargs="+",
        metavar="SYM",
        help="Specific symbols to update",
    )

    args = parser.parse_args()

    if args.stale:
        symbols = get_stale_symbols(args.dataset)
        print(f"\nFound {len(symbols)} stale {args.dataset}")
    elif args.symbols:
        symbols = args.symbols
    else:
        parser.print_help()
        sys.exit(1)

    ok, skipped, failed = process_symbols(symbols, args.dataset)
    print(f"\n{args.dataset}: {ok} updated, {skipped} skipped, {failed} failed")

    if failed > 0:
        sys.exit(1)
