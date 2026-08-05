"""ScreeningSummary -- the flat screening_summary.json/{nse,snp} DB table
schema, plus the industry-percentile computation that both the flat table
and each company's own industry_comparison read from.
"""

from collections.abc import Callable
from typing import Any

from .transform import safe_float as _safe_float


def _percentile(value, sorted_values):
    if value is None or not sorted_values:
        return None
    count = sum(1 for v in sorted_values if v < value)
    return min(99, max(0, int(100 * count / len(sorted_values))))


def _metric_value(c: dict, group: str, field: str):
    """Look up a metric value from a company dict via its current_snapshot
    sub-struct (e.g. "profitability") or a historical_trends.<series> path."""
    if group.startswith("historical_trends."):
        sub = c.get("historical_trends", {}).get(group.split(".", 1)[1], {})
    else:
        sub = c.get("current_snapshot", {}).get(group, {})
    return _safe_float(sub.get(field))


# ── Flat screening_summary schema ────────────────────────────────────
# (output key, source (group, field) or extractor) -- the only place the
# flat table's column list is declared.

def _snapshot(group: str, field: str) -> Callable[[dict], Any]:
    return lambda c: c.get("current_snapshot", {}).get(group, {}).get(field)


def _trend(series: str, field: str) -> Callable[[dict], Any]:
    return lambda c: c.get("historical_trends", {}).get(series, {}).get(field)


def _ownership(field: str) -> Callable[[dict], Any]:
    return lambda c: (c.get("institutional_ownership") or {}).get(field)


# Passed through as-is (no float coercion): text fields, and cik (an integer
# identifier, not a measurement -- _safe_float would turn it into a float).
TEXT_COLUMNS: list[tuple[str, Callable[[dict], Any]]] = [
    ("symbol", lambda c: c.get("symbol", "")),
    ("company_name", lambda c: c.get("company_name", "")),
    ("sector", lambda c: c.get("sector", "")),
    ("industry", lambda c: c.get("industry", "Unknown")),
    ("currency", lambda c: c.get("currency")),
    ("cik", lambda c: c.get("cik")),
]

# Coerced through _safe_float. pct_insider/pct_institutional/cik are SNP-only
# in practice (no writer populates them for NSE) but always present as keys,
# null on the NSE side -- matching the existing promoter_*/fii_* convention
# below for the reverse case (NSE-only, null on SNP).
NUMERIC_COLUMNS: list[tuple[str, Callable[[dict], Any]]] = [
    ("market_cap", _snapshot("size", "market_cap")),
    ("trailing_pe", _snapshot("price_metrics", "trailing_pe")),
    ("forward_pe", _snapshot("price_metrics", "forward_pe")),
    ("price_to_book", _snapshot("price_metrics", "price_to_book")),
    ("roe", _snapshot("profitability", "return_on_equity")),
    ("profit_margin", _snapshot("profitability", "profit_margin")),
    ("debt_to_equity", _snapshot("financial_health", "debt_to_equity")),
    ("beta", _snapshot("financial_health", "beta")),
    ("revenue_cagr_3yr", _trend("revenue", "cagr_3yr")),
    ("net_income_cagr_3yr", _trend("net_income", "cagr_3yr")),
    ("eps_cagr_3yr", _trend("eps", "cagr_3yr")),
    ("pct_insider", _ownership("pct_insider")),
    ("pct_institutional", _ownership("pct_institutional")),
]

# (current_snapshot/historical_trends source, flat-table percentile column
# name) -- drives both the flat table's <key>_percentile columns and
# industry_stats' per-metric bands. Kept separate from industry_comparison's
# own metric names below since the flat table's names (pe, margin) predate
# this split and queries/docs already depend on them.
METRICS_FOR_PERCENTILE: list[tuple[str, str, str]] = [
    ("price_metrics", "trailing_pe", "pe"),
    ("price_metrics", "forward_pe", "forward_pe"),
    ("price_metrics", "price_to_book", "price_to_book"),
    ("profitability", "profit_margin", "margin"),
    ("profitability", "operating_margin", "operating_margin"),
    ("profitability", "return_on_equity", "roe"),
    ("profitability", "return_on_assets", "roa"),
    ("financial_health", "debt_to_equity", "debt_to_equity"),
    ("price_metrics", "enterprise_to_ebitda", "ev_to_ebitda"),
    ("historical_trends.revenue", "cagr_3yr", "revenue_cagr_3yr"),
    ("historical_trends.eps", "cagr_3yr", "eps_cagr_3yr"),
]

_SHAREHOLDING_HOLDERS = ("promoter", "fii", "dii", "public")

# industry_comparison (data/SCHEMA.md) names two of METRICS_FOR_PERCENTILE's
# metrics differently (pe -> trailing_pe, margin -> profit_margin); everything
# else already matches. Keeping one (group, field, key) list -- rather than a
# second full copy with its own names -- is what compute_industry_comparison
# below looks up industry_stats with; a second copy previously drifted out of
# sync with the first (wrong keys, silently None metrics).
_INDUSTRY_COMPARISON_NAMES = {"pe": "trailing_pe", "margin": "profit_margin"}


def compute_industry_stats(companies: list[dict]) -> dict:
    """Per-industry percentile bands for every METRICS_FOR_PERCENTILE metric.
    A metric needs at least 2 peer values to produce stats; below that
    (common for S&P's fine-grained GICS sub-industries -- see
    compute_industry_comparison) it's None."""
    groups: dict[str, list[dict]] = {}
    for c in companies:
        groups.setdefault(c.get("industry", "Unknown"), []).append(c)

    industry_stats = {}
    for ind, group_companies in groups.items():
        metrics_stats = {}
        for group, field, key in METRICS_FOR_PERCENTILE:
            vals = [_metric_value(c, group, field) for c in group_companies]
            vals = [v for v in vals if v is not None]
            if len(vals) >= 2:
                sorted_vals = sorted(vals)
                n = len(sorted_vals)
                median = (
                    sorted_vals[n // 2] if n % 2 == 1
                    else (sorted_vals[n // 2 - 1] + sorted_vals[n // 2]) / 2
                )
                mean = sum(vals) / n
                metrics_stats[key] = {
                    "median": median,
                    "mean": mean,
                    "std": (sum((v - mean) ** 2 for v in vals) / n) ** 0.5,
                    "p25": sorted_vals[n // 4],
                    "p75": sorted_vals[3 * n // 4],
                    "min": sorted_vals[0],
                    "max": sorted_vals[-1],
                    "count": n,
                    "_values": sorted_vals,
                }
            else:
                metrics_stats[key] = None
        industry_stats[ind] = {"company_count": len(group_companies), "metrics": metrics_stats}
    return industry_stats


def compute_summary_row(company: dict, industry_stats: dict) -> dict:
    """One flat screening_summary row -- TEXT_COLUMNS/NUMERIC_COLUMNS plus
    industry percentiles and shareholding latest/trend."""
    ind = company.get("industry", "Unknown")
    ind_stats = industry_stats.get(ind, {}).get("metrics", {})

    entry = {key: extractor(company) for key, extractor in TEXT_COLUMNS}
    entry.update({key: _safe_float(extractor(company)) for key, extractor in NUMERIC_COLUMNS})

    for group, field, key in METRICS_FOR_PERCENTILE:
        val = _metric_value(company, group, field)
        stat = ind_stats.get(key)
        entry[f"{key}_percentile"] = _percentile(val, stat.get("_values")) if stat else None

    # Shareholding latest/trend (always present, null if missing). S&P profiles
    # always carry an explicit `"shareholding": null` key, so `.get(k, {})`
    # alone won't fall back -- `or {}` is needed to catch the None value too.
    sh = company.get("shareholding") or {}
    sh_trends = sh.get("trends") or {}
    for holder in _SHAREHOLDING_HOLDERS:
        vals = sh.get(holder, [])
        entry[f"{holder}_latest"] = vals[-1] if vals else None
        entry[f"{holder}_trend"] = sh_trends.get(holder)

    return entry


def compute_industry_comparison(company: dict, industry_stats: dict) -> dict:
    """A company's own percentile-vs-peers block (data/SCHEMA.md's
    industry_comparison) -- written back onto the company's own JSON by
    index.build_indices, for both markets.

    vs_median is a relative difference ((value - median) / abs(median)), not
    an absolute one, so it's comparable across metrics of very different
    scale (a PE ratio vs. a margin). A metric is None when its industry has
    fewer than 2 peer values for it (see compute_industry_stats) -- common
    for S&P's fine-grained GICS sub-industries, several of which have only
    one constituent."""
    ind = company.get("industry", "Unknown")
    stats = industry_stats.get(ind, {})
    ind_metrics = stats.get("metrics", {})

    metrics = {}
    for group, field, perc_key in METRICS_FOR_PERCENTILE:
        out_key = _INDUSTRY_COMPARISON_NAMES.get(perc_key, perc_key)
        val = _metric_value(company, group, field)
        stat = ind_metrics.get(perc_key)
        if val is None or not stat:
            metrics[out_key] = None
            continue
        median = stat["median"]
        metrics[out_key] = {
            "value": val,
            "industry_median": median,
            "percentile": _percentile(val, stat.get("_values")),
            "vs_median": (val - median) / abs(median) if median else None,
        }

    return {"industry": ind, "peer_count": stats.get("company_count", 0), "metrics": metrics}
