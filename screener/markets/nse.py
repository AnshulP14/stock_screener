"""NSE500 data update — unified pipeline entry point."""

import json
import time
from datetime import datetime
from pathlib import Path

import pandas as pd

from screener.config import (
    COMPANIES_DIR, INDICES_DIR, RAW_DIR, ROOT, MAX_WORKERS, NSE_FAILED_TICKERS,
    RATE_LIMIT_DELAY,
)
from screener import (
    fetch_nse500_tickers,
    fetch_ticker_data,
    build_current_snapshot,
    build_historical_trends,
    generate_insights,
    build_company_json,
    build_indices,
    process_symbols,
    get_stale_symbols,
    update_manifest,
    run_fetch_pipeline,
    write_failure_log,
)
from screener.freshness import AgeDays, Market, QuarterLag, stale_symbols

_QUARTER_POLICY = QuarterLag(field=("shareholding", "quarters", -1), market=Market.NSE)


def _fiscal_year(d: datetime) -> int:
    """Indian FY: Apr 1 - Mar 31. FY ending Mar 2024 = FY2024."""
    return d.year + 1 if d.month >= 4 else d.year


def _top_symbols_by_mcap(n=50):
    """Return top N symbols by market cap from screening_summary.json."""
    if not INDICES_DIR.exists():
        print("  No screening_summary.json found.")
        return []
    with open(INDICES_DIR / "screening_summary.json") as f:
        data = json.load(f)
    companies = data.get("companies", [])
    ranked = sorted([c for c in companies if c.get("market_cap_inr")],
                    key=lambda x: x["market_cap_inr"], reverse=True)
    return [c["symbol"] for c in ranked[:n]]


def _save_current_csv(metrics_list):
    """Save current snapshot metrics to CSV."""
    df = pd.DataFrame(metrics_list)
    csv_path = RAW_DIR / "nse" / "current_metrics.csv"
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(csv_path, index=False)
    return len(metrics_list)


def _save_historical_csv(trends_list):
    """Save historical trends to CSV."""
    records = []
    for t in trends_list:
        for fy in t.get("years_available", []):
            records.append({
                "symbol": t.get("symbol"),
                "fiscal_year": fy,
                "revenue": t.get("revenue", {}).get("values_inr"),
                "net_income": t.get("net_income", {}).get("values_inr"),
                "eps": t.get("eps", {}).get("values"),
            })
    df = pd.DataFrame(records)
    csv_path = RAW_DIR / "nse" / "historical_annual.csv"
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(csv_path, index=False)
    return len(records)


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
        mode: full | incremental | sync-universe | transform-only
        symbols: specific symbols to fetch (all if None)
        workers: parallel fetch workers (default: MAX_WORKERS)
        dry_run: show what would be fetched
        days_old: staleness threshold for incremental mode
        no_transform: skip index/DB rebuild

    Returns:
        dict: {fetched, failed, skipped, elapsed}
    """
    if workers is None:
        workers = MAX_WORKERS

    start = time.time()
    print("\n" + "=" * 60)
    print(f"  NSE500 Data Update — {mode}")
    print(f"  {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 60)

    # ── sync-universe ──
    if mode == "sync-universe":
        print("\nMode: SYNC UNIVERSE")
        tickers, nse_metadata = fetch_nse500_tickers()
        current_symbols = {t.replace(".NS", "") for t in tickers}
        existing_symbols = {p.stem for p in COMPANIES_DIR.glob("*.json")} if COMPANIES_DIR.exists() else set()

        removed = sorted(existing_symbols - current_symbols)
        for sym in removed:
            path = COMPANIES_DIR / f"{sym}.json"
            if path.exists():
                path.unlink()
                print(f"    Deleted {path.name}")

        stale = stale_symbols(COMPANIES_DIR, _QUARTER_POLICY, symbols=sorted(current_symbols))

        print(f"  Removed from index: {len(removed)}")
        print(f"  Stale/missing: {len(stale)}")
        symbols = stale
        if not symbols:
            print("  All data up-to-date.")
            build_indices(companies_dir=COMPANIES_DIR, indices_dir=INDICES_DIR)
            elapsed = time.time() - start
            _write_manifest(nse_metadata)
            print(f"\nDone in {elapsed:.1f}s\n")
            return {"fetched": 0, "failed": 0, "skipped": 0, "elapsed": elapsed}

    elif mode == "transform-only":
        print("\nMode: TRANSFORM ONLY")
        build_indices(companies_dir=COMPANIES_DIR, indices_dir=INDICES_DIR)
        elapsed = time.time() - start
        _write_manifest(None)
        print(f"\nDone in {elapsed:.1f}s\n")
        return {"fetched": 0, "failed": 0, "skipped": 0, "elapsed": elapsed}

    else:
        # fetch or targeted
        nse_metadata = None

        if symbols is None:
            if mode == "full":
                print(f"\nMode: FULL ({MAX_WORKERS} workers)")
                tickers, nse_metadata = fetch_nse500_tickers()
                symbols = [t.replace(".NS", "") for t in tickers]
            elif mode == "quick":
                symbols = _top_symbols_by_mcap(50)
                if not symbols:
                    print("  No existing data; fetching top 50...")
                    tickers, nse_metadata = fetch_nse500_tickers()
                    symbols = [t.replace(".NS", "") for t in tickers[:50]]
                print(f"\nMode: QUICK (top {len(symbols)} by mcap)")
            else:
                # incremental
                print(f"\nMode: INCREMENTAL (staleness: {days_old} days)")
                all_symbols = []
                if COMPANIES_DIR.exists():
                    all_symbols = [p.stem for p in COMPANIES_DIR.glob("*.json")]
                if not all_symbols:
                    print("  No existing data; fetching full NSE500 list...")
                    tickers, nse_metadata = fetch_nse500_tickers()
                    all_symbols = [t.replace(".NS", "") for t in tickers]
                print(f"  {len(all_symbols)} companies in database")
                age_policy = AgeDays(field=("current_snapshot", "as_of"), days=days_old)
                stale_quarter = stale_symbols(COMPANIES_DIR, _QUARTER_POLICY, symbols=all_symbols)
                stale_age = stale_symbols(COMPANIES_DIR, age_policy, symbols=all_symbols)
                symbols = sorted(set(stale_quarter) | set(stale_age))
                print(f"  {len(symbols)} stale, {len(all_symbols) - len(symbols)} up-to-date")

        if not symbols:
            print("\nAll data up-to-date.")
            build_indices(companies_dir=COMPANIES_DIR, indices_dir=INDICES_DIR)
            elapsed = time.time() - start
            _write_manifest(nse_metadata)
            print(f"\nDone in {elapsed:.1f}s\n")
            return {"fetched": 0, "failed": 0, "skipped": 0, "elapsed": elapsed}

        if dry_run:
            print(f"\nDry run — would fetch {len(symbols)} stocks")
            for chunk_start in range(0, len(symbols), 10):
                chunk = symbols[chunk_start:chunk_start + 10]
                print("  " + "  ".join(chunk))
            if len(symbols) > 10:
                print(f"  … and {len(symbols) - 10} more")
            eta = len(symbols) * (RATE_LIMIT_DELAY + 3) / workers / 60
            print(f"\nEstimated time: {eta:.0f}-{eta * 1.5:.0f} minutes")
            elapsed = time.time() - start
            return {"fetched": 0, "failed": 0, "skipped": 0, "elapsed": elapsed}

        # ── Fetch + save ──
        # Each company is written as its fetch lands, so an interrupted or
        # rate-limited run keeps everything fetched up to that point.
        COMPANIES_DIR.mkdir(parents=True, exist_ok=True)

        def handle(sym: str, raw: dict) -> dict:
            trends = build_historical_trends(raw)
            company = build_company_json(sym, raw, nse_metadata, trends, None, None)
            tmp = COMPANIES_DIR / f".{sym}.json.tmp"
            with open(tmp, "w") as f:
                json.dump(company, f, indent=2)
            tmp.replace(COMPANIES_DIR / f"{sym}.json")  # atomic: no torn files
            trends["symbol"] = sym
            return {"snapshot": build_current_snapshot(raw, nse_metadata), "trends": trends}

        report = run_fetch_pipeline(
            symbols,
            fetch_fn=lambda s: fetch_ticker_data(f"{s}.NS"),
            handle_fn=handle,
            workers=workers,
            label="stocks",
        )
        results, failed = report.saved, report.failed
        write_failure_log(NSE_FAILED_TICKERS, failed)

        # ── Save CSVs ──
        if results:
            _save_current_csv([r["snapshot"] for r in results])
            _save_historical_csv([r["trends"] for r in results])

        if not no_transform:
            print("\nRebuilding indices...")
            build_indices(companies_dir=COMPANIES_DIR, indices_dir=INDICES_DIR)

        print("\nUpdating enrichments...")
        for dataset in ("shareholding", "credit_ratings"):
            stale = get_stale_symbols(dataset)
            if stale:
                ok, skipped, failed_n = process_symbols(stale, dataset)
                print(f"  {dataset}: {ok} updated  {skipped} skipped  {failed_n} failed")

        elapsed = time.time() - start
        print("\n" + "=" * 60)
        print(f"  Update complete in {elapsed:.1f}s")
        print(f"  Fetched: {len(results)}")
        print(f"  Failed:  {len(failed)}")
        skipped_count = len(symbols) - len(results) - len(failed)
        print(f"  Skipped: {max(skipped_count, 0)}")
        if failed:
            print(f"  Retry:   python scripts/data_refresh.py --market nse --symbols "
                  f"{' '.join(s for s, _ in failed[:5])}"
                  f"{' …' if len(failed) > 5 else ''}")
            print(f"           (full list: {NSE_FAILED_TICKERS.relative_to(ROOT)})")
        if INDICES_DIR.exists():
            with open(INDICES_DIR / "screening_summary.json") as f:
                summary = json.load(f)
                gen = summary.get("generated_at", "")[:16].replace("T", " ")
                total = summary.get("total_companies", 0)
                print(f"  Index: {total} companies (as of {gen})")
        print("=" * 60 + "\n")

        _write_manifest(nse_metadata)
        return {"fetched": len(results), "failed": len(failed), "skipped": max(skipped_count, 0), "elapsed": round(elapsed, 1)}


def _write_manifest(nse_metadata):
    """Update data/manifest.json after pipeline run. Doesn't touch `db` — this
    pipeline never rebuilds screener.db; `rebuild_market_db` owns that key."""
    update_manifest("nse", {
        "total_companies": len(list(COMPANIES_DIR.glob("*.json"))),
    })
