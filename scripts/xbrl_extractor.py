"""
XBRL Financial Statement Extractor

Parses XBRL XML files to extract structured financial data:
- Balance Sheet (Assets, Liabilities, Equity)
- Profit & Loss Statement
- Cash Flow Statement

Designed for Indian filings (Ind AS / Indian GAAP taxonomy).
"""

import json
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any
from xml.etree import ElementTree as ET

logger = logging.getLogger(__name__)

# =============================================================================
# XBRL Namespace Definitions (Common Indian Filing Namespaces)
# =============================================================================

NAMESPACES = {
    "xbrli": "http://www.xbrl.org/2003/instance",
    "link": "http://www.xbrl.org/2003/linkbase",
    "xlink": "http://www.w3.org/1999/xlink",
    "iso4217": "http://www.xbrl.org/2003/iso4217",
    "in-gaap": "http://www.mca.gov.in/XBRL/2019-20/in-gaap",
    "in-bse": "http://www.bseindia.com/xbrl",
    "indas": "http://www.mca.gov.in/xbrl/ind-as",
}

# =============================================================================
# Taxonomy Mappings: XBRL Element Names -> Friendly Names
# =============================================================================

# Balance Sheet Elements (Ind AS & Indian GAAP)
BALANCE_SHEET_ELEMENTS = {
    # Assets
    "PropertyPlantAndEquipment": "property_plant_equipment",
    "InvestmentProperty": "investment_property",
    "Goodwill": "goodwill",
    "OtherIntangibleAssets": "intangible_assets",
    "InvestmentsInSubsidiaries": "investments_subsidiaries",
    "InvestmentsInAssociates": "investments_associates",
    "FinancialAssets": "financial_assets",
    "DeferredTaxAssets": "deferred_tax_assets",
    "OtherNonCurrentAssets": "other_non_current_assets",
    "TotalNonCurrentAssets": "total_non_current_assets",
    "Inventories": "inventories",
    "TradeReceivables": "trade_receivables",
    "CashAndCashEquivalents": "cash_and_equivalents",
    "BankBalances": "bank_balances",
    "OtherCurrentAssets": "other_current_assets",
    "TotalCurrentAssets": "total_current_assets",
    "TotalAssets": "total_assets",
    # Equity
    "EquityShareCapital": "share_capital",
    "OtherEquity": "other_equity",
    "TotalEquity": "total_equity",
    "RetainedEarnings": "retained_earnings",
    "ReservesAndSurplus": "reserves_surplus",
    # Liabilities
    "LongTermBorrowings": "long_term_borrowings",
    "DeferredTaxLiabilities": "deferred_tax_liabilities",
    "OtherNonCurrentLiabilities": "other_non_current_liabilities",
    "TotalNonCurrentLiabilities": "total_non_current_liabilities",
    "ShortTermBorrowings": "short_term_borrowings",
    "TradePayables": "trade_payables",
    "OtherCurrentLiabilities": "other_current_liabilities",
    "TotalCurrentLiabilities": "total_current_liabilities",
    "TotalLiabilities": "total_liabilities",
}

# Profit & Loss Elements
PL_ELEMENTS = {
    "RevenueFromOperations": "revenue_operations",
    "OtherIncome": "other_income",
    "TotalIncome": "total_income",
    "CostOfMaterialsConsumed": "cost_materials",
    "PurchasesOfStockInTrade": "purchases_stock_trade",
    "ChangesInInventories": "changes_inventories",
    "EmployeeBenefitExpense": "employee_costs",
    "FinanceCosts": "finance_costs",
    "DepreciationAndAmortisation": "depreciation",
    "OtherExpenses": "other_expenses",
    "TotalExpenses": "total_expenses",
    "ProfitBeforeTax": "profit_before_tax",
    "TaxExpense": "tax_expense",
    "CurrentTax": "current_tax",
    "DeferredTax": "deferred_tax",
    "ProfitForThePeriod": "net_profit",
    "OtherComprehensiveIncome": "other_comprehensive_income",
    "TotalComprehensiveIncome": "total_comprehensive_income",
    "EarningsPerShareBasic": "eps_basic",
    "EarningsPerShareDiluted": "eps_diluted",
}

# Cash Flow Elements
CASHFLOW_ELEMENTS = {
    "CashFlowsFromOperatingActivities": "cf_operations",
    "NetCashFromOperatingActivities": "net_cf_operations",
    "CashFlowsFromInvestingActivities": "cf_investing",
    "NetCashFromInvestingActivities": "net_cf_investing",
    "CashFlowsFromFinancingActivities": "cf_financing",
    "NetCashFromFinancingActivities": "net_cf_financing",
    "NetIncreaseInCashAndCashEquivalents": "net_change_cash",
    "CashAndCashEquivalentsAtBeginning": "cash_beginning",
    "CashAndCashEquivalentsAtEnd": "cash_ending",
    "CapitalExpenditures": "capex",
    "DividendsPaid": "dividends_paid",
    "InterestPaid": "interest_paid",
}


# =============================================================================
# Data Classes
# =============================================================================


@dataclass
class FinancialPeriod:
    """Represents a financial reporting period."""

    start_date: str | None = None
    end_date: str | None = None
    instant: str | None = None
    context_id: str = ""

    @property
    def period_type(self) -> str:
        return "instant" if self.instant else "duration"

    @property
    def fiscal_year(self) -> int | None:
        date_str = self.instant or self.end_date
        if date_str:
            try:
                return datetime.strptime(date_str, "%Y-%m-%d").year
            except ValueError:
                return None
        return None


@dataclass
class FinancialStatement:
    """Extracted financial statement data."""

    company_name: str = ""
    symbol: str = ""
    period: FinancialPeriod | None = None
    currency: str = "INR"
    balance_sheet: dict[str, float] = field(default_factory=dict)
    profit_loss: dict[str, float] = field(default_factory=dict)
    cash_flow: dict[str, float] = field(default_factory=dict)
    raw_elements: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "company_name": self.company_name,
            "symbol": self.symbol,
            "period": {
                "start_date": self.period.start_date if self.period else None,
                "end_date": self.period.end_date if self.period else None,
                "instant": self.period.instant if self.period else None,
                "fiscal_year": self.period.fiscal_year if self.period else None,
            },
            "currency": self.currency,
            "balance_sheet": self.balance_sheet,
            "profit_loss": self.profit_loss,
            "cash_flow": self.cash_flow,
        }


# =============================================================================
# XBRL Parser
# =============================================================================


class XBRLParser:
    """Parses XBRL XML files to extract financial statements."""

    def __init__(self, file_path: str | Path):
        self.file_path = Path(file_path)
        self.tree: ET.ElementTree | None = None
        self.root: ET.Element | None = None
        self.contexts: dict[str, FinancialPeriod] = {}
        self.units: dict[str, str] = {}

    def parse(self) -> FinancialStatement:
        """Parse the XBRL file and extract financial data."""
        logger.info(f"Parsing XBRL file: {self.file_path}")

        self.tree = ET.parse(self.file_path)
        self.root = self.tree.getroot()

        # Extract namespace mappings from root
        self._extract_namespaces()

        # Parse contexts (reporting periods)
        self._parse_contexts()

        # Parse units (currencies)
        self._parse_units()

        # Extract financial data
        statement = FinancialStatement()
        statement.company_name = self._extract_company_name()
        statement.symbol = self._extract_symbol()

        # Get the most recent period
        statement.period = self._get_latest_period()
        statement.currency = self._get_primary_currency()

        # Extract statements
        statement.balance_sheet = self._extract_balance_sheet()
        statement.profit_loss = self._extract_profit_loss()
        statement.cash_flow = self._extract_cash_flow()

        # Store raw elements for debugging
        statement.raw_elements = self._extract_all_elements()

        return statement

    def _extract_namespaces(self):
        """Extract namespace mappings from the root element."""
        if self.root is not None:
            for attr, value in self.root.attrib.items():
                if attr.startswith("{"):
                    continue
                if ":" in attr:
                    _, prefix = attr.split(":")
                    NAMESPACES[prefix] = value

    def _parse_contexts(self):
        """Parse all context elements to extract reporting periods."""
        if self.root is None:
            return

        for context in self.root.iter():
            if context.tag.endswith("}context") or context.tag == "context":
                ctx_id = context.get("id", "")
                period = FinancialPeriod(context_id=ctx_id)

                # Look for period information
                for child in context:
                    tag = child.tag.split("}")[-1] if "}" in child.tag else child.tag

                    if tag == "period":
                        for period_elem in child:
                            ptag = (
                                period_elem.tag.split("}")[-1]
                                if "}" in period_elem.tag
                                else period_elem.tag
                            )
                            if ptag == "instant":
                                period.instant = period_elem.text
                            elif ptag == "startDate":
                                period.start_date = period_elem.text
                            elif ptag == "endDate":
                                period.end_date = period_elem.text

                self.contexts[ctx_id] = period

    def _parse_units(self):
        """Parse unit elements to identify currencies."""
        if self.root is None:
            return

        for unit in self.root.iter():
            if unit.tag.endswith("}unit") or unit.tag == "unit":
                unit_id = unit.get("id", "")
                for measure in unit.iter():
                    if measure.tag.endswith("}measure") or measure.tag == "measure":
                        if measure.text:
                            # Extract currency code (e.g., "iso4217:INR" -> "INR")
                            currency = measure.text.split(":")[-1]
                            self.units[unit_id] = currency

    def _get_latest_period(self) -> FinancialPeriod | None:
        """Get the most recent reporting period."""
        latest = None
        latest_date = None

        for ctx in self.contexts.values():
            date_str = ctx.instant or ctx.end_date
            if date_str:
                try:
                    date = datetime.strptime(date_str, "%Y-%m-%d")
                    if latest_date is None or date > latest_date:
                        latest_date = date
                        latest = ctx
                except ValueError:
                    continue

        return latest

    def _get_primary_currency(self) -> str:
        """Get the primary currency used in the filing."""
        # Default to INR for Indian filings
        for currency in self.units.values():
            if currency in ("INR", "USD", "EUR"):
                return currency
        return "INR"

    def _extract_company_name(self) -> str:
        """Extract company name from the XBRL file."""
        if self.root is None:
            return ""

        # Common element names for company name
        name_patterns = [
            "NameOfCompany",
            "EntityName",
            "CompanyName",
            "NameOfReportingEntity",
        ]

        for elem in self.root.iter():
            tag = elem.tag.split("}")[-1] if "}" in elem.tag else elem.tag
            if tag in name_patterns and elem.text:
                return elem.text.strip()

        return ""

    def _extract_symbol(self) -> str:
        """Extract stock symbol if available."""
        if self.root is None:
            return ""

        symbol_patterns = ["ScripCode", "Symbol", "StockSymbol", "TradingSymbol"]

        for elem in self.root.iter():
            tag = elem.tag.split("}")[-1] if "}" in elem.tag else elem.tag
            if tag in symbol_patterns and elem.text:
                return elem.text.strip()

        # Try to extract from filename
        filename = self.file_path.stem
        match = re.search(r"^([A-Z]+)", filename)
        if match:
            return match.group(1)

        return ""

    def _extract_numeric_value(self, element: ET.Element) -> float | None:
        """Extract numeric value from an XBRL element."""
        if element.text is None:
            return None

        try:
            value = float(element.text.replace(",", ""))

            # Handle scale/decimals attributes
            decimals = element.get("decimals")
            scale = element.get("scale")

            if scale:
                value *= 10 ** int(scale)
            elif decimals and decimals != "INF":
                # decimals indicates precision, not scaling
                pass

            return value
        except (ValueError, TypeError):
            return None

    def _extract_balance_sheet(self) -> dict[str, float]:
        """Extract balance sheet items."""
        return self._extract_statement_items(BALANCE_SHEET_ELEMENTS)

    def _extract_profit_loss(self) -> dict[str, float]:
        """Extract profit & loss items."""
        return self._extract_statement_items(PL_ELEMENTS)

    def _extract_cash_flow(self) -> dict[str, float]:
        """Extract cash flow items."""
        return self._extract_statement_items(CASHFLOW_ELEMENTS)

    def _extract_statement_items(
        self, element_mapping: dict[str, str]
    ) -> dict[str, float]:
        """Extract items matching the given element mapping."""
        if self.root is None:
            return {}

        items: dict[str, float] = {}

        for elem in self.root.iter():
            # Get local name without namespace
            tag = elem.tag.split("}")[-1] if "}" in elem.tag else elem.tag

            # Check if this tag matches any of our target elements
            if tag in element_mapping:
                value = self._extract_numeric_value(elem)
                if value is not None:
                    friendly_name = element_mapping[tag]
                    # Keep the most recent value (later in file typically more recent)
                    items[friendly_name] = value

        return items

    def _extract_all_elements(self) -> dict[str, Any]:
        """Extract all numeric elements for debugging/exploration."""
        if self.root is None:
            return {}

        elements: dict[str, Any] = {}

        for elem in self.root.iter():
            tag = elem.tag.split("}")[-1] if "}" in elem.tag else elem.tag
            value = self._extract_numeric_value(elem)

            if value is not None:
                if tag not in elements:
                    elements[tag] = []
                elements[tag].append(
                    {
                        "value": value,
                        "context": elem.get("contextRef"),
                        "unit": elem.get("unitRef"),
                    }
                )

        return elements


# =============================================================================
# High-Level API Functions
# =============================================================================


def parse_xbrl_file(file_path: str | Path) -> FinancialStatement:
    """
    Parse an XBRL file and return structured financial data.

    Args:
        file_path: Path to the XBRL XML file

    Returns:
        FinancialStatement object containing balance sheet, P&L, and cash flow data
    """
    parser = XBRLParser(file_path)
    return parser.parse()


def parse_xbrl_to_json(file_path: str | Path, output_path: str | Path | None = None) -> dict:
    """
    Parse an XBRL file and optionally save as JSON.

    Args:
        file_path: Path to the XBRL XML file
        output_path: Optional path to save the JSON output

    Returns:
        Dictionary containing the parsed financial data
    """
    statement = parse_xbrl_file(file_path)
    data = statement.to_dict()

    if output_path:
        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with open(output_path, "w") as f:
            json.dump(data, f, indent=2)
        logger.info(f"Saved parsed data to: {output_path}")

    return data


def extract_financial_summary(file_path: str | Path) -> dict[str, Any]:
    """
    Extract a summarized view of key financial metrics.

    Args:
        file_path: Path to the XBRL XML file

    Returns:
        Dictionary with key metrics suitable for AI agent consumption
    """
    statement = parse_xbrl_file(file_path)

    bs = statement.balance_sheet
    pl = statement.profit_loss
    cf = statement.cash_flow

    return {
        "company": statement.company_name,
        "symbol": statement.symbol,
        "fiscal_year": statement.period.fiscal_year if statement.period else None,
        "currency": statement.currency,
        "key_metrics": {
            # Size
            "total_assets": bs.get("total_assets"),
            "total_equity": bs.get("total_equity"),
            "total_liabilities": bs.get("total_liabilities"),
            # Liquidity
            "cash_and_equivalents": bs.get("cash_and_equivalents"),
            "current_assets": bs.get("total_current_assets"),
            "current_liabilities": bs.get("total_current_liabilities"),
            # Profitability
            "revenue": pl.get("revenue_operations") or pl.get("total_income"),
            "net_profit": pl.get("net_profit"),
            "profit_before_tax": pl.get("profit_before_tax"),
            "eps_basic": pl.get("eps_basic"),
            # Cash Flow
            "operating_cash_flow": cf.get("net_cf_operations"),
            "investing_cash_flow": cf.get("net_cf_investing"),
            "financing_cash_flow": cf.get("net_cf_financing"),
            "free_cash_flow": (
                (cf.get("net_cf_operations") or 0) - abs(cf.get("capex") or 0)
                if cf.get("net_cf_operations")
                else None
            ),
        },
        "ratios": {
            "debt_to_equity": (
                bs.get("total_liabilities", 0) / bs.get("total_equity", 1)
                if bs.get("total_equity")
                else None
            ),
            "current_ratio": (
                bs.get("total_current_assets", 0) / bs.get("total_current_liabilities", 1)
                if bs.get("total_current_liabilities")
                else None
            ),
            "net_margin": (
                pl.get("net_profit", 0) / pl.get("revenue_operations", 1)
                if pl.get("revenue_operations")
                else None
            ),
        },
    }


# =============================================================================
# CLI Interface
# =============================================================================


def main():
    """CLI entry point for XBRL extraction."""
    import argparse

    parser = argparse.ArgumentParser(
        description="Extract financial statements from XBRL files"
    )
    parser.add_argument("file", help="Path to XBRL XML file")
    parser.add_argument("-o", "--output", help="Output JSON file path")
    parser.add_argument(
        "-s", "--summary", action="store_true", help="Output summary only"
    )
    parser.add_argument("-v", "--verbose", action="store_true", help="Verbose output")

    args = parser.parse_args()

    if args.verbose:
        logging.basicConfig(level=logging.INFO)

    if args.summary:
        result = extract_financial_summary(args.file)
    else:
        result = parse_xbrl_to_json(args.file, args.output)

    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
