"""Transform raw fetch data into company JSONs with aligned annual series."""

from datetime import date
from itertools import pairwise
from typing import Any

import pandas as pd

from .market import NSE, MarketConfig
from .statements import AnnualStatements, safe_float

# ── Extractors ──────────────────────────────────────────────────────

def build_current_snapshot(
    data: dict[str, Any], market: MarketConfig = NSE, *, drawdown: float | None = None,
) -> dict:
    """Build a market-aware snapshot from Yahoo info."""
    info = data.get("info", {})

    return {
        "as_of": data.get("fetch_time", date.today().isoformat()).split("T")[0],
        "price_metrics": {
            "trailing_pe": safe_float(info.get("trailingPE")),
            "forward_pe": safe_float(info.get("forwardPE")),
            "price_to_book": safe_float(info.get("priceToBook")),
            "peg_ratio": safe_float(info.get("pegRatio")),
            "price_to_sales": safe_float(info.get("priceToSalesTrailing12Months")),
            "enterprise_to_ebitda": safe_float(info.get("enterpriseToEbitda")),
            "enterprise_to_revenue": safe_float(info.get("enterpriseToRevenue")),
        },
        "profitability": {
            "profit_margin": safe_float(info.get("profitMargins")),
            "gross_margin": safe_float(info.get("grossMargins")),
            "operating_margin": safe_float(info.get("operatingMargins")),
            "ebitda_margin": safe_float(info.get("ebitdaMargins")),
            "return_on_equity": safe_float(info.get("returnOnEquity")),
            "return_on_assets": safe_float(info.get("returnOnAssets")),
        },
        "financial_health": {
            "debt_to_equity": safe_float(info.get("debtToEquity")),
            "current_ratio": safe_float(info.get("currentRatio")),
            "quick_ratio": safe_float(info.get("quickRatio")),
            "beta": safe_float(info.get("beta")),
        },
        "size": {
            "market_cap": safe_float(info.get("marketCap")),
            "enterprise_value": safe_float(info.get("enterpriseValue")),
            "total_revenue": safe_float(info.get("totalRevenue")),
            "employees": safe_float(info.get("fullTimeEmployees")),
        },
        "dividends": {
            "dividend_rate": safe_float(info.get("dividendRate")),
            "dividend_yield": safe_float(info.get("dividendYield")),
            "payout_ratio": safe_float(info.get("payoutRatio")),
        },
        "growth": {
            "revenue_growth": safe_float(info.get("revenueGrowth")),
            "earnings_growth": safe_float(info.get("earningsGrowth")),
            "earnings_quarterly_growth": safe_float(info.get("earningsQuarterlyGrowth")),
        },
        "per_share": {
            "trailing_eps": safe_float(info.get("trailingEps")),
            "forward_eps": safe_float(info.get("forwardEps")),
            "book_value": safe_float(info.get("bookValue")),
            "revenue_per_share": safe_float(info.get("revenuePerShare")),
        },
        "risk": {"drawdown_52w": safe_float(drawdown)},
    }


def drawdown_52w(prices: pd.DataFrame) -> float | None:
    """Calculate peak-to-trough drawdown from adjusted-price observations."""
    if prices.empty or not {"date", "adjusted_close"} <= set(prices.columns):
        return None
    prices = prices[["date", "adjusted_close"]].copy()
    prices["date"] = pd.to_datetime(prices["date"], errors="coerce")
    prices["adjusted_close"] = pd.to_numeric(prices["adjusted_close"], errors="coerce")
    prices = prices.dropna().query("adjusted_close > 0").sort_values("date")
    if len(prices) < 2:
        return None
    prices = prices[prices["date"] >= prices["date"].iloc[-1] - pd.Timedelta(weeks=52)]
    if len(prices) < 2:
        return None
    value = (prices["adjusted_close"] / prices["adjusted_close"].cummax() - 1).min()
    return safe_float(value)


# ── Trend computation ───────────────────────────────────────────────

def build_series(
    statements: AnnualStatements,
    *,
    source: str,
    regulatory: dict[int, dict[str, float | None]] | None = None,
) -> dict[str, Any]:
    """Build positionally aligned annual statement and derived series."""
    regulatory = regulatory or {}
    years = sorted(set(statements.years) | set(regulatory))
    if not years:
        return {"source": source, "fiscal_years": [], "error": "no_data"}

    items = {
        year: statements.by_year.get(year, None)
        for year in years
    }
    base_fields = (
        "revenue", "gross_profit", "operating_income", "net_income", "diluted_eps",
        "operating_cash_flow", "total_assets", "current_liabilities",
        "cash_and_equivalents", "total_debt", "stockholders_equity", "diluted_shares",
        "ebitda",
    )
    out: dict[str, Any] = {"source": source, "fiscal_years": years}
    for field in base_fields:
        out[field] = [getattr(items[year], field) if items[year] else None for year in years]

    raw_capex = [items[year].capex if items[year] else None for year in years]
    out["capex"] = [abs(value) if value is not None else None for value in raw_capex]
    for field in (
        "loans", "deposits", "nonperforming_loans_ratio", "net_npa_ratio", "cet1_ratio",
    ):
        out[field] = [safe_float(regulatory.get(year, {}).get(field)) for year in years]

    def ratios(numerators, denominators, *, positive_denominator=False):
        return [
            numerator / denominator
            if (
                numerator is not None and denominator is not None and denominator != 0
                and (not positive_denominator or denominator > 0)
            )
            else None
            for numerator, denominator in zip(numerators, denominators)
        ]

    out["free_cash_flow"] = [
        cash_flow - capex if cash_flow is not None and capex is not None else None
        for cash_flow, capex in zip(out["operating_cash_flow"], out["capex"])
    ]
    out["gross_margin"] = ratios(out["gross_profit"], out["revenue"])
    out["operating_margin"] = ratios(out["operating_income"], out["revenue"])
    out["fcf_margin"] = ratios(out["free_cash_flow"], out["revenue"])
    out["cfo_to_net_income"] = ratios(out["operating_cash_flow"], out["net_income"])
    out["capex_intensity"] = ratios(out["capex"], out["revenue"])
    out["net_debt"] = [
        debt - cash if debt is not None and cash is not None else None
        for debt, cash in zip(out["total_debt"], out["cash_and_equivalents"])
    ]
    out["net_debt_to_ebitda"] = ratios(
        out["net_debt"], out["ebitda"], positive_denominator=True,
    )

    capital_employed = [
        assets - liabilities if assets is not None and liabilities is not None else None
        for assets, liabilities in zip(out["total_assets"], out["current_liabilities"])
    ]

    def average_return(numerators, balances):
        values = [None]
        for index in range(1, len(years)):
            previous, current = balances[index - 1], balances[index]
            average = (
                (previous + current) / 2
                if years[index] == years[index - 1] + 1
                and previous is not None and current is not None
                else None
            )
            numerator = numerators[index]
            values.append(numerator / average if numerator is not None and average and average > 0 else None)
        return values

    out["roe"] = average_return(out["net_income"], out["stockholders_equity"])
    out["roa"] = average_return(out["net_income"], out["total_assets"])
    out["roce"] = average_return(out["operating_income"], capital_employed)
    return out


def build_historical_series(
    data: dict[str, Any],
    market: MarketConfig = NSE,
    *,
    regulatory: dict[int, dict[str, float | None]] | None = None,
) -> dict[str, Any]:
    """Compute aligned historical series from Yahoo annual data."""
    statements = AnnualStatements.from_yfinance(
        data.get("annual_income", pd.DataFrame()),
        data.get("annual_balance", pd.DataFrame()),
        data.get("annual_cashflow", pd.DataFrame()),
        market.fiscal_year,
    )
    return build_series(statements, source="yfinance", regulatory=regulatory)


def build_historical_series_edgar(
    facts: dict | None,
    market: MarketConfig | None = None,
    *,
    regulatory: dict[int, dict[str, float | None]] | None = None,
) -> dict[str, Any]:
    """Build aligned S&P historical series from SEC companyfacts."""
    if market is None:
        from .market import SNP as _snp
        market = _snp
    statements = AnnualStatements.from_edgar(facts)
    return build_series(statements, source="edgar_xbrl", regulatory=regulatory)


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

def generate_insights(series: dict[str, Any]) -> list[str]:
    """Leave screening and trend signals to Phase 3C."""
    del series
    return []


# ── Full company JSON ───────────────────────────────────────────────

def build_company_json(
    symbol: str,
    data: dict[str, Any],
    metadata: dict[str, dict] | None = None,
    historical_series: dict[str, Any] | None = None,
    market: MarketConfig = NSE,
    cik: int | None = None,
    institutional_ownership: dict | None = None,
    drawdown: float | None = None,
) -> dict:
    """Assemble the final per-company JSON."""
    info = data.get("info", {})
    snapshot = build_current_snapshot(data, market, drawdown=drawdown)
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
        # Keep the persisted key until the curated-schema migration in Phase 3C.
        "historical_trends": historical_series or {},
        "key_insights": generate_insights(historical_series or {}),
        "institutional_ownership": institutional_ownership,
    }


# ── Trend classifiers (former trends.py) ────────────────────────────


def classify_growth(values: list[float | None]) -> str:
    clean = [v for v in values if v is not None]
    if len(clean) < 3:
        return "insufficient_data"
    ups = sum(1 for a, b in pairwise(clean) if b > a)
    if ups >= len(clean) - 1:
        return "consistently_growing"
    if ups == 0:
        return "declining"
    if ups >= (len(clean) - 1) * 0.6:
        return "mostly_growing"
    return "volatile"


def classify_margin_direction(values: list[float | None]) -> str:
    clean = [v for v in values if v is not None]
    if len(clean) < 2:
        return "insufficient_data"
    change = clean[-1] - clean[0]
    if change > 0.02:
        return "expanding"
    if change < -0.02:
        return "contracting"
    return "stable"


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
    """Return mean ROE over the trailing fiscal-year window."""
    trailing = [v for v in values[-window:] if v is not None]
    if not trailing:
        return None
    return sum(trailing) / len(trailing)


def classify_leverage(values: list[float | None]) -> str:
    """Band the *level* of the most recent debt/equity ratio — not a delta
    over time like classify_margin_direction. `values` must be the ratio
    (debt/equity), not raw debt."""
    clean = [v for v in values if v is not None]
    if not clean:
        return "insufficient_data"
    latest = clean[-1]
    if latest < 0.05:
        return "debt_free"
    if latest < 0.5:
        return "low"
    if latest < 1.5:
        return "moderate"
    return "high"
