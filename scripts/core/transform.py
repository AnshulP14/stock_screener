"""Transform raw fetch data into company JSONs with trends."""

import math
from datetime import datetime, date
from io import StringIO
from pathlib import Path
from typing import Any

import pandas as pd




# ── Helpers ─────────────────────────────────────────────────────────

def _safe_float(v: Any) -> float | None:
    if v is None or (isinstance(v, float) and (math.isnan(v) or math.isinf(v))):
        return None
    try:
        f = float(v)
        return f if not (math.isnan(f) or math.isinf(f)) else None
    except (ValueError, TypeError):
        return None


def _get_fiscal_year(ts: pd.Timestamp) -> int:
    """Indian FY: Apr 1 - Mar 31. FY ending Mar 2024 = FY2024."""
    if ts.month >= 4:
        return ts.year + 1
    return ts.year


def _serialize_df(df: pd.DataFrame) -> dict | None:
    if df is None or df.empty:
        return None
    try:
        return {col: df[col].to_dict() for col in df.columns}
    except Exception:
        return None


# ── Extractors ──────────────────────────────────────────────────────

def build_current_snapshot(data: dict[str, Any], nse_metadata: dict[str, dict] | None = None) -> dict:
    """Build snapshot dict from yfinance info."""
    info = data.get("info", {})
    symbol = data["symbol"].replace(".NS", "")

    metrics = {"symbol": symbol}
    if nse_metadata and f"{symbol}.NS" in nse_metadata:
        m = nse_metadata[f"{symbol}.NS"]
        metrics["nse_company_name"] = m.get("nse_company_name")
        metrics["nse_industry"] = m.get("nse_industry")
        metrics["isin_code"] = m.get("isin_code")
    else:
        metrics["nse_company_name"] = None
        metrics["nse_industry"] = None
        metrics["isin_code"] = None

    for f in (
        "trailingPE", "forwardPE", "priceToBook", "pegRatio",
        "enterpriseToEbitda", "enterpriseToRevenue", "priceToSalesTrailing12Months",
        "returnOnEquity", "returnOnAssets", "profitMargins",
        "grossMargins", "operatingMargins", "ebitdaMargins",
        "debtToEquity", "currentRatio", "quickRatio",
        "revenueGrowth", "earningsGrowth", "earningsQuarterlyGrowth",
        "marketCap", "enterpriseValue", "totalRevenue",
        "freeCashflow", "operatingCashflow", "ebitda", "grossProfits",
        "trailingEps", "forwardEps", "bookValue", "revenuePerShare",
        "dividendRate", "dividendYield", "payoutRatio",
        "fullTimeEmployees", "sector", "industry",
    ):
        metrics[f] = info.get(f)

    metrics["shortName"] = info.get("shortName")
    metrics["longName"] = info.get("longName")
    metrics["fetch_time"] = data.get("fetch_time", date.today().isoformat())
    metrics["error"] = data.get("error")

    # Return structured snapshot
    return {
        "as_of": metrics["fetch_time"].split("T")[0],
        "price_metrics": {
            "trailing_pe": _safe_float(metrics.get("trailingPE")),
            "forward_pe": _safe_float(metrics.get("forwardPE")),
            "price_to_book": _safe_float(metrics.get("priceToBook")),
            "peg_ratio": _safe_float(metrics.get("pegRatio")),
            "price_to_sales": _safe_float(metrics.get("priceToSalesTrailing12Months")),
            "enterprise_to_ebitda": _safe_float(metrics.get("enterpriseToEbitda")),
            "enterprise_to_revenue": _safe_float(metrics.get("enterpriseToRevenue")),
        },
        "profitability": {
            "profit_margin": _safe_float(metrics.get("profitMargins")),
            "gross_margin": _safe_float(metrics.get("grossMargins")),
            "operating_margin": _safe_float(metrics.get("operatingMargins")),
            "ebitda_margin": _safe_float(metrics.get("ebitdaMargins")),
            "return_on_equity": _safe_float(metrics.get("returnOnEquity")),
            "return_on_assets": _safe_float(metrics.get("returnOnAssets")),
        },
        "financial_health": {
            "debt_to_equity": _safe_float(metrics.get("debtToEquity")),
            "current_ratio": _safe_float(metrics.get("currentRatio")),
            "quick_ratio": _safe_float(metrics.get("quickRatio")),
            "beta": _safe_float(metrics.get("beta")),
        },
        "size": {
            "market_cap_inr": _safe_float(metrics.get("marketCap")),
            "enterprise_value_inr": _safe_float(metrics.get("enterpriseValue")),
            "total_revenue_inr": _safe_float(metrics.get("totalRevenue")),
            "employees": _safe_float(metrics.get("fullTimeEmployees")),
        },
        "dividends": {
            "dividend_rate": _safe_float(metrics.get("dividendRate")),
            "dividend_yield": _safe_float(metrics.get("dividendYield")),
            "payout_ratio": _safe_float(metrics.get("payoutRatio")),
        },
        "growth": {
            "revenue_growth": _safe_float(metrics.get("revenueGrowth")),
            "earnings_growth": _safe_float(metrics.get("earningsGrowth")),
            "earnings_quarterly_growth": _safe_float(metrics.get("earningsQuarterlyGrowth")),
        },
        "per_share": {
            "trailing_eps": _safe_float(metrics.get("trailingEps")),
            "forward_eps": _safe_float(metrics.get("forwardEps")),
            "book_value": _safe_float(metrics.get("bookValue")),
            "revenue_per_share": _safe_float(metrics.get("revenuePerShare")),
        },
    }


def _process_annual_statement(df: pd.DataFrame) -> pd.DataFrame:
    """Transpose annual statement, extract fiscal years."""
    if df is None or df.empty:
        return pd.DataFrame()
    try:
        transposed = df.T.copy()
        transposed.index = pd.to_datetime(transposed.index)
        transposed["fiscal_year"] = transposed.index.to_series().apply(lambda t: t.year + 1 if t.month >= 4 else t.year)
        return transposed.groupby("fiscal_year").last().T
    except Exception:
        return pd.DataFrame()


def _price_stats(price_hist: pd.DataFrame) -> dict[int, dict]:
    """Average/high/low per fiscal year."""
    if price_hist is None or price_hist.empty:
        return {}
    try:
        df = price_hist.copy()
        df.index = pd.to_datetime(df.index)
        df["fiscal_year"] = df.index.to_series().apply(lambda t: t.year + 1 if t.month >= 4 else t.year)
        out = {}
        for fy, g in df.groupby("fiscal_year"):
            out[fy] = {
                "avg_price": g["Close"].mean() if "Close" in g.columns else None,
                "high_price": g["High"].max() if "High" in g.columns else None,
                "low_price": g["Low"].min() if "Low" in g.columns else None,
                "year_end_price": g["Close"].iloc[-1] if "Close" in g.columns else None,
                "avg_volume": g["Volume"].mean() if "Volume" in g.columns else None,
            }
        return out
    except Exception:
        return {}


def _row_values(df: pd.DataFrame, label: str) -> list[float | None]:
    """Extract a named line-item row's per-column (per-fiscal-year) values, in
    column order. `df[df.index == label]` is a 1-row-wide DataFrame — iterating
    it directly yields column labels, not the row, so index into it explicitly."""
    if label not in df.index:
        return [None] * len(df.columns)
    row = df.loc[label]
    if isinstance(row, pd.DataFrame):
        row = row.iloc[0]
    return [_safe_float(v) for v in row]


def _yoy(values: list[float | None]) -> list[float | None]:
    if len(values) < 2:
        return [None] * len(values)
    out = [None]
    for i in range(1, len(values)):
        a, b = values[i-1], values[i]
        out.append((b - a) / abs(a) if a and b and a != 0 else None)
    return out


def _cagr(values: list[float | None]) -> float | None:
    valid = [(i, v) for i, v in enumerate(values) if v is not None and v > 0]
    if len(valid) < 2:
        return None
    n = valid[-1][0] - valid[0][0]
    if n <= 0:
        return None
    return (valid[-1][1] / valid[0][1]) ** (1 / n) - 1


def _trend(values: list[float | None]) -> str:
    clean = [v for v in values if v is not None]
    if len(clean) < 3:
        return "insufficient_data"
    ups = sum(1 for a, b in zip(clean, clean[1:]) if b > a)
    if ups >= len(clean) - 1:
        return "consistently_growing"
    if ups == 0:
        return "declining"
    if ups >= (len(clean) - 1) * 0.6:
        return "mostly_growing"
    return "volatile"


def _classify_margin(values: list[float | None]) -> str:
    v = [v for v in values if v is not None]
    if len(v) < 2:
        return "insufficient_data"
    change = v[-1] - v[0]
    if change > 0.02:
        return "expanding"
    if change < -0.02:
        return "contracting"
    return "stable"


# ── Trend computation ───────────────────────────────────────────────

def build_historical_trends(data: dict[str, Any]) -> dict[str, Any]:
    """Compute revenue, EPS, margin, ROE, debt trends from annual data."""
    info = data.get("info", {})
    symbol = data["symbol"].replace(".NS", "")

    # Build annual dataframes
    annual_income = _process_annual_statement(data.get("annual_income", pd.DataFrame()))
    annual_balance = _process_annual_statement(data.get("annual_balance", pd.DataFrame()))
    annual_cashflow = _process_annual_statement(data.get("annual_cashflow", pd.DataFrame()))

    # Inherit sector/industry from info
    sector = info.get("sector", "")
    industry = info.get("industry", "")

    # Yearly stats
    years = sorted(annual_income.columns)
    if not years:
        return {"source": "yfinance", "years_available": [], "error": "no_annual_data"}

    rev_vals = _row_values(annual_income, "Total Revenue")
    ni_vals = _row_values(annual_income, "Net Income")
    eps_vals = _row_values(annual_income, "Diluted EPS")
    gross_vals = _row_values(annual_income, "Gross Profit")
    op_vals = _row_values(annual_income, "Operating Income")
    cf_vals = _row_values(annual_cashflow, "Free Cash Flow")
    debt_vals = _row_values(annual_balance, "Total Debt")
    eq_vals = _row_values(annual_balance, "Stockholders Equity")

    rev = [v for v in rev_vals if _safe_float(v)]
    ni = [v for v in ni_vals if _safe_float(v)]
    eps = [v for v in eps_vals if _safe_float(v)]

    return {
        "source": "yfinance",
        "years_available": years,
        "revenue": {
            "values_inr": rev_vals,
            "yoy_growth": _yoy(rev_vals),
            "cagr_3yr": _cagr(rev),
            "trend": _trend(rev_vals),
        },
        "net_income": {
            "values_inr": ni_vals,
            "cagr_3yr": _cagr(ni),
            "trend": _trend(ni_vals),
        },
        "eps": {
            "values": eps_vals,
            "cagr_3yr": _cagr(eps),
            "trend": _trend(eps_vals),
        },
        "gross_profit": {
            "values_inr": gross_vals,
            "trend": _trend(gross_vals),
        },
        "operating_margin": {
            "values": _yoy(operating_margin := _compute_operating_margin(rev_vals, op_vals)),
            "direction": _classify_margin(operating_margin),
        },
        "roe": {
            "values": [_safe_float(ni_v / eq) if _safe_float(ni_v) is not None and eq else None
                       for ni_v, eq in zip(ni_vals, eq_vals)],
            "avg_3yr": _cagr(ni) if _cagr(ni) else None,  # proxy
        },
        "debt_to_equity": {
            "values": [_safe_float(d / e) if _safe_float(d) is not None and e else None
                       for d, e in zip(debt_vals, eq_vals)],
            "trend": _classify_margin(debt_vals),
        },
        "free_cash_flow": {
            "values_inr": cf_vals,
            "positive_years": sum(1 for v in cf_vals if _safe_float(v) and v > 0),
            "trend": _trend(cf_vals),
        },
    }


def _compute_operating_margin(rev, op):
    m = []
    for r, o in zip(rev, op):
        if _safe_float(r) and _safe_float(o) and r != 0:
            m.append(o / r)
        else:
            m.append(None)
    return m


# ── Insights ────────────────────────────────────────────────────────

def generate_insights(trends: dict[str, Any]) -> list[str]:
    """Generate 3-5 key insights from computed trends."""
    insights = []

    rev = trends.get("revenue", {})
    eps = trends.get("eps", {})
    cf = trends.get("free_cash_flow", {})
    debt = trends.get("debt_to_equity", {})

    rcagr = rev.get("cagr_3yr")
    ecagr = eps.get("cagr_3yr")
    if rcagr is not None:
        insights.append(f"Revenue CAGR: {rcagr*100:+.1f}%")
    if ecagr is not None:
        insights.append(f"EPS CAGR: {ecagr*100:+.1f}%")

    op_dir = cf.get("trend")
    if op_dir == "consistently_growing" or op_dir == "mostly_growing":
        insights.append(f"Operating cash flow: {op_dir}")
    if op_dir == "declining":
        insights.append("Operating cash flow declining")

    ddte = debt.get("trend")
    if ddte == "debt_free":
        insights.append("Virtually debt-free")
    if ddte == "low":
        insights.append(f"Low leverage (D/E {debt['values'][-1]:.2f})")
    if ddte == "high":
        insights.append(f"High leverage (D/E {debt['values'][-1]:.2f})")

    return insights[:5]


# ── Full company JSON ───────────────────────────────────────────────

def build_company_json(
    symbol: str,
    data: dict[str, Any],
    nse_metadata: dict[str, dict] | None = None,
    historical_trends: dict[str, Any] | None = None,
    industry_comparison: dict[str, Any] | None = None,
    shareholding: dict | None = None,
    credit_ratings: dict | None = None,
) -> dict:
    """Assemble the final per-company JSON."""
    info = data.get("info", {})
    snapshot = build_current_snapshot(data, nse_metadata)

    return {
        "symbol": symbol.replace(".NS", ""),
        "company_name": info.get("longName") or info.get("shortName") or "",
        "sector": info.get("sector") or "",
        "industry": info.get("industry") or "",
        "currency": "INR",
        "cik": None,
        "current_snapshot": snapshot,
        "historical_trends": historical_trends or {},
        "key_insights": generate_insights(historical_trends or {}),
        "industry_comparison": industry_comparison,
        "shareholding": shareholding,
        "credit_ratings": credit_ratings,
    }
