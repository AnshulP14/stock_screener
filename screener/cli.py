"""Refresh NSE500 and S&P500 data.
Run `python scripts/data_refresh.py --help` for options.
"""

import argparse
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

from screener.market import ALL_MODES, MARKETS
from screener.pipeline import run_pipeline


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
        choices=ALL_MODES,
        default="quick-sync",
        help="Refresh mode (default: quick-sync)",
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
        "--skip-reports",
        action="store_true",
        help="Skip the annual report / 10-K download+index step (the slow leg) -- "
             "independent of --mode, e.g. a fast `--mode full --skip-reports` run",
    )
    args = parser.parse_args()

    market_configs = [MARKETS[key] for key in (("nse", "snp") if args.market == "all" else [args.market])]

    jobs = [
        (mc.label, mc, {
            "mode": args.mode,
            "symbols": args.symbols,
            "workers": args.workers,
            "days_old": args.days_old,
            "fetch_reports": not args.skip_reports,
        })
        for mc in market_configs
    ]

    # Markets run concurrently; host limiters coordinate their requests.
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
