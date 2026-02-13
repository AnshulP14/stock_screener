"""
Stock Screener Tool - Minimal LLM API

Provides a minimal set of LLM-callable functions for stock screening.
Designed for simplicity with rich parameter documentation for investment strategies.

LLM-CALLABLE FUNCTIONS (3 total):
    1. screen_stocks() - Filter stocks by any combination of metrics
    2. get_company() - Get detailed profile for a specific company
    3. get_screener_metadata() - List available sectors, industries, and sortable fields
"""

import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, Any, Literal

from pydantic import Field

logger = logging.getLogger(__name__)


# =============================================================================
# Path Configuration
# =============================================================================

DATA_DIR = Path(__file__).parent.parent.parent.parent / "data"
SCREENING_SUMMARY = DATA_DIR / "indices" / "screening_summary.json"
BY_SECTOR = DATA_DIR / "indices" / "by_sector.json"
BY_INDUSTRY = DATA_DIR / "indices" / "by_industry.json"
INDUSTRY_STATS = DATA_DIR / "indices" / "industry_stats.json"
COMPANIES_DIR = DATA_DIR / "companies"

# =============================================================================
# Allowed Values (Literal types for LLM tool schema)
# Generated dynamically from data files at module load time
# =============================================================================

def _load_industries_from_json() -> tuple[str, ...]:
    """Load industry names from JSON file, excluding invalid entries."""
    with open(BY_INDUSTRY) as f:
        data = json.load(f)
    # Filter out "NaN" and sort by company count (most companies first)
    industries = sorted(
        [(k, len(v)) for k, v in data.items() if k != "NaN"],
        key=lambda x: -x[1]
    )
    return tuple(k for k, _ in industries)

# Load industries at module load time
_INDUSTRIES = _load_industries_from_json()

# Create Literal type dynamically from the loaded industries
# This allows Pydantic to see the enum values in the schema
Industry = Literal[_INDUSTRIES]  # type: ignore[valid-type]

# Sort fields are static - these are the available metrics
SortField = Literal[
    "market_cap_inr",
    "trailing_pe",
    "forward_pe",
    "price_to_book",
    "roe",
    "profit_margin",
    "revenue_cagr_3yr",
    "eps_cagr_3yr",
    "debt_to_equity",
]

ProfileSection = Literal[
    "basic", "valuation", "profitability", "financial_health",
    "size", "dividends", "growth", "historical", "insights", "comparison"
]


# =============================================================================
# Internal Data Classes & Helpers (not exposed to LLM)
# =============================================================================

@dataclass
class _FilterCriteria:
    """Internal filter criteria for screening stocks."""
    pe_max: float | None = None
    pe_min: float | None = None
    forward_pe_max: float | None = None
    forward_pe_min: float | None = None
    pb_max: float | None = None
    pb_min: float | None = None
    roe_min: float | None = None
    profit_margin_min: float | None = None
    revenue_cagr_min: float | None = None
    eps_cagr_min: float | None = None
    debt_to_equity_max: float | None = None
    market_cap_min: float | None = None
    market_cap_max: float | None = None
    industry: str | None = None
    pe_percentile_max: int | None = None
    margin_percentile_min: int | None = None
    roe_percentile_min: int | None = None
    sort_by: str | None = None
    sort_order: Literal["asc", "desc"] = "desc"
    limit: int = 20


def _load_screening_data() -> list[dict[str, Any]]:
    """Load the screening summary data."""
    with open(SCREENING_SUMMARY) as f:
        data = json.load(f)
    return data.get("companies", [])


def _load_industry_index() -> dict[str, list[str]]:
    """Load industry index."""
    with open(BY_INDUSTRY) as f:
        return json.load(f)


def _load_sector_index() -> dict[str, list[str]]:
    """Load sector index (sector name -> list of symbols)."""
    with open(BY_SECTOR) as f:
        return json.load(f)


def _get_company_details(symbol: str) -> dict[str, Any] | None:
    """Load full company details from individual JSON file."""
    company_file = COMPANIES_DIR / f"{symbol}.json"
    if company_file.exists():
        with open(company_file) as f:
            return json.load(f)
    return None


def _apply_filter(value: Any, min_val: float | None, max_val: float | None) -> bool:
    """Check if value passes min/max filter. None values fail the filter."""
    if value is None:
        return False
    if min_val is not None and value < min_val:
        return False
    if max_val is not None and value > max_val:
        return False
    return True


def _screen_stocks_internal(criteria: _FilterCriteria) -> list[dict[str, Any]]:
    """Internal screening logic."""
    companies = _load_screening_data()
    results = []
    
    for company in companies:
        if not company.get("symbol"):
            continue
        
        # Industry filter
        if criteria.industry and company.get("industry") != criteria.industry:
            continue
        
        # Valuation filters
        if criteria.pe_min is not None or criteria.pe_max is not None:
            pe = company.get("trailing_pe")
            if not _apply_filter(pe, criteria.pe_min, criteria.pe_max):
                continue
        
        if criteria.forward_pe_min is not None or criteria.forward_pe_max is not None:
            fwd_pe = company.get("forward_pe")
            if not _apply_filter(fwd_pe, criteria.forward_pe_min, criteria.forward_pe_max):
                continue
        
        if criteria.pb_min is not None or criteria.pb_max is not None:
            pb = company.get("price_to_book")
            if not _apply_filter(pb, criteria.pb_min, criteria.pb_max):
                continue
        
        # Profitability filters
        if criteria.roe_min is not None:
            roe = company.get("roe")
            if not _apply_filter(roe, criteria.roe_min, None):
                continue
        
        if criteria.profit_margin_min is not None:
            margin = company.get("profit_margin")
            if not _apply_filter(margin, criteria.profit_margin_min, None):
                continue
        
        # Growth filters
        if criteria.revenue_cagr_min is not None:
            rev_cagr = company.get("revenue_cagr_3yr")
            if not _apply_filter(rev_cagr, criteria.revenue_cagr_min, None):
                continue
        
        if criteria.eps_cagr_min is not None:
            eps_cagr = company.get("eps_cagr_3yr")
            if not _apply_filter(eps_cagr, criteria.eps_cagr_min, None):
                continue
        
        # Financial health filters
        if criteria.debt_to_equity_max is not None:
            de = company.get("debt_to_equity")
            if de is not None and de > criteria.debt_to_equity_max:
                continue
        
        # Market cap filters
        if criteria.market_cap_min is not None or criteria.market_cap_max is not None:
            mcap = company.get("market_cap_inr")
            if not _apply_filter(mcap, criteria.market_cap_min, criteria.market_cap_max):
                continue
        
        # Percentile filters (for relative value screening)
        if criteria.pe_percentile_max is not None:
            pe_pct = company.get("pe_percentile")
            if pe_pct is None or pe_pct > criteria.pe_percentile_max:
                continue
        
        if criteria.margin_percentile_min is not None:
            margin_pct = company.get("margin_percentile")
            if margin_pct is None or margin_pct < criteria.margin_percentile_min:
                continue
        
        if criteria.roe_percentile_min is not None:
            roe_pct = company.get("roe_percentile")
            if roe_pct is None or roe_pct < criteria.roe_percentile_min:
                continue
        
        results.append(company)
    
    # Sort results
    if criteria.sort_by and results:
        reverse = criteria.sort_order == "desc"
        results.sort(
            key=lambda x: (x.get(criteria.sort_by) is None, x.get(criteria.sort_by) or 0),
            reverse=reverse,
        )
    
    if criteria.limit:
        results = results[:criteria.limit]
    
    return results


def _format_currency(value: float | None) -> str:
    """Format large currency values in Cr (crore) or L Cr (lakh crore)."""
    if value is None:
        return "N/A"
    cr = value / 1e7  # 1 crore = 10 million
    if cr >= 100000:
        return f"₹{cr/100000:.1f}L Cr"
    if cr >= 1000:
        return f"₹{cr/1000:.1f}K Cr"
    return f"₹{cr:.0f} Cr"


def _format_percent(value: float | None) -> str:
    """Format decimal as percentage."""
    if value is None:
        return "N/A"
    return f"{value*100:.1f}%"


def _format_ratio(value: float | None, decimals: int = 2) -> str:
    """Format ratio value."""
    if value is None:
        return "N/A"
    return f"{value:.{decimals}f}"


def _format_screening_results(results: list[dict[str, Any]], include_percentiles: bool = False) -> str:
    """Format screening results as a readable string for LLM output.
    
    Core metrics (all industries): Market Cap, P/E, Forward P/E, ROE, Profit Margin, Rev CAGR, EPS CAGR
    Industry-specific metrics:
      - Financial Services: P/B (critical for bank valuation)
      - Industrials/Utilities/Real Estate/Basic Materials: D/E, P/B
    """
    if not results:
        return "No companies matched the screening criteria."
    
    # Sectors that need capital structure metrics
    capital_intensive_sectors = {"Industrials", "Utilities", "Real Estate", "Basic Materials", "Energy"}
    
    lines = [f"Found {len(results)} companies:\n"]
    
    for i, company in enumerate(results, 1):
        symbol = company.get("symbol", "?")
        name = company.get("company_name", "")
        sector = company.get("sector", "")
        industry = company.get("industry", "")
        
        # Core metrics (all industries)
        mcap = _format_currency(company.get("market_cap_inr"))
        pe = _format_ratio(company.get("trailing_pe"))
        fwd_pe = _format_ratio(company.get("forward_pe"))
        roe = _format_percent(company.get("roe"))
        margin = _format_percent(company.get("profit_margin"))
        rev_cagr = _format_percent(company.get("revenue_cagr_3yr"))
        eps_cagr = _format_percent(company.get("eps_cagr_3yr"))
        
        # Industry-specific metrics
        pb = _format_ratio(company.get("price_to_book"))
        de = _format_ratio(company.get("debt_to_equity"))
        
        # Build output
        lines.append(f"{i}. **{symbol}** - {name}")
        lines.append(f"   Industry: {industry} | Market Cap: {mcap}")
        lines.append(f"   P/E: {pe} | Fwd P/E: {fwd_pe} | ROE: {roe} | Margin: {margin}")
        lines.append(f"   Rev CAGR: {rev_cagr} | EPS CAGR: {eps_cagr}")
        
        # Industry-specific line
        if sector == "Financial Services":
            lines.append(f"   [Financials] P/B: {pb}")
        elif sector in capital_intensive_sectors:
            lines.append(f"   [Capital-Intensive] D/E: {de} | P/B: {pb}")
        
        if include_percentiles:
            pe_pct = company.get("pe_percentile")
            margin_pct = company.get("margin_percentile")
            roe_pct = company.get("roe_percentile")
            if any(x is not None for x in [pe_pct, margin_pct, roe_pct]):
                lines.append(
                    f"   Industry Percentiles: P/E={pe_pct or 'N/A'} | Margin={margin_pct or 'N/A'} | ROE={roe_pct or 'N/A'}"
                )
        lines.append("")
    
    return "\n".join(lines)


# =============================================================================
# Section Formatters for Company Profile
# =============================================================================

def _format_basic_section(company: dict[str, Any]) -> str:
    """Format basic company info section."""
    symbol = company.get("symbol", "?")
    return "\n".join([
        f"# {company.get('company_name', symbol)} ({symbol})\n",
        f"**Sector:** {company.get('sector', 'N/A')}",
        f"**Industry:** {company.get('industry', 'N/A')}\n",
    ])


def _format_valuation_section(snapshot: dict[str, Any]) -> str:
    """Format valuation metrics section."""
    price = snapshot.get("price_metrics", {})
    lines = [
        "## Valuation\n",
        f"- P/E (TTM): {_format_ratio(price.get('trailing_pe'))}",
        f"- P/E (Forward): {_format_ratio(price.get('forward_pe'))}",
        f"- P/B: {_format_ratio(price.get('price_to_book'))}",
        f"- P/S: {_format_ratio(price.get('price_to_sales'))}",
        f"- EV/EBITDA: {_format_ratio(price.get('enterprise_to_ebitda'))}",
        f"- EV/Revenue: {_format_ratio(price.get('enterprise_to_revenue'))}\n",
    ]
    return "\n".join(lines)


def _format_profitability_section(snapshot: dict[str, Any]) -> str:
    """Format profitability metrics section."""
    profit = snapshot.get("profitability", {})
    lines = [
        "## Profitability\n",
        f"- Profit Margin: {_format_percent(profit.get('profit_margin'))}",
        f"- Gross Margin: {_format_percent(profit.get('gross_margin'))}",
        f"- Operating Margin: {_format_percent(profit.get('operating_margin'))}",
        f"- EBITDA Margin: {_format_percent(profit.get('ebitda_margin'))}",
        f"- ROE: {_format_percent(profit.get('return_on_equity'))}",
        f"- ROA: {_format_percent(profit.get('return_on_assets'))}\n",
    ]
    return "\n".join(lines)


def _format_financial_health_section(snapshot: dict[str, Any]) -> str:
    """Format financial health section."""
    health = snapshot.get("financial_health", {})
    lines = [
        "## Financial Health\n",
        f"- Debt/Equity: {_format_ratio(health.get('debt_to_equity'))}",
        f"- Current Ratio: {_format_ratio(health.get('current_ratio'))}",
        f"- Quick Ratio: {_format_ratio(health.get('quick_ratio'))}\n",
    ]
    return "\n".join(lines)


def _format_size_section(snapshot: dict[str, Any]) -> str:
    """Format size metrics section."""
    size = snapshot.get("size", {})
    employees = size.get("employees")
    emp_str = f"{int(employees):,}" if employees else "N/A"
    lines = [
        "## Size\n",
        f"- Market Cap: {_format_currency(size.get('market_cap_inr'))}",
        f"- Enterprise Value: {_format_currency(size.get('enterprise_value_inr'))}",
        f"- Revenue: {_format_currency(size.get('total_revenue_inr'))}",
        f"- Employees: {emp_str}\n",
    ]
    return "\n".join(lines)


def _format_dividends_section(snapshot: dict[str, Any]) -> str:
    """Format dividends section."""
    div = snapshot.get("dividends", {})
    rate = div.get("dividend_rate")
    rate_str = f"₹{rate:.2f}" if rate else "N/A"
    lines = [
        "## Dividends\n",
        f"- Dividend Rate: {rate_str}",
        f"- Dividend Yield: {_format_percent(div.get('dividend_yield') / 100 if div.get('dividend_yield') else None)}",
        f"- Payout Ratio: {_format_percent(div.get('payout_ratio'))}\n",
    ]
    return "\n".join(lines)


def _format_growth_section(snapshot: dict[str, Any]) -> str:
    """Format current growth metrics section."""
    growth = snapshot.get("growth", {})
    lines = [
        "## Growth (Current)\n",
        f"- Revenue Growth: {_format_percent(growth.get('revenue_growth'))}",
        f"- Earnings Growth: {_format_percent(growth.get('earnings_growth'))}",
        f"- Quarterly Earnings Growth: {_format_percent(growth.get('earnings_quarterly_growth'))}\n",
    ]
    return "\n".join(lines)


def _format_historical_section(trends: dict[str, Any]) -> str:
    """Format historical trends section."""
    lines = ["## Historical Trends\n"]
    years = trends.get("years_available", [])
    rev = trends.get("revenue", {})
    eps_data = trends.get("eps", {})
    op_margin = trends.get("operating_margin", {})
    
    # Summary CAGRs
    lines.append("**3-Year CAGRs:**")
    lines.append(f"- Revenue: {_format_percent(rev.get('cagr_3yr'))} ({rev.get('trend', 'N/A')})")
    lines.append(f"- EPS: {_format_percent(eps_data.get('cagr_3yr'))} ({eps_data.get('trend', 'N/A')})\n")
    
    # Year-by-year table
    if years and rev.get("values_inr"):
        lines.append(f"**Historical Data ({years[0]}-{years[-1]}):**\n")
        lines.append("| Year | Revenue | YoY% | EPS | Op. Margin |")
        lines.append("|------|---------|------|-----|------------|")
        
        rev_vals = rev.get("values_inr", [])
        rev_yoy = rev.get("yoy_growth", [])
        eps_vals = eps_data.get("values", [])
        margin_vals = op_margin.get("values", [])
        
        for i, year in enumerate(years):
            rev_str = _format_currency(rev_vals[i]) if i < len(rev_vals) and rev_vals[i] else "N/A"
            yoy_str = _format_percent(rev_yoy[i]) if i < len(rev_yoy) and rev_yoy[i] is not None else "-"
            eps_str = f"{eps_vals[i]:.2f}" if i < len(eps_vals) and eps_vals[i] is not None else "N/A"
            margin_str = _format_percent(margin_vals[i]) if i < len(margin_vals) and margin_vals[i] is not None else "N/A"
            lines.append(f"| {year} | {rev_str} | {yoy_str} | {eps_str} | {margin_str} |")
        lines.append("")
    
    # Additional trend metrics
    fcf_data = trends.get("free_cash_flow", {})
    roe_data = trends.get("roe", {})
    de_data = trends.get("debt_to_equity", {})
    
    if any([fcf_data.get("values_inr"), roe_data.get("values"), de_data.get("values")]):
        lines.append("**Additional Trends:**\n")
        
        if fcf_data.get("values_inr") and years:
            fcf_vals = fcf_data.get("values_inr", [])
            fcf_trend = fcf_data.get("trend", "N/A")
            positive_years = fcf_data.get("fcf_positive_years", 0)
            fcf_strs = [_format_currency(v) if v else "N/A" for v in fcf_vals]
            lines.append(f"- Free Cash Flow: {' → '.join(fcf_strs)}")
            lines.append(f"  Trend: {fcf_trend} | Positive in {positive_years}/{len(years)} years")
        
        if roe_data.get("values") and years:
            roe_vals = roe_data.get("values", [])
            roe_direction = roe_data.get("direction", "N/A")
            roe_strs = [_format_percent(v) if v else "N/A" for v in roe_vals]
            lines.append(f"- ROE: {' → '.join(roe_strs)} ({roe_direction})")
        
        if de_data.get("values") and years:
            de_vals = de_data.get("values", [])
            de_trend = de_data.get("trend", "N/A")
            de_strs = [_format_ratio(v) if v else "N/A" for v in de_vals]
            lines.append(f"- Debt/Equity: {' → '.join(de_strs)} ({de_trend})")
        
        lines.append("")
    
    return "\n".join(lines)


def _format_insights_section(insights: list[str]) -> str:
    """Format key insights section."""
    if not insights:
        return ""
    lines = ["## Key Insights\n"]
    for insight in insights:
        lines.append(f"- {insight}")
    return "\n".join(lines)


def _format_comparison_section(comparison: dict[str, Any]) -> str:
    """Format industry comparison section."""
    if not comparison or not comparison.get("metrics"):
        return ""
    
    industry = comparison.get("industry", "Unknown")
    peer_count = comparison.get("peer_count", 0)
    metrics = comparison.get("metrics", {})
    
    lines = [
        f"## Industry Comparison: {industry} ({peer_count} peers)\n",
        "_Percentile shows % of peers you outperform. Higher = better for margins/ROE, Lower = better for P/E._\n",
    ]
    
    key_metrics = [
        ("trailing_pe", "P/E", "lower is better"),
        ("profit_margin", "Profit Margin", "higher is better"),
        ("roe", "ROE", "higher is better"),
        ("debt_to_equity", "D/E", "lower is better"),
    ]
    
    for metric_key, display_name, note in key_metrics:
        if metric_key in metrics:
            m = metrics[metric_key]
            val = m.get("value")
            pct = m.get("percentile")
            vs_med = m.get("vs_median")
            
            if metric_key in ["profit_margin", "roe"]:
                val_str = _format_percent(val)
            else:
                val_str = _format_ratio(val)
            
            pct_str = f"P{pct}" if pct is not None else "N/A"
            vs_str = f"{vs_med:.2f}x median" if vs_med else ""
            
            lines.append(f"- {display_name}: {val_str} | {pct_str} | {vs_str} ({note})")
    
    return "\n".join(lines)


# =============================================================================
# LLM-CALLABLE FUNCTIONS (3 functions total)
# =============================================================================

def screen_stocks(
    # === VALUATION FILTERS (for VALUE INVESTING) ===
    pe_min: Annotated[float | None, Field(
        default=None,
        description="Minimum P/E ratio. Use to exclude loss-making companies (pe_min=1) or find high-growth expensive stocks."
    )] = None,
    pe_max: Annotated[float | None, Field(
        default=None,
        description="Maximum P/E ratio. KEY VALUE METRIC: Lower P/E = cheaper stock relative to earnings. "
                    "Use <15 for deep value, <20 for moderate value, <25 for GARP. "
                    "Warning: Very low P/E may indicate problems (cyclical/declining business)."
    )] = None,
    forward_pe_min: Annotated[float | None, Field(
        default=None,
        description="Minimum Forward P/E ratio. Uses analyst earnings estimates. Useful to find stocks "
                    "expected to have lower future earnings."
    )] = None,
    forward_pe_max: Annotated[float | None, Field(
        default=None,
        description="Maximum Forward P/E ratio. FORWARD VALUE METRIC: Uses analyst earnings estimates. "
                    "Lower forward P/E = cheaper based on expected earnings. Compare with trailing P/E "
                    "to see if earnings are expected to grow (forward < trailing) or decline."
    )] = None,
    pb_min: Annotated[float | None, Field(
        default=None,
        description="Minimum Price-to-Book ratio. Rarely used; helps find premium-priced stocks."
    )] = None,
    pb_max: Annotated[float | None, Field(
        default=None,
        description="Maximum Price-to-Book ratio. VALUE METRIC: Lower P/B = cheaper relative to book value/assets. "
                    "Use <1.5 for deep value, <3 for moderate. Banks naturally have low P/B."
    )] = None,
    
    # === PROFITABILITY FILTERS (for QUALITY INVESTING) ===
    roe_min: Annotated[float | None, Field(
        default=None,
        description="Minimum Return on Equity (decimal, e.g., 0.15 = 15%). QUALITY METRIC: High ROE = efficient "
                    "use of shareholder capital. Buffett favors ROE>15%. Use >0.15 for quality, >0.20 for high quality."
    )] = None,
    profit_margin_min: Annotated[float | None, Field(
        default=None,
        description="Minimum net profit margin (decimal, e.g., 0.10 = 10%). QUALITY METRIC: High margins = "
                    "pricing power and operational efficiency. Use >0.10 for decent, >0.15 for strong margins."
    )] = None,
    
    # === GROWTH FILTERS (for GROWTH INVESTING) ===
    revenue_cagr_min: Annotated[float | None, Field(
        default=None,
        description="Minimum 3-year revenue CAGR (decimal, e.g., 0.15 = 15%). GROWTH METRIC: High revenue growth = "
                    "expanding market share. Use >0.15 for growth stocks, >0.25 for high-growth compounders."
    )] = None,
    eps_cagr_min: Annotated[float | None, Field(
        default=None,
        description="Minimum 3-year EPS CAGR (decimal). GROWTH METRIC: High EPS growth = company converting "
                    "revenue to profit effectively. Use >0.15 for growth, >0.20 for high growth."
    )] = None,
    
    # === FINANCIAL HEALTH FILTERS (for SAFETY) ===
    debt_to_equity_max: Annotated[float | None, Field(
        default=None,
        description="Maximum Debt-to-Equity ratio. SAFETY METRIC: Lower = less financial risk. "
                    "Use <0.5 for conservative, <1.0 for moderate. Note: Banks have different capital structures."
    )] = None,
    
    # === SIZE FILTERS ===
    market_cap_min: Annotated[float | None, Field(
        default=None,
        description="Minimum market cap in INR. SIZE FILTER: Large cap >500B (>50K Cr), Mid cap >100B (>10K Cr). "
                    "Example: 500_000_000_000 for large caps only."
    )] = None,
    market_cap_max: Annotated[float | None, Field(
        default=None,
        description="Maximum market cap in INR. SIZE FILTER: Small cap <100B (<10K Cr). "
                    "Example: 100_000_000_000 for small caps only."
    )] = None,
    
    # === INDUSTRY FILTER ===
    industry: Annotated[Industry | None, Field(
        default=None,
        description="Filter by industry. Common examples: 'Banks - Regional' (26 companies), "
                    "'Information Technology Services' (20), 'Drug Manufacturers - Specialty & Generic' (30), "
                    "'Auto Parts' (20), 'Credit Services' (22), 'Steel' (16)."
    )] = None,
    
    # === RELATIVE VALUE FILTERS (vs industry peers) ===
    pe_percentile_max: Annotated[int | None, Field(
        default=None,
        description="Maximum P/E percentile vs industry peers (0-100). RELATIVE VALUE: Finds stocks cheap vs their "
                    "own industry, not absolute. Example: 30 = cheaper than 70% of industry peers."
    )] = None,
    margin_percentile_min: Annotated[int | None, Field(
        default=None,
        description="Minimum profit margin percentile vs industry (0-100). RELATIVE QUALITY: Finds companies with "
                    "better margins than peers. Example: 60 = better margins than 60% of industry peers."
    )] = None,
    roe_percentile_min: Annotated[int | None, Field(
        default=None,
        description="Minimum ROE percentile vs industry (0-100). RELATIVE QUALITY: Finds companies more profitable "
                    "than peers. Example: 70 = higher ROE than 70% of industry peers."
    )] = None,
    
    # === SORTING & PAGINATION ===
    sort_by: Annotated[SortField, Field(
        default="market_cap_inr",
        description="Field to sort results by. Use 'market_cap_inr' for size, 'trailing_pe' for valuation (cheapest), "
                    "'roe'/'profit_margin' for profitability, 'eps_cagr_3yr'/'revenue_cagr_3yr' for growth."
    )] = "market_cap_inr",
    sort_order: Annotated[Literal["asc", "desc"], Field(
        default="desc",
        description="Sort order: 'desc' (highest first) or 'asc' (lowest first). "
                    "Use 'asc' with trailing_pe to get cheapest first, or with debt_to_equity for safest first."
    )] = "desc",
    limit: Annotated[int, Field(
        default=20,
        description="Maximum number of results to return. Default 20."
    )] = 20,
) -> str:
    """
    Screen stocks using flexible filters. All filter parameters are optional - only set the ones you need.
    
    COMMON SCREENING STRATEGIES:
    - VALUE: Set pe_max=15, pb_max=2, roe_min=0.12
    - GROWTH: Set revenue_cagr_min=0.15, eps_cagr_min=0.15, profit_margin_min=0.10
    - QUALITY: Set roe_min=0.18, profit_margin_min=0.12, debt_to_equity_max=0.5
    - GARP (Growth At Reasonable Price): Set pe_max=25, revenue_cagr_min=0.15, eps_cagr_min=0.15
    - RELATIVE VALUE: Set pe_percentile_max=35, margin_percentile_min=60 (cheap vs peers but high quality)
    
    Returns a formatted list of matching companies with key metrics.
    """
    # Log active filters (non-None values only)
    active_filters = {
        k: v for k, v in {
            "pe_min": pe_min, "pe_max": pe_max, "forward_pe_min": forward_pe_min,
            "forward_pe_max": forward_pe_max, "pb_min": pb_min, "pb_max": pb_max,
            "roe_min": roe_min, "profit_margin_min": profit_margin_min,
            "revenue_cagr_min": revenue_cagr_min, "eps_cagr_min": eps_cagr_min,
            "debt_to_equity_max": debt_to_equity_max, "market_cap_min": market_cap_min,
            "market_cap_max": market_cap_max, "industry": industry,
            "pe_percentile_max": pe_percentile_max, "margin_percentile_min": margin_percentile_min,
            "roe_percentile_min": roe_percentile_min, "sort_by": sort_by, "sort_order": sort_order,
            "limit": limit,
        }.items() if v is not None and v != "market_cap_inr" and v != "desc" and v != 20
    }
    logger.info("Tool: screen_stocks called with filters=%s", active_filters)

    include_percentiles = any([pe_percentile_max, margin_percentile_min, roe_percentile_min])
    
    criteria = _FilterCriteria(
        pe_min=pe_min,
        pe_max=pe_max,
        forward_pe_min=forward_pe_min,
        forward_pe_max=forward_pe_max,
        pb_min=pb_min,
        pb_max=pb_max,
        roe_min=roe_min,
        profit_margin_min=profit_margin_min,
        revenue_cagr_min=revenue_cagr_min,
        eps_cagr_min=eps_cagr_min,
        debt_to_equity_max=debt_to_equity_max,
        market_cap_min=market_cap_min,
        market_cap_max=market_cap_max,
        industry=industry,
        pe_percentile_max=pe_percentile_max,
        margin_percentile_min=margin_percentile_min,
        roe_percentile_min=roe_percentile_min,
        sort_by=sort_by,
        sort_order=sort_order,
        limit=limit,
    )
    results = _screen_stocks_internal(criteria)
    logger.debug("Tool: screen_stocks found %d results", len(results))
    return _format_screening_results(results, include_percentiles=include_percentiles)


def get_company(
    symbol: Annotated[str, Field(
        description="Stock ticker symbol (e.g., 'RELIANCE', 'TCS', 'HDFCBANK', 'INFY', 'WIPRO'). "
                    "Use NSE ticker symbols. Case-insensitive."
    )],
    sections: Annotated[list[ProfileSection] | None, Field(
        default=None,
        description="Sections to include. Options: basic, valuation, profitability, financial_health, "
                    "size, dividends, growth, historical, insights, comparison. "
                    "None returns all sections."
    )] = None,
) -> str:
    """
    Get detailed profile for a specific company. Optionally request specific sections only.
    
    Use this to deep-dive into a specific company after screening, or to look up a company by name.
    """
    logger.info("Tool: get_company called with symbol=%s, sections=%s", symbol, sections)
    company = _get_company_details(symbol.upper())
    if not company:
        return f"Company '{symbol}' not found. Check the symbol spelling."
    
    all_sections = [
        "basic", "valuation", "profitability", "financial_health",
        "size", "dividends", "growth", "historical", "insights", "comparison"
    ]
    requested = sections if sections else all_sections
    
    snapshot = company.get("current_snapshot", {})
    trends = company.get("historical_trends", {})
    insights_data = company.get("key_insights", [])
    comparison = company.get("industry_comparison", {})
    
    parts: list[str] = []
    
    if "basic" in requested:
        parts.append(_format_basic_section(company))
    if "size" in requested:
        parts.append(_format_size_section(snapshot))
    if "valuation" in requested:
        parts.append(_format_valuation_section(snapshot))
    if "profitability" in requested:
        parts.append(_format_profitability_section(snapshot))
    if "financial_health" in requested:
        parts.append(_format_financial_health_section(snapshot))
    if "dividends" in requested:
        parts.append(_format_dividends_section(snapshot))
    if "growth" in requested:
        parts.append(_format_growth_section(snapshot))
    if "historical" in requested:
        parts.append(_format_historical_section(trends))
    if "comparison" in requested:
        section = _format_comparison_section(comparison)
        if section:
            parts.append(section)
    if "insights" in requested:
        section = _format_insights_section(insights_data)
        if section:
            parts.append(section)
    
    return "\n".join(parts)


def get_screener_metadata() -> str:
    """
    Get reference data for the screener: all available industries with company counts.
    
    Note: The industry and sort_by parameters in screen_stocks() are constrained to valid values,
    so you can also just use those directly without calling this function first.
    """
    industries = _load_industry_index()
    
    lines = ["# Stock Screener Metadata\n"]
    
    # Industries sorted by company count
    lines.append("## All Industries (by company count)\n")
    sorted_industries = sorted(industries.items(), key=lambda x: -len(x[1]))
    for industry, symbols in sorted_industries:
        if industry != "NaN":  # Skip invalid entries
            lines.append(f"- {industry}: {len(symbols)} companies")
    
    lines.append(f"\n_Total: {len(industries) - 1} industries, ~500 companies_")

    return "\n".join(lines)


def list_sectors() -> str:
    """Return all sectors with company counts as formatted text."""
    sector_index = _load_sector_index()
    lines = ["Available Sectors:\n"]
    for name, symbols in sorted(sector_index.items(), key=lambda x: -len(x[1])):
        if name != "NaN":
            lines.append(f"- {name}: {len(symbols)} companies")
    return "\n".join(lines)


def list_companies_in_industry(industry: str) -> str:
    """Return all company symbols in the given industry as formatted text."""
    industry_index = _load_industry_index()
    symbols = industry_index.get(industry, []) if industry else []
    if not symbols:
        return f"No companies found for industry '{industry}'."
    lines = [f"Companies in {industry} ({len(symbols)} total):\n"]
    lines.append(", ".join(symbols))
    return "\n".join(lines)


def get_companies(symbols: list[str], sections: list[ProfileSection] | None = None) -> str:
    """Get detailed profiles for one or more companies. Returns formatted text for each."""
    parts: list[str] = []
    for symbol in symbols:
        parts.append(get_company(symbol, sections=sections))
    return "\n\n---\n\n".join(parts)


# =============================================================================
# Quick test
# =============================================================================

if __name__ == "__main__":
    sep = "\n" + "=" * 70 + "\n"
    
    # 1. VALUE STOCKS - Bargain hunting (low P/E, low P/B, decent quality)
    print(sep)
    print("VALUE STOCKS (Low P/E, Low P/B, Good ROE, Low Debt)")
    print(sep)
    print(screen_stocks(
        pe_max=12, pb_max=1.5, roe_min=0.15, debt_to_equity_max=1.0,
        sort_by="trailing_pe", sort_order="asc"
    ))
    
    # 2. GROWTH STOCKS - High compounders
    print(sep)
    print("HIGH GROWTH COMPOUNDERS (>20% Revenue & EPS CAGR)")
    print(sep)
    print(screen_stocks(
        revenue_cagr_min=0.20, eps_cagr_min=0.20, profit_margin_min=0.10,
        sort_by="eps_cagr_3yr", sort_order="desc"
    ))
    
    # 3. QUALITY STOCKS - Best businesses
    print(sep)
    print("QUALITY STOCKS (High ROE, High Margins, Low Debt)")
    print(sep)
    print(screen_stocks(
        roe_min=0.20, profit_margin_min=0.15, debt_to_equity_max=0.5,
        sort_by="roe", sort_order="desc"
    ))
    
    # 4. GARP - Growth At Reasonable Price
    print(sep)
    print("GARP - Growth At Reasonable Price (P/E < 25, Growth > 15%)")
    print(sep)
    print(screen_stocks(
        pe_max=25, revenue_cagr_min=0.15, eps_cagr_min=0.15, roe_min=0.12,
        sort_by="eps_cagr_3yr"
    ))
    
    # 5. RELATIVE VALUE - Cheap vs industry peers but high quality
    print(sep)
    print("RELATIVE VALUE (Cheap P/E vs peers, High Margins vs peers)")
    print(sep)
    print(screen_stocks(
        pe_percentile_max=35, margin_percentile_min=60,
        sort_by="market_cap_inr"
    ))
    
    # 6. INDUSTRY SCREENING - IT Services
    print(sep)
    print("IT SERVICES INDUSTRY - Top by Market Cap")
    print(sep)
    print(screen_stocks(industry="Information Technology Services", sort_by="market_cap_inr"))
    
    # 7. COMPANY PROFILE
    print(sep)
    print("COMPANY PROFILE - TCS")
    print(sep)
    print(get_company("TCS"))
    
    # 8. METADATA
    print(sep)
    print("SCREENER METADATA")
    print(sep)
    print(get_screener_metadata())
