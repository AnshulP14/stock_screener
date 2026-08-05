"""Data fetching: NSE/US tickers, yfinance fundamentals, SEC EDGAR."""

import io
import json
import os
import threading
from datetime import date
from pathlib import Path
from typing import Any

import pandas as pd
import requests
import yfinance as yf
from bs4 import BeautifulSoup
from curl_cffi import requests as curl_requests

from .config import (
    EDGAR_CACHE_DIR,
    EDGAR_CONTACT_FILE,
    EDGAR_FACTS,
    EDGAR_RATE_LIMIT,
    EDGAR_SUBMISSIONS,
    EDGAR_TICKERS,
    FETCH_TIMEOUT,
    NSE500_URL,
    WIKIPEDIA_URL,
    YFINANCE_USER_AGENT,
)
from .runner import AdaptiveRateLimiter

_EDGAR_LIMITER = AdaptiveRateLimiter(base_interval=EDGAR_RATE_LIMIT)


def _df_or_empty(df: pd.DataFrame | None) -> pd.DataFrame:
    """`df or pd.DataFrame()` raises on a real DataFrame (ambiguous truth value) —
    only None should fall back to empty."""
    return df if df is not None else pd.DataFrame()


def _edgar_ua() -> str:
    """Build User-Agent for SEC EDGAR, reading contact email if available.

    $SEC_EDGAR_CONTACT takes priority (the more conventional, discoverable
    mechanism -- e.g. for CI) over the ~/.screener_edgar_email dotfile
    (EDGAR_CONTACT_FILE), which exists mainly so a long-lived dev machine only
    has to set it once. Neither being set isn't an error -- SEC just gets a
    generic browser UA instead of a proper contact, which is worse but not fatal.
    """
    contact = os.environ.get("SEC_EDGAR_CONTACT")
    if contact:
        return f"sp500-screener-bot ({contact})"
    if EDGAR_CONTACT_FILE.exists():
        return f"sp500-screener-bot ({EDGAR_CONTACT_FILE.read_text().strip()})"
    return YFINANCE_USER_AGENT


def _edgar_get(url: str, **kwargs) -> requests.Response:
    """GET against SEC EDGAR, throttled and adaptive to 429s -- shared by every
    EDGAR call site (facts, submissions, document download) so a throttling
    response on any of them backs off the others too."""
    _EDGAR_LIMITER.acquire()
    resp = requests.get(url, headers={"User-Agent": _edgar_ua()}, **kwargs)
    if resp.status_code == 429:
        _EDGAR_LIMITER.penalize()
    else:
        _EDGAR_LIMITER.reward()
    return resp


# ── NSE500 ───────────────────────────────────────────────────────────

def fetch_nse500_tickers() -> tuple[list[str], dict[str, dict]]:
    """
    Fetch NSE500 constituent tickers from NSE's official CSV.

    Returns (tickers, metadata) where tickers is ["RELIANCE.NS", ...]
    and metadata maps symbol→{company_name, industry, isin}.
    """
    headers = {"User-Agent": YFINANCE_USER_AGENT}
    resp = requests.get(NSE500_URL, headers=headers, timeout=30)
    resp.raise_for_status()

    df = pd.read_csv(io.StringIO(resp.text))
    metadata = {}
    for _, row in df.iterrows():
        sym = row["Symbol"]
        metadata[f"{sym}.NS"] = {
            "nse_company_name": row.get("Company Name"),
            "nse_industry": row.get("Industry"),
            "isin_code": row.get("ISIN Code"),
        }

    tickers = [f"{row['Symbol']}.NS" for _, row in df.iterrows()]
    return tickers, metadata


# ── S&P 500 ────────────────────────────────────────────────────────

def fetch_sp500_universe() -> list[dict]:
    """Scrape Wikipedia S&P 500 constituent table."""
    resp = requests.get(WIKIPEDIA_URL, headers={"User-Agent": YFINANCE_USER_AGENT}, timeout=20)
    resp.raise_for_status()

    soup = BeautifulSoup(resp.text, "html.parser")
    table = soup.find("table", {"id": "constituents"})
    if not table:
        raise RuntimeError("Could not find constituents table on Wikipedia")

    companies = []
    for row in table.find_all("tr")[1:]:
        cells = row.find_all("td")
        if len(cells) < 4:
            continue
        ticker = cells[0].text.strip().replace(".", "-")
        companies.append({
            "symbol": ticker,
            "company_name": cells[1].text.strip(),
            "gics_sector": cells[2].text.strip(),
            "gics_industry": cells[3].text.strip(),
        })
    return companies


def build_cik_map() -> dict[str, int]:
    """Fetch fresh CIK→ticker map from SEC."""
    resp = requests.get(EDGAR_TICKERS, headers={"User-Agent": _edgar_ua()}, timeout=30)
    resp.raise_for_status()
    data = resp.json()
    return {entry["ticker"].upper(): entry["cik_str"] for entry in data.values()}


def fetch_edgar_facts(symbol: str, cik: int | None) -> dict | None:
    """Fetch EDGAR XBRL facts (with 90-day local cache). Returns None on failure."""
    cache_path = EDGAR_CACHE_DIR / f"{symbol}.json"
    cache_path.parent.mkdir(parents=True, exist_ok=True)

    # Check cache freshness
    if cache_path.exists():
        age = (date.today() - date.fromtimestamp(cache_path.stat().st_mtime)).days
        if age < 90:
            with open(cache_path) as f:
                return json.load(f)

    try:
        url = EDGAR_FACTS.format(cik=cik)
        resp = _edgar_get(url, timeout=30)
        resp.raise_for_status()
        facts = resp.json()
        with open(cache_path, "w") as f:
            json.dump(facts, f)
        return facts
    except Exception as e:
        print(f"  EDGAR fetch failed for {symbol}: {e}")
        return None


# ── Per-symbol fetcher ──────────────────────────────────────────────

_thread_local = threading.local()

# Serializes every yfinance call process-wide -- both NSE and SNP pipelines
# hit the same Yahoo Finance host, so this makes it one sequential stream
# even when the two markets' pipelines run concurrently in separate threads.
_YFINANCE_LOCK = threading.Lock()


def _yf_session():
    """Per-thread yfinance session carrying a hard timeout.

    yfinance sets no timeout of its own, so one unresponsive endpoint can stall
    a worker indefinitely. `impersonate` must match yfinance's own default
    (`_http.py`: `Session(impersonate="chrome")`) — a plain curl_cffi session
    gets a blanket 429 from Yahoo. Sessions are thread-local because the
    underlying curl handles are not safe to share across threads.
    """
    session = getattr(_thread_local, "session", None)
    if session is None:
        session = curl_requests.Session(impersonate="chrome", timeout=FETCH_TIMEOUT)
        _thread_local.session = session
    return session


def _empty_result(symbol: str, error: str | None) -> dict[str, Any]:
    return {
        "symbol": symbol,
        "info": {},
        "annual_income": pd.DataFrame(),
        "annual_balance": pd.DataFrame(),
        "annual_cashflow": pd.DataFrame(),
        "institutional_holders": pd.DataFrame(),
        "fetch_time": date.today().isoformat(),
        "error": error,
    }


def fetch_ticker_data(
    symbol: str, *, institutional_holders: bool = False, annual_statements: bool = True
) -> dict[str, Any]:
    """Fetch all fundamentals for a single symbol via yfinance.

    Never raises — failures come back as a populated "error" key so callers can
    classify them (see `runner.is_rate_limit_error`) and decide about retrying.

    institutional_holders: also fetch ticker.institutional_holders (S&P-only;
    see MarketConfig.fetch_institutional_holders) -- skipped by default since
    NSE has no use for it and it's a distinct network call.

    annual_statements: fetch ticker.income_stmt/balance_sheet/cashflow. Markets
    whose historical_trends come from SEC EDGAR instead (MarketConfig.uses_edgar
    -- S&P since Phase 5) have no use for these; skipping them there saves 3
    yfinance calls per company. Quarterly statements were fetched here too until
    Phase 7's doc reconciliation found nothing, for either market, ever read
    them -- removed rather than kept as an unused, undocumented-as-dead fetch.
    """
    try:
        with _YFINANCE_LOCK:
            ticker = yf.Ticker(symbol, session=_yf_session())
            info = ticker.info or {}
            # An unknown/delisted symbol returns a stub with no pricing fields.
            if not info.get("symbol") and not info.get("regularMarketPrice"):
                return _empty_result(symbol, "no data returned (delisted or unknown symbol)")
            return {
                "symbol": symbol,
                "info": info,
                "annual_income": _df_or_empty(ticker.income_stmt) if annual_statements else pd.DataFrame(),
                "annual_balance": _df_or_empty(ticker.balance_sheet) if annual_statements else pd.DataFrame(),
                "annual_cashflow": _df_or_empty(ticker.cashflow) if annual_statements else pd.DataFrame(),
                "institutional_holders": (
                    _df_or_empty(ticker.institutional_holders) if institutional_holders else pd.DataFrame()
                ),
                "fetch_time": date.today().isoformat(),
                "error": None,
            }
    except Exception as e:
        return _empty_result(symbol, str(e))


def fetch_edgar_submissions(cik: int) -> dict | None:
    """Fetch a company's SEC filing history (submissions API): every filing's
    form type, accession number, filing/report date, and primary document
    filename. No local cache -- unlike fetch_edgar_facts, this is only hit
    once per annual-report download, not on every pipeline run.
    """
    try:
        url = EDGAR_SUBMISSIONS.format(cik=cik)
        resp = _edgar_get(url, timeout=30)
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        print(f"  EDGAR submissions fetch failed for CIK {cik}: {e}")
        return None


def get_10k_filings(submissions: dict, max_reports: int = 5) -> list[dict]:
    """Extract the most recent 10-K filings (year, accession, document URL)
    from a submissions payload's 'recent' filings list.

    Only looks at 'recent' (SEC's last ~1000 filings per company), not the
    paginated older-filings files -- S&P500 companies file annually, so 10-Ks
    going back well past `max_reports` years are always inside that window.
    """
    cik = int(submissions.get("cik", 0))
    recent = submissions.get("filings", {}).get("recent", {})
    forms = recent.get("form", [])
    accessions = recent.get("accessionNumber", [])
    filing_dates = recent.get("filingDate", [])
    report_dates = recent.get("reportDate", [])
    primary_docs = recent.get("primaryDocument", [])

    filings = []
    for form, accession, filing_date, report_date, doc in zip(
        forms, accessions, filing_dates, report_dates, primary_docs
    ):
        if form != "10-K":
            continue
        accession_nodash = accession.replace("-", "")
        url = f"https://www.sec.gov/Archives/edgar/data/{cik}/{accession_nodash}/{doc}"
        filings.append({
            "year": (report_date or filing_date)[:4],
            "accession": accession,
            "filing_date": filing_date,
            "url": url,
        })

    filings.sort(key=lambda f: f["filing_date"], reverse=True)
    return filings[:max_reports]


def download_edgar_document(url: str, output_path: Path) -> bool:
    """Download a single EDGAR filing document (10-K htm/pdf) to disk."""
    try:
        resp = _edgar_get(url, timeout=60, stream=True)
        if resp.status_code != 200:
            return False

        output_path.parent.mkdir(parents=True, exist_ok=True)
        with open(output_path, "wb") as f:
            f.writelines(resp.iter_content(chunk_size=8192))

        if output_path.stat().st_size < 5000:
            output_path.unlink()
            return False
        return True

    except Exception as e:
        print(f"  Download failed for {url}: {e}")
        return False
