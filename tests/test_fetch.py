"""Tests for provider response parsing and Yahoo fetch outcomes."""

import pandas as pd

from screener import fetch


class _Response:
    def __init__(self, text):
        self.text = text

    def raise_for_status(self):
        pass


def test_nse_csv_produces_yahoo_symbols_and_metadata(monkeypatch):
    csv = "Company Name,Industry,Symbol,Series,ISIN Code\nReliance,Oil & Gas,RELIANCE,EQ,INE002A01018\n"
    monkeypatch.setattr(fetch.requests, "get", lambda *args, **kwargs: _Response(csv))

    symbols, metadata = fetch.fetch_nse500_tickers()

    assert symbols == ["RELIANCE.NS"]
    assert metadata["RELIANCE.NS"] == {
        "nse_company_name": "Reliance",
        "nse_industry": "Oil & Gas",
        "isin_code": "INE002A01018",
    }


def test_sp500_html_normalizes_dot_tickers(monkeypatch):
    html = """
      <table id="constituents"><tr><th>Symbol</th></tr>
        <tr><td>BRK.B</td><td>Berkshire Hathaway</td><td>Financials</td>
            <td>Multi-Sector Holdings</td></tr>
      </table>
    """
    monkeypatch.setattr(fetch.requests, "get", lambda *args, **kwargs: _Response(html))

    assert fetch.fetch_sp500_universe() == [{
        "symbol": "BRK-B",
        "company_name": "Berkshire Hathaway",
        "gics_sector": "Financials",
        "gics_industry": "Multi-Sector Holdings",
    }]


def test_fetch_ticker_data_returns_complete_empty_shape_on_unknown_symbol(monkeypatch):
    class Ticker:
        def __init__(self, symbol, session):
            self.info = {}

    monkeypatch.setattr(fetch.yf, "Ticker", Ticker)
    monkeypatch.setattr(fetch, "_yf_session", object)

    result = fetch.fetch_ticker_data("UNKNOWN")

    assert result["error"] == "no data returned (delisted or unknown symbol)"
    assert result["symbol"] == "UNKNOWN"
    assert all(isinstance(result[key], pd.DataFrame) for key in (
        "annual_income", "annual_balance", "annual_cashflow", "institutional_holders",
    ))
