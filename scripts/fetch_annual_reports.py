#!/usr/bin/env python3
"""
Annual Report Downloader

Downloads annual report PDFs from screener.in for NSE500 stocks.

Usage:
    python fetch_annual_reports.py --symbol RELIANCE     # Single stock
    python fetch_annual_reports.py --symbols TCS INFY   # Multiple stocks
    python fetch_annual_reports.py --all --limit 10     # All NSE500 (limited)
"""

import argparse
import json
import logging
import os
import time
from pathlib import Path

import requests
from bs4 import BeautifulSoup
from tqdm import tqdm

# =============================================================================
# Configuration
# =============================================================================

DATA_DIR = Path(__file__).parent.parent / "data"
REPORTS_DIR = DATA_DIR / "annual_reports"
COMPANIES_DIR = DATA_DIR / "companies"

SCREENER_BASE_URL = "https://www.screener.in/company"
USER_AGENT = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"

RATE_LIMIT_DELAY = 1.5

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


# =============================================================================
# NSE500 Symbol Loading
# =============================================================================


def get_all_nse500_symbols() -> list[str]:
    """Get all NSE500 symbols from screening summary."""
    summary_file = DATA_DIR / "indices" / "screening_summary.json"
    if summary_file.exists():
        with open(summary_file) as f:
            data = json.load(f)
            return [c["symbol"] for c in data.get("companies", [])]
    return []


# =============================================================================
# Screener.in Scraper
# =============================================================================


def get_session() -> requests.Session:
    """Create session with proper headers."""
    session = requests.Session()
    session.headers.update({
        "User-Agent": USER_AGENT,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
    })
    return session


def fetch_annual_reports_from_screener(
    symbol: str, 
    session: requests.Session,
    max_reports: int = 5,
) -> dict:
    """
    Fetch annual report links from screener.in for a symbol.
    
    Returns dict with:
        - symbol: stock symbol
        - success: bool
        - reports: list of {year, url} dicts
        - downloaded: list of downloaded file paths
        - error: error message if failed
    """
    result = {
        "symbol": symbol,
        "success": False,
        "reports": [],
        "downloaded": [],
        "error": None,
    }
    
    # Try consolidated first, then standalone
    for view in ["consolidated", ""]:
        url = f"{SCREENER_BASE_URL}/{symbol}/{view}#documents"
        
        try:
            response = session.get(url, timeout=30)
            
            if response.status_code == 404:
                continue
            
            if response.status_code != 200:
                continue
            
            soup = BeautifulSoup(response.text, "html.parser")
            
            # Find the annual reports section specifically
            annual_reports = []
            
            # Look for the annual-reports div specifically
            ar_section = soup.find("div", {"class": "annual-reports"})
            
            if ar_section:
                # Find all links in the annual reports section
                links = ar_section.find_all("a", href=True)
                for link in links:
                    href = link.get("href", "")
                    text = link.get_text(strip=True)
                    
                    if ".pdf" in href.lower():
                        # Extract year from text or href
                        year = extract_year_from_text(text) or extract_year_from_text(href)
                        annual_reports.append({
                            "year": year or "unknown",
                            "url": href,
                            "text": text,
                        })
            
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
    """Extract year from text like 'Financial Year 2024' or 'Annual Report 2023-24'."""
    import re
    
    # Try to find year patterns
    patterns = [
        r"20\d{2}-\d{2}",  # 2023-24
        r"20\d{2}",        # 2024
    ]
    
    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            return match.group(0)
    
    return None


def download_pdf(url: str, output_path: Path, session: requests.Session) -> bool:
    """Download a PDF file."""
    try:
        response = session.get(url, timeout=120, stream=True)
        
        if response.status_code != 200:
            logger.debug(f"Download failed with status {response.status_code}")
            return False
        
        output_path.parent.mkdir(parents=True, exist_ok=True)
        
        with open(output_path, "wb") as f:
            for chunk in response.iter_content(chunk_size=8192):
                f.write(chunk)
        
        # Verify file size (PDFs should be at least 10KB)
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
    """Fetch report links and download PDFs."""
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
        
        # Filter by year if specified
        if year and year not in str(report_year):
            continue
        
        # Determine filename
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
        
        # Limit downloads
        if downloaded_count >= max_reports and not year:
            break
    
    result["success"] = len(result["downloaded"]) > 0
    return result


# =============================================================================
# Main Functions
# =============================================================================


def process_single_stock(symbol: str, year: str | None = None) -> None:
    """Process a single stock with detailed output."""
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
    """Process multiple stocks from NSE500."""
    session = get_session()
    
    if limit:
        symbols = symbols[:limit]
    
    results = {"success": [], "failed": []}
    
    for symbol in tqdm(symbols, desc="Fetching reports"):
        result = fetch_and_download_reports(
            symbol, session, max_reports=max_reports_per_stock
        )
        
        if result["success"]:
            results["success"].append({
                "symbol": symbol,
                "downloaded": result["downloaded"],
            })
        else:
            results["failed"].append({
                "symbol": symbol,
                "error": result.get("error"),
            })
        
        time.sleep(RATE_LIMIT_DELAY)
    
    return results


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="Download annual report PDFs from screener.in"
    )
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
        results = process_multiple_stocks(
            args.symbols, args.limit, args.reports
        )
        print(f"\nSuccess: {len(results['success'])}, Failed: {len(results['failed'])}")
    
    elif args.all:
        symbols = get_all_nse500_symbols()
        if not symbols:
            print("No NSE500 symbols found. Run nse500_data_pipeline.py first.")
            return
        print(f"Processing {len(symbols)} NSE500 stocks...")
        results = process_multiple_stocks(symbols, args.limit, args.reports)
        print(f"\nSuccess: {len(results['success'])}, Failed: {len(results['failed'])}")
        
        # Save summary
        summary_path = REPORTS_DIR / "_download_summary.json"
        summary_path.parent.mkdir(parents=True, exist_ok=True)
        with open(summary_path, "w") as f:
            json.dump(results, f, indent=2)
        print(f"Summary saved to: {summary_path}")
    
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
