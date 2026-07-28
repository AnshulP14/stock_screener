"""S&P 500 data update — thin wrapper preserving the existing public
interface (scripts/cli.py, scripts/data_refresh.py target this unchanged).
All orchestration logic now lives in run_pipeline, shared with nse.py; see
screener.market.SNP for what makes this market's pipeline behave the way
it does (currency, fiscal-year rule, no ticker suffix, days-old-based
staleness, no enrichment steps yet)."""

from screener.market import SNP
from screener.markets import run_pipeline


def run(
    *,
    mode: str = "incremental",
    symbols: list[str] | None = None,
    workers: int | None = None,
    dry_run: bool = False,
    days_old: int = 7,
) -> dict:
    """
    Unified S&P 500 data refresh entry point.

    Args:
        mode: full | incremental | sync-universe | rebuild
        symbols: specific symbols to fetch (all if None)
        workers: parallel fetch workers (default: MAX_WORKERS)
        dry_run: show what would be fetched
        days_old: staleness threshold for incremental mode

    Returns:
        dict: {fetched, failed, skipped, elapsed}
    """
    return run_pipeline(SNP, mode=mode, symbols=symbols, workers=workers, dry_run=dry_run, days_old=days_old)
