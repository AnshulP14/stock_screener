"""Transform raw fetch data into company JSONs with trends.

Includes former trends.py: GrowthTrend, MarginDirection, LeverageBand StrEnums
and classify_growth, classify_margin_direction, classify_leverage, yoy, cagr, average_roe.
"""

import math
from datetime import date
from enum import StrEnum
from itertools import pairwise
from typing import Any

import pandas as pd

from .market import NSE, MarketConfig


def safe_float(v: Any) -> float | None:
    """Convert to float; return None for invalid, NaN, or Inf."""
    if v is None or (isinstance(v, float) and (math.isnan(v) or math.isinf(v))):
        return None
    try:
        f = float(v)
        return f if not (math.isnan(f) or math.isinf(f)) else None
    except (ValueError, TypeError):
        return None


from .statements import AnnualStatements
from .statements import AnnualStatements

# ── Extractors ──────────────────────────────────────────────────────

def build_current_snapshot(data: dict[str, Any], market: MarketConfig = NSE) -> dict:
    """Build snapshot dict from yfinance info. `market` defaults to NSE;
    real callers should pass it explicitly. Universe metadata
    (isin/nse_industry/gics_sector/...) lives on the company JSON's top
    level instead — see MarketConfig.metadata_fields."""
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
            "trailing_pe": safe_float(metrics.get("trailingPE")),
            "forward_pe": safe_float(metrics.get("forwardPE")),
            "price_to_book": safe_float(metrics.get("priceToBook")),
            "peg_ratio": safe_float(metrics.get("pegRatio")),
            "price_to_sales": safe_float(metrics.get("priceToSalesTrailing12Months")),
            "enterprise_to_ebitda": safe_float(metrics.get("enterpriseToEbitda")),
            "enterprise_to_revenue": safe_float(metrics.get("enterpriseToRevenue")),
        },
        "profitability": {
            "profit_margin": safe_float(metrics.get("profitMargins")),
            "gross_margin": safe_float(metrics.get("grossMargins")),
            "operating_margin": safe_float(metrics.get("operatingMargins")),
            "ebitda_margin": safe_float(metrics.get("ebitdaMargins")),
            "return_on_equity": safe_float(metrics.get("returnOnEquity")),
            "return_on_assets": safe_float(metrics.get("returnOnAssets")),
        },
        "financial_health": {
            "debt_to_equity": safe_float(metrics.get("debtToEquity")),
            "current_ratio": safe_float(metrics.get("currentRatio")),
            "quick_ratio": safe_float(metrics.get("quickRatio")),
            "beta": safe_float(metrics.get("beta")),
        },
        "size": {
            "market_cap": safe_float(metrics.get("marketCap")),
            "enterprise_value": safe_float(metrics.get("enterpriseValue")),
            "total_revenue": safe_float(metrics.get("totalRevenue")),
            "employees": safe_float(metrics.get("fullTimeEmployees")),
        },
        "dividends": {
            "dividend_rate": safe_float(metrics.get("dividendRate")),
            "dividend_yield": safe_float(metrics.get("dividendYield")),
            "payout_ratio": safe_float(metrics.get("payoutRatio")),
        },
        "growth": {
            "revenue_growth": safe_float(metrics.get("revenueGrowth")),
            "earnings_growth": safe_float(metrics.get("earningsGrowth")),
            "earnings_quarterly_growth": safe_float(metrics.get("earningsQuarterlyGrowth")),
        },
        "per_share": {
            "trailing_eps": safe_float(metrics.get("trailingEps")),
            "forward_eps": safe_float(metrics.get("forwardEps")),
            "book_value": safe_float(metrics.get("bookValue")),
            "revenue_per_share": safe_float(metrics.get("revenuePerShare")),
        },
    }


# ── Trend computation ───────────────────────────────────────────────

def _compute_operating_margin(rev, op):
    m = []
    for r, o in zip(rev, op):
        if safe_float(r) and safe_float(o) and r != 0:
            m.append(o / r)
        else:
            m.append(None)
    return m


def build_trends(statements: AnnualStatements, series_spec: tuple, *, source: str) -> dict[str, Any]:
    """Build historical_trends dict from statements + series spec
    (MarketConfig.trend_series). Also derives composite metrics
    (operating_margin, roe, debt_to_equity) when their inputs are present."""
    years = statements.years
    if not years:
        return {"source": source, "years_available": [], "error": "no_data"}

    out: dict[str, Any] = {"source": source, "years_available": years}

    attr_map = {key: attr for key, attr in series_spec}

    for out_key, attr_name in series_spec:
        vals = getattr(statements, attr_name)
        clean = [v for v in vals if safe_float(v)]

        entry: dict[str, Any] = {"values": vals}

        if out_key in ("revenue", "net_income", "eps"):
            entry["cagr_3yr"] = cagr(clean)
            entry["trend"] = classify_growth(vals)

        if out_key == "revenue":
            entry["yoy_growth"] = yoy(vals)

        if out_key in ("gross_profit", "free_cash_flow", "operating_cash_flow"):
            entry["trend"] = classify_growth(vals)
            entry["positive_years"] = sum(1 for v in vals if safe_float(v) and v > 0)

        out[out_key] = entry

    # ── Composite: operating margin (needs revenue + operating_income) ──
    if "revenue" in attr_map and "operating_income" in attr_map:
        rev_vals = getattr(statements, attr_map["revenue"])
        op_vals = getattr(statements, attr_map["operating_income"])
        om = _compute_operating_margin(rev_vals, op_vals)
        out["operating_margin"] = {
            "values": om,
            "direction": classify_margin_direction(om),
        }

    # ── Composite: ROE (needs net_income + stockholders_equity) ──
    if "net_income" in attr_map and "stockholders_equity" in attr_map:
        ni_vals = getattr(statements, attr_map["net_income"])
        eq_vals = getattr(statements, attr_map["stockholders_equity"])
        roe_values = [
            safe_float(ni_v / eq) if safe_float(ni_v) is not None and eq else None
            for ni_v, eq in zip(ni_vals, eq_vals)
        ]
        out["roe"] = {
            "values": roe_values,
            "avg_3yr": average_roe(roe_values),
        }

    # ── Composite: debt_to_equity (needs total_debt + stockholders_equity) ──
    if "total_debt" in attr_map and "stockholders_equity" in attr_map:
        debt_vals = getattr(statements, attr_map["total_debt"])
        eq_vals = getattr(statements, attr_map["stockholders_equity"])
        de_values = [
            safe_float(d / e) if safe_float(d) is not None and e else None
            for d, e in zip(debt_vals, eq_vals)
        ]
        out["debt_to_equity"] = {
            "values": de_values,
            "trend": classify_leverage(de_values),
        }

    return out


def build_historical_trends(data: dict[str, Any], market: MarketConfig = NSE) -> dict[str, Any]:
    """Compute historical trends from yfinance annual data (NSE only)."""
    statements = AnnualStatements.from_yfinance(
        data.get("annual_income", pd.DataFrame()),
        data.get("annual_balance", pd.DataFrame()),
        data.get("annual_cashflow", pd.DataFrame()),
        market.fiscal_year,
    )
    return build_trends(statements, market.trend_series, source="yfinance")


def build_historical_trends_edgar(facts: dict | None, market: MarketConfig | None = None) -> dict[str, Any]:
    """Compute historical trends from SEC EDGAR XBRL companyfacts (S&P 500 only).

    market defaults to SNP for backward compatibility with call sites that
    predate the unified build_trends path.
    """
    if market is None:
        from .market import SNP as _snp
        market = _snp
    statements = AnnualStatements.from_edgar(facts)
    return build_trends(statements, market.trend_series, source="edgar_xbrl")


def build_institutional_ownership(data: dict[str, Any]) -> dict | None:
    """S&P-only. Percentages stored as whole numbers (52.3 = 52.3%), matching
    NSE's shareholding convention. Returns None if nothing was fetched."""
    info = data.get("info", {})
    pct_insider = safe_float(info.get("heldPercentInsiders"))
    pct_institutional = safe_float(info.get("heldPercentInstitutions"))

    holders_df = data.get("institutional_holders")
    top_holders = []
    if holders_df is not None and not holders_df.empty:
        for _, row in holders_df.iterrows():
            pct_out = safe_float(row.get("pctHeld"))
            top_holders.append({
                "holder": row.get("Holder"),
                "shares": safe_float(row.get("Shares")),
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
    market: MarketConfig = NSE,
    cik: int | None = None,
    institutional_ownership: dict | None = None,
) -> dict:
    """Assemble the final per-company JSON."""
    info = data.get("info", {})
    snapshot = build_current_snapshot(data, market)
    bare_symbol = symbol.replace(market.ticker_suffix, "")

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
        "institutional_ownership": institutional_ownership,
    }


# ── Trend classifiers (former trends.py) ────────────────────────────


class GrowthTrend(StrEnum):
    CONSISTENTLY_GROWING = "consistently_growing"
    MOSTLY_GROWING = "mostly_growing"
    DECLINING = "declining"
    VOLATILE = "volatile"
    INSUFFICIENT_DATA = "insufficient_data"


def classify_growth(values: list[float | None]) -> GrowthTrend:
    clean = [v for v in values if v is not None]
    if len(clean) < 3:
        return GrowthTrend.INSUFFICIENT_DATA
    ups = sum(1 for a, b in pairwise(clean) if b > a)
    if ups >= len(clean) - 1:
        return GrowthTrend.CONSISTENTLY_GROWING
    if ups == 0:
        return GrowthTrend.DECLINING
    if ups >= (len(clean) - 1) * 0.6:
        return GrowthTrend.MOSTLY_GROWING
    return GrowthTrend.VOLATILE


class MarginDirection(StrEnum):
    EXPANDING = "expanding"
    CONTRACTING = "contracting"
    STABLE = "stable"
    INSUFFICIENT_DATA = "insufficient_data"


def classify_margin_direction(values: list[float | None]) -> MarginDirection:
    clean = [v for v in values if v is not None]
    if len(clean) < 2:
        return MarginDirection.INSUFFICIENT_DATA
    change = clean[-1] - clean[0]
    if change > 0.02:
        return MarginDirection.EXPANDING
    if change < -0.02:
        return MarginDirection.CONTRACTING
    return MarginDirection.STABLE


class LeverageBand(StrEnum):
    DEBT_FREE = "debt_free"
    LOW = "low"
    MODERATE = "moderate"
    HIGH = "high"
    INSUFFICIENT_DATA = "insufficient_data"


def yoy(values: list[float | None]) -> list[float | None]:
    if len(values) < 2:
        return [None] * len(values)
    out: list[float | None] = [None]
    for i in range(1, len(values)):
        a, b = values[i - 1], values[i]
        out.append((b - a) / abs(a) if a and b and a != 0 else None)
    return out


def cagr(values: list[float | None]) -> float | None:
    valid = [(i, v) for i, v in enumerate(values) if v is not None and v > 0]
    if len(valid) < 2:
        return None
    n = valid[-1][0] - valid[0][0]
    if n <= 0:
        return None
    return (valid[-1][1] / valid[0][1]) ** (1 / n) - 1


def average_roe(values: list[float | None], window: int = 3) -> float | None:
    """Mean ROE over the trailing `window` fiscal years. Previously computed
    as net-income CAGR standing in for an ROE average — a different metric
    entirely, just because both happened to be "a number about profitability
    over 3 years"."""
    trailing = [v for v in values[-window:] if v is not None]
    if not trailing:
        return None
    return sum(trailing) / len(trailing)


def classify_leverage(values: list[float | None]) -> LeverageBand:
    """Band the *level* of the most recent debt/equity ratio — not a delta
    over time like classify_margin_direction. `values` must be the ratio
    (debt/equity), not raw debt."""
    clean = [v for v in values if v is not None]
    if not clean:
        return LeverageBand.INSUFFICIENT_DATA
    latest = clean[-1]
    if latest < 0.05:
        return LeverageBand.DEBT_FREE
    if latest < 0.5:
        return LeverageBand.LOW
    if latest < 1.5:
        return LeverageBand.MODERATE
    return LeverageBand.HIGH
