"""Transform raw fetch data into company JSONs with trends."""

import math
from datetime import datetime, date
from io import StringIO
from pathlib import Path
from typing import Any

import pandas as pd

from .market import NSE, MarketConfig
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

def build_current_snapshot(data: dict[str, Any], market: MarketConfig = NSE) -> dict:
    """Build snapshot dict from yfinance info.

    market defaults to NSE for backward compatibility with call sites (mostly
    tests) that predate MarketConfig and never cared about market-specific
    behavior. Every production call site (screener.markets.run_pipeline) passes
    market explicitly. A new direct caller that forgets to pass it gets NSE's
    INR/.NS-stripping semantics silently rather than an error -- worth keeping
    in mind before adding another real (non-test) caller of this function.

    Universe metadata (isin/nse_industry/gics_sector/...) is not part of the
    snapshot -- see MarketConfig.metadata_fields and build_company_json,
    which copy it onto the company JSON's top level instead.
    """
    info = data.get("info", {})
    symbol = data["symbol"].replace(market.ticker_suffix, "")

    metrics = {"symbol": symbol}
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
            "market_cap": _safe_float(metrics.get("marketCap")),
            "enterprise_value": _safe_float(metrics.get("enterpriseValue")),
            "total_revenue": _safe_float(metrics.get("totalRevenue")),
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

def build_historical_trends(data: dict[str, Any], market: MarketConfig = NSE) -> dict[str, Any]:
    """Compute revenue, EPS, margin, ROE, debt trends from annual data."""
    info = data.get("info", {})
    symbol = data["symbol"].replace(market.ticker_suffix, "")

    statements = AnnualStatements.from_yfinance(
        data.get("annual_income", pd.DataFrame()),
        data.get("annual_balance", pd.DataFrame()),
        data.get("annual_cashflow", pd.DataFrame()),
        market.fiscal_year,
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
            "values": rev_vals,
            "yoy_growth": yoy(rev_vals),
            "cagr_3yr": cagr(rev),
            "trend": classify_growth(rev_vals),
        },
        "net_income": {
            "values": ni_vals,
            "cagr_3yr": cagr(ni),
            "trend": classify_growth(ni_vals),
        },
        "eps": {
            "values": eps_vals,
            "cagr_3yr": cagr(eps),
            "trend": classify_growth(eps_vals),
        },
        "gross_profit": {
            "values": gross_vals,
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
            "values": cf_vals,
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


def build_historical_trends_edgar(facts: dict | None) -> dict[str, Any]:
    """Compute S&P500's historical_trends from a SEC XBRL companyfacts
    payload (screener.fetch.fetch_edgar_facts), via AnnualStatements.from_edgar.

    Deliberately a smaller, different metric set than NSE's yfinance-derived
    build_historical_trends -- see data/SCHEMA.md's historical_trends table:
    no yoy_growth, and no roe/debt_to_equity/free_cash_flow (AnnualStatements.
    from_edgar doesn't extract balance-sheet tags for those), plus
    operating_cash_flow, which NSE doesn't track.
    """
    statements = AnnualStatements.from_edgar(facts)
    years = statements.years
    if not years:
        return {"source": "edgar_xbrl", "years_available": [], "error": "no_edgar_data"}

    rev_vals = statements.revenue
    ni_vals = statements.net_income
    eps_vals = statements.diluted_eps
    gross_vals = statements.gross_profit
    op_vals = statements.operating_income
    ocf_vals = statements.operating_cash_flow

    rev = [v for v in rev_vals if _safe_float(v)]
    ni = [v for v in ni_vals if _safe_float(v)]
    eps = [v for v in eps_vals if _safe_float(v)]

    operating_margin = _compute_operating_margin(rev_vals, op_vals)

    return {
        "source": "edgar_xbrl",
        "years_available": years,
        "revenue": {
            "values": rev_vals,
            "cagr_3yr": cagr(rev),
            "trend": classify_growth(rev_vals),
        },
        "net_income": {
            "values": ni_vals,
            "cagr_3yr": cagr(ni),
            "trend": classify_growth(ni_vals),
        },
        "eps": {
            "values": eps_vals,
            "cagr_3yr": cagr(eps),
            "trend": classify_growth(eps_vals),
        },
        "operating_margin": {
            "values": operating_margin,
        },
        "gross_profit": {
            "values_usd": gross_vals,
        },
        "operating_cash_flow": {
            "values_usd": ocf_vals,
            "positive_years": sum(1 for v in ocf_vals if _safe_float(v) and v > 0),
            "trend": classify_growth(ocf_vals),
        },
    }


def build_institutional_ownership(data: dict[str, Any]) -> dict | None:
    """S&P-only: pct_insider/pct_institutional come from yfinance's info blob
    (already fetched for current_snapshot, no extra network call);
    top_holders comes from ticker.institutional_holders, a distinct yfinance
    call gated by MarketConfig.fetch_institutional_holders (see fetch.
    fetch_ticker_data). Percentages are stored as whole numbers (52.3 =
    52.3%), matching NSE's shareholding convention -- yfinance itself
    reports these as 0-1 fractions.

    Returns None when nothing was fetched (NSE, or a failed/empty SNP fetch),
    same convention as shareholding/credit_ratings.
    """
    info = data.get("info", {})
    pct_insider = _safe_float(info.get("heldPercentInsiders"))
    pct_institutional = _safe_float(info.get("heldPercentInstitutions"))

    holders_df = data.get("institutional_holders")
    top_holders = []
    if holders_df is not None and not holders_df.empty:
        for _, row in holders_df.iterrows():
            pct_out = _safe_float(row.get("pctHeld"))
            top_holders.append({
                "holder": row.get("Holder"),
                "shares": _safe_float(row.get("Shares")),
                "pct_out": pct_out * 100 if pct_out is not None else None,
            })

    if pct_insider is None and pct_institutional is None and not top_holders:
        return None

    return {
        "updated_at": date.today().isoformat(),
        "pct_insider": pct_insider * 100 if pct_insider is not None else None,
        "pct_institutional": pct_institutional * 100 if pct_institutional is not None else None,
        "top_holders": top_holders,
    }


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
    # classify_leverage() bands the latest *non-null* value (the most recent
    # year can be None if not yet reported) -- mirror that here rather than
    # indexing values[-1] directly, which crashed on names like NESTLEIND.
    latest_de = next((v for v in reversed(debt.get("values", [])) if v is not None), None)
    if ddte == "debt_free":
        insights.append("Virtually debt-free")
    if ddte == "low" and latest_de is not None:
        insights.append(f"Low leverage (D/E {latest_de:.2f})")
    if ddte == "high" and latest_de is not None:
        insights.append(f"High leverage (D/E {latest_de:.2f})")

    return insights[:5]


# ── Full company JSON ───────────────────────────────────────────────

def build_company_json(
    symbol: str,
    data: dict[str, Any],
    metadata: dict[str, dict] | None = None,
    historical_trends: dict[str, Any] | None = None,
    industry_comparison: dict[str, Any] | None = None,
    shareholding: dict | None = None,
    credit_ratings: dict | None = None,
    market: MarketConfig = NSE,
    cik: int | None = None,
    institutional_ownership: dict | None = None,
) -> dict:
    """Assemble the final per-company JSON."""
    info = data.get("info", {})
    snapshot = build_current_snapshot(data, market)
    bare_symbol = symbol.replace(market.ticker_suffix, "")

    # market.metadata_fields maps output-key -> metadata-key (see MarketConfig):
    # NSE gets isin/nse_industry from its official CSV, SNP gets gics_sector/
    # gics_industry from Wikipedia's table -- both fetched by fetch_universe
    # and passed in here as `metadata`, keyed by the suffixed symbol.
    m = (metadata or {}).get(f"{bare_symbol}{market.ticker_suffix}", {})
    extra_fields = {out_key: m.get(meta_key) for out_key, meta_key in market.metadata_fields.items()}

    return {
        "symbol": bare_symbol,
        "company_name": info.get("longName") or info.get("shortName") or "",
        "sector": info.get("sector") or "",
        "industry": info.get("industry") or "",
        "currency": market.currency,
        "cik": cik,
        **extra_fields,
        "current_snapshot": snapshot,
        "historical_trends": historical_trends or {},
        "key_insights": generate_insights(historical_trends or {}),
        "industry_comparison": industry_comparison,
        "shareholding": shareholding,
        "credit_ratings": credit_ratings,
        "institutional_ownership": institutional_ownership,
    }
