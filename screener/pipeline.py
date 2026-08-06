"""Run the shared market data pipeline."""

import json
import queue
import threading
import time
from concurrent.futures import Future, ThreadPoolExecutor
from datetime import datetime
from pathlib import Path

import pandas as pd

from screener.annual_reports import (
    NSE_REPORTS_DIR,
    SNP_REPORTS_DIR,
    download_reports,
    fetch_snp_reports,
    is_report_stale,
    screener_session,
)
from screener.config import MAX_WORKERS, RAW_DIR, ROOT
from screener.db import rebuild as rebuild_db
from screener.edgar import build_cik_map, fetch_facts
from screener.enrich import get_stale_symbols, process_symbol_full
from screener.fetch import cache_price_history, fetch_ticker_data
from screener.filings import html_filings as hf
from screener.filings import pdf_filings as pf
from screener.freshness import stale_symbols
from screener.index import (
    build_indices,
    delete_company,
    iter_companies,
    list_symbols,
    merge_company,
    update_manifest,
)
from screener.market import ALL_MODES, MarketConfig
from screener.regulatory import (
    completed_december_years,
    download_ffiec_years,
    download_nse_bank_filings,
    ffiec_rssd_ids,
    is_yahoo_bank,
    parse_ffiec_history,
    parse_nse_bank_history,
    rssd_id,
)
from screener.runner import run_fetch_pipeline, write_failure_log
from screener.transform import (
    build_company_json,
    build_current_snapshot,
    build_historical_trends,
    build_historical_trends_edgar,
    build_institutional_ownership,
    drawdown_52w,
)


def _rank_by_mcap(indices_dir: Path, universe: set[str]) -> list[str]:
    """Order symbols by known market cap, then append unknowns."""
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
    """Update company and enrichment coverage without touching DB metadata."""
    entry: dict[str, int | float] = {"total_companies": len(list_symbols(companies_dir))}

    for dataset in market.enrichment_datasets:
        entry[f"{dataset}_coverage"] = _coverage(companies_dir, lambda c, d=dataset: bool(c.get(d)))

    if market.uses_edgar:
        entry["edgar_coverage"] = _coverage(
            companies_dir,
            lambda c: bool(
                c.get("historical_trends", {}).get("fiscal_years")
                or c.get("historical_trends", {}).get("years_available")
            ),
        )

    update_manifest(market.id, entry)


def _finish(market: MarketConfig, companies_dir: Path, start: float, *, skipped: int = 0) -> dict:
    build_indices(companies_dir=companies_dir, indices_dir=market.indices_dir, market=market.id)
    rebuild_db(market)
    elapsed = time.time() - start
    _write_manifest(market, companies_dir)
    print(f"\nDone in {elapsed:.1f}s\n")
    return {"fetched": 0, "failed": 0, "skipped": skipped, "elapsed": elapsed}


# Consumers enrich each saved symbol; report downloads run off the critical path.

def _nse_consumer(
    q: "queue.Queue[str | None]",
    companies_dir: Path,
    fetch_reports: bool,
    report_pool: ThreadPoolExecutor,
    report_futures: list[tuple[str, Future[bool]]],
    stats: dict[str, int],
    label: str,
) -> None:
    session = screener_session()
    while (sym := q.get()) is not None:
        try:
            symbol_dir = NSE_REPORTS_DIR / sym
            need_report = fetch_reports and is_report_stale(symbol_dir, "*.pdf")
            links = process_symbol_full(sym, companies_dir, session, need_report=need_report)
            if links:
                report_futures.append((
                    sym,
                    report_pool.submit(_download_and_index_nse, sym, links, symbol_dir),
                ))
            elif need_report:
                stats["report_missing"] += 1
                print(f"  [{label} reports] {sym}: no annual-report link found", flush=True)
        except Exception as e:
            stats["errors"] += 1
            print(f"  [{label} checks] {sym}: enrichment error: {e}", flush=True)
        finally:
            stats["checked"] += 1
            if stats["checked"] % 25 == 0:
                print(
                    f"  [{label} checks] {stats['checked']} complete, "
                    f"{stats['errors']} errors",
                    flush=True,
                )
            q.task_done()


def _download_and_index_nse(sym: str, links: list[dict], symbol_dir: Path) -> bool:
    print(f"  [NSE500 reports] {sym}: downloading...", flush=True)
    session = screener_session()
    try:
        for path_str in download_reports(sym, links, symbol_dir, session, max_reports=1):
            pf.build_index(Path(path_str))
            print(f"  [NSE500 reports] {sym}: ready", flush=True)
            return True
    except Exception as e:
        print(f"  [NSE500 reports] {sym}: failed: {e}", flush=True)
        return False
    print(f"  [NSE500 reports] {sym}: no report downloaded", flush=True)
    return False


def _snp_consumer(
    q: "queue.Queue[str | None]",
    cik_map: dict[str, int],
    fetch_reports: bool,
    report_pool: ThreadPoolExecutor,
    report_futures: list[tuple[str, Future[bool]]],
    stats: dict[str, int],
    label: str,
) -> None:
    while (sym := q.get()) is not None:
        try:
            cik = cik_map.get(sym)
            need_report = fetch_reports and is_report_stale(SNP_REPORTS_DIR / sym, "*.htm")
            if need_report and cik:
                future = report_pool.submit(_download_and_index_snp, sym, cik)
                report_futures.append((sym, future))
            elif need_report:
                stats["report_missing"] += 1
                print(f"  [{label} reports] {sym}: no SEC CIK found", flush=True)
        except Exception as e:
            stats["errors"] += 1
            print(f"  [{label} checks] {sym}: report check error: {e}", flush=True)
        finally:
            stats["checked"] += 1
            if stats["checked"] % 25 == 0:
                print(
                    f"  [{label} checks] {stats['checked']} complete, "
                    f"{stats['errors']} errors",
                    flush=True,
                )
            q.task_done()


def _download_and_index_snp(sym: str, cik: int) -> bool:
    print(f"  [S&P 500 reports] {sym}: downloading...", flush=True)
    try:
        result = fetch_snp_reports(sym, cik, max_reports=1)
        if result.get("error"):
            raise RuntimeError(result["error"])
        for path_str in result.get("downloaded", []):
            hf.build_index(Path(path_str))
            print(f"  [S&P 500 reports] {sym}: ready", flush=True)
            return True
    except Exception as e:
        print(f"  [S&P 500 reports] {sym}: failed: {e}", flush=True)
        return False
    print(f"  [S&P 500 reports] {sym}: no report downloaded", flush=True)
    return False


def _fetch_and_save(
    market: MarketConfig,
    symbols: list[str],
    metadata: dict[str, dict] | None,
    *,
    workers: int,
    start: float,
    targeted: bool = False,
    fetch_reports: bool = True,
    days_old: int = 7,
) -> dict:
    """Fetch and persist symbols, run enrichment, then rebuild indices and DB."""
    companies_dir = market.companies_dir
    indices_dir = market.indices_dir

    # Each company is written as its fetch lands, so an interrupted or
    # rate-limited run keeps everything fetched up to that point.
    companies_dir.mkdir(parents=True, exist_ok=True)

    # Built once per run, not per symbol -- build_cik_map does a single bulk
    # fetch of SEC's full ticker->CIK table (see fetch.build_cik_map's
    # docstring: no cik_map.json cache, resolved fresh in memory each run).
    cik_map = build_cik_map() if market.uses_edgar else {}
    bank_errors: list[str] = []
    missing_rssd: set[str] = set()
    bank_lock = threading.Lock()

    # A BHCF file contains every institution, so fetch each year once per run.
    mapped_rssd = {sym: rssd_id(sym) for sym in symbols if market.id == "snp" and rssd_id(sym)}
    if mapped_rssd:
        try:
            ffiec_paths, ffiec_errors = download_ffiec_years(
                completed_december_years(), days_old=days_old,
            )
            bank_errors.extend(f"FFIEC {year}: {error}" for year, error in ffiec_errors)
            if ffiec_paths:
                latest_path = ffiec_paths[max(ffiec_paths)]
                present = ffiec_rssd_ids(latest_path)
                for sym, resolved in mapped_rssd.items():
                    if resolved not in present:
                        bank_errors.append(
                            f"{sym}: RSSD {resolved} absent from {latest_path.name}"
                        )
        except Exception as e:
            bank_errors.append(f"FFIEC: {e}")

    already = set(symbols)
    extra: set[str] = set()
    enrichment_stale: dict[str, int] = {}
    if not targeted:
        for dataset in market.enrichment_datasets:
            stale = set(get_stale_symbols(dataset, companies_dir))
            enrichment_stale[dataset] = len(stale)
            extra |= stale

    report_candidates: set[str] = set()
    if fetch_reports:
        reports_dir, glob_pat = (
            (SNP_REPORTS_DIR, "*.htm")
            if market.uses_edgar
            else (NSE_REPORTS_DIR, "*.pdf")
        )
        report_universe = already | (set(list_symbols(companies_dir)) if not targeted else set())
        report_candidates = {
            sym for sym in report_universe if is_report_stale(reports_dir / sym, glob_pat)
        }
        if not targeted:
            extra |= report_candidates

    checks_planned = already | extra
    print(f"\n[{market.label}] Work plan", flush=True)
    print(f"  [{market.label}] Fundamentals to fetch: {len(symbols)}", flush=True)
    print(f"  [{market.label}] Company checks: up to {len(checks_planned)}", flush=True)
    for dataset, count in enrichment_stale.items():
        print(f"  [{market.label}] {dataset} stale/missing: {count}", flush=True)
    if fetch_reports:
        print(
            f"  [{market.label}] Annual reports stale/missing: {len(report_candidates)}",
            flush=True,
        )
    else:
        print(f"  [{market.label}] Annual reports: disabled", flush=True)

    q: queue.Queue[str | None] = queue.Queue()
    stats = {"checked": 0, "errors": 0, "report_missing": 0}
    report_futures: list[tuple[str, Future[bool]]] = []
    # One shared rate limiter means more report workers would not help.
    report_pool = ThreadPoolExecutor(max_workers=1)
    if market.uses_edgar:
        args = (q, cik_map, fetch_reports, report_pool, report_futures, stats, market.label)
        consumer = threading.Thread(target=_snp_consumer, args=args, daemon=True)
    else:
        args = (
            q,
            companies_dir,
            fetch_reports,
            report_pool,
            report_futures,
            stats,
            market.label,
        )
        consumer = threading.Thread(target=_nse_consumer, args=args, daemon=True)
    consumer.start()

    if not targeted:
        for sym in sorted(extra - already):
            q.put(sym)

    def handle(sym: str, raw: dict) -> dict:
        price_path = RAW_DIR / market.id / "prices" / f"{sym}.csv"
        try:
            cache_price_history(
                raw.get("price_history", pd.DataFrame()),
                price_path,
            )
        except Exception as e:
            print(f"  [{market.label} prices] {sym}: cache error: {e}", flush=True)

        regulatory_history = {}
        if market.id == "nse" and is_yahoo_bank(raw.get("info", {})):
            try:
                download_nse_bank_filings(sym, days_old=days_old)
            except Exception as e:
                with bank_lock:
                    bank_errors.append(f"{sym}: {e}")
            regulatory_history = parse_nse_bank_history(sym)

        if market.uses_edgar:
            cik = cik_map.get(sym)
            resolved = rssd_id(sym)
            if is_yahoo_bank(raw.get("info", {})) and resolved is not None:
                regulatory_history = parse_ffiec_history(resolved)
            trends = build_historical_trends_edgar(
                fetch_facts(sym, cik), market, regulatory=regulatory_history,
            )
        else:
            cik = None
            trends = build_historical_trends(raw, market, regulatory=regulatory_history)
        institutional_ownership = (
            build_institutional_ownership(raw) if market.uses_edgar else None
        )
        company = build_company_json(
            sym, raw, metadata, trends, market=market,
            cik=cik, institutional_ownership=institutional_ownership,
            drawdown=drawdown_52w(price_path),
        )
        if market.id == "snp" and is_yahoo_bank(raw.get("info", {})):
            resolved = rssd_id(sym)
            if resolved is None:
                with bank_lock:
                    missing_rssd.add(sym)
            else:
                company["rssd_id"] = resolved
        merge_company(companies_dir, sym, company)
        q.put(sym)
        trends["symbol"] = sym
        return {"snapshot": build_current_snapshot(raw, market), "trends": trends}

    report = run_fetch_pipeline(
        symbols,
        fetch_fn=lambda s: fetch_ticker_data(
            f"{s}{market.ticker_suffix}",
            institutional_holders=market.uses_edgar,
            annual_statements=not market.uses_edgar,
        ),
        handle_fn=handle,
        workers=workers,
        label=f"{market.label} fundamentals",
    )
    results, failed = report.saved, report.failed
    write_failure_log(market.failed_tickers_path, failed)

    q.put(None)
    consumer.join()

    # NSE-only: raw CSV snapshots (historical-trend CSVs never used for SNP).
    if market.raw_csv_dir and results:
        market.raw_csv_dir.mkdir(parents=True, exist_ok=True)
        pd.DataFrame([r["snapshot"] for r in results]).to_csv(
            market.raw_csv_dir / "current_metrics.csv", index=False
        )
        records = []
        for result in results:
            trends = result["trends"]
            years = trends.get("fiscal_years") or trends.get("years_available", [])
            for index, fiscal_year in enumerate(years):
                record = {"symbol": trends.get("symbol"), "fiscal_year": fiscal_year}
                for output, series in (
                    ("revenue", "revenue"), ("net_income", "net_income"),
                    ("eps", "diluted_eps"),
                ):
                    values = trends.get(series, [])
                    if isinstance(values, dict):
                        values = values.get("values", [])
                    record[output] = values[index] if index < len(values) else None
                records.append(record)
        pd.DataFrame(records).to_csv(market.raw_csv_dir / "historical_annual.csv", index=False)

    # Stage B is done (consumer joined above); rebuild indices/DB now rather
    # than waiting on Stage C's report downloads, which don't feed either one.
    print(f"\n[{market.label}] Rebuilding indices...", flush=True)
    build_indices(companies_dir=companies_dir, indices_dir=indices_dir, market=market.id)
    print(f"[{market.label}] Rebuilding screener.db...", flush=True)
    rebuild_db(market)

    skipped_count = len(symbols) - len(results) - len(failed)
    if report_futures:
        print(
            f"[{market.label}] Waiting for {len(report_futures)} annual-report job(s)...",
            flush=True,
        )
    report_pool.shutdown(wait=True)

    reports_ready = 0
    reports_failed = 0
    for sym, future in report_futures:
        try:
            if future.result():
                reports_ready += 1
            else:
                reports_failed += 1
        except Exception as e:
            reports_failed += 1
            print(f"  [{market.label} reports] {sym}: failed: {e}", flush=True)

    reports_unchecked = max(
        len(report_candidates) - len(report_futures) - stats["report_missing"],
        0,
    )
    reports_unresolved = reports_failed + stats["report_missing"] + reports_unchecked
    elapsed = time.time() - start
    _write_manifest(market, companies_dir)

    print("\n" + "=" * 60)
    print(f"  [{market.label}] Update complete in {elapsed:.1f}s")
    print(
        f"  [{market.label}] Fundamentals: {len(results)} fetched, {len(failed)} failed, "
        f"{max(skipped_count, 0)} skipped"
    )
    print(
        f"  [{market.label}] Company checks: "
        f"{stats['checked']} complete, {stats['errors']} errors"
    )
    print(f"  [{market.label}] Bank downloads: {len(bank_errors)} errors")
    if missing_rssd:
        print(
            f"  [{market.label}] Bank warning: no reviewed RSSD mapping for "
            f"{', '.join(sorted(missing_rssd))}"
        )
    if fetch_reports:
        print(
            f"  [{market.label}] Annual reports: "
            f"{reports_ready} refreshed, {reports_unresolved} unresolved"
        )
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
            print(f"  [{market.label}] Index: {total} companies (as of {gen})")
    print("=" * 60 + "\n")
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
    """Run a full, quick, or targeted refresh for one market."""
    if symbols is None and mode not in ALL_MODES:
        raise ValueError(f"{market.label}: mode {mode!r} not supported (valid modes: {ALL_MODES})")

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
            stale = set(stale_symbols(
                companies_dir, policies, symbols=sorted(current_symbols)
            ))
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
        days_old=days_old,
    )
