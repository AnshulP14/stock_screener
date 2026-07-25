#!/usr/bin/env python3
"""
Annual Report Downloader — downloads annual report PDFs from screener.in for NSE500 stocks.

Usage:
    python fetch_annual_reports.py --symbol RELIANCE     # Single stock
    python fetch_annual_reports.py --symbols TCS INFY   # Multiple stocks
    python fetch_annual_reports.py --all --limit 10     # All NSE500 (limited)
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import argparse
import json
import logging
import re
import time

import requests
from bs4 import BeautifulSoup
from tqdm import tqdm

from core.config import RAW_DIR, INDICES_DIR, SCREENER_BASE_URL, SCREENER_USER_AGENT, RATE_LIMIT_DELAY

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

REPORTS_DIR = RAW_DIR / "nse" / "annual_reports"


def get_all_nse500_symbols() -> list[str]:
    summary_file = INDICES_DIR / "screening_summary.json"
    if summary_file.exists():
        with open(summary_file) as f:
            data = json.load(f)
            return [c["symbol"] for c in data.get("companies", [])]
    return []


def get_session() -> requests.Session:
    session = requests.Session()
    session.headers.update({
        "User-Agent": SCREENER_USER_AGENT,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
    })
    return session


def fetch_annual_reports_from_screener(
    symbol: str,
    session: requests.Session,
    max_reports: int = 5,
) -> dict:
    """Scrape annual report links (year, url) from screener.in's documents section."""
    result = {"symbol": symbol, "success": False, "reports": [], "downloaded": [], "error": None}

    for view in ["consolidated", ""]:
        url = f"{SCREENER_BASE_URL}/{symbol}/{view}#documents"
        try:
            response = session.get(url, timeout=30)
            if response.status_code != 200:
                continue

            soup = BeautifulSoup(response.text, "html.parser")
            annual_reports = []
            ar_section = soup.find("div", {"class": "annual-reports"})
            if ar_section:
                for link in ar_section.find_all("a", href=True):
                    href = link.get("href", "")
                    text = link.get_text(strip=True)
                    if ".pdf" in href.lower():
                        year = extract_year_from_text(text) or extract_year_from_text(href)
                        annual_reports.append({"year": year or "unknown", "url": href, "text": text})

            if annual_reports:
                result["reports"] = annual_reports[:max_reports]
                result["success"] = True
                break

        except Exception as e:
            logger.debug(f"Error fetching {symbol} from screener: {e}")
            result["error"] = str(e)

    if not result["reports"]:
        result["error"] = result["error"] or "No annual reports found on screener.in"

    return result


def extract_year_from_text(text: str) -> str | None:
    """Extract '2023-24' or '2024' style year from report link text/href."""
    for pattern in (r"20\d{2}-\d{2}", r"20\d{2}"):
        match = re.search(pattern, text)
        if match:
            return match.group(0)
    return None


def download_pdf(url: str, output_path: Path, session: requests.Session) -> bool:
    try:
        response = session.get(url, timeout=120, stream=True)
        if response.status_code != 200:
            logger.debug(f"Download failed with status {response.status_code}")
            return False

        output_path.parent.mkdir(parents=True, exist_ok=True)
        with open(output_path, "wb") as f:
            for chunk in response.iter_content(chunk_size=8192):
                f.write(chunk)

        if output_path.stat().st_size < 10000:
            logger.debug(f"Downloaded file too small: {output_path.stat().st_size} bytes")
            output_path.unlink()
            return False
        return True

    except Exception as e:
        logger.debug(f"Download error: {e}")
        return False


def fetch_and_download_reports(
    symbol: str,
    session: requests.Session,
    year: str | None = None,
    max_reports: int = 1,
) -> dict:
    result = fetch_annual_reports_from_screener(symbol, session, max_reports=5)
    if not result["success"]:
        return result

    symbol_dir = REPORTS_DIR / symbol
    downloaded_count = 0

    for report in result["reports"]:
        report_year = report.get("year", "unknown")
        report_url = report.get("url", "")
        if not report_url:
            continue
        if year and year not in str(report_year):
            continue

        year_str = str(report_year).replace("-", "_").replace("/", "_")
        filename = f"{symbol}_AR_{year_str}.pdf"
        output_path = symbol_dir / filename

        if output_path.exists():
            logger.info(f"  Already exists: {filename}")
            result["downloaded"].append(str(output_path))
            downloaded_count += 1
        else:
            logger.info(f"  Downloading: {filename}")
            time.sleep(RATE_LIMIT_DELAY)
            if download_pdf(report_url, output_path, session):
                result["downloaded"].append(str(output_path))
                downloaded_count += 1
                logger.info(f"  Saved: {output_path}")
            else:
                logger.warning(f"  Failed to download: {filename}")

        if downloaded_count >= max_reports and not year:
            break

    result["success"] = len(result["downloaded"]) > 0
    return result


def process_single_stock(symbol: str, year: str | None = None) -> None:
    print(f"\n{'='*60}")
    print(f"Fetching Annual Report for: {symbol}")
    print("=" * 60)

    session = get_session()
    result = fetch_and_download_reports(symbol, session, year=year, max_reports=5)

    print(f"\nResult: {'Success' if result['success'] else 'Failed'}")
    if result.get("error"):
        print(f"Error: {result['error']}")
    if result.get("reports"):
        print("\nAvailable reports on screener.in:")
        for r in result["reports"][:5]:
            print(f"  - {r['year']}: {r['text']}")
    if result.get("downloaded"):
        print("\nDownloaded:")
        for path in result["downloaded"]:
            print(f"  - {path}")


def process_multiple_stocks(
    symbols: list[str],
    limit: int | None = None,
    max_reports_per_stock: int = 1,
) -> dict:
    session = get_session()
    if limit:
        symbols = symbols[:limit]

    results = {"success": [], "failed": []}
    for symbol in tqdm(symbols, desc="Fetching reports"):
        result = fetch_and_download_reports(symbol, session, max_reports=max_reports_per_stock)
        if result["success"]:
            results["success"].append({"symbol": symbol, "downloaded": result["downloaded"]})
        else:
            results["failed"].append({"symbol": symbol, "error": result.get("error")})
        time.sleep(RATE_LIMIT_DELAY)

    return results


def main():
    parser = argparse.ArgumentParser(description="Download annual report PDFs from screener.in")
    parser.add_argument("--symbol", type=str, help="Single stock symbol")
    parser.add_argument("--symbols", type=str, nargs="+", help="Multiple symbols")
    parser.add_argument("--all", action="store_true", help="All NSE500 stocks")
    parser.add_argument("--limit", type=int, help="Limit number of stocks")
    parser.add_argument("--year", type=str, help="Specific year (e.g., 2024)")
    parser.add_argument(
        "--reports", type=int, default=1,
        help="Max reports per stock (default: 1, i.e., latest 1 years)"
    )
    parser.add_argument("-v", "--verbose", action="store_true")

    args = parser.parse_args()
    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    if args.symbol:
        process_single_stock(args.symbol, year=args.year)

    elif args.symbols:
        results = process_multiple_stocks(args.symbols, args.limit, args.reports)
        print(f"\nSuccess: {len(results['success'])}, Failed: {len(results['failed'])}")

    elif args.all:
        symbols = get_all_nse500_symbols()
        if not symbols:
            print("No NSE500 symbols found. Run python scripts/data_refresh.py --market nse --mode full first.")
            return
        print(f"Processing {len(symbols)} NSE500 stocks...")
        results = process_multiple_stocks(symbols, args.limit, args.reports)
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
