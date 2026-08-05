"""Annual-report downloads: Screener.in PDFs and SEC EDGAR 10-Ks."""

from __future__ import annotations

import logging
import re
import time
from pathlib import Path

import requests
from bs4 import BeautifulSoup, Tag

from screener.config import (
    RAW_DIR,
    SCREENER_USER_AGENT,
    SNP_ANNUAL_REPORTS_DIR,
)
from screener.edgar import download_document, fetch_submissions, get_10k_filings
from screener.runner import SCREENER_LIMITER

logger = logging.getLogger(__name__)

NSE_REPORTS_DIR = RAW_DIR / "nse" / "annual_reports"
SNP_REPORTS_DIR = SNP_ANNUAL_REPORTS_DIR


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
    documents section. The enrichment pass reuses the page it already fetched."""
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
    """Return whether a report folder is empty or older than `max_age_days`."""
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


# ── S&P500: SEC EDGAR 10-Ks ─────────────────────────────────────

def fetch_snp_reports(symbol: str, cik: int, *, max_reports: int = 1) -> dict:
    """Fetch + download 10-K filing documents from SEC EDGAR for one symbol."""
    submissions = fetch_submissions(cik)
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
        if output_path.exists() or download_document(filing["url"], output_path):
            downloaded.append(str(output_path))

    return {"symbol": symbol, "success": bool(downloaded), "items": items, "downloaded": downloaded,
            "error": None if downloaded else "Download failed"}
