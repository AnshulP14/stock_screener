"""S&P 500 data update — unified pipeline entry point."""

import json
import time
from datetime import datetime, date
from pathlib import Path

import pandas as pd

from screener.config import (
    SNP_COMPANIES_DIR as COMPANIES_DIR, SNP_INDICES_DIR as INDICES_DIR,
    RAW_DIR, ROOT, MAX_WORKERS, EDGAR_CACHE_DIR, SNP_FAILED_TICKERS,
)
from screener import (
    fetch_sp500_universe,
    fetch_edgar_facts,
    fetch_ticker_data,
    build_current_snapshot,
    build_historical_trends,
    generate_insights,
    build_company_json,
    build_indices,
    update_manifest,
    run_fetch_pipeline,
    write_failure_log,
)
from screener.fetch import _build_cik_map


def _is_stale(symbol: str) -> bool:
    """Check if a company's data is stale (missing or > 7 days old)."""
    path = COMPANIES_DIR / f"{symbol}.json"
    if not path.exists():
        return True
    try:
        with open(path) as f:
            company = json.load(f)
        as_of = company.get("current_snapshot", {}).get("as_of", "")
        if not as_of:
            return True
        days = (date.today() - date.fromisoformat(as_of)).days
        return days > 7
    except Exception:
        return True


def run(
    *,
    mode: str = "incremental",
    symbols: list[str] | None = None,
    workers: int | None = None,
    dry_run: bool = False,
) -> dict:
    """
    Unified S&P 500 data refresh entry point.

    Args:
        mode: full | incremental | sync-universe | rebuild
        symbols: specific symbols to fetch (all if None)
        workers: parallel fetch workers (default: MAX_WORKERS)
        dry_run: show what would be fetched

    Returns:
        dict: {fetched, failed, skipped, elapsed}
    """
    if workers is None:
        workers = MAX_WORKERS

    start = time.time()
    print("\n" + "=" * 60)
    print(f"  S&P 500 Data Update — {mode}")
    print(f"  {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 60)

    universe = fetch_sp500_universe()
    universe_map = {c["symbol"]: c for c in universe}

    # ── sync-universe: remove companies not in index ──
    if mode == "sync-universe":
        print("\nMode: SYNC UNIVERSE")
        existing_symbols = {p.stem for p in COMPANIES_DIR.glob("*.json")} if COMPANIES_DIR.exists() else set()
        removed = sorted(existing_symbols - universe_map.keys())
        for sym in removed:
            path = COMPANIES_DIR / f"{sym}.json"
            if path.exists():
                path.unlink()
                print(f"    Deleted {path.name}")
        print(f"  Removed {len(removed)} companies no longer in S&P 500")
        build_indices(companies_dir=COMPANIES_DIR, indices_dir=INDICES_DIR)
        elapsed = time.time() - start
        _write_manifest()
        print(f"\nDone in {elapsed:.1f}s\n")
        return {"fetched": 0, "failed": 0, "skipped": len(universe), "elapsed": elapsed}

    # ── rebuild: rebuild indices without fetching ──
    if mode == "rebuild":
        print("\nMode: REBUILD INDICES")
        build_indices(companies_dir=COMPANIES_DIR, indices_dir=INDICES_DIR)
        elapsed = time.time() - start
        _write_manifest()
        print(f"\nDone in {elapsed:.1f}s\n")
        return {"fetched": 0, "failed": 0, "skipped": 0, "elapsed": elapsed}

    # ── fetch loop ──
    cik_map = _build_cik_map()

    if symbols is None:
        if mode == "full":
            symbols = list(universe_map.keys())
            print(f"\nMode: FULL ({len(symbols)} companies)")
        else:
            symbols = list(universe_map.keys())
            stale = [s for s in symbols if _is_stale(s)]
            print(f"\nMode: INCREMENTAL ({len(symbols)} companies, {len(stale)} stale)")
            if stale:
                symbols = stale
            else:
                print("  All companies up-to-date.")
                build_indices(companies_dir=COMPANIES_DIR, indices_dir=INDICES_DIR)
                elapsed = time.time() - start
                _write_manifest()
                print(f"\nDone in {elapsed:.1f}s\n")
                return {"fetched": 0, "failed": 0, "skipped": len(universe), "elapsed": elapsed}
    else:
        symbols = [s.upper() for s in symbols]
        print(f"\nMode: TARGETED ({len(symbols)} companies)")

    if dry_run:
        print(f"\nDry run — would fetch {len(symbols)} companies")
        for chunk_start in range(0, len(symbols), 10):
            chunk = symbols[chunk_start:chunk_start + 10]
            print("  " + "  ".join(chunk))
        if len(symbols) > 10:
            print(f"  … and {len(symbols) - 10} more")
        elapsed = time.time() - start
        return {"fetched": 0, "failed": 0, "skipped": 0, "elapsed": elapsed}

    # ── Fetch + save ──
    # Each company is written as its fetch lands, so an interrupted or
    # rate-limited run keeps everything fetched up to that point.
    COMPANIES_DIR.mkdir(parents=True, exist_ok=True)

    def handle(sym: str, raw: dict) -> str:
        trends = build_historical_trends(raw)
        company = build_company_json(sym, raw, None, trends, None, None)
        tmp = COMPANIES_DIR / f".{sym}.json.tmp"
        with open(tmp, "w") as f:
            json.dump(company, f, indent=2)
        tmp.replace(COMPANIES_DIR / f"{sym}.json")  # atomic: no torn files
        return sym

    report = run_fetch_pipeline(
        symbols,
        fetch_fn=fetch_ticker_data,
        handle_fn=handle,
        workers=workers,
        label="companies",
    )
    results, failed = report.saved, report.failed
    write_failure_log(SNP_FAILED_TICKERS, failed)

    print("\nRebuilding indices...")
    build_indices(companies_dir=COMPANIES_DIR, indices_dir=INDICES_DIR)

    elapsed = time.time() - start
    print("\n" + "=" * 60)
    print(f"  Update complete in {elapsed:.1f}s")
    print(f"  Fetched: {len(results)}")
    print(f"  Failed:  {len(failed)}")
    skipped = len(symbols) - len(results) - len(failed)
    print(f"  Skipped: {max(skipped, 0)}")
    if failed:
        print(f"  Retry:   python scripts/data_refresh.py --market us --symbols "
              f"{' '.join(s for s, _ in failed[:5])}"
              f"{' …' if len(failed) > 5 else ''}")
        print(f"           (full list: {SNP_FAILED_TICKERS.relative_to(ROOT)})")
    print("=" * 60 + "\n")

    _write_manifest()
    return {"fetched": len(results), "failed": len(failed), "skipped": max(skipped, 0), "elapsed": round(elapsed, 1)}


def _write_manifest():
    """Update data/manifest.json after pipeline run. Doesn't touch `db` — this
    pipeline never rebuilds screener.db; `rebuild_market_db` owns that key."""
    update_manifest("snp", {
        "total_companies": len(list(COMPANIES_DIR.glob("*.json"))),
    })
