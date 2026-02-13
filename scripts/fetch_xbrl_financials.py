#!/usr/bin/env python3
"""
Financial Statement Extractor

Extracts structured financial statements (Balance Sheet, P&L, Cash Flow) 
from yfinance raw data into clean, structured format.

This is a one-time batch script (not an agent tool).

Usage:
    python fetch_xbrl_financials.py --symbol RELIANCE       # Single stock test
    python fetch_xbrl_financials.py --all                   # All NSE500 stocks  
    python fetch_xbrl_financials.py --symbols RELIANCE TCS  # Specific stocks
"""

import argparse
import json
import logging
import math
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from tqdm import tqdm

# =============================================================================
# Configuration
# =============================================================================

DATA_DIR = Path(__file__).parent.parent / "data"
RAW_JSON = DATA_DIR / "nse500_quarterly_raw.json"
FINANCIALS_DIR = DATA_DIR / "financials"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


# =============================================================================
# Field Mappings: yfinance -> Clean Names
# =============================================================================

BALANCE_SHEET_MAPPING = {
    # Assets
    "Total Assets": "total_assets",
    "Total Non Current Assets": "non_current_assets",
    "Net PPE": "property_plant_equipment",
    "Goodwill And Other Intangible Assets": "goodwill_intangibles",
    "Goodwill": "goodwill",
    "Other Intangible Assets": "intangible_assets",
    "Investmentin Financial Assets": "financial_investments",
    "Non Current Deferred Taxes Assets": "deferred_tax_assets",
    "Other Non Current Assets": "other_non_current_assets",
    "Total Current Assets": "current_assets",
    "Current Assets": "current_assets",
    "Inventory": "inventory",
    "Inventories": "inventory",
    "Accounts Receivable": "trade_receivables",
    "Cash And Cash Equivalents": "cash_and_equivalents",
    "Cash Cash Equivalents And Short Term Investments": "cash_and_investments",
    "Other Current Assets": "other_current_assets",
    "Prepaid Assets": "prepaid_expenses",
    "Restricted Cash": "restricted_cash",
    # Equity
    "Total Equity Gross Minority Interest": "total_equity",
    "Stockholders Equity": "shareholders_equity",
    "Common Stock Equity": "common_equity",
    "Capital Stock": "share_capital",
    "Common Stock": "common_stock",
    "Retained Earnings": "retained_earnings",
    "Additional Paid In Capital": "additional_paid_in_capital",
    "Other Equity Interest": "other_equity",
    "Minority Interest": "minority_interest",
    # Liabilities
    "Total Liabilities Net Minority Interest": "total_liabilities",
    "Total Non Current Liabilities Net Minority Interest": "non_current_liabilities",
    "Long Term Debt": "long_term_debt",
    "Long Term Debt And Capital Lease Obligation": "long_term_debt_and_leases",
    "Long Term Capital Lease Obligation": "long_term_leases",
    "Non Current Deferred Taxes Liabilities": "deferred_tax_liabilities",
    "Long Term Provisions": "long_term_provisions",
    "Other Non Current Liabilities": "other_non_current_liabilities",
    "Current Liabilities": "current_liabilities",
    "Total Debt": "total_debt",
    "Current Debt": "current_debt",
    "Current Debt And Capital Lease Obligation": "current_debt_and_leases",
    "Accounts Payable": "trade_payables",
    "Payables": "total_payables",
    "Other Current Liabilities": "other_current_liabilities",
    "Current Provisions": "current_provisions",
    "Total Tax Payable": "tax_payable",
    # Derived
    "Working Capital": "working_capital",
    "Net Tangible Assets": "net_tangible_assets",
    "Tangible Book Value": "tangible_book_value",
    "Invested Capital": "invested_capital",
}

INCOME_STATEMENT_MAPPING = {
    "Total Revenue": "total_revenue",
    "Operating Revenue": "operating_revenue",
    "Cost Of Revenue": "cost_of_revenue",
    "Gross Profit": "gross_profit",
    "Operating Expense": "operating_expenses",
    "Selling General And Administration": "sg_and_a",
    "Research And Development": "r_and_d",
    "Operating Income": "operating_income",
    "EBITDA": "ebitda",
    "EBIT": "ebit",
    "Interest Expense": "interest_expense",
    "Interest Income": "interest_income",
    "Net Interest Income": "net_interest_income",
    "Other Income Expense": "other_income",
    "Pretax Income": "profit_before_tax",
    "Tax Provision": "tax_expense",
    "Net Income": "net_income",
    "Net Income Common Stockholders": "net_income_to_shareholders",
    "Net Income Continuous Operations": "net_income_continuing",
    "Basic EPS": "eps_basic",
    "Diluted EPS": "eps_diluted",
    "Basic Average Shares": "shares_basic",
    "Diluted Average Shares": "shares_diluted",
    # Derived ratios (will be computed)
    "Gross Margin": "gross_margin",
    "Operating Margin": "operating_margin",
    "Net Margin": "net_margin",
}

CASHFLOW_MAPPING = {
    "Operating Cash Flow": "operating_cash_flow",
    "Cash Flow From Continuing Operating Activities": "cf_continuing_operations",
    "Net Income From Continuing Operations": "net_income_cf",
    "Depreciation And Amortization": "depreciation_amortization",
    "Depreciation Amortization Depletion": "depreciation_amortization",
    "Change In Working Capital": "change_working_capital",
    "Change In Receivables": "change_receivables",
    "Change In Inventory": "change_inventory",
    "Change In Payables And Accrued Expense": "change_payables",
    "Investing Cash Flow": "investing_cash_flow",
    "Capital Expenditure": "capex",
    "Purchase Of Investment": "purchase_investments",
    "Sale Of Investment": "sale_investments",
    "Purchase Of Business": "acquisitions",
    "Sale Of Business": "divestitures",
    "Financing Cash Flow": "financing_cash_flow",
    "Issuance Of Debt": "debt_issuance",
    "Repayment Of Debt": "debt_repayment",
    "Issuance Of Capital Stock": "equity_issuance",
    "Repurchase Of Capital Stock": "share_buyback",
    "Common Stock Dividend Paid": "dividends_paid",
    "Cash Dividends Paid": "dividends_paid",
    "Free Cash Flow": "free_cash_flow",
    "Changes In Cash": "net_change_in_cash",
    "Beginning Cash Position": "cash_beginning",
    "End Cash Position": "cash_ending",
}


# =============================================================================
# Data Classes
# =============================================================================


@dataclass
class FinancialStatements:
    """Complete financial statements for a company."""
    
    symbol: str
    company_name: str = ""
    currency: str = "INR"
    fiscal_years: list[int] = field(default_factory=list)
    balance_sheets: dict[int, dict[str, float]] = field(default_factory=dict)
    income_statements: dict[int, dict[str, float]] = field(default_factory=dict)
    cash_flows: dict[int, dict[str, float]] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)
    
    def to_dict(self) -> dict:
        return {
            "symbol": self.symbol,
            "company_name": self.company_name,
            "currency": self.currency,
            "fiscal_years": self.fiscal_years,
            "balance_sheets": {str(k): v for k, v in self.balance_sheets.items()},
            "income_statements": {str(k): v for k, v in self.income_statements.items()},
            "cash_flows": {str(k): v for k, v in self.cash_flows.items()},
            "metadata": self.metadata,
        }


# =============================================================================
# Extraction Functions
# =============================================================================


def safe_float(value: Any) -> float | None:
    """Safely convert value to float."""
    if value is None:
        return None
    if isinstance(value, float) and math.isnan(value):
        return None
    try:
        result = float(value)
        if math.isnan(result) or math.isinf(result):
            return None
        return result
    except (ValueError, TypeError):
        return None


def get_fiscal_year(date_str: str) -> int:
    """Extract fiscal year from date string. Indian FY ending March = that year."""
    try:
        # Parse date like "2025-03-31 00:00:00"
        date = datetime.strptime(date_str.split()[0], "%Y-%m-%d")
        # Indian fiscal year ends March 31
        if date.month >= 4:
            return date.year + 1  # FY starts April
        return date.year
    except Exception:
        return 0


def extract_statement(
    raw_data: dict[str, dict[str, Any]],
    mapping: dict[str, str],
) -> dict[int, dict[str, float]]:
    """Extract a statement (BS/PL/CF) from raw yfinance data."""
    result = {}
    
    if not raw_data:
        return result
    
    for date_str, values in raw_data.items():
        fy = get_fiscal_year(date_str)
        if fy == 0:
            continue
        
        statement = {}
        for raw_name, clean_name in mapping.items():
            if raw_name in values:
                val = safe_float(values[raw_name])
                if val is not None:
                    statement[clean_name] = val
        
        if statement:
            result[fy] = statement
    
    return result


def compute_ratios(income_stmt: dict[str, float]) -> dict[str, float]:
    """Compute financial ratios from income statement."""
    ratios = {}
    
    revenue = income_stmt.get("total_revenue") or income_stmt.get("operating_revenue")
    if revenue and revenue > 0:
        if "gross_profit" in income_stmt:
            ratios["gross_margin"] = income_stmt["gross_profit"] / revenue
        if "operating_income" in income_stmt:
            ratios["operating_margin"] = income_stmt["operating_income"] / revenue
        if "net_income" in income_stmt:
            ratios["net_margin"] = income_stmt["net_income"] / revenue
    
    return ratios


def compute_balance_sheet_ratios(bs: dict[str, float], pl: dict[str, float]) -> dict[str, float]:
    """Compute balance sheet ratios."""
    ratios = {}
    
    # Current ratio
    if bs.get("current_assets") and bs.get("current_liabilities"):
        ratios["current_ratio"] = bs["current_assets"] / bs["current_liabilities"]
    
    # Debt to equity
    equity = bs.get("shareholders_equity") or bs.get("total_equity")
    debt = bs.get("total_debt") or (
        (bs.get("long_term_debt") or 0) + (bs.get("current_debt") or 0)
    )
    if equity and equity > 0 and debt:
        ratios["debt_to_equity"] = debt / equity
    
    # ROE
    net_income = pl.get("net_income")
    if equity and equity > 0 and net_income:
        ratios["return_on_equity"] = net_income / equity
    
    # ROA
    total_assets = bs.get("total_assets")
    if total_assets and total_assets > 0 and net_income:
        ratios["return_on_assets"] = net_income / total_assets
    
    return ratios


def extract_company_financials(symbol: str, raw_data: dict) -> FinancialStatements:
    """Extract all financial statements for a company from raw yfinance data."""
    result = FinancialStatements(symbol=symbol)
    
    # Get company info
    info = raw_data.get("info", {})
    result.company_name = info.get("longName") or info.get("shortName") or symbol
    result.currency = info.get("financialCurrency", "INR")
    
    # Extract statements
    result.balance_sheets = extract_statement(
        raw_data.get("annual_balance", {}), 
        BALANCE_SHEET_MAPPING
    )
    result.income_statements = extract_statement(
        raw_data.get("annual_income", {}),
        INCOME_STATEMENT_MAPPING
    )
    result.cash_flows = extract_statement(
        raw_data.get("annual_cashflow", {}),
        CASHFLOW_MAPPING
    )
    
    # Get all fiscal years
    all_years = set()
    all_years.update(result.balance_sheets.keys())
    all_years.update(result.income_statements.keys())
    all_years.update(result.cash_flows.keys())
    result.fiscal_years = sorted(all_years)
    
    # Compute ratios for each year
    for fy in result.fiscal_years:
        # Income statement ratios
        if fy in result.income_statements:
            ratios = compute_ratios(result.income_statements[fy])
            result.income_statements[fy].update(ratios)
        
        # Balance sheet ratios
        if fy in result.balance_sheets and fy in result.income_statements:
            ratios = compute_balance_sheet_ratios(
                result.balance_sheets[fy],
                result.income_statements[fy]
            )
            result.balance_sheets[fy].update(ratios)
    
    # Metadata
    result.metadata = {
        "extracted_at": datetime.now().isoformat(),
        "source": "yfinance",
        "fetch_time": raw_data.get("fetch_time"),
    }
    
    return result


def validate_financials(fin: FinancialStatements) -> dict[str, Any]:
    """Validate extracted financial statements."""
    report = {
        "symbol": fin.symbol,
        "company_name": fin.company_name,
        "valid": True,
        "warnings": [],
        "summary": {
            "fiscal_years": fin.fiscal_years,
            "balance_sheet_years": len(fin.balance_sheets),
            "income_statement_years": len(fin.income_statements),
            "cash_flow_years": len(fin.cash_flows),
        },
        "latest_metrics": {},
    }
    
    if not fin.fiscal_years:
        report["valid"] = False
        report["warnings"].append("No fiscal year data found")
        return report
    
    latest_fy = max(fin.fiscal_years)
    
    # Check balance sheet
    bs = fin.balance_sheets.get(latest_fy, {})
    if not bs:
        report["warnings"].append(f"No balance sheet for FY{latest_fy}")
    else:
        report["latest_metrics"]["total_assets"] = bs.get("total_assets")
        report["latest_metrics"]["total_equity"] = bs.get("shareholders_equity") or bs.get("total_equity")
        report["latest_metrics"]["total_debt"] = bs.get("total_debt")
        
        # Validate balance sheet equation
        assets = bs.get("total_assets")
        equity = bs.get("shareholders_equity") or bs.get("total_equity")
        liab = bs.get("total_liabilities")
        if assets and equity and liab:
            expected = equity + liab
            diff_pct = abs(assets - expected) / max(assets, 1) * 100
            if diff_pct > 5:
                report["warnings"].append(
                    f"Balance sheet imbalance: Assets={assets:,.0f}, "
                    f"Equity+Liab={expected:,.0f} ({diff_pct:.1f}% diff)"
                )
    
    # Check income statement
    pl = fin.income_statements.get(latest_fy, {})
    if not pl:
        report["warnings"].append(f"No income statement for FY{latest_fy}")
    else:
        report["latest_metrics"]["revenue"] = pl.get("total_revenue")
        report["latest_metrics"]["net_income"] = pl.get("net_income")
        report["latest_metrics"]["eps"] = pl.get("eps_diluted") or pl.get("eps_basic")
    
    # Check cash flow
    cf = fin.cash_flows.get(latest_fy, {})
    if not cf:
        report["warnings"].append(f"No cash flow statement for FY{latest_fy}")
    else:
        report["latest_metrics"]["operating_cash_flow"] = cf.get("operating_cash_flow")
        report["latest_metrics"]["free_cash_flow"] = cf.get("free_cash_flow")
    
    return report


# =============================================================================
# Main Functions
# =============================================================================


def load_raw_data() -> dict[str, dict]:
    """Load the raw yfinance data."""
    if not RAW_JSON.exists():
        raise FileNotFoundError(
            f"Raw data file not found: {RAW_JSON}\n"
            "Run nse500_data_pipeline.py first to fetch the data."
        )
    
    logger.info(f"Loading raw data from {RAW_JSON}...")
    with open(RAW_JSON) as f:
        return json.load(f)


def process_single_stock(symbol: str, raw_data: dict) -> tuple[FinancialStatements | None, dict]:
    """Process a single stock."""
    # Try with .NS suffix first
    key = f"{symbol}.NS"
    if key not in raw_data:
        key = symbol
    if key not in raw_data:
        return None, {"error": f"Symbol {symbol} not found in raw data"}
    
    fin = extract_company_financials(symbol, raw_data[key])
    validation = validate_financials(fin)
    
    return fin, validation


def test_single_stock(symbol: str) -> None:
    """Test extraction on a single stock with detailed output."""
    print(f"\n{'='*60}")
    print(f"Testing Financial Extraction for: {symbol}")
    print("=" * 60)
    
    raw_data = load_raw_data()
    fin, validation = process_single_stock(symbol, raw_data)
    
    print("\n--- VALIDATION REPORT ---")
    print(json.dumps(validation, indent=2, default=str))
    
    if fin and fin.fiscal_years:
        latest_fy = max(fin.fiscal_years)
        
        print(f"\n--- BALANCE SHEET (FY{latest_fy}) ---")
        bs = fin.balance_sheets.get(latest_fy, {})
        for key in ["total_assets", "shareholders_equity", "total_liabilities", 
                    "current_assets", "current_liabilities", "total_debt",
                    "cash_and_equivalents", "inventory", "trade_receivables",
                    "current_ratio", "debt_to_equity", "return_on_equity"]:
            if key in bs:
                val = bs[key]
                if isinstance(val, float) and val > 1000:
                    print(f"  {key}: {val:,.0f}")
                else:
                    print(f"  {key}: {val:.4f}" if isinstance(val, float) else f"  {key}: {val}")
        
        print(f"\n--- INCOME STATEMENT (FY{latest_fy}) ---")
        pl = fin.income_statements.get(latest_fy, {})
        for key in ["total_revenue", "gross_profit", "operating_income", 
                    "net_income", "eps_basic", "eps_diluted",
                    "gross_margin", "operating_margin", "net_margin"]:
            if key in pl:
                val = pl[key]
                if isinstance(val, float) and val > 1000:
                    print(f"  {key}: {val:,.0f}")
                elif isinstance(val, float) and abs(val) < 10:
                    print(f"  {key}: {val:.4f}")
                else:
                    print(f"  {key}: {val}")
        
        print(f"\n--- CASH FLOW (FY{latest_fy}) ---")
        cf = fin.cash_flows.get(latest_fy, {})
        for key in ["operating_cash_flow", "investing_cash_flow", "financing_cash_flow",
                    "capex", "free_cash_flow", "dividends_paid"]:
            if key in cf:
                val = cf[key]
                print(f"  {key}: {val:,.0f}" if isinstance(val, float) else f"  {key}: {val}")
        
        # Save to file
        FINANCIALS_DIR.mkdir(parents=True, exist_ok=True)
        output_path = FINANCIALS_DIR / f"{symbol}_financials.json"
        with open(output_path, "w") as f:
            json.dump(fin.to_dict(), f, indent=2)
        print(f"\n--- SAVED TO: {output_path} ---")
    else:
        print("\n*** EXTRACTION FAILED ***")


def process_all_stocks(symbols: list[str] | None = None, limit: int | None = None) -> dict:
    """Process all stocks and save financial statements."""
    raw_data = load_raw_data()
    
    if symbols is None:
        # Use all symbols from raw data
        symbols = [s.replace(".NS", "") for s in raw_data.keys()]
    
    if limit:
        symbols = symbols[:limit]
    
    FINANCIALS_DIR.mkdir(parents=True, exist_ok=True)
    
    results = {"success": [], "failed": [], "warnings": []}
    
    for symbol in tqdm(symbols, desc="Extracting financials"):
        try:
            fin, validation = process_single_stock(symbol, raw_data)
            
            if fin and fin.fiscal_years:
                output_path = FINANCIALS_DIR / f"{symbol}_financials.json"
                with open(output_path, "w") as f:
                    json.dump(fin.to_dict(), f, indent=2)
                
                results["success"].append({"symbol": symbol, "years": fin.fiscal_years})
                
                if validation.get("warnings"):
                    results["warnings"].append({
                        "symbol": symbol,
                        "warnings": validation["warnings"]
                    })
            else:
                results["failed"].append({"symbol": symbol, "reason": validation})
                
        except Exception as e:
            logger.error(f"Error processing {symbol}: {e}")
            results["failed"].append({"symbol": symbol, "reason": str(e)})
    
    # Save summary
    summary_path = FINANCIALS_DIR / "_extraction_summary.json"
    with open(summary_path, "w") as f:
        json.dump({
            "extracted_at": datetime.now().isoformat(),
            "total_processed": len(symbols),
            "success_count": len(results["success"]),
            "failed_count": len(results["failed"]),
            "warning_count": len(results["warnings"]),
            "failed": results["failed"],
            "warnings": results["warnings"],
        }, f, indent=2)
    
    return results


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="Extract financial statements from yfinance raw data"
    )
    parser.add_argument("--symbol", type=str, help="Single stock symbol to test")
    parser.add_argument("--symbols", type=str, nargs="+", help="Multiple stock symbols")
    parser.add_argument("--all", action="store_true", help="Process all NSE500 stocks")
    parser.add_argument("--limit", type=int, help="Limit number of stocks")
    parser.add_argument("-v", "--verbose", action="store_true", help="Verbose output")
    
    args = parser.parse_args()
    
    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)
    
    if args.symbol:
        test_single_stock(args.symbol)
    
    elif args.symbols:
        results = process_all_stocks(args.symbols, limit=args.limit)
        print(f"\nResults: {len(results['success'])} success, {len(results['failed'])} failed")
    
    elif args.all:
        results = process_all_stocks(limit=args.limit)
        print(f"\nResults: {len(results['success'])} success, {len(results['failed'])} failed")
        if results["warnings"]:
            print(f"  {len(results['warnings'])} stocks had warnings")
    
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
