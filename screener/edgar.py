"""SEC EDGAR access: identifiers, XBRL facts, submissions, and 10-K downloads."""

import json
import os
from datetime import date
from pathlib import Path

import requests

from .config import (
    EDGAR_CACHE_DIR,
    EDGAR_CONTACT_FILE,
    EDGAR_FACTS,
    EDGAR_RATE_LIMIT,
    EDGAR_SUBMISSIONS,
    EDGAR_TICKERS,
    YFINANCE_USER_AGENT,
)
from .runner import AdaptiveRateLimiter

_LIMITER = AdaptiveRateLimiter(base_interval=EDGAR_RATE_LIMIT)


def _user_agent() -> str:
    contact = os.environ.get("SEC_EDGAR_CONTACT")
    if contact:
        return f"sp500-screener-bot ({contact})"
    if EDGAR_CONTACT_FILE.exists():
        return f"sp500-screener-bot ({EDGAR_CONTACT_FILE.read_text().strip()})"
    return YFINANCE_USER_AGENT


def _get(url: str, **kwargs) -> requests.Response:
    """GET shared by every EDGAR endpoint so all calls honor one limiter."""
    _LIMITER.acquire()
    response = requests.get(url, headers={"User-Agent": _user_agent()}, **kwargs)
    (_LIMITER.penalize if response.status_code == 429 else _LIMITER.reward)()
    return response


def build_cik_map() -> dict[str, int]:
    """Fetch the current ticker-to-CIK map."""
    response = requests.get(EDGAR_TICKERS, headers={"User-Agent": _user_agent()}, timeout=30)
    response.raise_for_status()
    return {
        entry["ticker"].upper(): entry["cik_str"]
        for entry in response.json().values()
    }


def fetch_facts(symbol: str, cik: int | None) -> dict | None:
    """Fetch SEC XBRL company facts, using a 90-day local cache."""
    cache_path = EDGAR_CACHE_DIR / f"{symbol}.json"
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    if cache_path.exists():
        age = (date.today() - date.fromtimestamp(cache_path.stat().st_mtime)).days
        if age < 90:
            return json.loads(cache_path.read_text())

    try:
        response = _get(EDGAR_FACTS.format(cik=cik), timeout=30)
        response.raise_for_status()
        facts = response.json()
        cache_path.write_text(json.dumps(facts))
        return facts
    except Exception as exc:
        print(f"  EDGAR fetch failed for {symbol}: {exc}")
        return None


def fetch_submissions(cik: int) -> dict | None:
    """Fetch a company's SEC filing history."""
    try:
        response = _get(EDGAR_SUBMISSIONS.format(cik=cik), timeout=30)
        response.raise_for_status()
        return response.json()
    except Exception as exc:
        print(f"  EDGAR submissions fetch failed for CIK {cik}: {exc}")
        return None


def get_10k_filings(submissions: dict, max_reports: int = 5) -> list[dict]:
    """Extract the latest 10-K filing metadata from an SEC submissions payload."""
    cik = int(submissions.get("cik", 0))
    recent = submissions.get("filings", {}).get("recent", {})
    filings = []
    for form, accession, filing_date, report_date, document in zip(
        recent.get("form", []),
        recent.get("accessionNumber", []),
        recent.get("filingDate", []),
        recent.get("reportDate", []),
        recent.get("primaryDocument", []),
    ):
        if form != "10-K":
            continue
        accession_path = accession.replace("-", "")
        filings.append({
            "year": (report_date or filing_date)[:4],
            "accession": accession,
            "filing_date": filing_date,
            "url": (
                f"https://www.sec.gov/Archives/edgar/data/"
                f"{cik}/{accession_path}/{document}"
            ),
        })
    return sorted(filings, key=lambda filing: filing["filing_date"], reverse=True)[:max_reports]


def download_document(url: str, output_path: Path) -> bool:
    """Download one EDGAR filing document."""
    try:
        response = _get(url, timeout=60, stream=True)
        if response.status_code != 200:
            return False
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with output_path.open("wb") as file:
            file.writelines(response.iter_content(chunk_size=8192))
        if output_path.stat().st_size < 5000:
            output_path.unlink()
            return False
        return True
    except Exception as exc:
        print(f"  Download failed for {url}: {exc}")
        return False
