#!/usr/bin/env python3
"""
Annual Report Downloader — downloads 10-K filing documents from SEC EDGAR
for S&P500 stocks.

Usage:
    python fetch_annual_reports_snp.py --symbol AAPL         # Single stock
    python fetch_annual_reports_snp.py --symbols AAPL MSFT   # Multiple stocks
    python fetch_annual_reports_snp.py --all --limit 10      # All S&P500 (limited)
"""

from pathlib import Path

import argparse
import json
import logging

from tqdm import tqdm

from screener.config import SNP_ANNUAL_REPORTS_DIR, SNP_INDICES_DIR
from screener.fetch import download_edgar_document, fetch_edgar_submissions, get_10k_filings

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

REPORTS_DIR = SNP_ANNUAL_REPORTS_DIR


def get_all_snp500_symbols() -> dict[str, int]:
    """Return {symbol: cik} for every S&P500 company in the curated summary."""
    summary_file = SNP_INDICES_DIR / "screening_summary.json"
    if summary_file.exists():
        with open(summary_file) as f:
            data = json.load(f)
            return {c["symbol"]: c["cik"] for c in data.get("companies", []) if c.get("cik")}
    return {}


def fetch_and_download_10ks(symbol: str, cik: int, max_reports: int = 1) -> dict:
    result = {"symbol": symbol, "success": False, "filings": [], "downloaded": [], "error": None}

    submissions = fetch_edgar_submissions(cik)
    if not submissions:
        result["error"] = "Could not fetch EDGAR submissions"
        return result

    filings = get_10k_filings(submissions, max_reports=max_reports)
    if not filings:
        result["error"] = "No 10-K filings found"
        return result

    result["filings"] = filings
    symbol_dir = REPORTS_DIR / symbol

    for filing in filings:
        year = filing["year"]
        ext = Path(filing["url"]).suffix or ".htm"
        filename = f"{symbol}_10K_{year}{ext}"
        output_path = symbol_dir / filename

        if output_path.exists():
            logger.info(f"  Already exists: {filename}")
            result["downloaded"].append(str(output_path))
            continue

        logger.info(f"  Downloading: {filename}")
        if download_edgar_document(filing["url"], output_path):
            result["downloaded"].append(str(output_path))
            logger.info(f"  Saved: {output_path}")
        else:
            logger.warning(f"  Failed to download: {filename}")

    result["success"] = len(result["downloaded"]) > 0
    return result


def process_single_stock(symbol: str, cik: int, max_reports: int) -> None:
    print(f"\n{'='*60}")
    print(f"Fetching 10-K filings for: {symbol} (CIK {cik})")
    print("=" * 60)

    result = fetch_and_download_10ks(symbol, cik, max_reports)

    print(f"\nResult: {'Success' if result['success'] else 'Failed'}")
    if result.get("error"):
        print(f"Error: {result['error']}")
    if result.get("filings"):
        print("\nAvailable 10-K filings:")
        for f in result["filings"]:
            print(f"  - {f['year']}: filed {f['filing_date']}")
    if result.get("downloaded"):
        print("\nDownloaded:")
        for path in result["downloaded"]:
            print(f"  - {path}")


def process_multiple_stocks(
    symbol_ciks: dict[str, int],
    limit: int | None = None,
    max_reports_per_stock: int = 1,
) -> dict:
    items = list(symbol_ciks.items())
    if limit:
        items = items[:limit]

    results = {"success": [], "failed": []}
    for symbol, cik in tqdm(items, desc="Fetching 10-Ks"):
        result = fetch_and_download_10ks(symbol, cik, max_reports_per_stock)
        if result["success"]:
            results["success"].append({"symbol": symbol, "downloaded": result["downloaded"]})
        else:
            results["failed"].append({"symbol": symbol, "error": result.get("error")})

    return results


def main():
    parser = argparse.ArgumentParser(description="Download 10-K filing documents from SEC EDGAR")
    parser.add_argument("--symbol", type=str, help="Single stock symbol")
    parser.add_argument("--symbols", type=str, nargs="+", help="Multiple symbols")
    parser.add_argument("--all", action="store_true", help="All S&P500 stocks")
    parser.add_argument("--limit", type=int, help="Limit number of stocks")
    parser.add_argument(
        "--reports", type=int, default=1,
        help="Max 10-K filings per stock (default: 1, i.e., latest 1 year)"
    )
    parser.add_argument("-v", "--verbose", action="store_true")

    args = parser.parse_args()
    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    all_symbols = get_all_snp500_symbols()
    if not all_symbols:
        print("No S&P500 symbols/CIKs found. Run python scripts/data_refresh.py --market snp --mode full first.")
        return

    if args.symbol:
        symbol = args.symbol.upper()
        cik = all_symbols.get(symbol)
        if not cik:
            print(f"No CIK found for {symbol}")
            return
        process_single_stock(symbol, cik, args.reports)

    elif args.symbols:
        requested = [s.upper() for s in args.symbols]
        symbol_ciks = {s: all_symbols[s] for s in requested if s in all_symbols}
        missing = [s for s in requested if s not in all_symbols]
        if missing:
            print(f"No CIK found for: {', '.join(missing)}")
        results = process_multiple_stocks(symbol_ciks, args.limit, args.reports)
        print(f"\nSuccess: {len(results['success'])}, Failed: {len(results['failed'])}")

    elif args.all:
        print(f"Processing {len(all_symbols)} S&P500 stocks...")
        results = process_multiple_stocks(all_symbols, args.limit, args.reports)
        print(f"\nSuccess: {len(results['success'])}, Failed: {len(results['failed'])}")

        summary_path = REPORTS_DIR / "_download_summary.json"
        summary_path.parent.mkdir(parents=True, exist_ok=True)
        with open(summary_path, "w") as f:
            json.dump(results, f, indent=2)
        print(f"Summary saved to: {summary_path}")

    else:
        parser.print_help()


if __name__ == "__main__":
    main()
