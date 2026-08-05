"""
Market-specific data pipeline orchestration.

run_pipeline is the single engine for both markets, driven entirely by the
MarketConfig passed in (screener.market.NSE / screener.market.SNP) -- see
screener.cli for the CLI that calls it.
"""

import json
import queue
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path

from screener.filings import html_filings as hf
from screener.filings import pdf_filings as pf
from screener.annual_reports import (
    NSE_REPORTS_DIR,
    SNP_REPORTS_DIR,
    download_reports,
    fetch_snp_reports,
    is_report_stale,
    screener_session,
)
from screener.config import MAX_WORKERS, ROOT
from screener.db import rebuild as rebuild_db
from screener.enrich import get_stale_symbols, process_symbol_full
from screener.fetch import build_cik_map, fetch_edgar_facts, fetch_ticker_data
from screener.freshness import stale_symbols
from screener.index import build_indices, update_manifest
from screener.market import MarketConfig
from screener.runner import run_fetch_pipeline, write_failure_log
from screener.index import delete_company, iter_companies, list_symbols, merge_company
from screener.transform import (
    build_company_json,
    build_current_snapshot,
    build_historical_trends,
    build_historical_trends_edgar,
    build_institutional_ownership,
)


def _stale_symbols_for(companies_dir: Path, symbols: list[str], policies) -> list[str]:
    """A symbol refetches if any of `policies` flag it.

    Delegates to freshness.stale_symbols which now accepts a list of policies
    and reads each file once regardless."""
    return stale_symbols(companies_dir, policies, symbols=symbols)


def _rank_by_mcap(indices_dir: Path, universe: set[str]) -> list[str]:
    """`universe` ordered by market cap descending (from the existing
    screening_summary.json, if any), with symbols that have no cap data yet
    (new listings, or a fresh bootstrap with no summary at all) appended
    after -- so capping this list to N always yields something, even before
    a single company has ever been fetched."""
    summary_path = indices_dir / "screening_summary.json"
    ranked: list[str] = []
    if summary_path.exists():
        with open(summary_path) as f:
            companies = json.load(f).get("companies", [])
        by_mcap = sorted(
            (c for c in companies if c.get("market_cap") and c["symbol"] in universe),
            key=lambda c: c["market_cap"], reverse=True,
        )
        ranked = [c["symbol"] for c in by_mcap]
    return ranked + sorted(universe - set(ranked))


def _coverage(companies_dir: Path, predicate) -> float:
    """Fraction of company files whose loaded JSON satisfies `predicate`.
    An unreadable file just doesn't count as covered."""
    all_symbols = list_symbols(companies_dir)
    if not all_symbols:
        return 0.0
    covered = sum(1 for _, c in iter_companies(companies_dir) if predicate(c))
    return round(covered / len(all_symbols), 4)


def _write_manifest(market: MarketConfig, companies_dir: Path) -> None:
    """Update data/manifest.json after pipeline run. Doesn't touch `db` --
    this pipeline never rebuilds screener.db; rebuild_market_db owns that key.

    Enrichment coverage is config-driven off the same fields that gate the
    enrichment steps themselves, not a hardcoded per-market list."""
    entry: dict[str, int | float] = {"total_companies": len(list_symbols(companies_dir))}

    for dataset in market.enrichment_datasets:
        entry[f"{dataset}_coverage"] = _coverage(companies_dir, lambda c, d=dataset: bool(c.get(d)))

    if market.uses_edgar:
        entry["edgar_coverage"] = _coverage(
            companies_dir, lambda c: bool(c.get("historical_trends", {}).get("years_available"))
        )

    update_manifest(market.id, entry)


def _finish(market: MarketConfig, companies_dir: Path, start: float, *, skipped: int = 0) -> dict:
    build_indices(companies_dir=companies_dir, indices_dir=market.indices_dir)
    rebuild_db(market)
    elapsed = time.time() - start
    _write_manifest(market, companies_dir)
    print(f"\nDone in {elapsed:.1f}s\n")
    return {"fetched": 0, "failed": 0, "skipped": skipped, "elapsed": elapsed}


# ── Stage B/C consumers: drain the "fundamentals just landed" queue ───────
#
# Stage A (fundamentals) pushes a symbol the instant its file is written, so
# these run concurrently with Stage A instead of waiting for it to finish --
# each symbol is enriched/report-checked as soon as it's safe to (its file
# already exists), no batching, one symbol handled and forgotten at a time.
# A `None` sentinel on the queue ends the consumer loop.
#
# Report download+indexing (Stage C) is handed off to `report_pool` instead
# of run inline: it's the slow leg (network download of a PDF/10-K), and
# nothing downstream (indices, screener.db) actually depends on it -- it
# would otherwise gate a fast DB rebuild behind a slow scrape for no reason.

def _nse_consumer(
    q: "queue.Queue[str | None]", companies_dir: Path, fetch_reports: bool, report_pool: ThreadPoolExecutor,
) -> None:
    session = screener_session()
    while (sym := q.get()) is not None:
        try:
            symbol_dir = NSE_REPORTS_DIR / sym
            need_report = fetch_reports and is_report_stale(symbol_dir, "*.pdf")
            links = process_symbol_full(sym, companies_dir, session, need_report=need_report)
            if links:
                report_pool.submit(_download_and_index_nse, sym, links, symbol_dir)
        except Exception as e:
            print(f"  {sym}: enrichment error: {e}")
        finally:
            q.task_done()


def _download_and_index_nse(sym: str, links: list[dict], symbol_dir: Path) -> None:
    session = screener_session()
    for path_str in download_reports(sym, links, symbol_dir, session, max_reports=1):
        try:
            pf.build_index(Path(path_str))
        except Exception as e:
            print(f"  {sym}: report index failed: {e}")


def _snp_consumer(
    q: "queue.Queue[str | None]", cik_map: dict[str, int], fetch_reports: bool, report_pool: ThreadPoolExecutor,
) -> None:
    while (sym := q.get()) is not None:
        try:
            cik = cik_map.get(sym)
            if fetch_reports and cik and is_report_stale(SNP_REPORTS_DIR / sym, "*.htm"):
                report_pool.submit(_download_and_index_snp, sym, cik)
        except Exception as e:
            print(f"  {sym}: report check error: {e}")
        finally:
            q.task_done()


def _download_and_index_snp(sym: str, cik: int) -> None:
    result = fetch_snp_reports(sym, cik, max_reports=1)
    for path_str in result.get("downloaded", []):
        try:
            hf.build_index(Path(path_str))
        except Exception as e:
            print(f"  {sym}: report index failed: {e}")


def _fetch_and_save(
    market: MarketConfig,
    symbols: list[str],
    metadata: dict[str, dict] | None,
    *,
    workers: int,
    start: float,
    targeted: bool = False,
    fetch_reports: bool = True,
) -> dict:
    """Fetch, transform and persist `symbols`, then rebuild indices/DB.
    Shared by every mode that ends in an actual fetch (sync-universe, full,
    quick, incremental, targeted).

    Stage A (fundamentals) runs concurrently with Stage B (shareholding/
    credit-ratings, NSE) and Stage C (annual report / 10-K download+index):
    each symbol is handed off the instant its fundamentals file lands, via a
    queue, rather than B/C waiting for the whole Stage A batch to finish.

    targeted: True for a --symbols run -- restricts B/C to just the
    requested symbols instead of also sweeping the rest of companies_dir for
    unrelated staleness (shareholding/reports roll over on their own, slower
    cadence, so they're independently stale-checked on every full/incremental
    run -- but not on a single-symbol retry)."""
    companies_dir = market.companies_dir
    indices_dir = market.indices_dir

    # Each company is written as its fetch lands, so an interrupted or
    # rate-limited run keeps everything fetched up to that point.
    companies_dir.mkdir(parents=True, exist_ok=True)

    # Built once per run, not per symbol -- build_cik_map does a single bulk
    # fetch of SEC's full ticker->CIK table (see fetch.build_cik_map's
    # docstring: no cik_map.json cache, resolved fresh in memory each run).
    cik_map = build_cik_map() if market.uses_edgar else {}

    q: queue.Queue[str | None] = queue.Queue()
    report_pool = ThreadPoolExecutor(max_workers=1)  # one shared rate limiter -- more workers wouldn't help
    if market.uses_edgar:
        consumer = threading.Thread(target=_snp_consumer, args=(q, cik_map, fetch_reports, report_pool), daemon=True)
    else:
        consumer = threading.Thread(target=_nse_consumer, args=(q, companies_dir, fetch_reports, report_pool), daemon=True)
    consumer.start()

    if not targeted:
        already = set(symbols)
        extra: set[str] = set()
        for dataset in market.enrichment_datasets:
            extra |= set(get_stale_symbols(dataset, companies_dir))
        if fetch_reports:
            reports_dir, glob_pat = (SNP_REPORTS_DIR, "*.htm") if market.uses_edgar else (NSE_REPORTS_DIR, "*.pdf")
            extra |= {s for s in list_symbols(companies_dir) if is_report_stale(reports_dir / s, glob_pat)}
        for sym in sorted(extra - already):
            q.put(sym)

    def handle(sym: str, raw: dict) -> dict:
        if market.uses_edgar:
            cik = cik_map.get(sym)
            trends = build_historical_trends_edgar(fetch_edgar_facts(sym, cik), market)
        else:
            cik = None
            trends = build_historical_trends(raw, market)
        institutional_ownership = (
            build_institutional_ownership(raw) if market.fetch_institutional_holders else None
        )
        company = build_company_json(
            sym, raw, metadata, trends, market=market,
            cik=cik, institutional_ownership=institutional_ownership,
        )
        merge_company(companies_dir, sym, company)
        q.put(sym)
        trends["symbol"] = sym
        return {"snapshot": build_current_snapshot(raw, market), "trends": trends}

    report = run_fetch_pipeline(
        symbols,
        fetch_fn=lambda s: fetch_ticker_data(
            f"{s}{market.ticker_suffix}",
            institutional_holders=market.fetch_institutional_holders,
            annual_statements=not market.uses_edgar,
        ),
        handle_fn=handle,
        workers=workers,
        label=market.fetch_label,
    )
    results, failed = report.saved, report.failed
    write_failure_log(market.failed_tickers_path, failed)

    q.put(None)
    consumer.join()

    # NSE-only: raw CSV snapshots (historical-trend CSVs never used for SNP).
    if market.raw_csv_dir and results:
        import pandas as pd
        market.raw_csv_dir.mkdir(parents=True, exist_ok=True)
        pd.DataFrame([r["snapshot"] for r in results]).to_csv(
            market.raw_csv_dir / "current_metrics.csv", index=False
        )
        records = [
            {"symbol": r["trends"].get("symbol"), "fiscal_year": fy,
             "revenue": r["trends"].get("revenue", {}).get("values"),
             "net_income": r["trends"].get("net_income", {}).get("values"),
             "eps": r["trends"].get("eps", {}).get("values")}
            for r in results for fy in r["trends"].get("years_available", [])
        ]
        pd.DataFrame(records).to_csv(market.raw_csv_dir / "historical_annual.csv", index=False)

    # Stage B is done (consumer joined above); rebuild indices/DB now rather
    # than waiting on Stage C's report downloads, which don't feed either one.
    print("\nRebuilding indices...")
    build_indices(companies_dir=companies_dir, indices_dir=indices_dir)
    print("Rebuilding screener.db...")
    rebuild_db(market)

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

    print("Finishing annual report downloads...")
    report_pool.shutdown(wait=True)

    _write_manifest(market, companies_dir)
    return {"fetched": len(results), "failed": len(failed), "skipped": max(skipped_count, 0), "elapsed": round(elapsed, 1)}


QUICK_SYNC_LIMIT = 50  # quick-sync's cap -- keeps it fast even on a cold bootstrap


def run_pipeline(
    market: MarketConfig,
    *,
    mode: str = "quick-sync",
    symbols: list[str] | None = None,
    workers: int | None = None,
    days_old: int = 7,
    fetch_reports: bool = True,
) -> dict:
    """Shared orchestration engine for both NSE500 and S&P500 pipelines.

    Every market-specific behavior (universe fetching, staleness policy,
    ticker suffix, optional enrichment/CSV steps) comes from `market` -- see
    screener.market.NSE / screener.market.SNP.

    Both modes always sync the universe first (fetch the live constituent
    list, delete anything dropped from it) before deciding which survivors
    to fetch -- there's no separate "just sync, don't fetch" mode, since a
    symbol with no file yet is automatically stale either way.

    `mode="full-sync"`: fetch every current symbol, unconditionally.
    `mode="quick-sync"`: fetch only stale symbols, capped at the top
    `QUICK_SYNC_LIMIT` by market cap -- fast on routine runs (little is
    stale) *and* on a cold bootstrap (cap still applies when "stale" means
    "everything").

    `fetch_reports` is an orthogonal switch for the slow annual-report/10-K
    leg -- independent of mode, not implied by it.

    `symbols` bypasses mode selection entirely (a `--symbols` targeted run):
    no universe sync, no staleness check, just fetch exactly what's named.
    """
    if symbols is None and mode not in market.valid_modes:
        raise ValueError(f"{market.label}: mode {mode!r} not supported (valid modes: {market.valid_modes})")

    if workers is None:
        workers = MAX_WORKERS

    start = time.time()
    print("\n" + "=" * 60)
    print(f"  {market.label} Data Update — {mode}")
    print(f"  {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 60)

    companies_dir = market.companies_dir

    metadata = None
    total_considered = 0
    targeted = False

    if symbols is not None:
        symbols = [s.upper() for s in symbols]
        total_considered = len(symbols)
        targeted = True
        print(f"\nMode: TARGETED ({len(symbols)} companies)")
    else:
        print(f"\nMode: {mode.upper()}")
        universe_symbols, metadata = market.fetch_universe()
        current_symbols = set(universe_symbols)
        existing_symbols = set(list_symbols(companies_dir))

        removed = sorted(existing_symbols - current_symbols)
        for sym in removed:
            delete_company(companies_dir, sym)
            print(f"    Deleted {sym}.json")
        total_considered = len(current_symbols)
        print(f"  Removed from index: {len(removed)}")

        if mode == "full-sync":
            symbols = sorted(current_symbols)
            print(f"  Fetching all {len(symbols)} companies ({workers} workers)")
        else:
            # quick-sync
            policies = market.staleness_policies(days_old)
            stale = set(_stale_symbols_for(companies_dir, sorted(current_symbols), policies))
            ranked = _rank_by_mcap(market.indices_dir, current_symbols)
            symbols = [s for s in ranked if s in stale][:QUICK_SYNC_LIMIT]
            print(f"  {len(stale)} stale, capped to top {QUICK_SYNC_LIMIT} by market cap: {len(symbols)} to fetch")

    if not symbols:
        print("\nAll data up-to-date.")
        return _finish(market, companies_dir, start, skipped=total_considered)

    return _fetch_and_save(
        market, symbols, metadata,
        workers=workers, start=start,
        targeted=targeted,
        fetch_reports=fetch_reports,
    )
