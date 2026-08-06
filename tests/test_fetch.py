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
        "price_history",
    ))


def test_fetch_ticker_data_requests_one_year_of_unadjusted_history(monkeypatch):
    calls = []
    prices = pd.DataFrame(
        {"Adj Close": [100.0, 101.0]},
        index=pd.to_datetime(["2026-01-02", "2026-01-05"]),
    )

    class Ticker:
        def __init__(self, symbol, session):
            self.info = {"symbol": symbol}
            self.income_stmt = self.balance_sheet = self.cashflow = pd.DataFrame()

        def history(self, **kwargs):
            calls.append(kwargs)
            return prices

    monkeypatch.setattr(fetch.yf, "Ticker", Ticker)
    monkeypatch.setattr(fetch, "_yf_session", object)

    result = fetch.fetch_ticker_data("AAA", annual_statements=False)

    assert result["price_history"] is prices
    assert calls == [{"period": "1y", "auto_adjust": False, "actions": False}]


def test_cache_price_history_keeps_only_contract_columns_and_replaces_atomically(tmp_path):
    path = tmp_path / "prices" / "AAA.csv"
    path.parent.mkdir()
    path.write_text("old cache")
    prices = pd.DataFrame(
        {"Open": [99.0, 100.0], "Adj Close": [100.0, 101.5], "Volume": [1, 2]},
        index=pd.to_datetime(["2026-01-02", "2026-01-05"]),
    )

    assert fetch.cache_price_history(prices, path) is True

    cached = pd.read_csv(path)
    assert list(cached.columns) == ["date", "adjusted_close"]
    assert cached.to_dict("records") == [
        {"date": "2026-01-02", "adjusted_close": 100.0},
        {"date": "2026-01-05", "adjusted_close": 101.5},
    ]
    assert list(path.parent.iterdir()) == [path]


def test_empty_price_history_does_not_overwrite_valid_cache(tmp_path):
    path = tmp_path / "AAA.csv"
    path.write_text("date,adjusted_close\n2026-01-02,100\n")

    assert fetch.cache_price_history(pd.DataFrame(), path) is False
    assert path.read_text() == "date,adjusted_close\n2026-01-02,100\n"
