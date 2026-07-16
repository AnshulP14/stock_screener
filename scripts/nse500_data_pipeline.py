#!/usr/bin/env python3
"""
NSE500 Data Pipeline

Complete pipeline for fetching and transforming NSE500 fundamental data:
1. Fetch fundamental data from yfinance for all NSE500 stocks
2. Transform to per-company JSON files with pre-computed trends

Output files:
- data/nse500_current_metrics.csv - Current snapshot metrics
- data/nse500_historical_annual.csv - Aggregated annual financials
- data/companies/*.json - Per-company JSON files with trends
- data/indices/by_sector.json - Sector index
- data/indices/by_industry.json - Industry index
- data/indices/screening_summary.json - Screening summary

Usage:
    python nse500_data_pipeline.py                  # Full pipeline: fetch + transform
    python nse500_data_pipeline.py --fetch-only     # Only fetch data to CSVs
    python nse500_data_pipeline.py --transform-only # Only transform existing CSVs to JSON
    python nse500_data_pipeline.py --resume         # Resume interrupted fetch
"""

import argparse
import json
import logging
import math
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from io import StringIO
from pathlib import Path
from typing import Any

import pandas as pd
import requests
import yfinance as yf
from tqdm import tqdm

# =============================================================================
# Configuration
# =============================================================================

# Paths
DATA_DIR = Path(__file__).parent.parent / "data"
COMPANIES_DIR = DATA_DIR / "companies"
INDICES_DIR = DATA_DIR / "indices"

# Files
HISTORICAL_CSV = DATA_DIR / "nse500_historical_annual.csv"
CURRENT_CSV = DATA_DIR / "nse500_current_metrics.csv"
RAW_JSON = DATA_DIR / "nse500_quarterly_raw.json"
LOG_FILE = DATA_DIR / "fetch_nse500.log"

# API Settings
NSE500_URL = "https://nsearchives.nseindia.com/content/indices/ind_nifty500list.csv"
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
MAX_WORKERS = 3
RATE_LIMIT_DELAY = 1.0

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(LOG_FILE),
    ],
)
logger = logging.getLogger(__name__)

# =============================================================================
# Metrics Configuration
# =============================================================================

INFO_METRICS = [
    # Valuation
    "trailingPE", "forwardPE", "priceToBook", "pegRatio",
    "enterpriseToEbitda", "enterpriseToRevenue", "priceToSalesTrailing12Months",
    # Profitability
    "returnOnEquity", "returnOnAssets", "profitMargins",
    "grossMargins", "operatingMargins", "ebitdaMargins",
    # Financial Health
    "debtToEquity", "currentRatio", "quickRatio",
    "totalDebt", "totalCash", "totalCashPerShare",
    # Growth
    "revenueGrowth", "earningsGrowth", "earningsQuarterlyGrowth",
    # Other key metrics
    "marketCap", "enterpriseValue", "totalRevenue",
    "freeCashflow", "operatingCashflow", "ebitda", "grossProfits",
    "trailingEps", "forwardEps", "bookValue", "revenuePerShare",
    # Dividends
    "dividendRate", "dividendYield", "payoutRatio", "fiveYearAvgDividendYield",
    # Company info
    "sector", "industry", "fullTimeEmployees", "currency",
]

INCOME_STMT_METRICS = [
    "Total Revenue", "Gross Profit", "Operating Income", "Operating Expense",
    "Net Income", "Basic EPS", "Diluted EPS", "EBIT", "EBITDA",
    "Interest Expense", "Tax Provision", "Cost Of Revenue",
]

BALANCE_SHEET_METRICS = [
    "Total Assets", "Total Liabilities Net Minority Interest", "Stockholders Equity",
    "Total Debt", "Current Assets", "Current Liabilities", "Cash And Cash Equivalents",
    "Long Term Debt", "Short Long Term Debt", "Net Debt", "Retained Earnings",
    "Common Stock Equity", "Invested Capital", "Tangible Book Value", "Working Capital",
]

CASHFLOW_METRICS = [
    "Operating Cash Flow", "Free Cash Flow", "Capital Expenditure",
    "Investing Cash Flow", "Financing Cash Flow", "Changes In Cash",
    "Repurchase Of Capital Stock", "Cash Dividends Paid",
    "Issuance Of Debt", "Repayment Of Debt",
]


# =============================================================================
# Utility Functions
# =============================================================================

def safe_float(value: Any) -> float | None:
    """Convert value to float, returning None for invalid values."""
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return None
    try:
        result = float(value)
        if math.isnan(result) or math.isinf(result):
            return None
        return result
    except (ValueError, TypeError):
        return None


def get_fiscal_year(date: pd.Timestamp) -> int:
    """Get Indian fiscal year (April-March). FY ending March 2024 = FY2024."""
    if date.month >= 4:
        return date.year + 1
    return date.year


def serialize_dataframe(df: pd.DataFrame) -> dict | None:
    """Serialize DataFrame to dict for JSON storage."""
    if df is None or df.empty:
        return None
    try:
        result = {}
        for col in df.columns:
            col_str = str(col)
            result[col_str] = df[col].to_dict()
        return result
    except Exception:
        return None


# =============================================================================
# Phase 1: Data Fetching
# =============================================================================

def fetch_nse500_tickers() -> tuple[list[str], dict[str, dict]]:
    """Fetch NSE500 constituent tickers from NSE's official CSV."""
    logger.info("Fetching NSE500 ticker list from NSE...")
    
    headers = {"User-Agent": USER_AGENT}
    
    try:
        response = requests.get(NSE500_URL, headers=headers, timeout=30)
        response.raise_for_status()
        
        df = pd.read_csv(StringIO(response.text))
        
        nse_metadata = {}
        for _, row in df.iterrows():
            symbol = row["Symbol"]
            nse_metadata[f"{symbol}.NS"] = {
                "nse_company_name": row.get("Company Name"),
                "nse_industry": row.get("Industry"),
                "isin_code": row.get("ISIN Code"),
            }
        
        tickers = [f"{symbol}.NS" for symbol in df["Symbol"].tolist()]
        
        logger.info(f"Successfully fetched {len(tickers)} NSE500 tickers")
        return tickers, nse_metadata
        
    except requests.RequestException as e:
        logger.error(f"Failed to fetch NSE500 tickers: {e}")
        raise


def fetch_ticker_data(symbol: str) -> dict[str, Any]:
    """Fetch all fundamental data for a single ticker."""
    logger.debug(f"Fetching data for {symbol}")
    
    try:
        ticker = yf.Ticker(symbol)
        
        info = ticker.info or {}
        quarterly_income = ticker.quarterly_income_stmt
        quarterly_balance = ticker.quarterly_balance_sheet
        quarterly_cashflow = ticker.quarterly_cashflow
        annual_income = ticker.income_stmt
        annual_balance = ticker.balance_sheet
        annual_cashflow = ticker.cashflow
        price_history = ticker.history(period="5y")
        
        return {
            "symbol": symbol,
            "info": info,
            "quarterly_income": quarterly_income,
            "quarterly_balance": quarterly_balance,
            "quarterly_cashflow": quarterly_cashflow,
            "annual_income": annual_income,
            "annual_balance": annual_balance,
            "annual_cashflow": annual_cashflow,
            "price_history": price_history,
            "fetch_time": datetime.now().isoformat(),
            "error": None,
        }
        
    except Exception as e:
        logger.warning(f"Error fetching data for {symbol}: {e}")
        return {
            "symbol": symbol,
            "info": {},
            "quarterly_income": pd.DataFrame(),
            "quarterly_balance": pd.DataFrame(),
            "quarterly_cashflow": pd.DataFrame(),
            "annual_income": pd.DataFrame(),
            "annual_balance": pd.DataFrame(),
            "annual_cashflow": pd.DataFrame(),
            "price_history": pd.DataFrame(),
            "fetch_time": datetime.now().isoformat(),
            "error": str(e),
        }


def process_annual_statement(df: pd.DataFrame) -> pd.DataFrame:
    """Process annual financial statement to have fiscal years as columns."""
    if df is None or df.empty:
        return pd.DataFrame()
    
    try:
        transposed = df.T.copy()
        transposed.index = pd.to_datetime(transposed.index)
        transposed["fiscal_year"] = transposed.index.to_series().apply(get_fiscal_year)
        result = transposed.groupby("fiscal_year").last()
        return result.T
    except Exception as e:
        logger.warning(f"Error processing annual statement: {e}")
        return pd.DataFrame()


def calculate_avg_price_by_fiscal_year(price_history: pd.DataFrame) -> dict[int, dict]:
    """Calculate average, high, low prices per fiscal year."""
    if price_history is None or price_history.empty:
        return {}
    
    try:
        df = price_history.copy()
        df.index = pd.to_datetime(df.index)
        df["fiscal_year"] = df.index.to_series().apply(get_fiscal_year)
        
        result = {}
        for fy, group in df.groupby("fiscal_year"):
            result[fy] = {
                "avg_price": group["Close"].mean() if "Close" in group.columns else None,
                "high_price": group["High"].max() if "High" in group.columns else None,
                "low_price": group["Low"].min() if "Low" in group.columns else None,
                "year_end_price": group["Close"].iloc[-1] if "Close" in group.columns and len(group) > 0 else None,
                "avg_volume": group["Volume"].mean() if "Volume" in group.columns else None,
            }
        return result
    except Exception as e:
        logger.warning(f"Error calculating price stats: {e}")
        return {}


def extract_current_metrics(
    data: dict[str, Any],
    nse_metadata: dict[str, dict] | None = None,
) -> dict[str, Any]:
    """Extract current snapshot metrics from ticker info."""
    info = data.get("info", {})
    symbol = data["symbol"]
    
    metrics = {"symbol": symbol.replace(".NS", "")}
    
    if nse_metadata and symbol in nse_metadata:
        nse_info = nse_metadata[symbol]
        metrics["nse_company_name"] = nse_info.get("nse_company_name")
        metrics["nse_industry"] = nse_info.get("nse_industry")
        metrics["isin_code"] = nse_info.get("isin_code")
    else:
        metrics["nse_company_name"] = None
        metrics["nse_industry"] = None
        metrics["isin_code"] = None
    
    for metric in INFO_METRICS:
        metrics[metric] = info.get(metric)
    
    metrics["shortName"] = info.get("shortName")
    metrics["longName"] = info.get("longName")
    metrics["fetch_time"] = data.get("fetch_time")
    metrics["error"] = data.get("error")
    
    return metrics


def extract_historical_metrics(data: dict[str, Any]) -> list[dict[str, Any]]:
    """Extract historical metrics from annual financial statements."""
    symbol = data["symbol"].replace(".NS", "")
    records = []
    
    annual_income = process_annual_statement(data.get("annual_income", pd.DataFrame()))
    annual_balance = process_annual_statement(data.get("annual_balance", pd.DataFrame()))
    annual_cashflow = process_annual_statement(data.get("annual_cashflow", pd.DataFrame()))
    price_stats = calculate_avg_price_by_fiscal_year(data.get("price_history", pd.DataFrame()))
    
    all_years = set()
    for df in [annual_income, annual_balance, annual_cashflow]:
        if isinstance(df, pd.DataFrame) and not df.empty:
            all_years.update(df.columns.tolist())
    
    for fy in sorted(all_years):
        record = {"symbol": symbol, "fiscal_year": fy}
        
        fy_price = price_stats.get(fy, {})
        record["price_avg"] = fy_price.get("avg_price")
        record["price_high"] = fy_price.get("high_price")
        record["price_low"] = fy_price.get("low_price")
        record["price_year_end"] = fy_price.get("year_end_price")
        record["volume_avg"] = fy_price.get("avg_volume")
        
        for metric in INCOME_STMT_METRICS:
            try:
                if isinstance(annual_income, pd.DataFrame) and metric in annual_income.index and fy in annual_income.columns:
                    record[f"income_{metric.replace(' ', '_').lower()}"] = annual_income.loc[metric, fy]
                else:
                    record[f"income_{metric.replace(' ', '_').lower()}"] = None
            except Exception:
                record[f"income_{metric.replace(' ', '_').lower()}"] = None
        
        for metric in BALANCE_SHEET_METRICS:
            try:
                if isinstance(annual_balance, pd.DataFrame) and metric in annual_balance.index and fy in annual_balance.columns:
                    record[f"balance_{metric.replace(' ', '_').lower()}"] = annual_balance.loc[metric, fy]
                else:
                    record[f"balance_{metric.replace(' ', '_').lower()}"] = None
            except Exception:
                record[f"balance_{metric.replace(' ', '_').lower()}"] = None
        
        for metric in CASHFLOW_METRICS:
            try:
                if isinstance(annual_cashflow, pd.DataFrame) and metric in annual_cashflow.index and fy in annual_cashflow.columns:
                    record[f"cashflow_{metric.replace(' ', '_').lower()}"] = annual_cashflow.loc[metric, fy]
                else:
                    record[f"cashflow_{metric.replace(' ', '_').lower()}"] = None
            except Exception:
                record[f"cashflow_{metric.replace(' ', '_').lower()}"] = None
        
        # Derived metrics
        try:
            net_income = record.get("income_net_income")
            total_equity = record.get("balance_stockholders_equity")
            total_assets = record.get("balance_total_assets")
            total_revenue = record.get("income_total_revenue")
            gross_profit = record.get("income_gross_profit")
            operating_income = record.get("income_operating_income")
            
            record["calc_roe"] = net_income / total_equity if net_income and total_equity and total_equity != 0 else None
            record["calc_roa"] = net_income / total_assets if net_income and total_assets and total_assets != 0 else None
            record["calc_net_margin"] = net_income / total_revenue if net_income and total_revenue and total_revenue != 0 else None
            record["calc_gross_margin"] = gross_profit / total_revenue if gross_profit and total_revenue and total_revenue != 0 else None
            record["calc_operating_margin"] = operating_income / total_revenue if operating_income and total_revenue and total_revenue != 0 else None
        except Exception:
            record["calc_roe"] = None
            record["calc_roa"] = None
            record["calc_net_margin"] = None
            record["calc_gross_margin"] = None
            record["calc_operating_margin"] = None
        
        records.append(record)
    
    return records


def fetch_all_tickers(
    tickers: list[str],
    max_workers: int = MAX_WORKERS,
    delay: float = RATE_LIMIT_DELAY,
) -> tuple[list[dict], list[tuple[str, str]]]:
    """Fetch data for all tickers with concurrent processing and rate limiting."""
    results = []
    failed = []
    total = len(tickers)
    
    logger.info(f"Starting to fetch data for {total} tickers with {max_workers} workers")
    
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_to_ticker = {
            executor.submit(fetch_ticker_data, ticker): ticker
            for ticker in tickers
        }
        
        with tqdm(
            total=total,
            desc="Fetching tickers",
            unit="ticker",
            ncols=100,
            bar_format="{l_bar}{bar}| {n_fmt}/{total_fmt} [{elapsed}<{remaining}, {rate_fmt}]",
        ) as pbar:
            for future in as_completed(future_to_ticker):
                ticker = future_to_ticker[future]
                
                try:
                    result = future.result()
                    if result.get("error"):
                        failed.append((ticker, result["error"]))
                        pbar.set_postfix_str(f"Failed: {ticker.replace('.NS', '')}")
                    else:
                        results.append(result)
                        pbar.set_postfix_str(f"OK: {ticker.replace('.NS', '')}")
                except Exception as e:
                    failed.append((ticker, str(e)))
                    pbar.set_postfix_str(f"Error: {ticker.replace('.NS', '')}")
                
                pbar.update(1)
                time.sleep(delay)
    
    logger.info(f"Completed: {len(results)} successful, {len(failed)} failed")
    return results, failed


def save_fetch_results(
    results: list[dict],
    failed: list[tuple[str, str]],
    nse_metadata: dict[str, dict] | None = None,
) -> None:
    """Save fetched results to CSV and JSON files."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    
    # Current metrics CSV
    logger.info("Saving current metrics...")
    current_metrics = [extract_current_metrics(r, nse_metadata) for r in results]
    current_df = pd.DataFrame(current_metrics)
    current_df.to_csv(CURRENT_CSV, index=False)
    logger.info(f"Saved {len(current_df)} rows to {CURRENT_CSV.name}")
    
    # Historical annual CSV
    logger.info("Saving historical metrics...")
    historical_records = []
    for r in results:
        historical_records.extend(extract_historical_metrics(r))
    
    if historical_records:
        historical_df = pd.DataFrame(historical_records)
        historical_df.to_csv(HISTORICAL_CSV, index=False)
        logger.info(f"Saved {len(historical_df)} rows to {HISTORICAL_CSV.name}")
    
    # Raw quarterly JSON
    logger.info("Saving raw quarterly data...")
    raw_data = {}
    for r in results:
        symbol = r["symbol"]
        raw_data[symbol] = {
            "info": r.get("info", {}),
            "quarterly_income": serialize_dataframe(r.get("quarterly_income")),
            "quarterly_balance": serialize_dataframe(r.get("quarterly_balance")),
            "quarterly_cashflow": serialize_dataframe(r.get("quarterly_cashflow")),
            "annual_income": serialize_dataframe(r.get("annual_income")),
            "annual_balance": serialize_dataframe(r.get("annual_balance")),
            "annual_cashflow": serialize_dataframe(r.get("annual_cashflow")),
            "fetch_time": r.get("fetch_time"),
        }
    
    with open(RAW_JSON, "w") as f:
        json.dump(raw_data, f, indent=2, default=str)
    logger.info(f"Saved raw data to {RAW_JSON.name}")
    
    # Failed tickers
    if failed:
        failed_file = DATA_DIR / "failed_tickers.txt"
        with open(failed_file, "w") as f:
            for ticker, error in failed:
                f.write(f"{ticker}: {error}\n")
        logger.info(f"Saved {len(failed)} failed tickers to failed_tickers.txt")


def get_already_fetched_tickers() -> set[str]:
    """Get set of tickers already present in output files."""
    fetched = set()
    if CURRENT_CSV.exists():
        try:
            df = pd.read_csv(CURRENT_CSV)
            fetched = {f"{symbol}.NS" for symbol in df["symbol"].tolist()}
            logger.info(f"Found {len(fetched)} already fetched tickers")
        except Exception as e:
            logger.warning(f"Could not read existing data: {e}")
    return fetched


def append_fetch_results(
    results: list[dict],
    failed: list[tuple[str, str]],
    nse_metadata: dict[str, dict] | None = None,
) -> None:
    """Append new results to existing output files (for resume mode)."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    
    # Append current metrics
    current_metrics = [extract_current_metrics(r, nse_metadata) for r in results]
    new_current_df = pd.DataFrame(current_metrics)
    
    if CURRENT_CSV.exists():
        existing_df = pd.read_csv(CURRENT_CSV)
        combined_df = pd.concat([existing_df, new_current_df], ignore_index=True)
        combined_df.to_csv(CURRENT_CSV, index=False)
        logger.info(f"Appended {len(new_current_df)} rows to {CURRENT_CSV.name}")
    else:
        new_current_df.to_csv(CURRENT_CSV, index=False)
    
    # Append historical metrics
    historical_records = []
    for r in results:
        historical_records.extend(extract_historical_metrics(r))
    
    if historical_records:
        new_historical_df = pd.DataFrame(historical_records)
        if HISTORICAL_CSV.exists():
            existing_df = pd.read_csv(HISTORICAL_CSV)
            combined_df = pd.concat([existing_df, new_historical_df], ignore_index=True)
            combined_df.to_csv(HISTORICAL_CSV, index=False)
            logger.info(f"Appended {len(new_historical_df)} rows to {HISTORICAL_CSV.name}")
        else:
            new_historical_df.to_csv(HISTORICAL_CSV, index=False)
    
    # Append raw JSON
    existing_raw = {}
    if RAW_JSON.exists():
        try:
            with open(RAW_JSON) as f:
                existing_raw = json.load(f)
        except Exception:
            pass
    
    for r in results:
        symbol = r["symbol"]
        existing_raw[symbol] = {
            "info": r.get("info", {}),
            "quarterly_income": serialize_dataframe(r.get("quarterly_income")),
            "quarterly_balance": serialize_dataframe(r.get("quarterly_balance")),
            "quarterly_cashflow": serialize_dataframe(r.get("quarterly_cashflow")),
            "annual_income": serialize_dataframe(r.get("annual_income")),
            "annual_balance": serialize_dataframe(r.get("annual_balance")),
            "annual_cashflow": serialize_dataframe(r.get("annual_cashflow")),
            "fetch_time": r.get("fetch_time"),
        }
    
    with open(RAW_JSON, "w") as f:
        json.dump(existing_raw, f, indent=2, default=str)


# =============================================================================
# Phase 2: JSON Transformation
# =============================================================================

# Metrics to normalize by industry
METRICS_TO_NORMALIZE = [
    "trailing_pe",
    "forward_pe",
    "price_to_book",
    "profit_margin",
    "operating_margin",
    "roe",
    "roa",
    "debt_to_equity",
    "ev_to_ebitda",
    "revenue_cagr_3yr",
    "eps_cagr_3yr",
]


def compute_industry_statistics(companies_data: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """
    Compute statistics for each industry across all metrics.
    
    Returns dict: {industry_name: {metric_name: {median, mean, std, p25, p75, min, max, values}}}
    """
    # Group companies by industry
    industry_groups: dict[str, list[dict]] = {}
    for company in companies_data:
        industry = company.get("industry", "Unknown")
        if industry not in industry_groups:
            industry_groups[industry] = []
        industry_groups[industry].append(company)
    
    industry_stats = {}
    
    for industry, companies in industry_groups.items():
        metrics_stats = {}
        
        for metric in METRICS_TO_NORMALIZE:
            # Collect all valid values for this metric
            values = []
            for company in companies:
                val = company.get(metric)
                if val is not None and not (isinstance(val, float) and math.isnan(val)):
                    values.append(val)
            
            if len(values) >= 2:
                values_sorted = sorted(values)
                n = len(values_sorted)
                
                metrics_stats[metric] = {
                    "median": values_sorted[n // 2] if n % 2 == 1 else (values_sorted[n // 2 - 1] + values_sorted[n // 2]) / 2,
                    "mean": sum(values) / n,
                    "std": (sum((v - sum(values) / n) ** 2 for v in values) / n) ** 0.5,
                    "p25": values_sorted[n // 4],
                    "p75": values_sorted[3 * n // 4],
                    "min": values_sorted[0],
                    "max": values_sorted[-1],
                    "count": n,
                    "_values": values_sorted,  # Keep for percentile calculation
                }
            else:
                metrics_stats[metric] = None
        
        industry_stats[industry] = {
            "company_count": len(companies),
            "metrics": metrics_stats,
        }
    
    return industry_stats


def calculate_percentile(value: float, sorted_values: list[float]) -> int:
    """Calculate percentile rank of a value within a sorted list."""
    if not sorted_values:
        return 50
    
    # Count values less than the given value
    count_below = sum(1 for v in sorted_values if v < value)
    percentile = int(100 * count_below / len(sorted_values))
    return min(99, max(0, percentile))


def compute_company_industry_comparison(
    company_data: dict[str, Any],
    industry_stats: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    """
    Compute industry comparison metrics for a single company.
    
    Returns dict with percentile and vs_median for each metric.
    """
    industry = company_data.get("industry", "Unknown")
    stats = industry_stats.get(industry, {})
    metrics_stats = stats.get("metrics", {})
    
    comparison = {
        "industry": industry,
        "peer_count": stats.get("company_count", 0),
        "metrics": {},
    }
    
    for metric in METRICS_TO_NORMALIZE:
        value = company_data.get(metric)
        metric_stats = metrics_stats.get(metric)
        
        if value is None or metric_stats is None:
            continue
        
        median = metric_stats.get("median")
        sorted_values = metric_stats.get("_values", [])
        
        if median is None or median == 0:
            continue
        
        percentile = calculate_percentile(value, sorted_values)
        vs_median = round(value / median, 2) if median != 0 else None
        
        comparison["metrics"][metric] = {
            "value": round(value, 4) if isinstance(value, float) else value,
            "industry_median": round(median, 4) if isinstance(median, float) else median,
            "percentile": percentile,
            "vs_median": vs_median,
        }
    
    return comparison


def calculate_yoy_growth(values: list[float | None]) -> list[float | None]:
    """Calculate year-over-year growth rates."""
    if len(values) < 2:
        return [None] * len(values)
    
    growth = [None]
    for i in range(1, len(values)):
        prev_val = values[i - 1]
        curr_val = values[i]
        
        if prev_val is None or curr_val is None or prev_val == 0:
            growth.append(None)
        else:
            growth.append((curr_val - prev_val) / abs(prev_val))
    
    return growth


def calculate_cagr(values: list[float | None]) -> float | None:
    """Calculate Compound Annual Growth Rate."""
    valid_values = [(i, v) for i, v in enumerate(values) if v is not None and v > 0]
    
    if len(valid_values) < 2:
        return None
    
    first_idx, first_val = valid_values[0]
    last_idx, last_val = valid_values[-1]
    
    n_years = last_idx - first_idx
    if n_years <= 0 or first_val <= 0:
        return None
    
    try:
        cagr = (last_val / first_val) ** (1 / n_years) - 1
        if math.isnan(cagr) or math.isinf(cagr):
            return None
        return cagr
    except (ValueError, ZeroDivisionError):
        return None


def classify_growth_trend(cagr: float | None) -> str:
    """Classify growth trend based on CAGR."""
    if cagr is None:
        return "insufficient_data"
    if cagr > 0.20:
        return "strongly_growing"
    if cagr > 0.05:
        return "growing"
    if cagr >= -0.05:
        return "stable"
    return "declining"


def classify_margin_direction(values: list[float | None]) -> str:
    """Classify margin direction based on trend."""
    valid_values = [v for v in values if v is not None]
    if len(valid_values) < 2:
        return "insufficient_data"
    
    change = valid_values[-1] - valid_values[0]
    
    if change > 0.02:
        return "expanding"
    if change < -0.02:
        return "contracting"
    return "stable"


def classify_debt_trend(values: list[float | None]) -> str:
    """Classify debt level based on latest value."""
    valid_values = [v for v in values if v is not None]
    if not valid_values:
        return "insufficient_data"
    
    latest = valid_values[-1]
    if latest < 0.1:
        return "debt_free"
    if latest < 0.5:
        return "low"
    if latest <= 1.5:
        return "moderate"
    return "high"


def compute_trends(historical_df: pd.DataFrame, symbol: str) -> dict[str, Any]:
    """Compute all trends for a company."""
    symbol_data = historical_df[historical_df["symbol"] == symbol].copy()
    symbol_data = symbol_data.sort_values("fiscal_year")
    
    if symbol_data.empty:
        return {"years_available": [], "error": "no_historical_data"}
    
    years = symbol_data["fiscal_year"].tolist()
    
    # Extract metric series
    revenue_values = [safe_float(v) for v in symbol_data["income_total_revenue"].tolist()]
    net_income_values = [safe_float(v) for v in symbol_data["income_net_income"].tolist()]
    eps_values = [safe_float(v) for v in symbol_data["income_diluted_eps"].tolist()]
    operating_margin_values = [safe_float(v) for v in symbol_data["calc_operating_margin"].tolist()]
    roe_values = [safe_float(v) for v in symbol_data["calc_roe"].tolist()]
    fcf_values = [safe_float(v) for v in symbol_data["cashflow_free_cash_flow"].tolist()]
    
    # Compute debt/equity
    total_debt = symbol_data["balance_total_debt"].tolist()
    stockholders_equity = symbol_data["balance_stockholders_equity"].tolist()
    debt_equity_values = []
    for debt, equity in zip(total_debt, stockholders_equity):
        debt_f = safe_float(debt)
        equity_f = safe_float(equity)
        if debt_f is not None and equity_f is not None and equity_f != 0:
            debt_equity_values.append(debt_f / equity_f)
        else:
            debt_equity_values.append(None)
    
    # Calculate growth rates and CAGRs
    revenue_yoy = calculate_yoy_growth(revenue_values)
    net_income_yoy = calculate_yoy_growth(net_income_values)
    eps_yoy = calculate_yoy_growth(eps_values)
    
    revenue_cagr = calculate_cagr(revenue_values)
    net_income_cagr = calculate_cagr([v for v in net_income_values if v is None or v > 0])
    eps_cagr = calculate_cagr([v for v in eps_values if v is None or v > 0])
    
    fcf_positive_years = sum(1 for v in fcf_values if v is not None and v > 0)
    
    valid_margins = [v for v in operating_margin_values if v is not None]
    margin_change_3yr = valid_margins[-1] - valid_margins[0] if len(valid_margins) >= 2 else None
    
    valid_roe = [v for v in roe_values if v is not None]
    avg_roe = sum(valid_roe) / len(valid_roe) if valid_roe else None
    
    return {
        "years_available": [int(y) for y in years],
        "revenue": {
            "values_inr": revenue_values,
            "yoy_growth": [round(v, 4) if v is not None else None for v in revenue_yoy],
            "cagr_3yr": round(revenue_cagr, 4) if revenue_cagr is not None else None,
            "trend": classify_growth_trend(revenue_cagr),
        },
        "net_income": {
            "values_inr": net_income_values,
            "yoy_growth": [round(v, 4) if v is not None else None for v in net_income_yoy],
            "cagr_3yr": round(net_income_cagr, 4) if net_income_cagr is not None else None,
            "trend": classify_growth_trend(net_income_cagr),
        },
        "eps": {
            "values": eps_values,
            "yoy_growth": [round(v, 4) if v is not None else None for v in eps_yoy],
            "cagr_3yr": round(eps_cagr, 4) if eps_cagr is not None else None,
            "trend": classify_growth_trend(eps_cagr),
        },
        "operating_margin": {
            "values": [round(v, 4) if v is not None else None for v in operating_margin_values],
            "direction": classify_margin_direction(operating_margin_values),
            "change_3yr": round(margin_change_3yr, 4) if margin_change_3yr is not None else None,
        },
        "roe": {
            "values": [round(v, 4) if v is not None else None for v in roe_values],
            "direction": classify_margin_direction(roe_values),
            "avg_3yr": round(avg_roe, 4) if avg_roe is not None else None,
        },
        "free_cash_flow": {
            "values_inr": fcf_values,
            "trend": classify_growth_trend(calculate_cagr([v for v in fcf_values if v is None or v > 0])),
            "fcf_positive_years": fcf_positive_years,
        },
        "debt_to_equity": {
            "values": [round(v, 4) if v is not None else None for v in debt_equity_values],
            "trend": classify_debt_trend(debt_equity_values),
        },
    }


def generate_insights(trends: dict[str, Any]) -> list[str]:
    """Generate 3-5 key insights based on computed trends."""
    insights = []
    years_available = trends.get("years_available", [])
    n_years = len(years_available)
    
    if n_years < 2:
        return ["Insufficient historical data for trend analysis"]
    
    # Revenue
    revenue = trends.get("revenue", {})
    revenue_cagr = revenue.get("cagr_3yr")
    if revenue_cagr is not None:
        if revenue_cagr > 0:
            insights.append(f"Revenue CAGR of {revenue_cagr*100:.1f}% over {n_years-1} years")
        else:
            insights.append(f"Revenue declined at {abs(revenue_cagr)*100:.1f}% CAGR over {n_years-1} years")
    
    # EPS
    eps = trends.get("eps", {})
    eps_cagr = eps.get("cagr_3yr")
    eps_values = eps.get("values", [])
    valid_eps = [v for v in eps_values if v is not None and v > 0]
    if eps_cagr is not None and len(valid_eps) >= 2:
        if valid_eps[-1] > valid_eps[0] * 2:
            insights.append(f"EPS more than doubled from {valid_eps[0]:.1f} to {valid_eps[-1]:.1f}")
        elif eps_cagr > 0.15:
            insights.append(f"Strong EPS growth of {eps_cagr*100:.1f}% CAGR")
        elif eps_cagr > 0:
            insights.append(f"EPS grew at {eps_cagr*100:.1f}% CAGR")
        else:
            insights.append("EPS declined over the period")
    
    # Margin
    op_margin = trends.get("operating_margin", {})
    margin_direction = op_margin.get("direction")
    margin_change = op_margin.get("change_3yr")
    margin_values = op_margin.get("values", [])
    valid_margins = [v for v in margin_values if v is not None]
    
    if margin_direction == "expanding" and margin_change is not None:
        insights.append(f"Operating margin expanded by {margin_change*100:.1f} percentage points")
    elif margin_direction == "contracting" and margin_change is not None:
        insights.append(f"Operating margin contracted by {abs(margin_change)*100:.1f} percentage points")
    elif valid_margins:
        insights.append(f"Operating margin stable around {sum(valid_margins)/len(valid_margins)*100:.1f}%")
    
    # Cash flow
    fcf = trends.get("free_cash_flow", {})
    fcf_positive = fcf.get("fcf_positive_years", 0)
    if fcf_positive == n_years:
        insights.append(f"Positive free cash flow in all {n_years} years")
    elif fcf_positive > n_years // 2:
        insights.append(f"Positive free cash flow in {fcf_positive} of {n_years} years")
    
    # Debt
    debt = trends.get("debt_to_equity", {})
    debt_trend = debt.get("trend")
    debt_values = debt.get("values", [])
    valid_debt = [v for v in debt_values if v is not None]
    
    if debt_trend == "debt_free":
        insights.append("Virtually debt-free balance sheet")
    elif debt_trend == "low" and valid_debt:
        insights.append(f"Low leverage with debt/equity of {valid_debt[-1]:.2f}")
    elif debt_trend == "high" and valid_debt:
        insights.append(f"High leverage with debt/equity of {valid_debt[-1]:.2f}")
    
    # ROE
    roe = trends.get("roe", {})
    avg_roe = roe.get("avg_3yr")
    if avg_roe is not None:
        if avg_roe > 0.20:
            insights.append(f"Strong average ROE of {avg_roe*100:.1f}%")
        elif avg_roe > 0.15:
            insights.append(f"Good average ROE of {avg_roe*100:.1f}%")
    
    return insights[:5]


def build_current_snapshot(current_row: pd.Series) -> dict[str, Any]:
    """Build current snapshot from current metrics row."""
    fetch_time = current_row.get("fetch_time", "")
    as_of = fetch_time.split("T")[0] if fetch_time else datetime.now().strftime("%Y-%m-%d")
    
    return {
        "as_of": as_of,
        "price_metrics": {
            "trailing_pe": safe_float(current_row.get("trailingPE")),
            "forward_pe": safe_float(current_row.get("forwardPE")),
            "price_to_book": safe_float(current_row.get("priceToBook")),
            "peg_ratio": safe_float(current_row.get("pegRatio")),
            "price_to_sales": safe_float(current_row.get("priceToSalesTrailing12Months")),
            "enterprise_to_ebitda": safe_float(current_row.get("enterpriseToEbitda")),
            "enterprise_to_revenue": safe_float(current_row.get("enterpriseToRevenue")),
        },
        "profitability": {
            "profit_margin": safe_float(current_row.get("profitMargins")),
            "gross_margin": safe_float(current_row.get("grossMargins")),
            "operating_margin": safe_float(current_row.get("operatingMargins")),
            "ebitda_margin": safe_float(current_row.get("ebitdaMargins")),
            "return_on_equity": safe_float(current_row.get("returnOnEquity")),
            "return_on_assets": safe_float(current_row.get("returnOnAssets")),
        },
        "financial_health": {
            "debt_to_equity": safe_float(current_row.get("debtToEquity")),
            "current_ratio": safe_float(current_row.get("currentRatio")),
            "quick_ratio": safe_float(current_row.get("quickRatio")),
        },
        "size": {
            "market_cap_inr": safe_float(current_row.get("marketCap")),
            "enterprise_value_inr": safe_float(current_row.get("enterpriseValue")),
            "total_revenue_inr": safe_float(current_row.get("totalRevenue")),
            "employees": safe_float(current_row.get("fullTimeEmployees")),
        },
        "per_share": {
            "trailing_eps": safe_float(current_row.get("trailingEps")),
            "forward_eps": safe_float(current_row.get("forwardEps")),
            "book_value": safe_float(current_row.get("bookValue")),
            "revenue_per_share": safe_float(current_row.get("revenuePerShare")),
        },
        "dividends": {
            "dividend_rate": safe_float(current_row.get("dividendRate")),
            "dividend_yield": safe_float(current_row.get("dividendYield")),
            "payout_ratio": safe_float(current_row.get("payoutRatio")),
        },
        "growth": {
            "revenue_growth": safe_float(current_row.get("revenueGrowth")),
            "earnings_growth": safe_float(current_row.get("earningsGrowth")),
            "earnings_quarterly_growth": safe_float(current_row.get("earningsQuarterlyGrowth")),
        },
    }


def transform_to_json() -> None:
    """Transform CSV data to per-company JSON files."""
    print("\n" + "=" * 60)
    print("Phase 2: Transforming to JSON")
    print("=" * 60)
    
    # Load CSVs
    print("Loading CSV files...")
    historical_df = pd.read_csv(HISTORICAL_CSV)
    current_df = pd.read_csv(CURRENT_CSV)
    print(f"  Historical: {len(historical_df)} rows, {len(historical_df['symbol'].unique())} symbols")
    print(f"  Current: {len(current_df)} rows")
    
    # Create output directories
    COMPANIES_DIR.mkdir(parents=True, exist_ok=True)
    INDICES_DIR.mkdir(parents=True, exist_ok=True)
    
    # ==========================================================================
    # Pass 1: Collect all company data for industry statistics
    # ==========================================================================
    print("\nPass 1: Collecting metrics for industry statistics...")
    all_companies_data = []
    symbols = current_df["symbol"].unique()
    
    for symbol in symbols:
        current_row = current_df[current_df["symbol"] == symbol].iloc[0]
        trends = compute_trends(historical_df, symbol)
        
        # Collect metrics needed for industry comparison
        company_metrics = {
            "symbol": symbol,
            "industry": current_row.get("industry", "Unknown"),
            "sector": current_row.get("sector", "Unknown"),
            # Valuation metrics
            "trailing_pe": safe_float(current_row.get("trailingPE")),
            "forward_pe": safe_float(current_row.get("forwardPE")),
            "price_to_book": safe_float(current_row.get("priceToBook")),
            "ev_to_ebitda": safe_float(current_row.get("enterpriseToEbitda")),
            # Profitability metrics
            "profit_margin": safe_float(current_row.get("profitMargins")),
            "operating_margin": safe_float(current_row.get("operatingMargins")),
            "roe": safe_float(current_row.get("returnOnEquity")),
            "roa": safe_float(current_row.get("returnOnAssets")),
            # Financial health
            "debt_to_equity": safe_float(current_row.get("debtToEquity")),
            # Growth metrics
            "revenue_cagr_3yr": trends.get("revenue", {}).get("cagr_3yr"),
            "eps_cagr_3yr": trends.get("eps", {}).get("cagr_3yr"),
            # Store trends for later use
            "_trends": trends,
            "_current_row": current_row,
        }
        all_companies_data.append(company_metrics)
    
    print(f"  Collected metrics for {len(all_companies_data)} companies")
    
    # ==========================================================================
    # Compute industry statistics
    # ==========================================================================
    print("\nComputing industry statistics...")
    industry_stats = compute_industry_statistics(all_companies_data)
    print(f"  Computed statistics for {len(industry_stats)} industries")
    
    # ==========================================================================
    # Pass 2: Generate company JSONs with industry comparison
    # ==========================================================================
    print("\nPass 2: Generating company JSONs with industry comparison...")
    companies_summary = []
    
    for i, company_data in enumerate(all_companies_data):
        symbol = company_data["symbol"]
        current_row = company_data["_current_row"]
        trends = company_data["_trends"]
        
        # Build company JSON
        company_json = {
            "symbol": symbol,
            "company_name": current_row.get("nse_company_name", ""),
            "sector": current_row.get("sector", ""),
            "industry": current_row.get("industry", ""),
            "isin": current_row.get("isin_code", ""),
            "nse_industry": current_row.get("nse_industry", ""),
        }
        
        company_json["current_snapshot"] = build_current_snapshot(current_row)
        company_json["historical_trends"] = trends
        company_json["key_insights"] = generate_insights(trends)
        
        # Add industry comparison
        industry_comparison = compute_company_industry_comparison(company_data, industry_stats)
        company_json["industry_comparison"] = industry_comparison
        
        # Preserve enrichment fields managed by separate scripts
        output_path = COMPANIES_DIR / f"{symbol}.json"
        if output_path.exists():
            try:
                with open(output_path) as _ef:
                    _existing = json.load(_ef)
                for _key in ("shareholding", "credit_ratings"):
                    if _key in _existing:
                        company_json[_key] = _existing[_key]
            except Exception:
                pass

        with open(output_path, "w") as f:
            json.dump(company_json, f, indent=2)
        
        # Build summary entry with normalized metrics
        summary_entry = {
            "symbol": symbol,
            "company_name": company_json.get("company_name", ""),
            "sector": company_json.get("sector", ""),
            "industry": company_json.get("industry", ""),
            "market_cap_inr": safe_float(current_row.get("marketCap")),
            "trailing_pe": safe_float(current_row.get("trailingPE")),
            "forward_pe": safe_float(current_row.get("forwardPE")),
            "price_to_book": safe_float(current_row.get("priceToBook")),
            "roe": safe_float(current_row.get("returnOnEquity")),
            "profit_margin": safe_float(current_row.get("profitMargins")),
            "debt_to_equity": safe_float(current_row.get("debtToEquity")),
            "revenue_cagr_3yr": trends.get("revenue", {}).get("cagr_3yr"),
            "net_income_cagr_3yr": trends.get("net_income", {}).get("cagr_3yr"),
            "eps_cagr_3yr": trends.get("eps", {}).get("cagr_3yr"),
        }
        
        # Add normalized metrics to summary
        ic_metrics = industry_comparison.get("metrics", {})
        if ic_metrics.get("trailing_pe"):
            summary_entry["pe_percentile"] = ic_metrics["trailing_pe"].get("percentile")
            summary_entry["pe_vs_industry"] = ic_metrics["trailing_pe"].get("vs_median")
        if ic_metrics.get("profit_margin"):
            summary_entry["margin_percentile"] = ic_metrics["profit_margin"].get("percentile")
            summary_entry["margin_vs_industry"] = ic_metrics["profit_margin"].get("vs_median")
        if ic_metrics.get("roe"):
            summary_entry["roe_percentile"] = ic_metrics["roe"].get("percentile")
            summary_entry["roe_vs_industry"] = ic_metrics["roe"].get("vs_median")

        # Add shareholding latest values and trends
        sh = company_json.get("shareholding", {})
        promoter_vals = sh.get("promoter", [])
        fii_vals = sh.get("fii", [])
        if promoter_vals:
            summary_entry["promoter_latest"] = promoter_vals[-1]
        if fii_vals:
            summary_entry["fii_latest"] = fii_vals[-1]
        sh_trends = sh.get("trends", {})
        if sh_trends.get("promoter"):
            summary_entry["promoter_trend"] = sh_trends["promoter"]
        if sh_trends.get("fii"):
            summary_entry["fii_trend"] = sh_trends["fii"]

        companies_summary.append(summary_entry)
        
        if (i + 1) % 50 == 0:
            print(f"  Processed {i + 1}/{len(all_companies_data)} companies")
    
    print(f"  Completed processing {len(all_companies_data)} companies")
    
    # ==========================================================================
    # Build indices
    # ==========================================================================
    print("\nBuilding indices...")
    
    # by_sector
    by_sector: dict[str, list[str]] = {}
    for _, row in current_df.iterrows():
        sector = row.get("sector", "Unknown")
        symbol = row["symbol"]
        if sector not in by_sector:
            by_sector[sector] = []
        by_sector[sector].append(symbol)
    for sector in by_sector:
        by_sector[sector].sort()
    
    with open(INDICES_DIR / "by_sector.json", "w") as f:
        json.dump(by_sector, f, indent=2)
    print(f"  Created by_sector.json with {len(by_sector)} sectors")
    
    # by_industry
    by_industry: dict[str, list[str]] = {}
    for _, row in current_df.iterrows():
        industry = row.get("industry", "Unknown")
        symbol = row["symbol"]
        if industry not in by_industry:
            by_industry[industry] = []
        by_industry[industry].append(symbol)
    for industry in by_industry:
        by_industry[industry].sort()
    
    with open(INDICES_DIR / "by_industry.json", "w") as f:
        json.dump(by_industry, f, indent=2)
    print(f"  Created by_industry.json with {len(by_industry)} industries")
    
    # industry_stats (remove internal _values before saving)
    industry_stats_clean = {}
    for industry, stats in industry_stats.items():
        clean_metrics = {}
        for metric_name, metric_data in stats.get("metrics", {}).items():
            if metric_data is not None:
                clean_metrics[metric_name] = {
                    k: round(v, 4) if isinstance(v, float) else v
                    for k, v in metric_data.items()
                    if k != "_values"  # Remove internal values list
                }
        industry_stats_clean[industry] = {
            "company_count": stats.get("company_count", 0),
            "metrics": clean_metrics,
        }
    
    with open(INDICES_DIR / "industry_stats.json", "w") as f:
        json.dump(industry_stats_clean, f, indent=2)
    print(f"  Created industry_stats.json with {len(industry_stats_clean)} industries")
    
    # screening_summary
    screening_summary = {
        "generated_at": datetime.now().isoformat(),
        "total_companies": len(companies_summary),
        "companies": companies_summary,
    }
    
    with open(INDICES_DIR / "screening_summary.json", "w") as f:
        json.dump(screening_summary, f, indent=2)
    print(f"  Created screening_summary.json with {len(companies_summary)} companies")


# =============================================================================
# Main Entry Point
# =============================================================================

def run_fetch(
    tickers: list[str] | None = None,
    max_workers: int = MAX_WORKERS,
    limit: int | None = None,
    resume: bool = False,
) -> bool:
    """Run the fetch phase. Returns True if successful."""
    print("=" * 60)
    print("Phase 1: Fetching Data from yfinance")
    print("=" * 60)
    
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    
    nse_metadata = None
    if tickers is None:
        tickers, nse_metadata = fetch_nse500_tickers()
    
    if resume:
        already_fetched = get_already_fetched_tickers()
        original_count = len(tickers)
        tickers = [t for t in tickers if t not in already_fetched]
        logger.info(f"Resume: skipping {original_count - len(tickers)} already fetched, {len(tickers)} remaining")
    
    if limit:
        tickers = tickers[:limit]
        logger.info(f"Limited to {limit} tickers")
    
    if not tickers:
        logger.info("No tickers to fetch - all already done!")
        return True
    
    results, failed = fetch_all_tickers(tickers, max_workers=max_workers)
    
    if resume:
        append_fetch_results(results, failed, nse_metadata)
    else:
        save_fetch_results(results, failed, nse_metadata)
    
    logger.info(f"Fetch complete: {len(results)} successful, {len(failed)} failed")
    return len(results) > 0


def main():
    """Main entry point with CLI argument parsing."""
    parser = argparse.ArgumentParser(
        description="NSE500 Data Pipeline - Fetch and transform fundamental data",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
    python nse500_data_pipeline.py                  # Full pipeline
    python nse500_data_pipeline.py --fetch-only     # Only fetch
    python nse500_data_pipeline.py --transform-only # Only transform
    python nse500_data_pipeline.py --resume         # Resume fetch
    python nse500_data_pipeline.py --limit 10       # Test with 10 tickers
        """,
    )
    
    parser.add_argument(
        "--fetch-only",
        action="store_true",
        help="Only fetch data to CSVs, skip JSON transformation",
    )
    parser.add_argument(
        "--transform-only",
        action="store_true",
        help="Only transform existing CSVs to JSON (skip fetch)",
    )
    parser.add_argument(
        "--resume",
        action="store_true",
        help="Resume interrupted fetch - skip already fetched tickers",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Limit number of tickers to fetch (for testing)",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=MAX_WORKERS,
        help=f"Number of concurrent workers (default: {MAX_WORKERS})",
    )
    parser.add_argument(
        "--tickers",
        type=str,
        nargs="+",
        default=None,
        help="Specific tickers to fetch (overrides NSE500 list)",
    )
    
    args = parser.parse_args()
    
    start_time = time.time()
    
    print("\n" + "=" * 60)
    print("NSE500 Data Pipeline")
    print("=" * 60)
    
    # Determine what to run
    run_fetch_phase = not args.transform_only
    run_transform_phase = not args.fetch_only
    
    # Phase 1: Fetch
    if run_fetch_phase:
        fetch_success = run_fetch(
            tickers=args.tickers,
            max_workers=args.workers,
            limit=args.limit,
            resume=args.resume,
        )
        if not fetch_success and not CURRENT_CSV.exists():
            logger.error("Fetch failed and no existing data to transform")
            return
    
    # Phase 2: Transform
    if run_transform_phase:
        if not CURRENT_CSV.exists() or not HISTORICAL_CSV.exists():
            logger.error("CSV files not found. Run fetch first.")
            return
        transform_to_json()
    
    elapsed = time.time() - start_time
    
    print("\n" + "=" * 60)
    print("Pipeline Complete!")
    print(f"  Total time: {elapsed:.1f} seconds")
    print(f"  CSV files: {DATA_DIR}")
    print(f"  Company JSONs: {COMPANIES_DIR}")
    print(f"  Indices: {INDICES_DIR}")
    print("=" * 60)


if __name__ == "__main__":
    main()
