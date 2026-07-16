"""Stock screener over the local JSON data set.

Public surface (all return LLM-ready text):
    screen_stocks()           - filter stocks by any combination of metrics
    get_companies()           - detailed profiles for one or more symbols
    list_sectors()            - sectors with company counts
    list_industries()         - industries with company counts
    list_companies_in_industry()
"""

import json
import logging
from pathlib import Path
from typing import Annotated, Any, Literal

from pydantic import Field

logger = logging.getLogger(__name__)

DATA_DIR = Path(__file__).parent.parent.parent.parent / "data"
SCREENING_SUMMARY = DATA_DIR / "indices" / "screening_summary.json"
BY_SECTOR = DATA_DIR / "indices" / "by_sector.json"
BY_INDUSTRY = DATA_DIR / "indices" / "by_industry.json"
COMPANIES_DIR = DATA_DIR / "companies"


def _load(path: Path) -> Any:
    with open(path) as f:
        return json.load(f)


def _industry_names() -> tuple[str, ...]:
    """Industries by descending company count. Drives the Industry Literal below."""
    data = _load(BY_INDUSTRY)
    ranked = sorted(((k, len(v)) for k, v in data.items() if k != "NaN"), key=lambda x: -x[1])
    return tuple(k for k, _ in ranked)


# Built at import time so the LLM sees valid industries in the tool schema.
Industry = Literal[_industry_names()]  # type: ignore[valid-type]

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
    "size", "dividends", "growth", "historical", "insights", "comparison",
    "shareholding", "credit_ratings",
]

CAPITAL_INTENSIVE_SECTORS = {"Industrials", "Utilities", "Real Estate", "Basic Materials", "Energy"}

# filter arg -> (company field, bound, none_passes)
_FILTERS: dict[str, tuple[str, str, bool]] = {
    "pe_min": ("trailing_pe", "min", False),
    "pe_max": ("trailing_pe", "max", False),
    "forward_pe_min": ("forward_pe", "min", False),
    "forward_pe_max": ("forward_pe", "max", False),
    "pb_min": ("price_to_book", "min", False),
    "pb_max": ("price_to_book", "max", False),
    "roe_min": ("roe", "min", False),
    "profit_margin_min": ("profit_margin", "min", False),
    "revenue_cagr_min": ("revenue_cagr_3yr", "min", False),
    "eps_cagr_min": ("eps_cagr_3yr", "min", False),
    "market_cap_min": ("market_cap_inr", "min", False),
    "market_cap_max": ("market_cap_inr", "max", False),
    "pe_percentile_max": ("pe_percentile", "max", False),
    "margin_percentile_min": ("margin_percentile", "min", False),
    "roe_percentile_min": ("roe_percentile", "min", False),
    # Missing D/E is treated as passing: many companies legitimately report none.
    "debt_to_equity_max": ("debt_to_equity", "max", True),
}


def _passes(company: dict[str, Any], filters: dict[str, float]) -> bool:
    for arg, limit in filters.items():
        field, bound, none_passes = _FILTERS[arg]
        value = company.get(field)
        if value is None:
            if none_passes:
                continue
            return False
        if bound == "min" and value < limit:
            return False
        if bound == "max" and value > limit:
            return False
    return True


def _fmt_currency(value: float | None) -> str:
    if value is None:
        return "N/A"
    cr = value / 1e7
    if cr >= 100_000:
        return f"₹{cr/100_000:.1f}L Cr"
    if cr >= 1_000:
        return f"₹{cr/1_000:.1f}K Cr"
    return f"₹{cr:.0f} Cr"


def _fmt_pct(value: float | None) -> str:
    return "N/A" if value is None else f"{value*100:.1f}%"


def _fmt_ratio(value: float | None, decimals: int = 2) -> str:
    return "N/A" if value is None else f"{value:.{decimals}f}"


def _format_screening_results(results: list[dict[str, Any]], include_percentiles: bool) -> str:
    if not results:
        return "No companies matched the screening criteria."

    arrows = {"increasing": "↑", "decreasing": "↓"}
    lines = [f"Found {len(results)} companies:\n"]

    for i, c in enumerate(results, 1):
        sector = c.get("sector", "")
        lines.append(f"{i}. **{c.get('symbol', '?')}** - {c.get('company_name', '')}")
        lines.append(f"   Industry: {c.get('industry', '')} | Market Cap: {_fmt_currency(c.get('market_cap_inr'))}")
        lines.append(
            f"   P/E: {_fmt_ratio(c.get('trailing_pe'))} | Fwd P/E: {_fmt_ratio(c.get('forward_pe'))}"
            f" | ROE: {_fmt_pct(c.get('roe'))} | Margin: {_fmt_pct(c.get('profit_margin'))}"
        )
        lines.append(
            f"   Rev CAGR: {_fmt_pct(c.get('revenue_cagr_3yr'))} | EPS CAGR: {_fmt_pct(c.get('eps_cagr_3yr'))}"
        )

        if sector == "Financial Services":
            lines.append(f"   [Financials] P/B: {_fmt_ratio(c.get('price_to_book'))}")
        elif sector in CAPITAL_INTENSIVE_SECTORS:
            lines.append(
                f"   [Capital-Intensive] D/E: {_fmt_ratio(c.get('debt_to_equity'))}"
                f" | P/B: {_fmt_ratio(c.get('price_to_book'))}"
            )

        if include_percentiles:
            pcts = [c.get("pe_percentile"), c.get("margin_percentile"), c.get("roe_percentile")]
            if any(p is not None for p in pcts):
                pe_p, m_p, roe_p = (p if p is not None else "N/A" for p in pcts)
                lines.append(f"   Industry Percentiles: P/E={pe_p} | Margin={m_p} | ROE={roe_p}")

        promoter, fii = c.get("promoter_latest"), c.get("fii_latest")
        if promoter is not None or fii is not None:
            holding = []
            if promoter is not None:
                holding.append(f"Promoter: {promoter:.1f}%{arrows.get(c.get('promoter_trend'), '')}")
            if fii is not None:
                holding.append(f"FII: {fii:.1f}%{arrows.get(c.get('fii_trend'), '')}")
            lines.append(f"   Holding: {' | '.join(holding)}")

        lines.append("")

    return "\n".join(lines)


def screen_stocks(
    # === VALUATION FILTERS (for VALUE INVESTING) ===
    pe_min: Annotated[float | None, Field(
        description="Minimum P/E ratio. Use to exclude loss-making companies (pe_min=1) or find high-growth expensive stocks."
    )] = None,
    pe_max: Annotated[float | None, Field(
        description="Maximum P/E ratio. KEY VALUE METRIC: Lower P/E = cheaper stock relative to earnings. "
                    "Use <15 for deep value, <20 for moderate value, <25 for GARP. "
                    "Warning: Very low P/E may indicate problems (cyclical/declining business)."
    )] = None,
    forward_pe_min: Annotated[float | None, Field(
        description="Minimum Forward P/E ratio. Uses analyst earnings estimates. Useful to find stocks "
                    "expected to have lower future earnings."
    )] = None,
    forward_pe_max: Annotated[float | None, Field(
        description="Maximum Forward P/E ratio. FORWARD VALUE METRIC: Uses analyst earnings estimates. "
                    "Lower forward P/E = cheaper based on expected earnings. Compare with trailing P/E "
                    "to see if earnings are expected to grow (forward < trailing) or decline."
    )] = None,
    pb_min: Annotated[float | None, Field(
        description="Minimum Price-to-Book ratio. Rarely used; helps find premium-priced stocks."
    )] = None,
    pb_max: Annotated[float | None, Field(
        description="Maximum Price-to-Book ratio. VALUE METRIC: Lower P/B = cheaper relative to book value/assets. "
                    "Use <1.5 for deep value, <3 for moderate. Banks naturally have low P/B."
    )] = None,

    # === PROFITABILITY FILTERS (for QUALITY INVESTING) ===
    roe_min: Annotated[float | None, Field(
        description="Minimum Return on Equity (decimal, e.g., 0.15 = 15%). QUALITY METRIC: High ROE = efficient "
                    "use of shareholder capital. Buffett favors ROE>15%. Use >0.15 for quality, >0.20 for high quality."
    )] = None,
    profit_margin_min: Annotated[float | None, Field(
        description="Minimum net profit margin (decimal, e.g., 0.10 = 10%). QUALITY METRIC: High margins = "
                    "pricing power and operational efficiency. Use >0.10 for decent, >0.15 for strong margins."
    )] = None,

    # === GROWTH FILTERS (for GROWTH INVESTING) ===
    revenue_cagr_min: Annotated[float | None, Field(
        description="Minimum 3-year revenue CAGR (decimal, e.g., 0.15 = 15%). GROWTH METRIC: High revenue growth = "
                    "expanding market share. Use >0.15 for growth stocks, >0.25 for high-growth compounders."
    )] = None,
    eps_cagr_min: Annotated[float | None, Field(
        description="Minimum 3-year EPS CAGR (decimal). GROWTH METRIC: High EPS growth = company converting "
                    "revenue to profit effectively. Use >0.15 for growth, >0.20 for high growth."
    )] = None,

    # === FINANCIAL HEALTH FILTERS (for SAFETY) ===
    debt_to_equity_max: Annotated[float | None, Field(
        description="Maximum Debt-to-Equity ratio. SAFETY METRIC: Lower = less financial risk. "
                    "Use <0.5 for conservative, <1.0 for moderate. Note: Banks have different capital structures."
    )] = None,

    # === SIZE FILTERS ===
    market_cap_min: Annotated[float | None, Field(
        description="Minimum market cap in INR. SIZE FILTER: Large cap >500B (>50K Cr), Mid cap >100B (>10K Cr). "
                    "Example: 500_000_000_000 for large caps only."
    )] = None,
    market_cap_max: Annotated[float | None, Field(
        description="Maximum market cap in INR. SIZE FILTER: Small cap <100B (<10K Cr). "
                    "Example: 100_000_000_000 for small caps only."
    )] = None,

    # === INDUSTRY FILTER ===
    industry: Annotated[Industry | None, Field(
        description="Filter by industry. Common examples: 'Banks - Regional' (26 companies), "
                    "'Information Technology Services' (20), 'Drug Manufacturers - Specialty & Generic' (30), "
                    "'Auto Parts' (20), 'Credit Services' (22), 'Steel' (16)."
    )] = None,

    # === RELATIVE VALUE FILTERS (vs industry peers) ===
    pe_percentile_max: Annotated[int | None, Field(
        description="Maximum P/E percentile vs industry peers (0-100). RELATIVE VALUE: Finds stocks cheap vs their "
                    "own industry, not absolute. Example: 30 = cheaper than 70% of industry peers."
    )] = None,
    margin_percentile_min: Annotated[int | None, Field(
        description="Minimum profit margin percentile vs industry (0-100). RELATIVE QUALITY: Finds companies with "
                    "better margins than peers. Example: 60 = better margins than 60% of industry peers."
    )] = None,
    roe_percentile_min: Annotated[int | None, Field(
        description="Minimum ROE percentile vs industry (0-100). RELATIVE QUALITY: Finds companies more profitable "
                    "than peers. Example: 70 = higher ROE than 70% of industry peers."
    )] = None,

    # === SORTING & PAGINATION ===
    sort_by: Annotated[SortField, Field(
        description="Field to sort results by. Use 'market_cap_inr' for size, 'trailing_pe' for valuation (cheapest), "
                    "'roe'/'profit_margin' for profitability, 'eps_cagr_3yr'/'revenue_cagr_3yr' for growth."
    )] = "market_cap_inr",
    sort_order: Annotated[Literal["asc", "desc"], Field(
        description="Sort order: 'desc' (highest first) or 'asc' (lowest first). "
                    "Use 'asc' with trailing_pe to get cheapest first, or with debt_to_equity for safest first."
    )] = "desc",
    limit: Annotated[int, Field(description="Maximum number of results to return. Default 20.")] = 20,
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
    args = locals()
    filters = {k: v for k, v in args.items() if k in _FILTERS and v is not None}
    logger.info("Tool: screen_stocks filters=%s industry=%s sort_by=%s", filters, industry, sort_by)

    results = [
        c for c in _load(SCREENING_SUMMARY).get("companies", [])
        if c.get("symbol")
        and (not industry or c.get("industry") == industry)
        and _passes(c, filters)
    ]

    if sort_by:
        # None sorts last regardless of direction.
        results.sort(key=lambda c: (c.get(sort_by) is None, c.get(sort_by) or 0), reverse=sort_order == "desc")

    include_percentiles = any([pe_percentile_max, margin_percentile_min, roe_percentile_min])
    return _format_screening_results(results[:limit], include_percentiles)


# =============================================================================
# Company profile sections
# =============================================================================

def _basic(c: dict[str, Any]) -> str:
    symbol = c.get("symbol", "?")
    return "\n".join([
        f"# {c.get('company_name', symbol)} ({symbol})\n",
        f"**Sector:** {c.get('sector', 'N/A')}",
        f"**Industry:** {c.get('industry', 'N/A')}\n",
    ])


def _valuation(c: dict[str, Any]) -> str:
    p = c.get("current_snapshot", {}).get("price_metrics", {})
    return "\n".join([
        "## Valuation\n",
        f"- P/E (TTM): {_fmt_ratio(p.get('trailing_pe'))}",
        f"- P/E (Forward): {_fmt_ratio(p.get('forward_pe'))}",
        f"- P/B: {_fmt_ratio(p.get('price_to_book'))}",
        f"- P/S: {_fmt_ratio(p.get('price_to_sales'))}",
        f"- EV/EBITDA: {_fmt_ratio(p.get('enterprise_to_ebitda'))}",
        f"- EV/Revenue: {_fmt_ratio(p.get('enterprise_to_revenue'))}\n",
    ])


def _profitability(c: dict[str, Any]) -> str:
    p = c.get("current_snapshot", {}).get("profitability", {})
    return "\n".join([
        "## Profitability\n",
        f"- Profit Margin: {_fmt_pct(p.get('profit_margin'))}",
        f"- Gross Margin: {_fmt_pct(p.get('gross_margin'))}",
        f"- Operating Margin: {_fmt_pct(p.get('operating_margin'))}",
        f"- EBITDA Margin: {_fmt_pct(p.get('ebitda_margin'))}",
        f"- ROE: {_fmt_pct(p.get('return_on_equity'))}",
        f"- ROA: {_fmt_pct(p.get('return_on_assets'))}\n",
    ])


def _financial_health(c: dict[str, Any]) -> str:
    h = c.get("current_snapshot", {}).get("financial_health", {})
    return "\n".join([
        "## Financial Health\n",
        f"- Debt/Equity: {_fmt_ratio(h.get('debt_to_equity'))}",
        f"- Current Ratio: {_fmt_ratio(h.get('current_ratio'))}",
        f"- Quick Ratio: {_fmt_ratio(h.get('quick_ratio'))}\n",
    ])


def _size(c: dict[str, Any]) -> str:
    s = c.get("current_snapshot", {}).get("size", {})
    employees = s.get("employees")
    return "\n".join([
        "## Size\n",
        f"- Market Cap: {_fmt_currency(s.get('market_cap_inr'))}",
        f"- Enterprise Value: {_fmt_currency(s.get('enterprise_value_inr'))}",
        f"- Revenue: {_fmt_currency(s.get('total_revenue_inr'))}",
        f"- Employees: {f'{int(employees):,}' if employees else 'N/A'}\n",
    ])


def _dividends(c: dict[str, Any]) -> str:
    d = c.get("current_snapshot", {}).get("dividends", {})
    rate = d.get("dividend_rate")
    yield_ = d.get("dividend_yield")
    return "\n".join([
        "## Dividends\n",
        f"- Dividend Rate: {f'₹{rate:.2f}' if rate else 'N/A'}",
        f"- Dividend Yield: {_fmt_pct(yield_ / 100 if yield_ else None)}",
        f"- Payout Ratio: {_fmt_pct(d.get('payout_ratio'))}\n",
    ])


def _growth(c: dict[str, Any]) -> str:
    g = c.get("current_snapshot", {}).get("growth", {})
    return "\n".join([
        "## Growth (Current)\n",
        f"- Revenue Growth: {_fmt_pct(g.get('revenue_growth'))}",
        f"- Earnings Growth: {_fmt_pct(g.get('earnings_growth'))}",
        f"- Quarterly Earnings Growth: {_fmt_pct(g.get('earnings_quarterly_growth'))}\n",
    ])


def _historical(c: dict[str, Any]) -> str:
    trends = c.get("historical_trends", {})
    years = trends.get("years_available", [])
    rev = trends.get("revenue", {})
    eps = trends.get("eps", {})
    op_margin = trends.get("operating_margin", {})

    lines = [
        "## Historical Trends\n",
        "**3-Year CAGRs:**",
        f"- Revenue: {_fmt_pct(rev.get('cagr_3yr'))} ({rev.get('trend', 'N/A')})",
        f"- EPS: {_fmt_pct(eps.get('cagr_3yr'))} ({eps.get('trend', 'N/A')})\n",
    ]

    if years and rev.get("values_inr"):
        rev_vals = rev.get("values_inr", [])
        rev_yoy = rev.get("yoy_growth", [])
        eps_vals = eps.get("values", [])
        margin_vals = op_margin.get("values", [])

        def at(seq, i, fmt, blank="N/A"):
            return fmt(seq[i]) if i < len(seq) and seq[i] is not None else blank

        lines.append(f"**Historical Data ({years[0]}-{years[-1]}):**\n")
        lines.append("| Year | Revenue | YoY% | EPS | Op. Margin |")
        lines.append("|------|---------|------|-----|------------|")
        for i, year in enumerate(years):
            # A zero revenue means "not reported" in this data set, not "earned nothing".
            revenue = _fmt_currency(rev_vals[i]) if i < len(rev_vals) and rev_vals[i] else "N/A"
            lines.append(
                f"| {year} | {revenue} | {at(rev_yoy, i, _fmt_pct, blank='-')}"
                f" | {at(eps_vals, i, lambda v: f'{v:.2f}')} | {at(margin_vals, i, _fmt_pct)} |"
            )
        lines.append("")

    fcf = trends.get("free_cash_flow", {})
    roe = trends.get("roe", {})
    de = trends.get("debt_to_equity", {})

    if years and any([fcf.get("values_inr"), roe.get("values"), de.get("values")]):
        lines.append("**Additional Trends:**\n")
        if fcf.get("values_inr"):
            vals = " → ".join(_fmt_currency(v) if v else "N/A" for v in fcf["values_inr"])
            lines.append(f"- Free Cash Flow: {vals}")
            lines.append(
                f"  Trend: {fcf.get('trend', 'N/A')} |"
                f" Positive in {fcf.get('fcf_positive_years', 0)}/{len(years)} years"
            )
        if roe.get("values"):
            vals = " → ".join(_fmt_pct(v) if v else "N/A" for v in roe["values"])
            lines.append(f"- ROE: {vals} ({roe.get('direction', 'N/A')})")
        if de.get("values"):
            vals = " → ".join(_fmt_ratio(v) if v else "N/A" for v in de["values"])
            lines.append(f"- Debt/Equity: {vals} ({de.get('trend', 'N/A')})")
        lines.append("")

    return "\n".join(lines)


def _insights(c: dict[str, Any]) -> str:
    insights = c.get("key_insights", [])
    if not insights:
        return ""
    return "\n".join(["## Key Insights\n", *(f"- {i}" for i in insights)])


def _shareholding(c: dict[str, Any]) -> str:
    sh = c.get("shareholding", {})
    quarters = sh.get("quarters", [])
    if not quarters:
        return ""

    promoter = sh.get("promoter", [])
    fii = sh.get("fii", [])
    dii = sh.get("dii", [])
    public = sh.get("public", [])
    holders = sh.get("num_shareholders", [])
    trends = sh.get("trends", {})

    def pct(seq: list, i: int, decimals: int = 2) -> str:
        if not 0 <= i < len(seq) or seq[i] is None:
            return "N/A"
        return f"{seq[i]:.{decimals}f}%"

    def latest(seq: list, decimals: int = 2) -> str:
        return pct(seq, len(seq) - 1, decimals)

    lines = [f"## Shareholding Pattern (as of {sh.get('updated_at', '')})\n"]
    lines.append(
        f"**Trends (4Q):** Promoter: {trends.get('promoter', 'N/A')}"
        f" | FII: {trends.get('fii', 'N/A')} | DII: {trends.get('dii', 'N/A')}\n"
    )

    if promoter:
        lines.append(f"**Latest (as of {quarters[-1]}):**")
        lines.append(f"- Promoters: {latest(promoter)}")
        lines.append(f"- FII: {latest(fii)}")
        lines.append(f"- DII: {latest(dii)}")
        lines.append(f"- Public: {latest(public)}")
        lines.append("")

    recent_q = quarters[-8:]
    cols = [promoter[-8:], fii[-8:], dii[-8:], public[-8:]]
    recent_holders = holders[-8:] if holders else []
    show_holders = any(v is not None for v in recent_holders)

    header = "| Quarter | Promoter | FII | DII | Public |"
    sep = "|---------|----------|-----|-----|--------|"
    if show_holders:
        header += " Shareholders |"
        sep += "--------------|"
    lines += [header, sep]

    for i, q in enumerate(recent_q):
        row = f"| {q} | " + " | ".join(pct(col, i, 1) for col in cols) + " |"
        if show_holders:
            count = recent_holders[i] if i < len(recent_holders) else None
            row += f" {f'{int(count):,}' if count is not None else 'N/A'} |"
        lines.append(row)
    lines.append("")

    return "\n".join(lines)


def _credit_ratings(c: dict[str, Any]) -> str:
    ratings = c.get("credit_ratings", {})
    if not ratings:
        return ""
    if not ratings.get("has_ratings"):
        return "## Credit Ratings\n\nNo rated instruments.\n"

    entries = ratings.get("recent_entries", [])
    lines = ["## Credit Ratings\n"]
    lines.append(f"**Rated by:** {', '.join(ratings.get('agencies', []))}")
    lines.append(
        f"**Latest:** {ratings.get('latest_agency', '')} · "
        f"{ratings.get('latest_action') or '—'} · {ratings.get('latest_date', '')}"
    )

    notable = [e for e in entries if e.get("action") and e["action"] != "Reaffirmed"]
    if notable:
        summary = ", ".join(f"{e['agency']} {e['action']} ({e['date']})" for e in notable[:3])
        lines.append(f"**Notable:** {summary}")
    if entries:
        history = " | ".join(f"{e['agency']} {e.get('action') or '—'} {e['date']}" for e in entries[:5])
        lines.append(f"_History: {history}_")

    lines.append("")
    return "\n".join(lines)


def _comparison(c: dict[str, Any]) -> str:
    comp = c.get("industry_comparison", {})
    metrics = comp.get("metrics", {})
    if not metrics:
        return ""

    lines = [
        f"## Industry Comparison: {comp.get('industry', 'Unknown')} ({comp.get('peer_count', 0)} peers)\n",
        "_Percentile shows % of peers you outperform. Higher = better for margins/ROE, Lower = better for P/E._\n",
    ]
    key_metrics = [
        ("trailing_pe", "P/E", "lower is better", _fmt_ratio),
        ("profit_margin", "Profit Margin", "higher is better", _fmt_pct),
        ("roe", "ROE", "higher is better", _fmt_pct),
        ("debt_to_equity", "D/E", "lower is better", _fmt_ratio),
    ]
    for key, label, note, fmt in key_metrics:
        if key not in metrics:
            continue
        m = metrics[key]
        pct = m.get("percentile")
        vs_median = m.get("vs_median")
        lines.append(
            f"- {label}: {fmt(m.get('value'))} | {f'P{pct}' if pct is not None else 'N/A'}"
            f" | {f'{vs_median:.2f}x median' if vs_median else ''} ({note})"
        )
    return "\n".join(lines)


# Rendered in this order; sections returning "" are dropped.
_SECTIONS: dict[str, Any] = {
    "basic": _basic,
    "size": _size,
    "valuation": _valuation,
    "profitability": _profitability,
    "financial_health": _financial_health,
    "dividends": _dividends,
    "growth": _growth,
    "historical": _historical,
    "comparison": _comparison,
    "insights": _insights,
    "shareholding": _shareholding,
    "credit_ratings": _credit_ratings,
}


def _company_profile(symbol: str, sections: list[ProfileSection] | None) -> str:
    path = COMPANIES_DIR / f"{symbol.upper()}.json"
    if not path.exists():
        return f"Company '{symbol}' not found. Check the symbol spelling."

    company = _load(path)
    requested = sections or list(_SECTIONS)
    parts = [_SECTIONS[name](company) for name in _SECTIONS if name in requested]
    return "\n".join(p for p in parts if p)


def get_companies(symbols: list[str], sections: list[ProfileSection] | None = None) -> str:
    """Detailed profiles for one or more companies."""
    return "\n\n---\n\n".join(_company_profile(s, sections) for s in symbols)


def list_sectors() -> str:
    """All sectors with company counts."""
    return _counts_text("Available Sectors:", _load(BY_SECTOR))


def list_industries() -> str:
    """All industries with company counts."""
    return _counts_text("Available Industries (by company count):", _load(BY_INDUSTRY))


def _counts_text(heading: str, index: dict[str, list[str]]) -> str:
    lines = [heading + "\n"]
    for name, symbols in sorted(index.items(), key=lambda x: -len(x[1])):
        if name != "NaN":
            lines.append(f"- {name}: {len(symbols)} companies")
    return "\n".join(lines)


def list_companies_in_industry(industry: str) -> str:
    """All company symbols in the given industry."""
    symbols = _load(BY_INDUSTRY).get(industry, []) if industry else []
    if not symbols:
        return f"No companies found for industry '{industry}'."
    return f"Companies in {industry} ({len(symbols)} total):\n\n" + ", ".join(symbols)
