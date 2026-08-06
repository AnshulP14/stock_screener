"""Flat screening metrics and same-industry peer comparisons."""

from collections.abc import Callable
from typing import Any

from .statements import safe_float

COLUMN_DESCRIPTIONS = {
    "symbol": "Exchange ticker symbol.",
    "company_name": "Company name.",
    "sector": "Broad business sector.",
    "industry": "Industry used for same-market peer comparisons.",
    "currency": "Currency for absolute-value fields.",
    "snapshot_as_of": "Observation date for market-data snapshot metrics.",
    "fundamentals_fy": "Latest completed fiscal year used for annual metrics.",
    "industry_peer_count": "Other companies in the same market and industry.",
    "market_cap": "Current equity market value in the row currency.",
    "trailing_pe": "Positive current price divided by trailing earnings per share.",
    "forward_pe": "Positive current price divided by forecast earnings per share.",
    "price_to_book": "Positive current price divided by book value per share.",
    "enterprise_to_ebitda": "Positive current enterprise value divided by EBITDA.",
    "operating_margin": "Latest-fiscal-year operating income divided by revenue.",
    "roe": "Latest net income divided by average shareholder equity.",
    "roa": "Latest net income divided by average total assets.",
    "roce": "Latest EBIT divided by average capital employed.",
    "fcf_yield": "Latest annual free cash flow divided by current market cap.",
    "net_debt_to_ebitda": "Latest net debt divided by positive EBITDA.",
    "drawdown_52w": "Worst adjusted-price peak-to-trough decline over 52 weeks.",
    "nonperforming_loans_ratio": "Latest bank asset-quality ratio; definitions differ by market.",
    "cet1_ratio": "Latest reported common-equity Tier 1 capital ratio.",
    "revenue_cagr_3yr": "Revenue CAGR between the latest fiscal year and exactly three years earlier.",
    "eps_cagr_3yr": "Diluted-EPS CAGR between the latest fiscal year and exactly three years earlier.",
    "roce_avg_3yr": "Mean ROCE across the latest three consecutive fiscal years.",
    "operating_margin_change_3yr": "Operating-margin change from exactly three fiscal years earlier.",
    "fcf_positive_years_3yr": "Count of positive FCF observations in the latest three fiscal years.",
    "share_count_cagr_3yr": "Diluted-share CAGR from exactly three fiscal years earlier.",
}


def _number(value: Any, *, positive: bool = False, nonnegative: bool = False) -> float | None:
    value = safe_float(value)
    if value is None or (positive and value <= 0) or (nonnegative and value < 0):
        return None
    return value


def _snapshot(group: str, field: str, *, positive: bool = False) -> Callable[[dict], float | None]:
    return lambda company: _number(
        company.get("current_snapshot", {}).get(group, {}).get(field), positive=positive,
    )


def _years(company: dict) -> list[int]:
    years = company.get("historical_trends", {}).get("fiscal_years", [])
    return years if isinstance(years, list) else []


def _annual(company: dict, series: str, year: int | None = None) -> float | None:
    history = company.get("historical_trends", {})
    years = _years(company)
    values = history.get(series, [])
    if not years or not isinstance(values, list):
        return None
    target = years[-1] if year is None else year
    try:
        return _number(values[years.index(target)])
    except (ValueError, IndexError):
        return None


def _annual_nonnegative(series: str) -> Callable[[dict], float | None]:
    return lambda company: _number(_annual(company, series), nonnegative=True)


def _cagr(company: dict, series: str) -> float | None:
    years = _years(company)
    if not years:
        return None
    end_year = years[-1]
    start, end = _annual(company, series, end_year - 3), _annual(company, series, end_year)
    if start is None or end is None or start <= 0 or end <= 0:
        return None
    return (end / start) ** (1 / 3) - 1


def _three_year_values(company: dict, series: str) -> list[float] | None:
    years = _years(company)
    if not years:
        return None
    end_year = years[-1]
    values = [_annual(company, series, year) for year in range(end_year - 2, end_year + 1)]
    if any(value is None for value in values):
        return None
    return [value for value in values if value is not None]


def _fcf_yield(company: dict) -> float | None:
    market_cap = _snapshot("size", "market_cap", positive=True)(company)
    fcf = _annual(company, "free_cash_flow")
    return fcf / market_cap if fcf is not None and market_cap is not None else None


def _margin_change(company: dict) -> float | None:
    years = _years(company)
    if not years:
        return None
    current = _annual(company, "operating_margin")
    prior = _annual(company, "operating_margin", years[-1] - 3)
    return current - prior if current is not None and prior is not None else None


def _average_roce(company: dict) -> float | None:
    values = _three_year_values(company, "roce")
    return sum(values) / 3 if values else None


def _positive_fcf_years(company: dict) -> int | None:
    values = _three_year_values(company, "free_cash_flow")
    return sum(value > 0 for value in values) if values else None


TEXT_COLUMNS: dict[str, Callable[[dict], Any]] = {
    "symbol": lambda company: company.get("symbol", ""),
    "company_name": lambda company: company.get("company_name", ""),
    "sector": lambda company: company.get("sector", ""),
    "industry": lambda company: company.get("industry", "Unknown"),
    "currency": lambda company: company.get("currency"),
    "snapshot_as_of": lambda company: company.get("current_snapshot", {}).get("as_of"),
}

METRIC_COLUMNS: dict[str, Callable[[dict], float | int | None]] = {
    "market_cap": _snapshot("size", "market_cap", positive=True),
    "trailing_pe": _snapshot("price_metrics", "trailing_pe", positive=True),
    "forward_pe": _snapshot("price_metrics", "forward_pe", positive=True),
    "price_to_book": _snapshot("price_metrics", "price_to_book", positive=True),
    "enterprise_to_ebitda": _snapshot("price_metrics", "enterprise_to_ebitda", positive=True),
    "operating_margin": lambda company: _annual(company, "operating_margin"),
    "roe": lambda company: _annual(company, "roe"),
    "roa": lambda company: _annual(company, "roa"),
    "roce": lambda company: _annual(company, "roce"),
    "fcf_yield": _fcf_yield,
    "net_debt_to_ebitda": lambda company: _annual(company, "net_debt_to_ebitda"),
    "drawdown_52w": _snapshot("risk", "drawdown_52w"),
    "nonperforming_loans_ratio": _annual_nonnegative("nonperforming_loans_ratio"),
    "cet1_ratio": _annual_nonnegative("cet1_ratio"),
    "revenue_cagr_3yr": lambda company: _cagr(company, "revenue"),
    "eps_cagr_3yr": lambda company: _cagr(company, "diluted_eps"),
    "roce_avg_3yr": _average_roce,
    "operating_margin_change_3yr": _margin_change,
    "fcf_positive_years_3yr": _positive_fcf_years,
    "share_count_cagr_3yr": lambda company: _cagr(company, "diluted_shares"),
}

PERCENTILE_COLUMNS = {
    "trailing_pe": "pe_percentile",
    "forward_pe": "forward_pe_percentile",
    "price_to_book": "price_to_book_percentile",
    "enterprise_to_ebitda": "ev_to_ebitda_percentile",
    "operating_margin": "operating_margin_percentile",
    "roe": "roe_percentile",
    "roa": "roa_percentile",
    "roce": "roce_percentile",
    "fcf_yield": "fcf_yield_percentile",
    "net_debt_to_ebitda": "net_debt_to_ebitda_percentile",
    "revenue_cagr_3yr": "revenue_cagr_3yr_percentile",
    "eps_cagr_3yr": "eps_cagr_3yr_percentile",
    "nonperforming_loans_ratio": "nonperforming_loans_ratio_percentile",
    "cet1_ratio": "cet1_ratio_percentile",
}

for metric, percentile in PERCENTILE_COLUMNS.items():
    COLUMN_DESCRIPTIONS[percentile] = (
        f"Percentage of at least five valid same-industry peers below {metric}."
    )


def _median(values: list[float]) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    middle = len(ordered) // 2
    return ordered[middle] if len(ordered) % 2 else (ordered[middle - 1] + ordered[middle]) / 2


def _percentile(value: float | None, peer_values: list[float]) -> float | None:
    if value is None or len(peer_values) < 5:
        return None
    return 100 * sum(peer < value for peer in peer_values) / len(peer_values)


def _peer_values(company: dict, stat: dict | None) -> list[float]:
    if not stat:
        return []
    symbol = company.get("symbol")
    return [value for peer_symbol, value in stat["_values"] if peer_symbol != symbol]


def compute_industry_stats(companies: list[dict]) -> dict:
    """Compute metric distributions for each market-local industry."""
    groups: dict[str, list[dict]] = {}
    for company in companies:
        groups.setdefault(company.get("industry", "Unknown"), []).append(company)

    output = {}
    for industry, members in groups.items():
        metrics = {}
        for metric in PERCENTILE_COLUMNS:
            pairs = [
                (company.get("symbol"), value)
                for company in members
                if (value := METRIC_COLUMNS[metric](company)) is not None
            ]
            values = [value for _, value in pairs]
            if not values:
                metrics[metric] = None
                continue
            mean = sum(values) / len(values)
            ordered = sorted(values)
            metrics[metric] = {
                "median": _median(values),
                "mean": mean,
                "std": (sum((value - mean) ** 2 for value in values) / len(values)) ** 0.5,
                "p25": ordered[len(ordered) // 4],
                "p75": ordered[3 * len(ordered) // 4],
                "min": ordered[0],
                "max": ordered[-1],
                "count": len(values),
                "_values": pairs,
            }
        output[industry] = {"company_count": len(members), "metrics": metrics}
    return output


def compute_summary_row(company: dict, industry_stats: dict, *, market: str) -> dict:
    """Project one company into the shared flat NSE/S&P layout."""
    del market
    industry = company.get("industry", "Unknown")
    stats = industry_stats.get(industry, {})
    metric_stats = stats.get("metrics", {})
    row = {name: extractor(company) for name, extractor in TEXT_COLUMNS.items()}
    row["fundamentals_fy"] = _years(company)[-1] if _years(company) else None
    row["industry_peer_count"] = max(0, stats.get("company_count", 1) - 1)
    row.update({name: extractor(company) for name, extractor in METRIC_COLUMNS.items()})

    for metric, output_name in PERCENTILE_COLUMNS.items():
        row[output_name] = _percentile(row[metric], _peer_values(company, metric_stats.get(metric)))
    return row


def compute_industry_comparison(company: dict, industry_stats: dict) -> dict:
    """Retain per-metric peer context for nested company drill-downs."""
    industry = company.get("industry", "Unknown")
    stats = industry_stats.get(industry, {})
    metric_stats = stats.get("metrics", {})
    metrics = {}
    for metric in PERCENTILE_COLUMNS:
        value = METRIC_COLUMNS[metric](company)
        if value is None:
            metrics[metric] = None
            continue
        peers = _peer_values(company, metric_stats.get(metric))
        median = _median(peers)
        metrics[metric] = {
            "value": value,
            "industry_median": median,
            "percentile": _percentile(value, peers),
            "vs_median": (value - median) / abs(median) if median else None,
            "valid_peer_count": len(peers),
        }
    return {
        "industry": industry,
        "peer_count": max(0, stats.get("company_count", 1) - 1),
        "metrics": metrics,
    }
