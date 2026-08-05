"""Annual report / 10-K downloaders — NSE (screener.in PDFs) and S&P500 (SEC
EDGAR). Market-specific fetch logic (`fetch_nse_reports`/`fetch_snp_reports`)
plus a shared single/batch download engine used by scripts/fetch_annual_reports.py.
"""

from __future__ import annotations

import json
import logging
import re
import time
from pathlib import Path

import requests
from bs4 import BeautifulSoup, Tag
from tqdm import tqdm

from screener.config import (
    INDICES_DIR,
    RAW_DIR,
    SCREENER_BASE_URL,
    SCREENER_USER_AGENT,
    SNP_ANNUAL_REPORTS_DIR,
    SNP_INDICES_DIR,
)
from screener.fetch import download_edgar_document, fetch_edgar_submissions, get_10k_filings
from screener.runner import SCREENER_LIMITER

logger = logging.getLogger(__name__)

NSE_REPORTS_DIR = RAW_DIR / "nse" / "annual_reports"
SNP_REPORTS_DIR = SNP_ANNUAL_REPORTS_DIR


# ── symbol listing ──────────────────────────────────────────────

def nse500_symbols() -> list[str]:
    summary_file = INDICES_DIR / "screening_summary.json"
    if not summary_file.exists():
        return []
    return [c["symbol"] for c in json.loads(summary_file.read_text()).get("companies", [])]


def snp500_symbol_ciks() -> dict[str, int]:
    """Return {symbol: cik} for every S&P500 company in the curated summary."""
    summary_file = SNP_INDICES_DIR / "screening_summary.json"
    if not summary_file.exists():
        return {}
    companies = json.loads(summary_file.read_text()).get("companies", [])
    return {c["symbol"]: c["cik"] for c in companies if c.get("cik")}


# ── NSE: screener.in PDFs ───────────────────────────────────────

def screener_session() -> requests.Session:
    session = requests.Session()
    session.headers.update({
        "User-Agent": SCREENER_USER_AGENT,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
    })
    return session


def _extract_year(text: str) -> str | None:
    """Extract '2023-24' or '2024' style year from report link text/href."""
    for pattern in (r"20\d{2}-\d{2}", r"20\d{2}"):
        match = re.search(pattern, text)
        if match:
            return match.group(0)
    return None


def parse_annual_report_links(soup: BeautifulSoup) -> list[dict]:
    """Annual report links (year, url, label) from a screener.in company page's
    documents section. Shared by the standalone fetch below and by the
    combined enrichment pass (screener.enrich), which fetches the same page
    for shareholding/ratings and reuses that soup instead of a second request."""
    ar_section = soup.find("div", {"class": "annual-reports"})
    if not isinstance(ar_section, Tag):
        return []
    reports = []
    for link in ar_section.find_all("a", href=True):
        if not isinstance(link, Tag):
            continue
        href = str(link.get("href", ""))
        text = link.get_text(strip=True)
        if ".pdf" in href.lower():
            year = _extract_year(text) or _extract_year(href)
            reports.append({"year": year or "unknown", "url": href, "label": text})
    return reports


def _list_screener_reports(symbol: str, session: requests.Session) -> tuple[list[dict], str | None]:
    """Fetch a screener.in company page and scrape its annual report links."""
    for view in ("consolidated", ""):
        url = f"{SCREENER_BASE_URL}/{symbol}/{view}#documents"
        SCREENER_LIMITER.acquire()
        try:
            response = session.get(url, timeout=30)
            if response.status_code == 429:
                SCREENER_LIMITER.penalize()
            else:
                SCREENER_LIMITER.reward()
            if response.status_code != 200:
                continue
            reports = parse_annual_report_links(BeautifulSoup(response.text, "html.parser"))
            if reports:
                return reports, None
        except Exception as e:
            logger.debug(f"Error fetching {symbol} from screener: {e}")
            return [], str(e)
    return [], None


def download_reports(symbol: str, items: list[dict], symbol_dir: Path, session: requests.Session,
                      *, max_reports: int = 1) -> list[str]:
    """Download already-discovered report links (from parse_annual_report_links)
    for one symbol, skipping files already on disk. Used by the combined
    NSE enrichment pass, which discovers links without a second HTTP round-trip."""
    downloaded = []
    for report in items[:5]:
        url = report.get("url")
        if not url:
            continue
        year_str = str(report["year"]).replace("-", "_").replace("/", "_")
        output_path = symbol_dir / f"{symbol}_AR_{year_str}.pdf"
        if output_path.exists() or _download_pdf(url, output_path, session):
            downloaded.append(str(output_path))
        if len(downloaded) >= max_reports:
            break
    return downloaded


def is_report_stale(symbol_dir: Path, glob_pattern: str, max_age_days: int = 400) -> bool:
    """A symbol's report folder is stale if it has no matching file, or the
    newest one is older than `max_age_days` -- annual reports/10-Ks are only
    published ~once a year, so file mtime (not a fiscal-year label match) is
    a simple, robust enough staleness signal."""
    files = list(symbol_dir.glob(glob_pattern))
    if not files:
        return True
    newest = max(f.stat().st_mtime for f in files)
    return (time.time() - newest) > max_age_days * 86400


def _download_pdf(url: str, output_path: Path, session: requests.Session) -> bool:
    SCREENER_LIMITER.acquire()
    try:
        response = session.get(url, timeout=120, stream=True)
        if response.status_code == 429:
            SCREENER_LIMITER.penalize()
        else:
            SCREENER_LIMITER.reward()
        if response.status_code != 200:
            return False
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with open(output_path, "wb") as f:
            f.writelines(response.iter_content(chunk_size=8192))
        if output_path.stat().st_size < 10000:
            output_path.unlink()
            return False
        return True
    except Exception as e:
        logger.debug(f"Download error: {e}")
        return False


def fetch_nse_reports(
    symbol: str, session: requests.Session, *, year: str | None = None, max_reports: int = 1,
) -> dict:
    """Scrape + download NSE annual report PDFs from screener.in for one symbol."""
    items, error = _list_screener_reports(symbol, session)
    if not items:
        return {"symbol": symbol, "success": False, "items": [], "downloaded": [],
                "error": error or "No annual reports found on screener.in"}

    symbol_dir = NSE_REPORTS_DIR / symbol
    downloaded = []
    for report in items[:5]:
        report_year, url = report["year"], report["url"]
        if not url or (year and year not in str(report_year)):
            continue

        year_str = str(report_year).replace("-", "_").replace("/", "_")
        output_path = symbol_dir / f"{symbol}_AR_{year_str}.pdf"
        if output_path.exists() or _download_pdf(url, output_path, session):
            downloaded.append(str(output_path))

        if len(downloaded) >= max_reports and not year:
            break

    return {"symbol": symbol, "success": bool(downloaded), "items": items[:5], "downloaded": downloaded,
            "error": None if downloaded else "Download failed"}


# ── S&P500: SEC EDGAR 10-Ks ─────────────────────────────────────

def fetch_snp_reports(symbol: str, cik: int, *, max_reports: int = 1) -> dict:
    """Fetch + download 10-K filing documents from SEC EDGAR for one symbol."""
    submissions = fetch_edgar_submissions(cik)
    if not submissions:
        return {"symbol": symbol, "success": False, "items": [], "downloaded": [],
                "error": "Could not fetch EDGAR submissions"}

    filings = get_10k_filings(submissions, max_reports=max_reports)
    if not filings:
        return {"symbol": symbol, "success": False, "items": [], "downloaded": [],
                "error": "No 10-K filings found"}

    items = [{"year": f["year"], "label": f"filed {f['filing_date']}"} for f in filings]
    symbol_dir = SNP_REPORTS_DIR / symbol
    downloaded = []
    for filing in filings:
        ext = Path(filing["url"]).suffix or ".htm"
        output_path = symbol_dir / f"{symbol}_10K_{filing['year']}{ext}"
        if output_path.exists() or download_edgar_document(filing["url"], output_path):
            downloaded.append(str(output_path))

    return {"symbol": symbol, "success": bool(downloaded), "items": items, "downloaded": downloaded,
            "error": None if downloaded else "Download failed"}


# ── shared single/batch download engine ─────────────────────────

def process_single(symbol: str, fetch_fn, label: str) -> None:
    print(f"\n{'=' * 60}")
    print(f"Fetching {label} for: {symbol}")
    print("=" * 60)

    result = fetch_fn(symbol)
    print(f"\nResult: {'Success' if result['success'] else 'Failed'}")
    if result.get("error"):
        print(f"Error: {result['error']}")
    if result.get("items"):
        print(f"\nAvailable {label}:")
        for item in result["items"]:
            print(f"  - {item['year']}: {item.get('label', '')}")
    if result.get("downloaded"):
        print("\nDownloaded:")
        for path in result["downloaded"]:
            print(f"  - {path}")


def process_batch(symbols: list[str], fetch_fn, *, limit: int | None = None, desc: str) -> dict:
    if limit:
        symbols = symbols[:limit]

    results = {"success": [], "failed": []}
    for symbol in tqdm(symbols, desc=desc):
        result = fetch_fn(symbol)
        if result["success"]:
            results["success"].append({"symbol": symbol, "downloaded": result["downloaded"]})
        else:
            results["failed"].append({"symbol": symbol, "error": result.get("error")})
    return results


def save_summary(reports_dir: Path, results: dict) -> Path:
    summary_path = reports_dir / "_download_summary.json"
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    summary_path.write_text(json.dumps(results, indent=2))
    return summary_path
