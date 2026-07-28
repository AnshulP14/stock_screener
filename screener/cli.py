"""
Unified data refresh CLI — one command for US and India markets.

Usage:
    python scripts/cli.py                          # Both markets, incremental
    python scripts/cli.py --market nse --mode full # NSE500 full
    python scripts/cli.py --market snp --mode full # S&P 500 full
    python scripts/cli.py --market nse --mode quick # Top 50 NSE500
    python scripts/cli.py --market nse --symbols RELIANCE TCS
    python scripts/cli.py --dry-run                # Preview only
    python scripts/cli.py --workers 5              # More parallel workers
"""

import argparse
import sys
import time

from screener.markets import nse as nse_market
from screener.markets import snp as snp_market


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Unified data refresh for US and India markets",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )

    parser.add_argument(
        "--market",
        choices=["nse", "snp", "all"],
        default="all",
        help="Which market to refresh (default: all)",
    )
    parser.add_argument(
        "--mode",
        choices=["incremental", "full", "quick", "sync-universe", "transform-only", "rebuild"],
        default="incremental",
        help="Refresh mode (default: incremental)",
    )
    parser.add_argument(
        "--symbols",
        nargs="+",
        metavar="SYM",
        help="Specific symbols to update",
    )
    parser.add_argument(
        "--days-old",
        type=int,
        default=7,
        metavar="N",
        help="Incremental threshold: re-fetch data older than N days (default: 7)",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=None,
        metavar="N",
        help="Parallel fetch workers (default: per-market config)",
    )
    parser.add_argument(
        "--no-transform",
        action="store_true",
        help="Skip index rebuild after fetching (NSE500 only)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be fetched without fetching",
    )

    args = parser.parse_args()

    markets = []
    if args.market in ("nse", "all"):
        markets.append(("NSE500", nse_market.run))
    if args.market in ("snp", "all"):
        markets.append(("S&P 500", snp_market.run))

    if not markets:
        print("Nothing to do.", file=sys.stderr)
        sys.exit(1)

    start = time.time()
    results = {}

    for label, run_fn in markets:
        mode = args.mode

        if label == "S&P 500" and mode in ("quick", "transform-only"):
            print(f"  S&P 500: '{mode}' not applicable, using 'incremental'")
            mode = "incremental"
        if label == "NSE500" and mode == "rebuild":
            print("  NSE500: 'rebuild' not applicable, using 'transform-only'")
            mode = "transform-only"

        kwargs = {
            "mode": mode,
            "symbols": args.symbols,
            "workers": args.workers,
            "dry_run": args.dry_run,
            "days_old": args.days_old,
        }
        if label == "NSE500":
            kwargs["no_transform"] = args.no_transform

        print(f"\n{'─' * 60}")
        print(f"  {label}")
        print(f"{'─' * 60}")
        try:
            result = run_fn(**kwargs)
            results[label] = result
        except Exception as e:
            print(f"\n  ERROR: {e}")
            sys.exit(1)

    elapsed = time.time() - start
    print(f"\n\n{'═' * 60}")
    print(f"  Total time: {elapsed:.1f}s")
    print(f"{'═' * 60}")
    for label, result in results.items():
        print(f"  {label}: {result['fetched']} fetched, {result['failed']} failed, {result['skipped']} skipped")
    print(f"{'═' * 60}\n")
