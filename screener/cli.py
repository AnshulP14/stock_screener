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
from concurrent.futures import ThreadPoolExecutor, as_completed

from screener.market import MARKETS, coerce_mode
from screener.markets import run_pipeline


def _run_market(label: str, market, kwargs: dict):
    print(f"\n{'─' * 60}")
    print(f"  {label}")
    print(f"{'─' * 60}")
    return label, run_pipeline(market, **kwargs)


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

    market_configs = []
    for key in ("nse", "snp") if args.market == "all" else [args.market]:
        mc = MARKETS[key]
        mode = coerce_mode(args.mode, mc)
        if mode != args.mode:
            print(f"  {mc.label}: '{args.mode}' not applicable, using '{mode}'")
        market_configs.append(mc)

    if not market_configs:
        print("Nothing to do.", file=sys.stderr)
        sys.exit(1)

    jobs = []
    for mc in market_configs:
        mode = coerce_mode(args.mode, mc)
        kwargs = {
            "mode": mode,
            "symbols": args.symbols,
            "workers": args.workers,
            "dry_run": args.dry_run,
            "days_old": args.days_old,
        }
        if mc.id == "nse":
            kwargs["no_transform"] = args.no_transform
        jobs.append((mc.label, mc, kwargs))

    # Markets run concurrently as separate threads, but yfinance itself stays
    # a single sequential stream (screener.fetch._YFINANCE_LOCK) regardless --
    # this only buys overlap between NSE's Screener.in enrichment and SNP's
    # yfinance fetch, two genuinely different hosts.
    start = time.time()
    results = {}
    errors = []

    with ThreadPoolExecutor(max_workers=len(jobs)) as pool:
        futures = [pool.submit(_run_market, label, mc, kwargs) for label, mc, kwargs in jobs]
        for future in as_completed(futures):
            try:
                label, result = future.result()
                results[label] = result
            except Exception as e:
                errors.append(str(e))
                print(f"\n  ERROR: {e}")

    if errors:
        sys.exit(1)

    elapsed = time.time() - start
    print(f"\n\n{'═' * 60}")
    print(f"  Total time: {elapsed:.1f}s")
    print(f"{'═' * 60}")
    for label, result in results.items():
        print(f"  {label}: {result['fetched']} fetched, {result['failed']} failed, {result['skipped']} skipped")
    print(f"{'═' * 60}\n")
