#!/usr/bin/env python3
"""
S&P 500 Data Pipeline

Fetches and transforms fundamental data for S&P 500 companies.
  - Universe:    Wikipedia S&P 500 constituent table
  - Snapshot:    yfinance (P/E, margins, market cap, etc.)
  - History:     SEC EDGAR XBRL (revenue, EPS, cash flow from 10-K filings)
  - Ownership:   yfinance institutional_holders / major_holders

Output:
  data/us/companies/{SYMBOL}.json   — per-company profiles
  data/us/indices/                  — screening_summary, by_sector, by_industry

Usage:
    python sp500_data_pipeline.py                   # incremental (stale only)
    python sp500_data_pipeline.py --sync-universe   # update constituent list only
    python sp500_data_pipeline.py --full            # re-fetch all companies
    python sp500_data_pipeline.py --rebuild         # rebuild indices from existing JSONs
    python sp500_data_pipeline.py --symbol AAPL     # single company
"""

import argparse
import json
import logging
import math
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, date
from pathlib import Path
from typing import Any

import requests
import yfinance as yf
from bs4 import BeautifulSoup
from tqdm import tqdm

# =============================================================================
# Configuration
# =============================================================================

DATA_DIR       = Path(__file__).parent.parent / "data" / "us"
COMPANIES_DIR  = DATA_DIR / "companies"
INDICES_DIR    = DATA_DIR / "indices"
EDGAR_CACHE    = DATA_DIR / "edgar_cache"
UNIVERSE_FILE  = DATA_DIR / "sp500_universe.json"
CIK_MAP_FILE   = DATA_DIR / "cik_map.json"
LOG_FILE       = DATA_DIR / "fetch_sp500.log"

WIKIPEDIA_URL  = "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies"
EDGAR_TICKERS  = "https://www.sec.gov/files/company_tickers.json"
EDGAR_FACTS    = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik:010d}.json"

# SEC requires a descriptive User-Agent (name + email or org)
EDGAR_USER_AGENT = "sp500-screener-bot contact@example.com"
YFINANCE_USER_AGENT = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)"

MAX_WORKERS        = 5
STALE_DAYS         = 7
EDGAR_CACHE_DAYS   = 90   # re-fetch XBRL facts at most every 90 days
EDGAR_RATE_DELAY   = 0.12  # ~8 req/sec, well under SEC's 10 req/sec limit
CIK_MAP_TTL_DAYS   = 30

for d in (COMPANIES_DIR, INDICES_DIR, EDGAR_CACHE):
    d.mkdir(parents=True, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    handlers=[logging.StreamHandler(), logging.FileHandler(LOG_FILE)],
)
logger = logging.getLogger(__name__)


# =============================================================================
# Helpers
# =============================================================================

def safe_float(v: Any) -> float | None:
    try:
        f = float(v)
        return None if (math.isnan(f) or math.isinf(f)) else f
    except (TypeError, ValueError):
        return None


def _cagr(vals: list[float | None], n: int = 3) -> float | None:
    clean = [v for v in vals if v is not None and v > 0]
    if len(clean) < n + 1:
        return None
    return (clean[-1] / clean[-(n + 1)]) ** (1 / n) - 1


def _trend_direction(vals: list[float | None]) -> str:
    clean = [v for v in vals if v is not None]
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


# =============================================================================
# Universe — Wikipedia S&P 500 constituent list
# =============================================================================

def fetch_sp500_universe() -> list[dict]:
    """Scrape S&P 500 constituent table from Wikipedia."""
    r = requests.get(WIKIPEDIA_URL, headers={"User-Agent": YFINANCE_USER_AGENT}, timeout=20)
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "html.parser")
    table = soup.find("table", {"id": "constituents"})
    if not table:
        raise RuntimeError("Could not find constituents table on Wikipedia")

    companies = []
    for row in table.find_all("tr")[1:]:
        cells = row.find_all("td")
        if len(cells) < 4:
            continue
        ticker = cells[0].text.strip().replace(".", "-")  # BRK.B → BRK-B for yfinance
        companies.append({
            "symbol": ticker,
            "company_name": cells[1].text.strip(),
            "gics_sector": cells[2].text.strip(),
            "gics_industry": cells[3].text.strip(),
        })
    return companies


def sync_universe() -> tuple[list[dict], list[str], list[str]]:
    """
    Sync S&P 500 universe. Returns (current_list, added_symbols, removed_symbols).
    Saves universe to UNIVERSE_FILE.
    """
    print("Syncing S&P 500 universe from Wikipedia…")
    live = fetch_sp500_universe()
    live_syms = {c["symbol"] for c in live}

    stored: list[dict] = []
    if UNIVERSE_FILE.exists():
        with open(UNIVERSE_FILE) as f:
            stored = json.load(f).get("companies", [])
    stored_syms = {c["symbol"] for c in stored}

    added   = sorted(live_syms - stored_syms)
    removed = sorted(stored_syms - live_syms)

    if added:
        print(f"  Added {len(added)} new constituents: {', '.join(added[:10])}" +
              (f" … +{len(added)-10}" if len(added) > 10 else ""))
    if removed:
        print(f"  Removed {len(removed)} constituents: {', '.join(removed[:10])}" +
              (f" … +{len(removed)-10}" if len(removed) > 10 else ""))
    if not added and not removed:
        print("  Universe unchanged")

    universe = {
        "updated_at": date.today().isoformat(),
        "total": len(live),
        "companies": live,
    }
    with open(UNIVERSE_FILE, "w") as f:
        json.dump(universe, f, indent=2)

    return live, added, removed


def load_universe() -> list[dict]:
    if not UNIVERSE_FILE.exists():
        live, _, _ = sync_universe()
        return live
    with open(UNIVERSE_FILE) as f:
        return json.load(f).get("companies", [])


# =============================================================================
# SEC EDGAR — CIK map
# =============================================================================

def build_cik_map(force: bool = False) -> dict[str, int]:
    """
    Returns {ticker: cik}. Fetches from SEC and caches locally.
    Re-fetches if cache is older than CIK_MAP_TTL_DAYS.
    """
    if not force and CIK_MAP_FILE.exists():
        age = (datetime.now() - datetime.fromtimestamp(CIK_MAP_FILE.stat().st_mtime)).days
        if age < CIK_MAP_TTL_DAYS:
            with open(CIK_MAP_FILE) as f:
                return json.load(f)

    print("Fetching CIK map from SEC EDGAR…")
    r = requests.get(EDGAR_TICKERS, headers={"User-Agent": EDGAR_USER_AGENT}, timeout=30)
    r.raise_for_status()
    data = r.json()
    cik_map = {entry["ticker"].upper(): entry["cik_str"] for entry in data.values()}
    with open(CIK_MAP_FILE, "w") as f:
        json.dump(cik_map, f)
    print(f"  Cached {len(cik_map)} ticker→CIK mappings")
    return cik_map


# =============================================================================
# SEC EDGAR XBRL — historical financial statements
# =============================================================================

# Concept aliases: try each name in order, use first with data.
CONCEPT_MAP: dict[str, list[str]] = {
    "revenue": [
        "Revenues",
        "RevenueFromContractWithCustomerExcludingAssessedTax",
        "RevenueFromContractWithCustomerIncludingAssessedTax",
        "SalesRevenueNet",
        "SalesRevenueGoodsNet",
        "RevenuesNetOfInterestExpense",
    ],
    "net_income": [
        "NetIncomeLoss",
        "ProfitLoss",
        "NetIncomeLossAvailableToCommonStockholdersBasic",
    ],
    "eps_diluted": [
        "EarningsPerShareDiluted",
        "EarningsPerShareBasic",
    ],
    "gross_profit": [
        "GrossProfit",
    ],
    "operating_income": [
        "OperatingIncomeLoss",
    ],
    "free_cash_flow": [
        "NetCashProvidedByUsedInOperatingActivities",  # closest free proxy
    ],
    "total_assets": [
        "Assets",
    ],
    "total_debt": [
        "LongTermDebt",
        "LongTermDebtAndCapitalLeaseObligation",
        "DebtAndCapitalLeaseObligations",
    ],
    "shares_diluted": [
        "WeightedAverageNumberOfDilutedSharesOutstanding",
        "CommonStockSharesOutstanding",
    ],
}


def _fetch_edgar_facts_raw(cik: int) -> dict:
    """Fetch raw XBRL facts from SEC. Raises on HTTP error."""
    url = EDGAR_FACTS.format(cik=cik)
    r = requests.get(url, headers={"User-Agent": EDGAR_USER_AGENT}, timeout=30)
    r.raise_for_status()
    return r.json()


def fetch_edgar_facts(cik: int) -> dict | None:
    """Fetch EDGAR facts with local cache. Returns None on failure."""
    cache_path = EDGAR_CACHE / f"{cik}.json"

    if cache_path.exists():
        age = (datetime.now() - datetime.fromtimestamp(cache_path.stat().st_mtime)).days
        if age < EDGAR_CACHE_DAYS:
            with open(cache_path) as f:
                return json.load(f)

    try:
        time.sleep(EDGAR_RATE_DELAY)
        facts = _fetch_edgar_facts_raw(cik)
        with open(cache_path, "w") as f:
            json.dump(facts, f)
        return facts
    except Exception as e:
        logger.warning("EDGAR fetch failed for CIK %s: %s", cik, e)
        return None


def _extract_annual_series(facts: dict, metric: str) -> dict[int, float]:
    """
    Extract {fiscal_year: value} from XBRL facts for the given metric.
    Filters to annual (FY) 10-K filings only. Merges across all concept aliases
    so companies that changed concept names (e.g. Apple: SalesRevenueNet →
    Revenues → RevenueFromContractWithCustomer) get full coverage.
    Handles restatements by always keeping the most recently filed value per year.
    """
    gaap = facts.get("facts", {}).get("us-gaap", {})
    # Merged {fy: (filed_date, value)} across all aliases
    merged: dict[int, tuple[str, float]] = {}

    for concept in CONCEPT_MAP.get(metric, []):
        if concept not in gaap:
            continue
        units = gaap[concept].get("units", {})
        # Try all unit types: USD, shares, USD/shares (EPS)
        entries = units.get("USD") or units.get("shares") or units.get("USD/shares") or []

        for e in entries:
            if e.get("fp") != "FY" or e.get("form") not in ("10-K", "10-K405", "10-KT"):
                continue
            fy = e.get("fy")
            filed = e.get("filed", "")
            val = e.get("val")
            if fy is None or val is None:
                continue
            if fy not in merged or filed > merged[fy][0]:
                merged[fy] = (filed, float(val))

    return {fy: val for fy, (_, val) in sorted(merged.items())}


def build_historical_trends_edgar(symbol: str, cik: int | None) -> dict:
    """
    Build historical_trends dict from EDGAR XBRL.
    Falls back to empty trends if CIK unknown or fetch fails.
    """
    if cik is None:
        return {"source": "edgar_xbrl", "years_available": []}

    facts = fetch_edgar_facts(cik)
    if not facts:
        return {"source": "edgar_xbrl", "years_available": []}

    revenue        = _extract_annual_series(facts, "revenue")
    net_income     = _extract_annual_series(facts, "net_income")
    eps            = _extract_annual_series(facts, "eps_diluted")
    gross_profit   = _extract_annual_series(facts, "gross_profit")
    operating_inc  = _extract_annual_series(facts, "operating_income")
    op_cash_flow   = _extract_annual_series(facts, "free_cash_flow")

    # Last 6 fiscal years with revenue data
    all_years = sorted(revenue.keys())[-6:]
    if not all_years:
        return {"source": "edgar_xbrl", "years_available": []}

    rev_vals = [revenue.get(y) for y in all_years]
    ni_vals  = [net_income.get(y) for y in all_years]
    eps_vals = [eps.get(y) for y in all_years]
    gp_vals  = [gross_profit.get(y) for y in all_years]
    oi_vals  = [operating_inc.get(y) for y in all_years]
    cf_vals  = [op_cash_flow.get(y) for y in all_years]

    def _yoy(vals: list[float | None]) -> list[float | None]:
        out = [None]
        for a, b in zip(vals, vals[1:]):
            out.append((b - a) / abs(a) if a and b and a != 0 else None)
        return out

    # Operating margin requires both revenue and operating income
    op_margin_vals = []
    for r, o in zip(rev_vals, oi_vals):
        if r and o and r != 0:
            op_margin_vals.append(o / r)
        else:
            op_margin_vals.append(None)

    return {
        "source": "edgar_xbrl",
        "years_available": all_years,
        "revenue": {
            "values_usd": rev_vals,
            "yoy_growth": _yoy(rev_vals),
            "cagr_3yr": _cagr(rev_vals, 3),
            "trend": _trend_direction(rev_vals),
        },
        "net_income": {
            "values_usd": ni_vals,
            "cagr_3yr": _cagr(ni_vals, 3),
            "trend": _trend_direction(ni_vals),
        },
        "eps": {
            "values": eps_vals,
            "cagr_3yr": _cagr(eps_vals, 3),
            "trend": _trend_direction(eps_vals),
        },
        "gross_profit": {
            "values_usd": gp_vals,
        },
        "operating_margin": {
            "values": op_margin_vals,
        },
        "operating_cash_flow": {
            "values_usd": cf_vals,
            "positive_years": sum(1 for v in cf_vals if v and v > 0),
            "trend": _trend_direction(cf_vals),
        },
    }


# =============================================================================
# yfinance — snapshot metrics + ownership
# =============================================================================

SNAPSHOT_FIELDS = [
    # Valuation
    "trailingPE", "forwardPE", "priceToBook", "pegRatio",
    "enterpriseToEbitda", "enterpriseToRevenue", "priceToSalesTrailing12Months",
    # Profitability
    "returnOnEquity", "returnOnAssets", "profitMargins",
    "grossMargins", "operatingMargins", "ebitdaMargins",
    # Financial health
    "debtToEquity", "currentRatio", "quickRatio",
    # Size
    "marketCap", "enterpriseValue", "totalRevenue",
    # Per-share
    "trailingEps", "forwardEps", "bookValue",
    # Dividends
    "dividendRate", "dividendYield", "payoutRatio",
    # Growth (yfinance estimates)
    "revenueGrowth", "earningsGrowth", "earningsQuarterlyGrowth",
    # Other
    "freeCashflow", "ebitda", "fullTimeEmployees", "beta",
    "shortName", "longName", "sector", "industry",
]


def fetch_yfinance_snapshot(symbol: str) -> dict | None:
    """Fetch yfinance .info for a US ticker. Returns None on failure."""
    try:
        ticker = yf.Ticker(symbol)
        info = ticker.info
        if not info or info.get("trailingPE") is None and info.get("marketCap") is None:
            return None
        return info
    except Exception as e:
        logger.warning("yfinance failed for %s: %s", symbol, e)
        return None


def fetch_institutional_ownership(symbol: str) -> dict:
    """Fetch institutional/insider ownership summary from yfinance."""
    try:
        ticker = yf.Ticker(symbol)
        major = ticker.major_holders
        inst  = ticker.institutional_holders

        result: dict[str, Any] = {"updated_at": date.today().isoformat()}

        if major is not None and not major.empty:
            # Index is Breakdown name, single column is Value (0.0–1.0 fractions)
            idx = major.index.tolist()
            vals = major["Value"].tolist() if "Value" in major.columns else major.iloc[:, 0].tolist()
            lookup = {str(k).lower(): v for k, v in zip(idx, vals)}
            insider_pct = lookup.get("insiderspercentheld") or lookup.get("insiderpercentheld")
            inst_pct    = lookup.get("institutionspercentheld") or lookup.get("institutionpercentheld")
            if insider_pct is not None:
                result["pct_insider"] = safe_float(insider_pct)
            if inst_pct is not None:
                result["pct_institutional"] = safe_float(inst_pct)

        if inst is not None and not inst.empty:
            top = []
            for _, row in inst.head(5).iterrows():
                holder = str(row.get("Holder", ""))
                shares = safe_float(row.get("Shares"))
                pct    = safe_float(row.get("% Out"))
                if holder:
                    top.append({"holder": holder, "shares": shares, "pct_out": pct})
            result["top_holders"] = top

        return result
    except Exception as e:
        logger.debug("Ownership fetch failed for %s: %s", symbol, e)
        return {"updated_at": date.today().isoformat()}


def build_current_snapshot(info: dict) -> dict:
    """Build current_snapshot dict from yfinance .info."""
    return {
        "as_of": date.today().isoformat(),
        "price_metrics": {
            "trailing_pe":             safe_float(info.get("trailingPE")),
            "forward_pe":              safe_float(info.get("forwardPE")),
            "price_to_book":           safe_float(info.get("priceToBook")),
            "peg_ratio":               safe_float(info.get("pegRatio")),
            "price_to_sales":          safe_float(info.get("priceToSalesTrailing12Months")),
            "enterprise_to_ebitda":    safe_float(info.get("enterpriseToEbitda")),
            "enterprise_to_revenue":   safe_float(info.get("enterpriseToRevenue")),
        },
        "profitability": {
            "profit_margin":    safe_float(info.get("profitMargins")),
            "gross_margin":     safe_float(info.get("grossMargins")),
            "operating_margin": safe_float(info.get("operatingMargins")),
            "ebitda_margin":    safe_float(info.get("ebitdaMargins")),
            "return_on_equity": safe_float(info.get("returnOnEquity")),
            "return_on_assets": safe_float(info.get("returnOnAssets")),
        },
        "financial_health": {
            "debt_to_equity": safe_float(info.get("debtToEquity")),
            "current_ratio":  safe_float(info.get("currentRatio")),
            "quick_ratio":    safe_float(info.get("quickRatio")),
            "beta":           safe_float(info.get("beta")),
        },
        "size": {
            "market_cap_usd":      safe_float(info.get("marketCap")),
            "enterprise_value_usd": safe_float(info.get("enterpriseValue")),
            "total_revenue_usd":   safe_float(info.get("totalRevenue")),
            "employees":           info.get("fullTimeEmployees"),
        },
        "dividends": {
            "dividend_rate":  safe_float(info.get("dividendRate")),
            "dividend_yield": safe_float(info.get("dividendYield")),
            "payout_ratio":   safe_float(info.get("payoutRatio")),
        },
        "growth": {
            "revenue_growth":             safe_float(info.get("revenueGrowth")),
            "earnings_growth":            safe_float(info.get("earningsGrowth")),
            "earnings_quarterly_growth":  safe_float(info.get("earningsQuarterlyGrowth")),
        },
    }


# =============================================================================
# Insights
# =============================================================================

def generate_insights(trends: dict, snapshot: dict) -> list[str]:
    insights = []
    prof = snapshot.get("profitability", {})
    health = snapshot.get("financial_health", {})
    price = snapshot.get("price_metrics", {})

    rev = trends.get("revenue", {})
    eps = trends.get("eps", {})
    ocf = trends.get("operating_cash_flow", {})

    if rev.get("cagr_3yr") and rev["cagr_3yr"] > 0.15:
        insights.append(f"Strong revenue growth: {rev['cagr_3yr']*100:.1f}% 3yr CAGR")
    if eps.get("cagr_3yr") and eps["cagr_3yr"] > 0.15:
        insights.append(f"Strong EPS growth: {eps['cagr_3yr']*100:.1f}% 3yr CAGR")
    if prof.get("return_on_equity") and prof["return_on_equity"] > 0.20:
        insights.append(f"High ROE: {prof['return_on_equity']*100:.1f}%")
    if prof.get("profit_margin") and prof["profit_margin"] > 0.20:
        insights.append(f"High profit margin: {prof['profit_margin']*100:.1f}%")
    if health.get("debt_to_equity") is not None and health["debt_to_equity"] < 0.3:
        insights.append(f"Low leverage: D/E {health['debt_to_equity']:.2f}")
    if ocf.get("positive_years") and ocf.get("positive_years") == 6:
        insights.append("Consistently positive operating cash flow (6 years)")
    if price.get("peg_ratio") and price["peg_ratio"] < 1.0:
        insights.append(f"Potentially undervalued: PEG {price['peg_ratio']:.2f}")

    return insights


# =============================================================================
# Staleness
# =============================================================================

def is_stale(symbol: str) -> bool:
    path = COMPANIES_DIR / f"{symbol}.json"
    if not path.exists():
        return True
    try:
        with open(path) as f:
            data = json.load(f)
        as_of = data.get("current_snapshot", {}).get("as_of", "")
        if not as_of:
            return True
        return (date.today() - date.fromisoformat(as_of)).days >= STALE_DAYS
    except Exception:
        return True


# =============================================================================
# Per-company processing
# =============================================================================

def process_symbol(
    symbol: str,
    universe_entry: dict,
    cik_map: dict[str, int],
    *,
    force: bool = False,
) -> dict | None:
    """
    Fetch and build company JSON for one symbol.
    Returns the company dict on success, None on failure.
    """
    if not force and not is_stale(symbol):
        return "skipped"

    info = fetch_yfinance_snapshot(symbol)
    if not info:
        logger.warning("%s: no yfinance data", symbol)
        return None

    cik = cik_map.get(symbol)
    if cik is None:
        # Some tickers differ between Wikipedia and SEC (e.g. BRK-B vs BRK.B)
        alt = symbol.replace("-", ".")
        cik = cik_map.get(alt)

    snapshot  = build_current_snapshot(info)
    trends    = build_historical_trends_edgar(symbol, cik)
    ownership = fetch_institutional_ownership(symbol)
    insights  = generate_insights(trends, snapshot)

    company = {
        "symbol":       symbol,
        "company_name": info.get("longName") or info.get("shortName") or universe_entry.get("company_name", ""),
        "sector":       info.get("sector") or universe_entry.get("gics_sector", ""),
        "industry":     info.get("industry") or universe_entry.get("gics_industry", ""),
        "gics_sector":  universe_entry.get("gics_sector", ""),
        "gics_industry": universe_entry.get("gics_industry", ""),
        "currency":     "USD",
        "cik":          cik,
        "current_snapshot":    snapshot,
        "historical_trends":   trends,
        "institutional_ownership": ownership,
        "key_insights":        insights,
    }

    out_path = COMPANIES_DIR / f"{symbol}.json"
    with open(out_path, "w") as f:
        json.dump(company, f, indent=2)

    return company


# =============================================================================
# Indices
# =============================================================================

def build_indices() -> None:
    """Build screening_summary.json, by_sector.json, by_industry.json."""
    print("Building indices…")
    all_companies = []
    for path in sorted(COMPANIES_DIR.glob("*.json")):
        try:
            with open(path) as f:
                all_companies.append(json.load(f))
        except Exception:
            continue

    # Industry stats for percentile computation
    industry_metrics: dict[str, dict[str, list]] = {}
    for c in all_companies:
        ind = c.get("industry", "Unknown")
        snap = c.get("current_snapshot", {})
        price = snap.get("price_metrics", {})
        prof  = snap.get("profitability", {})
        if ind not in industry_metrics:
            industry_metrics[ind] = {"trailing_pe": [], "profit_margin": [], "roe": []}
        if price.get("trailing_pe") is not None:
            industry_metrics[ind]["trailing_pe"].append(price["trailing_pe"])
        if prof.get("profit_margin") is not None:
            industry_metrics[ind]["profit_margin"].append(prof["profit_margin"])
        if prof.get("return_on_equity") is not None:
            industry_metrics[ind]["roe"].append(prof["return_on_equity"])

    def _percentile(val: float | None, values: list[float]) -> int | None:
        if val is None or not values:
            return None
        return round(sum(1 for v in values if v <= val) / len(values) * 100)

    # Screening summary
    summary_entries = []
    by_sector:   dict[str, list[str]] = {}
    by_industry: dict[str, list[str]] = {}

    for c in all_companies:
        sym = c["symbol"]
        snap  = c.get("current_snapshot", {})
        price = snap.get("price_metrics", {})
        prof  = snap.get("profitability", {})
        health = snap.get("financial_health", {})
        size  = snap.get("size", {})
        trends = c.get("historical_trends", {})
        ind = c.get("industry", "Unknown")
        sector = c.get("sector", "Unknown")
        ownership = c.get("institutional_ownership", {})

        entry = {
            "symbol":           sym,
            "company_name":     c.get("company_name", ""),
            "sector":           sector,
            "industry":         ind,
            "market_cap_usd":   size.get("market_cap_usd"),
            "trailing_pe":      price.get("trailing_pe"),
            "forward_pe":       price.get("forward_pe"),
            "price_to_book":    price.get("price_to_book"),
            "roe":              prof.get("return_on_equity"),
            "profit_margin":    prof.get("profit_margin"),
            "debt_to_equity":   health.get("debt_to_equity"),
            "beta":             health.get("beta"),
            "revenue_cagr_3yr": trends.get("revenue", {}).get("cagr_3yr"),
            "eps_cagr_3yr":     trends.get("eps", {}).get("cagr_3yr"),
        }

        # Industry percentiles
        ind_m = industry_metrics.get(ind, {})
        pe_pct = _percentile(price.get("trailing_pe"), ind_m.get("trailing_pe", []))
        mg_pct = _percentile(prof.get("profit_margin"), ind_m.get("profit_margin", []))
        roe_pct = _percentile(prof.get("return_on_equity"), ind_m.get("roe", []))
        if pe_pct is not None:
            entry["pe_percentile"] = pe_pct
        if mg_pct is not None:
            entry["margin_percentile"] = mg_pct
        if roe_pct is not None:
            entry["roe_percentile"] = roe_pct

        # Institutional ownership
        pct_inst = ownership.get("pct_institutional")
        pct_ins  = ownership.get("pct_insider")
        if pct_inst is not None:
            entry["pct_institutional"] = pct_inst
        if pct_ins is not None:
            entry["pct_insider"] = pct_ins

        summary_entries.append(entry)

        by_sector.setdefault(sector, []).append(sym)
        by_industry.setdefault(ind, []).append(sym)

    with open(INDICES_DIR / "screening_summary.json", "w") as f:
        json.dump({"generated_at": datetime.now().isoformat(),
                   "total_companies": len(summary_entries),
                   "companies": summary_entries}, f, indent=2)

    with open(INDICES_DIR / "by_sector.json", "w") as f:
        json.dump({k: sorted(v) for k, v in sorted(by_sector.items())}, f, indent=2)

    with open(INDICES_DIR / "by_industry.json", "w") as f:
        json.dump({k: sorted(v) for k, v in sorted(by_industry.items())}, f, indent=2)

    print(f"  screening_summary.json: {len(summary_entries)} companies")
    print(f"  by_sector.json: {len(by_sector)} sectors")
    print(f"  by_industry.json: {len(by_industry)} industries")


# =============================================================================
# Batch fetch
# =============================================================================

def run_fetch(
    symbols: list[str],
    universe_map: dict[str, dict],
    cik_map: dict[str, int],
    *,
    force: bool = False,
    max_workers: int = MAX_WORKERS,
) -> tuple[int, int, int]:
    """Fetch and process symbols in parallel. Returns (ok, skipped, failed)."""
    ok = skipped = failed = 0

    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = {
            pool.submit(process_symbol, sym, universe_map.get(sym, {}), cik_map, force=force): sym
            for sym in symbols
        }
        with tqdm(total=len(symbols), desc="Fetching") as bar:
            for future in as_completed(futures):
                sym = futures[future]
                try:
                    result = future.result()
                    if result == "skipped":
                        skipped += 1
                    elif result is None:
                        failed += 1
                    else:
                        ok += 1
                except Exception as e:
                    logger.error("%s: unhandled error: %s", sym, e)
                    failed += 1
                bar.update(1)
                bar.set_postfix(ok=ok, skip=skipped, fail=failed)

    return ok, skipped, failed


# =============================================================================
# CLI
# =============================================================================

def main() -> None:
    parser = argparse.ArgumentParser(description="S&P 500 data pipeline")
    parser.add_argument("--sync-universe", action="store_true", help="Update constituent list only")
    parser.add_argument("--full",    action="store_true", help="Re-fetch all companies")
    parser.add_argument("--rebuild", action="store_true", help="Rebuild indices only")
    parser.add_argument("--symbol",  metavar="SYM",       help="Process a single symbol")
    parser.add_argument("--workers", type=int, default=MAX_WORKERS)
    args = parser.parse_args()

    if args.sync_universe:
        sync_universe()
        return

    if args.rebuild:
        build_indices()
        return

    universe = load_universe()
    universe_map = {c["symbol"]: c for c in universe}
    cik_map = build_cik_map()

    if args.symbol:
        symbols = [args.symbol.upper()]
    else:
        symbols = [c["symbol"] for c in universe]

    force = args.full or bool(args.symbol)

    stale = [s for s in symbols if force or is_stale(s)]
    if not stale:
        print("All companies up-to-date. Use --full to force re-fetch.")
        build_indices()
        return

    print(f"Fetching {len(stale)}/{len(symbols)} companies ({len(symbols)-len(stale)} skipped as current)…")
    ok, skipped, failed = run_fetch(stale, universe_map, cik_map, force=force, max_workers=args.workers)
    print(f"\nFetch complete — {ok} ok  {skipped} skipped  {failed} failed")

    build_indices()


if __name__ == "__main__":
    main()
