"""Market-specific data pipeline modules.

run_pipeline is the single orchestration engine both markets/nse.py and
markets/snp.py delegate to -- previously two separate, drifting run()
implementations (see the commit introducing MarketConfig for the full
inventory of what differed only by accident vs. what's a genuine,
market-specific behavior preserved here via MarketConfig fields).

While merging them, found that the original nse.py's --mode sync-universe
never actually fetched anything: its `if mode == "sync-universe": ... elif
... : ... else: <fetch+save logic>` structure meant the fetch+save code
only lived in the `else` branch, so sync-universe fell through the whole
if/elif/else with no return, implicitly returning None whenever there were
stale/missing symbols to fetch (reproduced with a minimal repro before
touching this code). _fetch_and_save below is now a shared step both
sync-universe and full/quick/incremental/targeted call into explicitly.
"""

import json
import time
from datetime import datetime

import pandas as pd

from screener.config import MAX_WORKERS, RATE_LIMIT_DELAY, ROOT
from screener.enrich import get_stale_symbols, process_symbols
from screener.fetch import build_cik_map, fetch_edgar_facts, fetch_ticker_data
from screener.freshness import is_stale
from screener.index import build_indices, update_manifest
from screener.market import MarketConfig
from screener.runner import run_fetch_pipeline, write_failure_log
from screener.transform import (
    build_company_json,
    build_current_snapshot,
    build_historical_trends,
    build_historical_trends_edgar,
    build_institutional_ownership,
)


def _stale_symbols_for(market: MarketConfig, symbols: list[str], days_old: int) -> list[str]:
    """A symbol refetches if any of this market's staleness policies flag it.

    Reads each company file once and checks every policy against it, rather
    than calling freshness.stale_symbols() once per policy -- which would
    re-glob and re-read every file in companies_dir once per policy (2x for
    NSE's QuarterLag + AgeDays combination on every incremental/sync-universe
    run)."""
    policies = market.staleness_policies(days_old)
    stale = []
    for sym in symbols:
        path = market.companies_dir / f"{sym}.json"
        if not path.exists():
            stale.append(sym)
            continue
        try:
            with open(path) as f:
                company = json.load(f)
        except (OSError, json.JSONDecodeError):
            stale.append(sym)
            continue
        if any(is_stale(company, policy) for policy in policies):
            stale.append(sym)
    return stale


def _top_symbols_by_mcap(market: MarketConfig, n: int = 50) -> list[str]:
    """Return top N symbols by market cap from screening_summary.json."""
    summary_path = market.indices_dir / "screening_summary.json"
    if not summary_path.exists():
        print("  No screening_summary.json found.")
        return []
    with open(summary_path) as f:
        data = json.load(f)
    companies = data.get("companies", [])
    ranked = sorted([c for c in companies if c.get("market_cap")],
                    key=lambda x: x["market_cap"], reverse=True)
    return [c["symbol"] for c in ranked[:n]]


def _save_raw_csvs(market: MarketConfig, results: list[dict]) -> None:
    """Save current-snapshot and historical-trend CSVs, for markets that
    opt in via MarketConfig.raw_csv_dir (NSE only, today)."""
    if not market.raw_csv_dir or not results:
        return
    market.raw_csv_dir.mkdir(parents=True, exist_ok=True)

    pd.DataFrame([r["snapshot"] for r in results]).to_csv(
        market.raw_csv_dir / "current_metrics.csv", index=False
    )

    records = []
    for r in results:
        t = r["trends"]
        for fy in t.get("years_available", []):
            records.append({
                "symbol": t.get("symbol"),
                "fiscal_year": fy,
                "revenue": t.get("revenue", {}).get("values"),
                "net_income": t.get("net_income", {}).get("values"),
                "eps": t.get("eps", {}).get("values"),
            })
    pd.DataFrame(records).to_csv(market.raw_csv_dir / "historical_annual.csv", index=False)


def _write_manifest(market: MarketConfig) -> None:
    """Update data/manifest.json after pipeline run. Doesn't touch `db` --
    this pipeline never rebuilds screener.db; rebuild_market_db owns that key."""
    update_manifest(market.id, {
        "total_companies": len(list(market.companies_dir.glob("*.json"))),
    })


def _finish(market: MarketConfig, start: float, *, skipped: int = 0) -> dict:
    build_indices(companies_dir=market.companies_dir, indices_dir=market.indices_dir)
    elapsed = time.time() - start
    _write_manifest(market)
    print(f"\nDone in {elapsed:.1f}s\n")
    return {"fetched": 0, "failed": 0, "skipped": skipped, "elapsed": elapsed}


def _fetch_and_save(
    market: MarketConfig,
    symbols: list[str],
    metadata: dict[str, dict] | None,
    *,
    workers: int,
    dry_run: bool,
    no_transform: bool,
    start: float,
    enrichment_symbols: list[str] | None = None,
) -> dict:
    """Fetch, transform and persist `symbols`, then rebuild indices and run
    any per-market enrichment. Shared by every mode that ends in an actual
    fetch (sync-universe, full, quick, incremental, targeted).

    enrichment_symbols: restricts the enrichment staleness check to this set
    (a targeted `--symbols` run) instead of the whole companies_dir -- see
    get_stale_symbols."""
    companies_dir = market.companies_dir
    indices_dir = market.indices_dir

    if dry_run:
        print(f"\nDry run — would fetch {len(symbols)} companies")
        for chunk_start in range(0, len(symbols), 10):
            chunk = symbols[chunk_start:chunk_start + 10]
            print("  " + "  ".join(chunk))
        if len(symbols) > 10:
            print(f"  … and {len(symbols) - 10} more")
        eta = len(symbols) * (RATE_LIMIT_DELAY + 3) / workers / 60
        print(f"\nEstimated time: {eta:.0f}-{eta * 1.5:.0f} minutes")
        elapsed = time.time() - start
        return {"fetched": 0, "failed": 0, "skipped": 0, "elapsed": elapsed}

    # Each company is written as its fetch lands, so an interrupted or
    # rate-limited run keeps everything fetched up to that point.
    companies_dir.mkdir(parents=True, exist_ok=True)

    # Built once per run, not per symbol -- build_cik_map does a single bulk
    # fetch of SEC's full ticker->CIK table (see fetch.build_cik_map's
    # docstring: no cik_map.json cache, resolved fresh in memory each run).
    cik_map = build_cik_map() if market.uses_edgar else {}

    def handle(sym: str, raw: dict) -> dict:
        if market.uses_edgar:
            cik = cik_map.get(sym)
            trends = build_historical_trends_edgar(fetch_edgar_facts(sym, cik))
        else:
            cik = None
            trends = build_historical_trends(raw, market)
        institutional_ownership = (
            build_institutional_ownership(raw) if market.fetch_institutional_holders else None
        )
        company = build_company_json(
            sym, raw, metadata, trends, None, None, market=market,
            cik=cik, institutional_ownership=institutional_ownership,
        )
        tmp = companies_dir / f".{sym}.json.tmp"
        with open(tmp, "w") as f:
            json.dump(company, f, indent=2)
        tmp.replace(companies_dir / f"{sym}.json")  # atomic: no torn files
        trends["symbol"] = sym
        return {"snapshot": build_current_snapshot(raw, market), "trends": trends}

    report = run_fetch_pipeline(
        symbols,
        fetch_fn=lambda s: fetch_ticker_data(
            f"{s}{market.ticker_suffix}", institutional_holders=market.fetch_institutional_holders
        ),
        handle_fn=handle,
        workers=workers,
        label=market.fetch_label,
    )
    results, failed = report.saved, report.failed
    write_failure_log(market.failed_tickers_path, failed)

    _save_raw_csvs(market, results)

    if not no_transform:
        print("\nRebuilding indices...")
        build_indices(companies_dir=companies_dir, indices_dir=indices_dir)

    if market.enrichment_datasets:
        print("\nUpdating enrichments...")
        for dataset in market.enrichment_datasets:
            stale = get_stale_symbols(dataset, enrichment_symbols)
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
        print(f"  Retry:   python scripts/data_refresh.py --market {market.id} --symbols "
              f"{' '.join(s for s, _ in failed[:5])}"
              f"{' …' if len(failed) > 5 else ''}")
        print(f"           (full list: {market.failed_tickers_path.relative_to(ROOT)})")
    if indices_dir.exists():
        with open(indices_dir / "screening_summary.json") as f:
            summary = json.load(f)
            gen = summary.get("generated_at", "")[:16].replace("T", " ")
            total = summary.get("total_companies", 0)
            print(f"  Index: {total} companies (as of {gen})")
    print("=" * 60 + "\n")

    _write_manifest(market)
    return {"fetched": len(results), "failed": len(failed), "skipped": max(skipped_count, 0), "elapsed": round(elapsed, 1)}


def run_pipeline(
    market: MarketConfig,
    *,
    mode: str = "incremental",
    symbols: list[str] | None = None,
    workers: int | None = None,
    dry_run: bool = False,
    days_old: int = 7,
    no_transform: bool = False,
) -> dict:
    """Shared orchestration engine for both NSE500 and S&P500 pipelines.

    Every market-specific behavior (universe fetching, staleness policy,
    ticker suffix, optional enrichment/CSV steps, valid modes) comes from
    `market` -- see screener.market.NSE / screener.market.SNP.
    """
    if mode not in market.valid_modes:
        raise ValueError(f"{market.label}: mode {mode!r} not supported (valid modes: {market.valid_modes})")

    if workers is None:
        workers = MAX_WORKERS

    start = time.time()
    print("\n" + "=" * 60)
    print(f"  {market.label} Data Update — {mode}")
    print(f"  {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 60)

    companies_dir = market.companies_dir

    if mode in ("transform-only", "rebuild"):
        print(f"\nMode: {'TRANSFORM ONLY' if mode == 'transform-only' else 'REBUILD INDICES'}")
        return _finish(market, start)

    metadata = None
    total_considered = 0
    targeted = False

    if mode == "sync-universe":
        print("\nMode: SYNC UNIVERSE")
        universe_symbols, metadata = market.fetch_universe()
        current_symbols = set(universe_symbols)
        existing_symbols = {p.stem for p in companies_dir.glob("*.json")} if companies_dir.exists() else set()

        removed = sorted(existing_symbols - current_symbols)
        for sym in removed:
            path = companies_dir / f"{sym}.json"
            if path.exists():
                path.unlink()
                print(f"    Deleted {path.name}")

        total_considered = len(current_symbols)
        symbols = _stale_symbols_for(market, sorted(current_symbols), days_old)
        print(f"  Removed from index: {len(removed)}")
        print(f"  Stale/missing: {len(symbols)}")

    elif symbols is None:
        if mode == "full":
            print(f"\nMode: FULL ({workers} workers)")
            symbols, metadata = market.fetch_universe()
            total_considered = len(symbols)
        elif mode == "quick":
            symbols = _top_symbols_by_mcap(market, 50)
            if not symbols:
                print("  No existing data; fetching top 50...")
                all_symbols, metadata = market.fetch_universe()
                symbols = all_symbols[:50]
            total_considered = len(symbols)
            print(f"\nMode: QUICK (top {len(symbols)} by mcap)")
        else:
            # incremental
            print(f"\nMode: INCREMENTAL (staleness: {days_old} days)")
            # Always re-check the live constituent list, not just the local
            # companies_dir, so a brand-new listing (no local file yet) is
            # picked up on the very next incremental run rather than only via
            # an explicit --mode sync-universe. A symbol with no local file
            # is automatically "stale" (see _stale_symbols_for), so no extra
            # logic is needed beyond always fetching the live list here.
            all_symbols, metadata = market.fetch_universe()
            total_considered = len(all_symbols)
            symbols = _stale_symbols_for(market, all_symbols, days_old)
            print(f"  {len(all_symbols)} companies in universe")
            print(f"  {len(symbols)} stale, {len(all_symbols) - len(symbols)} up-to-date")
    else:
        symbols = [s.upper() for s in symbols]
        total_considered = len(symbols)
        targeted = True
        print(f"\nMode: TARGETED ({len(symbols)} companies)")

    if not symbols:
        print("\nAll data up-to-date.")
        return _finish(market, start, skipped=total_considered)

    return _fetch_and_save(
        market, symbols, metadata,
        workers=workers, dry_run=dry_run, no_transform=no_transform, start=start,
        enrichment_symbols=symbols if targeted else None,
    )
