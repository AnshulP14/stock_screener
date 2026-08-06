"""NSE/S&P universes and per-symbol Yahoo Finance fundamentals."""

import io
import os
import tempfile
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
    FETCH_TIMEOUT,
    NSE500_URL,
    WIKIPEDIA_URL,
    YFINANCE_USER_AGENT,
)


def _df_or_empty(df: pd.DataFrame | None) -> pd.DataFrame:
    """`df or pd.DataFrame()` raises on a real DataFrame (ambiguous truth value) —
    only None should fall back to empty."""
    return df if df is not None else pd.DataFrame()


# ── NSE500 ───────────────────────────────────────────────────────────

def fetch_nse500_tickers() -> tuple[list[str], dict[str, dict]]:
    """Return NSE500 tickers and symbol metadata from NSE's CSV."""
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


# ── Per-symbol fetcher ──────────────────────────────────────────────

_thread_local = threading.local()

# Serializes every yfinance call process-wide -- both NSE and SNP pipelines
# hit the same Yahoo Finance host, so this makes it one sequential stream
# even when the two markets' pipelines run concurrently in separate threads.
_YFINANCE_LOCK = threading.Lock()


def _yf_session():
    """Return a thread-local Yahoo session with a hard timeout."""
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
        "price_history": pd.DataFrame(),
        "fetch_time": date.today().isoformat(),
        "error": error,
    }


def fetch_ticker_data(
    symbol: str, *, institutional_holders: bool = False, annual_statements: bool = True
) -> dict[str, Any]:
    """Fetch one symbol; return failures in `error` instead of raising."""
    try:
        with _YFINANCE_LOCK:
            ticker = yf.Ticker(symbol, session=_yf_session())
            info = ticker.info or {}
            # An unknown/delisted symbol returns a stub with no pricing fields.
            if not info.get("symbol") and not info.get("regularMarketPrice"):
                return _empty_result(symbol, "no data returned (delisted or unknown symbol)")
            try:
                prices = _df_or_empty(ticker.history(
                    period="1y", auto_adjust=False, actions=False,
                ))
            except Exception:
                prices = pd.DataFrame()
            return {
                "symbol": symbol,
                "info": info,
                "annual_income": _df_or_empty(ticker.income_stmt) if annual_statements else pd.DataFrame(),
                "annual_balance": _df_or_empty(ticker.balance_sheet) if annual_statements else pd.DataFrame(),
                "annual_cashflow": _df_or_empty(ticker.cashflow) if annual_statements else pd.DataFrame(),
                "institutional_holders": (
                    _df_or_empty(ticker.institutional_holders) if institutional_holders else pd.DataFrame()
                ),
                "price_history": prices,
                "fetch_time": date.today().isoformat(),
                "error": None,
            }
    except Exception as e:
        return _empty_result(symbol, str(e))


def cache_price_history(prices: pd.DataFrame, path: Path) -> bool:
    """Atomically cache Yahoo's date and adjusted close columns."""
    if prices.empty or "Adj Close" not in prices:
        return False

    compact = prices[["Adj Close"]].reset_index()
    compact = compact.iloc[:, [0, 1]]
    compact.columns = ["date", "adjusted_close"]
    compact["date"] = pd.to_datetime(compact["date"], errors="coerce").dt.date
    compact["adjusted_close"] = pd.to_numeric(compact["adjusted_close"], errors="coerce")
    compact = compact.dropna().drop_duplicates("date", keep="last").sort_values("date")
    if compact.empty:
        return False

    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
    try:
        with os.fdopen(fd, "w") as tmp:
            compact.to_csv(tmp, index=False)
        os.replace(tmp_name, path)
    except Exception:
        try:
            os.unlink(tmp_name)
        except FileNotFoundError:
            pass
        raise
    return True
