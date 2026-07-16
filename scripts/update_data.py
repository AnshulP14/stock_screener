#!/usr/bin/env python3
"""
Update NSE500 Data

Refresh stock data incrementally or in full. Automatically rebuilds
all JSON indices after fetching.

Modes:
  (default)      Incremental — re-fetch stocks with data older than --days-old (default 7)
  --symbols      Update specific stocks only (e.g. --symbols RELIANCE TCS INFY)
  --quick        Update top 50 stocks by market cap (fast sanity refresh)
  --full         Full refresh — re-fetch all 500 stocks (takes ~60-90 mins)
  --sync-universe  Sync against current NSE500 list: remove delisted stocks, fetch newly added
  --transform-only  Rebuild indices from existing CSVs without fetching

Usage:
    python update_data.py                          # Incremental (7-day threshold)
    python update_data.py --days-old 3             # Incremental, 3-day threshold
    python update_data.py --symbols RELIANCE TCS   # Specific stocks
    python update_data.py --quick                  # Top 50 stocks
    python update_data.py --full                   # All 500 stocks
    python update_data.py --sync-universe          # Remove delisted, fetch newly added
    python update_data.py --transform-only         # Rebuild indices only
    python update_data.py --dry-run                # Show what would be fetched
"""

import argparse
import json
import math
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta
from io import StringIO
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

ROOT = Path(__file__).parent.parent
DATA_DIR = ROOT / "data"
COMPANIES_DIR = DATA_DIR / "companies"
INDICES_DIR = DATA_DIR / "indices"
SCREENING_SUMMARY = INDICES_DIR / "screening_summary.json"
CURRENT_CSV = DATA_DIR / "nse500_current_metrics.csv"
HISTORICAL_CSV = DATA_DIR / "nse500_historical_annual.csv"

# Add scripts dir to path so we can import from the pipeline
sys.path.insert(0, str(Path(__file__).parent))

import pandas as pd
import requests
import yfinance as yf
from tqdm import tqdm

from nse500_data_pipeline import (
    CASHFLOW_METRICS,
    INCOME_STMT_METRICS,
    BALANCE_SHEET_METRICS,
    INFO_METRICS,
    MAX_WORKERS,
    NSE500_URL,
    RATE_LIMIT_DELAY,
    USER_AGENT,
    append_fetch_results,
    extract_current_metrics,
    extract_historical_metrics,
    fetch_nse500_tickers,
    fetch_ticker_data,
    safe_float,
    serialize_dataframe,
    transform_to_json,
)

from fetch_shareholding import get_stale_symbols as _sh_stale_symbols, process_symbols as _sh_process
from fetch_credit_ratings import get_stale_symbols as _cr_stale_symbols, process_symbols as _cr_process


def _refresh_shareholding() -> None:
    stale = _sh_stale_symbols()
    if not stale:
        print("  Shareholding: all up-to-date")
        return
    print(f"  Shareholding: fetching {len(stale)} companies…")
    ok, skipped, failed = _sh_process(stale, log_fn=print)
    print(f"  Shareholding: {ok} updated  {skipped} skipped  {failed} failed")


def _refresh_credit_ratings() -> None:
    stale = _cr_stale_symbols()
    if not stale:
        print("  Credit ratings: all up-to-date")
        return
    print(f"  Credit ratings: fetching {len(stale)} companies…")
    ok, skipped, failed = _cr_process(stale, log_fn=print)
    print(f"  Credit ratings: {ok} updated  {skipped} skipped  {failed} failed")

# ---------------------------------------------------------------------------
# Financial calendar helpers
# ---------------------------------------------------------------------------

def _expected_latest_annual_fy(today: datetime | None = None) -> int:
    """
    Latest Indian fiscal year (Apr-Mar) whose annual results should be
    fully available. Annual results are published ~60 days after Mar 31.

    Example: Apr 24 2026 → FY2025 (FY2026 due ~Jun 1 2026, not yet out).
    """
    d = (today or datetime.now()).date()
    from datetime import date
    fy_end = date(d.year, 3, 31)
    if d >= fy_end + timedelta(days=60):
        return d.year   # FY ending this March is available
    return d.year - 1


def _expected_latest_quarter_available_from(today: datetime | None = None):
    """
    Return the date from which the latest quarter's results should be
    available. Quarters end Mar 31 / Jun 30 / Sep 30 / Dec 31; results
    arrive ~45 days later.

    Example: Apr 24 2026 → Feb 14 2026 (Dec 31 2025 + 45 days).
             May 20 2026 → May 15 2026 (Mar 31 2026 + 45 days).
    """
    from datetime import date
    d = (today or datetime.now()).date()
    quarter_ends = []
    for year in (d.year - 1, d.year):
        for month, day in ((3, 31), (6, 30), (9, 30), (12, 31)):
            quarter_ends.append(date(year, month, day))
    available = [qe + timedelta(days=45) for qe in quarter_ends
                 if qe + timedelta(days=45) <= d]
    return max(available) if available else date(d.year - 1, 1, 1)


# ---------------------------------------------------------------------------
# Staleness detection
# ---------------------------------------------------------------------------

def _get_as_of(symbol: str) -> datetime | None:
    """Return the fetch date from the company JSON, or None if missing."""
    path = COMPANIES_DIR / f"{symbol}.json"
    if not path.exists():
        return None
    try:
        with open(path) as f:
            data = json.load(f)
        as_of = data.get("current_snapshot", {}).get("as_of", "")
        return datetime.strptime(as_of[:10], "%Y-%m-%d") if as_of else None
    except Exception:
        return None


def _is_financially_stale(symbol: str, hist_df: "pd.DataFrame") -> bool:
    """
    Return True if the symbol needs a refresh based on financial calendar:
    - Missing the expected latest annual fiscal year, OR
    - Last fetched before the latest quarter's results became available.
    """
    today = datetime.now()
    exp_fy = _expected_latest_annual_fy(today)
    exp_q_from = _expected_latest_quarter_available_from(today)

    # Annual check
    sym_rows = hist_df[hist_df["symbol"] == symbol]
    if sym_rows.empty or sym_rows["fiscal_year"].max() < exp_fy:
        return True

    # Quarterly check: was the data fetched after latest quarter results landed?
    as_of = _get_as_of(symbol)
    if as_of is None:
        return True
    return as_of.date() < exp_q_from


def _get_stale_symbols(all_symbols: list[str]) -> list[str]:
    """Return symbols that need updating based on the financial calendar."""
    if not HISTORICAL_CSV.exists():
        return list(all_symbols)
    hist_df = pd.read_csv(HISTORICAL_CSV, usecols=["symbol", "fiscal_year"])
    return [sym for sym in all_symbols if _is_financially_stale(sym, hist_df)]


def _get_data_age_days(symbol: str) -> float | None:
    """Return how many days old the stored data is (for --days-old CLI mode)."""
    as_of = _get_as_of(symbol)
    if as_of is None:
        return None
    return (datetime.now() - as_of).total_seconds() / 86400


def _get_top_symbols_by_mcap(n: int = 50) -> list[str]:
    """Return the top N symbols by market cap from existing screening data."""
    if not SCREENING_SUMMARY.exists():
        print("No screening_summary.json found; fetching top symbols from NSE list instead.")
        return []
    with open(SCREENING_SUMMARY) as f:
        data = json.load(f)
    companies = data.get("companies", [])
    # Sort by market_cap_inr descending, filter nulls
    ranked = sorted(
        [c for c in companies if c.get("market_cap_inr")],
        key=lambda x: x["market_cap_inr"],
        reverse=True,
    )
    return [c["symbol"] for c in ranked[:n]]


# ---------------------------------------------------------------------------
# Universe sync (delisted / newly added stocks)
# ---------------------------------------------------------------------------

def _remove_symbols(symbols: list[str]) -> None:
    """Delete all stored data for the given symbols."""
    symbol_set = set(symbols)

    for sym in symbols:
        path = COMPANIES_DIR / f"{sym}.json"
        if path.exists():
            path.unlink()
            print(f"    Deleted {path.name}")

    if CURRENT_CSV.exists():
        df = pd.read_csv(CURRENT_CSV)
        before = len(df)
        df = df[~df["symbol"].isin(symbol_set)]
        df.to_csv(CURRENT_CSV, index=False)
        print(f"    Removed {before - len(df)} rows from {CURRENT_CSV.name}")

    if HISTORICAL_CSV.exists():
        df = pd.read_csv(HISTORICAL_CSV)
        before = len(df)
        df = df[~df["symbol"].isin(symbol_set)]
        df.to_csv(HISTORICAL_CSV, index=False)
        print(f"    Removed {before - len(df)} rows from {HISTORICAL_CSV.name}")

    raw_path = DATA_DIR / "nse500_quarterly_raw.json"
    if raw_path.exists():
        try:
            with open(raw_path) as f:
                raw = json.load(f)
            removed_count = sum(1 for s in symbols if s in raw)
            for sym in symbols:
                raw.pop(sym, None)
            with open(raw_path, "w") as f:
                json.dump(raw, f, indent=2, default=str)
            print(f"    Removed {removed_count} entries from {raw_path.name}")
        except Exception as e:
            print(f"    Warning: could not update {raw_path.name}: {e}")


def sync_universe(*, dry_run: bool = False) -> tuple[list[str], dict[str, dict] | None]:
    """
    Diff local data against the live NSE 500 constituent list, then identify
    all symbols that need fetching:
      - Stocks missing a JSON (newly added or never fetched)
      - Stocks with stale financials per the Indian financial calendar

    Returns (symbols_to_fetch, nse_metadata).
    """
    print("\nFetching current NSE500 constituent list...")
    tickers_ns, nse_metadata = fetch_nse500_tickers()
    current_symbols = {t.replace(".NS", "") for t in tickers_ns}

    existing_symbols = {p.stem for p in COMPANIES_DIR.glob("*.json")} if COMPANIES_DIR.exists() else set()

    removed = sorted(existing_symbols - current_symbols)
    missing = sorted(current_symbols - existing_symbols)

    print(f"\n  Universe diff:")
    print(f"    Removed from index (delisted/replaced): {len(removed)}")
    print(f"    Missing financial data:                 {len(missing)}")

    if removed:
        if dry_run:
            print(f"\n  [dry-run] Would remove: {', '.join(removed)}")
        else:
            print(f"\n  Removing {len(removed)} stocks...")
            _remove_symbols(removed)

    # After removal, recalculate existing so stale check is accurate
    existing_after = current_symbols - set(missing)
    stale = _get_stale_symbols(sorted(existing_after))

    today = datetime.now()
    exp_fy = _expected_latest_annual_fy(today)
    exp_q_from = _expected_latest_quarter_available_from(today)
    print(f"\n  Staleness thresholds (financial calendar):")
    print(f"    Expected latest annual FY:  {exp_fy}")
    print(f"    Latest quarter available from: {exp_q_from}")
    print(f"    Stale (missing FY or fetched before quarter landed): {len(stale)}")

    to_fetch = sorted(set(missing) | set(stale))

    if to_fetch:
        action = "[dry-run] Would fetch" if dry_run else "Will fetch"
        print(f"\n  {action} {len(to_fetch)} stocks ({len(missing)} missing + {len(stale)} stale)")

    return to_fetch, nse_metadata


# ---------------------------------------------------------------------------
# CSV update helpers (replace rows for updated symbols)
# ---------------------------------------------------------------------------

def _update_current_csv(new_rows: list[dict]) -> None:
    """Replace or insert rows in CURRENT_CSV for updated symbols."""
    new_df = pd.DataFrame(new_rows)
    updated_symbols = set(new_df["symbol"].tolist())

    if CURRENT_CSV.exists():
        existing = pd.read_csv(CURRENT_CSV)
        # Drop rows for symbols being updated
        existing = existing[~existing["symbol"].isin(updated_symbols)]
        combined = pd.concat([existing, new_df], ignore_index=True)
    else:
        combined = new_df

    CURRENT_CSV.parent.mkdir(parents=True, exist_ok=True)
    combined.to_csv(CURRENT_CSV, index=False)
    print(f"  Updated {len(updated_symbols)} rows in {CURRENT_CSV.name}")


def _update_historical_csv(new_records: list[dict]) -> None:
    """Replace or insert historical rows for updated symbols."""
    new_df = pd.DataFrame(new_records)
    if new_df.empty:
        return
    updated_symbols = set(new_df["symbol"].tolist())

    if HISTORICAL_CSV.exists():
        existing = pd.read_csv(HISTORICAL_CSV)
        existing = existing[~existing["symbol"].isin(updated_symbols)]
        combined = pd.concat([existing, new_df], ignore_index=True)
    else:
        combined = new_df

    combined.to_csv(HISTORICAL_CSV, index=False)
    print(f"  Updated historical rows for {len(updated_symbols)} symbols in {HISTORICAL_CSV.name}")


def save_update_results(
    results: list[dict],
    nse_metadata: dict[str, dict] | None,
) -> None:
    """Save fetch results by merging into existing CSVs (replacing stale rows)."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)

    # Current metrics
    current_rows = [extract_current_metrics(r, nse_metadata) for r in results]
    _update_current_csv(current_rows)

    # Historical metrics
    historical_records = []
    for r in results:
        historical_records.extend(extract_historical_metrics(r))
    _update_historical_csv(historical_records)

    # Update raw JSON (merge by symbol)
    raw_path = DATA_DIR / "nse500_quarterly_raw.json"
    existing_raw: dict = {}
    if raw_path.exists():
        try:
            with open(raw_path) as f:
                existing_raw = json.load(f)
        except Exception:
            pass

    for r in results:
        sym = r["symbol"]
        existing_raw[sym] = {
            "info": r.get("info", {}),
            "quarterly_income": serialize_dataframe(r.get("quarterly_income")),
            "quarterly_balance": serialize_dataframe(r.get("quarterly_balance")),
            "quarterly_cashflow": serialize_dataframe(r.get("quarterly_cashflow")),
            "annual_income": serialize_dataframe(r.get("annual_income")),
            "annual_balance": serialize_dataframe(r.get("annual_balance")),
            "annual_cashflow": serialize_dataframe(r.get("annual_cashflow")),
            "fetch_time": r.get("fetch_time"),
        }

    with open(raw_path, "w") as f:
        json.dump(existing_raw, f, indent=2, default=str)


# ---------------------------------------------------------------------------
# Fetch helpers
# ---------------------------------------------------------------------------

def fetch_symbols(
    ns_symbols: list[str],
    nse_metadata: dict[str, dict] | None,
    *,
    workers: int = MAX_WORKERS,
    delay: float = RATE_LIMIT_DELAY,
) -> tuple[list[dict], list[tuple[str, str]]]:
    """Fetch data for a list of bare NSE symbols (no .NS suffix needed)."""
    # Ensure .NS suffix
    tickers = [s if s.endswith(".NS") else f"{s}.NS" for s in ns_symbols]
    results: list[dict] = []
    failed: list[tuple[str, str]] = []

    with ThreadPoolExecutor(max_workers=workers) as executor:
        future_to_ticker = {executor.submit(fetch_ticker_data, t): t for t in tickers}
        with tqdm(
            total=len(tickers),
            desc="Fetching",
            unit="stock",
            ncols=90,
            bar_format="{l_bar}{bar}| {n_fmt}/{total_fmt} [{elapsed}<{remaining}, {rate_fmt}]",
        ) as pbar:
            for future in as_completed(future_to_ticker):
                ticker = future_to_ticker[future]
                try:
                    r = future.result()
                    if r.get("error"):
                        failed.append((ticker, r["error"]))
                        pbar.set_postfix_str(f"FAIL {ticker.replace('.NS','')}")
                    else:
                        results.append(r)
                        pbar.set_postfix_str(f"OK   {ticker.replace('.NS','')}")
                except Exception as e:
                    failed.append((ticker, str(e)))
                    pbar.set_postfix_str(f"ERR  {ticker.replace('.NS','')}")
                pbar.update(1)
                time.sleep(delay)

    return results, failed


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Update NSE500 stock data",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )

    mode_group = parser.add_mutually_exclusive_group()
    mode_group.add_argument(
        "--full",
        action="store_true",
        help="Full refresh: re-fetch all 500 stocks (slow, ~60-90 mins)",
    )
    mode_group.add_argument(
        "--quick",
        action="store_true",
        help="Quick refresh: top 50 stocks by market cap",
    )
    mode_group.add_argument(
        "--symbols",
        nargs="+",
        metavar="SYMBOL",
        help="Update specific stocks, e.g. --symbols RELIANCE TCS INFY",
    )
    mode_group.add_argument(
        "--sync-universe",
        action="store_true",
        help="Sync against current NSE500 list: remove delisted stocks, fetch newly added ones",
    )
    mode_group.add_argument(
        "--transform-only",
        action="store_true",
        help="Only rebuild indices from existing CSVs (no fetch)",
    )

    parser.add_argument(
        "--days-old",
        type=int,
        default=7,
        metavar="N",
        help="Incremental mode: re-fetch stocks with data older than N days (default: 7)",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=MAX_WORKERS,
        metavar="N",
        help=f"Parallel fetch workers (default: {MAX_WORKERS})",
    )
    parser.add_argument(
        "--no-transform",
        action="store_true",
        help="Skip the transform/index-rebuild step after fetching",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be fetched without actually fetching",
    )

    args = parser.parse_args()

    start = time.time()
    print("\n" + "=" * 60)
    print("  NSE500 Data Update")
    print(f"  {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 60)

    # ------------------------------------------------------------------
    # Sync-universe mode
    # ------------------------------------------------------------------
    if args.sync_universe:
        print("\nMode: SYNC UNIVERSE")
        added, nse_metadata = sync_universe(dry_run=args.dry_run)
        if args.dry_run or not added:
            if not added:
                print("\nNo new stocks to fetch.")
            if not args.no_transform and not args.dry_run:
                print("\nRebuilding indices...")
                transform_to_json()
                print("\nUpdating shareholding patterns...")
                _refresh_shareholding()
                _refresh_credit_ratings()
            print(f"\nDone in {time.time() - start:.1f}s")
            return
        symbols = added
        # Fall through to the fetch/save/transform path below

    # ------------------------------------------------------------------
    # Transform-only mode
    # ------------------------------------------------------------------
    elif args.transform_only:
        if not CURRENT_CSV.exists() or not HISTORICAL_CSV.exists():
            print("\nError: CSVs not found. Run a fetch first.")
            sys.exit(1)
        print("\nRebuilding indices from existing CSVs...")
        transform_to_json()
        print(f"\nDone in {time.time() - start:.1f}s")
        return

    # ------------------------------------------------------------------
    # Determine which symbols to fetch (skipped when sync_universe already set them)
    # ------------------------------------------------------------------
    if not args.sync_universe:
        nse_metadata = None

        if args.full:
            print("\nMode: FULL refresh (all NSE500 stocks)")
            print("Fetching NSE500 ticker list...")
            tickers_ns, nse_metadata = fetch_nse500_tickers()
            symbols = [t.replace(".NS", "") for t in tickers_ns]

        elif args.symbols:
            symbols = [s.upper() for s in args.symbols]
            print(f"\nMode: TARGETED update ({len(symbols)} stocks)")
            if CURRENT_CSV.exists():
                try:
                    existing = pd.read_csv(CURRENT_CSV)
                    nse_metadata = {}
                    for _, row in existing.iterrows():
                        sym = str(row.get("symbol", ""))
                        nse_metadata[f"{sym}.NS"] = {
                            "nse_company_name": row.get("nse_company_name"),
                            "nse_industry": row.get("nse_industry"),
                            "isin_code": row.get("isin_code"),
                        }
                except Exception:
                    pass

        elif args.quick:
            print("\nMode: QUICK refresh (top 50 by market cap)")
            symbols = _get_top_symbols_by_mcap(50)
            if not symbols:
                print("  No existing data found; fetching top symbols from NSE list...")
                tickers_ns, nse_metadata = fetch_nse500_tickers()
                symbols = [t.replace(".NS", "") for t in tickers_ns[:50]]
            print(f"  Found {len(symbols)} symbols")

        else:
            # Default: incremental — financial calendar aware
            if args.days_old != 7:
                # Explicit --days-old flag: fall back to time-based check
                print(f"\nMode: INCREMENTAL (re-fetch data older than {args.days_old} days)")
                if CURRENT_CSV.exists():
                    try:
                        existing_df = pd.read_csv(CURRENT_CSV)
                        all_symbols = existing_df["symbol"].tolist()
                    except Exception:
                        all_symbols = []
                else:
                    all_symbols = []
                if not all_symbols:
                    print("  No existing data; fetching full NSE500 list...")
                    tickers_ns, nse_metadata = fetch_nse500_tickers()
                    all_symbols = [t.replace(".NS", "") for t in tickers_ns]
                symbols = [s for s in all_symbols
                           if (_get_data_age_days(s) or float("inf")) >= args.days_old]
                print(f"  {len(symbols)} symbols need updating ({len(all_symbols) - len(symbols)} are fresh)")
            else:
                # Default: use financial calendar (missing FY or pre-quarter fetch)
                today = datetime.now()
                exp_fy = _expected_latest_annual_fy(today)
                exp_q_from = _expected_latest_quarter_available_from(today)
                print(f"\nMode: INCREMENTAL (financial calendar)")
                print(f"  Expected latest annual FY: {exp_fy}")
                print(f"  Latest quarter available from: {exp_q_from}")
                if CURRENT_CSV.exists():
                    try:
                        existing_df = pd.read_csv(CURRENT_CSV)
                        all_symbols = existing_df["symbol"].tolist()
                    except Exception:
                        all_symbols = []
                else:
                    all_symbols = []
                if not all_symbols:
                    print("  No existing data; fetching full NSE500 list...")
                    tickers_ns, nse_metadata = fetch_nse500_tickers()
                    all_symbols = [t.replace(".NS", "") for t in tickers_ns]
                print(f"  Checking {len(all_symbols)} symbols...")
                symbols = _get_stale_symbols(all_symbols)
                print(f"  {len(symbols)} stale, {len(all_symbols) - len(symbols)} up-to-date")

    if not symbols:
        print("\nAll data is up-to-date. Nothing to fetch.")
        if not args.no_transform:
            print("\nRebuilding indices (in case of prior partial runs)...")
            transform_to_json()
            print("\nUpdating shareholding patterns...")
            _refresh_shareholding()
            _refresh_credit_ratings()
        print(f"\nDone in {time.time() - start:.1f}s")
        return

    # ------------------------------------------------------------------
    # Dry run
    # ------------------------------------------------------------------
    if args.dry_run:
        print(f"\nDry run — would fetch {len(symbols)} stocks:")
        for chunk_start in range(0, len(symbols), 10):
            chunk = symbols[chunk_start:chunk_start + 10]
            print("  " + "  ".join(chunk))
        print(f"\nTotal: {len(symbols)} stocks")
        return

    # ------------------------------------------------------------------
    # Fetch
    # ------------------------------------------------------------------
    print(f"\nFetching {len(symbols)} stocks with {args.workers} workers...")
    if len(symbols) > 100:
        eta_mins = len(symbols) * (RATE_LIMIT_DELAY + 3) / args.workers / 60
        print(f"Estimated time: {eta_mins:.0f}-{eta_mins * 1.5:.0f} minutes")

    results, failed = fetch_symbols(symbols, nse_metadata, workers=args.workers)

    # ------------------------------------------------------------------
    # Save
    # ------------------------------------------------------------------
    if results:
        print(f"\nSaving data for {len(results)} stocks...")
        save_update_results(results, nse_metadata)

    if failed:
        failed_path = DATA_DIR / "failed_tickers.txt"
        with open(failed_path, "w") as f:
            for ticker, err in failed:
                f.write(f"{ticker}: {err}\n")
        print(f"\n  {len(failed)} stocks failed (saved to data/failed_tickers.txt)")
        if len(failed) <= 10:
            for ticker, err in failed:
                print(f"    - {ticker}: {err[:80]}")

    # ------------------------------------------------------------------
    # Transform
    # ------------------------------------------------------------------
    if not args.no_transform and results:
        print("\nRebuilding indices...")
        transform_to_json()
        print("\nUpdating shareholding patterns...")
        _refresh_shareholding()
        _refresh_credit_ratings()

    # ------------------------------------------------------------------
    # Summary
    # ------------------------------------------------------------------
    elapsed = time.time() - start
    print("\n" + "=" * 60)
    print(f"  Update complete in {elapsed:.1f}s ({elapsed/60:.1f} min)")
    print(f"  Fetched:  {len(results):>4} stocks")
    print(f"  Failed:   {len(failed):>4} stocks")
    if SCREENING_SUMMARY.exists():
        try:
            with open(SCREENING_SUMMARY) as f:
                summary = json.load(f)
            gen = summary.get("generated_at", "")[:16].replace("T", " ")
            total = summary.get("total_companies", 0)
            print(f"  Index:    {total} companies (as of {gen})")
        except Exception:
            pass
    print("=" * 60 + "\n")


if __name__ == "__main__":
    main()
