"""Data fetching: NSE/US tickers, yfinance fundamentals, SEC EDGAR."""

import io
import json
import math
import threading
import time
from datetime import date
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
    EDGAR_TICKERS,
    FETCH_TIMEOUT,
    NSE500_URL,
    WIKIPEDIA_URL,
    YFINANCE_USER_AGENT,
)


# ── Helpers ──────────────────────────────────────────────────────────

def safe_float(v: Any) -> float | None:
    """Convert to float; return None for invalid, NaN, or Inf."""
    if v is None or (isinstance(v, float) and (math.isnan(v) or math.isinf(v))):
        return None
    try:
        f = float(v)
        return f if not (math.isnan(f) or math.isinf(f)) else None
    except (ValueError, TypeError):
        return None


def _df_or_empty(df: pd.DataFrame | None) -> pd.DataFrame:
    """`df or pd.DataFrame()` raises on a real DataFrame (ambiguous truth value) —
    only None should fall back to empty."""
    return df if df is not None else pd.DataFrame()


def _edgar_ua() -> str:
    """Build User-Agent for SEC EDGAR, reading contact email if available."""
    if EDGAR_CONTACT_FILE.exists():
        return f"sp500-screener-bot ({EDGAR_CONTACT_FILE.read_text().strip()})"
    return YFINANCE_USER_AGENT


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
        time.sleep(EDGAR_RATE_LIMIT)  # ~8 req/sec
        url = EDGAR_FACTS.format(cik=cik)
        resp = requests.get(url, headers={"User-Agent": _edgar_ua()}, timeout=30)
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
        "quarterly_income": pd.DataFrame(),
        "quarterly_balance": pd.DataFrame(),
        "quarterly_cashflow": pd.DataFrame(),
        "annual_income": pd.DataFrame(),
        "annual_balance": pd.DataFrame(),
        "annual_cashflow": pd.DataFrame(),
        "institutional_holders": pd.DataFrame(),
        "fetch_time": date.today().isoformat(),
        "error": error,
    }


def fetch_ticker_data(symbol: str, *, institutional_holders: bool = False) -> dict[str, Any]:
    """Fetch all fundamentals for a single symbol via yfinance.

    Never raises — failures come back as a populated "error" key so callers can
    classify them (see `runner.is_rate_limit_error`) and decide about retrying.

    institutional_holders: also fetch ticker.institutional_holders (S&P-only;
    see MarketConfig.fetch_institutional_holders) -- skipped by default since
    NSE has no use for it and it's a distinct network call.
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
                "quarterly_income": _df_or_empty(ticker.quarterly_income_stmt),
                "quarterly_balance": _df_or_empty(ticker.quarterly_balance_sheet),
                "quarterly_cashflow": _df_or_empty(ticker.quarterly_cashflow),
                "annual_income": _df_or_empty(ticker.income_stmt),
                "annual_balance": _df_or_empty(ticker.balance_sheet),
                "annual_cashflow": _df_or_empty(ticker.cashflow),
                "institutional_holders": (
                    _df_or_empty(ticker.institutional_holders) if institutional_holders else pd.DataFrame()
                ),
                "fetch_time": date.today().isoformat(),
                "error": None,
            }
    except Exception as e:
        return _empty_result(symbol, str(e))


def fetch_ownership_snapshot(symbol: str) -> dict[str, Any]:
    """Lightweight fetch for institutional_ownership alone: ticker.info (for
    heldPercentInsiders/heldPercentInstitutions) and ticker.institutional_holders.

    Skips the six statement calls fetch_ticker_data makes -- for callers that
    already have fresh snapshot/statement data on disk and only need to
    (re)compute institutional_ownership (see transform.build_institutional_ownership).
    """
    try:
        with _YFINANCE_LOCK:
            ticker = yf.Ticker(symbol, session=_yf_session())
            return {
                "symbol": symbol,
                "info": ticker.info or {},
                "institutional_holders": _df_or_empty(ticker.institutional_holders),
                "error": None,
            }
    except Exception as e:
        return {"symbol": symbol, "info": {}, "institutional_holders": pd.DataFrame(), "error": str(e)}
