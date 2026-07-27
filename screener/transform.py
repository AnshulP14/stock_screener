"""Transform raw fetch data into company JSONs with trends."""

import math
from datetime import datetime, date
from io import StringIO
from pathlib import Path
from typing import Any

import pandas as pd

from .statements import AnnualStatements
from .trends import (
    average_roe,
    cagr,
    classify_growth,
    classify_leverage,
    classify_margin_direction,
    yoy,
)


# ── Helpers ─────────────────────────────────────────────────────────

def _safe_float(v: Any) -> float | None:
    if v is None or (isinstance(v, float) and (math.isnan(v) or math.isinf(v))):
        return None
    try:
        f = float(v)
        return f if not (math.isnan(f) or math.isinf(f)) else None
    except (ValueError, TypeError):
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
        "debtToEquity", "currentRatio", "quickRatio", "beta",
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


# ── Trend computation ───────────────────────────────────────────────

def build_historical_trends(data: dict[str, Any]) -> dict[str, Any]:
    """Compute revenue, EPS, margin, ROE, debt trends from annual data."""
    info = data.get("info", {})
    symbol = data["symbol"].replace(".NS", "")

    statements = AnnualStatements.from_yfinance(
        data.get("annual_income", pd.DataFrame()),
        data.get("annual_balance", pd.DataFrame()),
        data.get("annual_cashflow", pd.DataFrame()),
    )

    # Inherit sector/industry from info
    sector = info.get("sector", "")
    industry = info.get("industry", "")

    # Yearly stats
    years = statements.years
    if not years:
        return {"source": "yfinance", "years_available": [], "error": "no_annual_data"}

    rev_vals = statements.revenue
    ni_vals = statements.net_income
    eps_vals = statements.diluted_eps
    gross_vals = statements.gross_profit
    op_vals = statements.operating_income
    cf_vals = statements.free_cash_flow
    debt_vals = statements.total_debt
    eq_vals = statements.stockholders_equity

    rev = [v for v in rev_vals if _safe_float(v)]
    ni = [v for v in ni_vals if _safe_float(v)]
    eps = [v for v in eps_vals if _safe_float(v)]

    operating_margin = _compute_operating_margin(rev_vals, op_vals)
    roe_values = [_safe_float(ni_v / eq) if _safe_float(ni_v) is not None and eq else None
                  for ni_v, eq in zip(ni_vals, eq_vals)]
    debt_to_equity_values = [_safe_float(d / e) if _safe_float(d) is not None and e else None
                             for d, e in zip(debt_vals, eq_vals)]

    return {
        "source": "yfinance",
        "years_available": years,
        "revenue": {
            "values_inr": rev_vals,
            "yoy_growth": yoy(rev_vals),
            "cagr_3yr": cagr(rev),
            "trend": classify_growth(rev_vals),
        },
        "net_income": {
            "values_inr": ni_vals,
            "cagr_3yr": cagr(ni),
            "trend": classify_growth(ni_vals),
        },
        "eps": {
            "values": eps_vals,
            "cagr_3yr": cagr(eps),
            "trend": classify_growth(eps_vals),
        },
        "gross_profit": {
            "values_inr": gross_vals,
            "trend": classify_growth(gross_vals),
        },
        "operating_margin": {
            "values": operating_margin,
            "direction": classify_margin_direction(operating_margin),
        },
        "roe": {
            "values": roe_values,
            "avg_3yr": average_roe(roe_values),
        },
        "debt_to_equity": {
            "values": debt_to_equity_values,
            "trend": classify_leverage(debt_to_equity_values),
        },
        "free_cash_flow": {
            "values_inr": cf_vals,
            "positive_years": sum(1 for v in cf_vals if _safe_float(v) and v > 0),
            "trend": classify_growth(cf_vals),
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
