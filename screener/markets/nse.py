"""NSE500 data update — thin wrapper preserving the existing public
interface (scripts/cli.py, scripts/data_refresh.py target this unchanged).
All orchestration logic now lives in run_pipeline, shared with us.py; see
screener.market.NSE for what makes this market's pipeline behave the way
it does (currency, fiscal-year rule, ticker suffix, staleness policy,
shareholding/credit-ratings enrichment, raw CSV export)."""

from screener.market import NSE
from screener.markets import run_pipeline


def run(
    *,
    mode: str = "incremental",
    symbols: list[str] | None = None,
    workers: int | None = None,
    dry_run: bool = False,
    days_old: int = 7,
    no_transform: bool = False,
) -> dict:
    """
    Unified NSE500 data refresh entry point.

    Args:
        mode: full | incremental | quick | sync-universe | transform-only
        symbols: specific symbols to fetch (all if None)
        workers: parallel fetch workers (default: MAX_WORKERS)
        dry_run: show what would be fetched
        days_old: staleness threshold for incremental mode
        no_transform: skip index/DB rebuild

    Returns:
        dict: {fetched, failed, skipped, elapsed}
    """
    return run_pipeline(
        NSE,
        mode=mode,
        symbols=symbols,
        workers=workers,
        dry_run=dry_run,
        days_old=days_old,
        no_transform=no_transform,
    )
