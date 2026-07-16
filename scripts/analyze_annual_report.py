#!/usr/bin/env python3
"""
Annual Report Analyzer

Downloads (if needed) and analyzes annual report PDFs using Gemini,
producing structured forward-looking JSON stored in each company's JSON file.

Usage:
    python analyze_annual_report.py --symbol RELIANCE
    python analyze_annual_report.py --symbols TCS INFY HDFCBANK
    python analyze_annual_report.py --stale          # all companies missing current FY analysis
    python analyze_annual_report.py --dry-run        # show what would be analyzed
"""

import argparse
import json
import logging
import os
import sys
import time
from datetime import date, datetime
from pathlib import Path

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

ROOT = Path(__file__).parent.parent
DATA_DIR = ROOT / "data"
COMPANIES_DIR = DATA_DIR / "companies"
REPORTS_DIR = DATA_DIR / "annual_reports"

sys.path.insert(0, str(Path(__file__).parent))

# ---------------------------------------------------------------------------
# Schema (matches the agreed JSON structure)
# ---------------------------------------------------------------------------

_SCHEMA_TEMPLATE = {
    "company_metadata": {
        "fiscal_year": "string — e.g. '2025'",
        "sector": "string",
        "report_analysis_date": "string — ISO date",
    },
    "forward_looking_growth": {
        "management_outlook": "2-3 sentences on demand environment and management confidence level.",
        "explicit_guidance": {
            "revenue_target": "Specific % or ₹ range, or null.",
            "margin_guidance": "Expected expansion/contraction in bps or %, or null.",
            "order_book_visibility": "For Infra/Cap-Goods/IT: value and execution timeline, or null.",
        },
        "capital_allocation": {
            "planned_capex": "₹ amount and specific purpose, or null.",
            "timeline": "Expected commissioning dates for new capacity, or null.",
        },
        "strategic_priorities": [
            "Top 3-4 specific bets management is making (e.g. 'Brownfield expansion in Gujarat by FY27')."
        ],
    },
    "risk_and_red_flags": {
        "contingent_liabilities": {
            "total_value": "₹ amount from Notes to Accounts, or null.",
            "nature": "Briefly describe the largest disputes (e.g. Income Tax, GST, Customs).",
        },
        "related_party_transactions": {
            "intensity_assessment": "High / Medium / Low",
            "notable_transactions": "Any significant money flow to promoter entities, or null.",
        },
        "key_audit_matters": [
            "Top 2 auditor concerns (e.g. Inventory valuation, Litigation provisions)."
        ],
        "governance_signals": {
            "director_changes": "Any Independent Director or KMP resignations, or null.",
            "board_attendance": "Any director with attendance <75%, or null.",
        },
        "company_specific_risks": [
            "Up to 3 non-generic risks (e.g. 'USFDA Form 483 at Dahej plant unresolved', 'Single client = 28% revenue')."
        ],
    },
    "analytical_summary": {
        "management_tone": {
            "score": "integer 1-10 (1=vague/promotional, 10=specific/metric-driven)",
            "justification": "One specific quote or observation justifying the score.",
        },
        "investor_takeaway": "2-sentence synthesis of the risk vs. reward narrative.",
    },
}

_SYSTEM_PROMPT = """You are a senior equity research analyst specialising in Indian listed companies (NSE/BSE).
Analyse the provided annual report PDF and extract structured intelligence.

CRITICAL RULES:
1. Forward-looking only. Skip sentences about past performance ("we achieved", "FY25 revenue was X"). Include only plans, targets, expectations ("we expect", "we plan to", "we will target", "guidance for FY26").
2. Use exact figures from the report. Never infer, calculate, or estimate numbers not explicitly stated.
3. Risks must be company-specific. Do NOT include generic risks (economic slowdown, geopolitical uncertainty, inflation).
4. If information is absent, use null — never fabricate or paraphrase boilerplate.
5. Arrays: maximum 4 items each. Strings: maximum 3 sentences each.
6. Output ONLY valid JSON. No preamble, no explanation, no markdown fences."""


def _make_prompt(symbol: str, sector: str, fy: str) -> str:
    schema_str = json.dumps(_SCHEMA_TEMPLATE, indent=2)
    return (
        f"Analyse this annual report for {symbol} (Sector: {sector}, FY{fy}).\n\n"
        f"Return ONLY valid JSON matching this exact structure "
        f"(preserve all keys; use null for absent data):\n\n{schema_str}"
    )


# ---------------------------------------------------------------------------
# Gemini client
# ---------------------------------------------------------------------------

def _get_api_key() -> str:
    key = os.environ.get("GOOGLE_API_KEY") or os.environ.get("GEMINI_API_KEY")
    if not key:
        # Try pydantic settings
        try:
            sys.path.insert(0, str(ROOT / "src"))
            from stock_screening.config import get_settings
            key = get_settings().google_api_key
        except Exception:
            pass
    if not key:
        raise RuntimeError(
            "No Google API key found. Set GOOGLE_API_KEY in your environment or .env file."
        )
    return key


def _analyze_pdf(pdf_path: Path, symbol: str, sector: str, fy: str) -> dict:
    """Upload PDF to Gemini and return parsed analysis dict."""
    from google import genai
    from google.genai import types

    client = genai.Client(api_key=_get_api_key())

    logger.info(f"  Uploading {pdf_path.name} to Gemini Files API…")
    uploaded = client.files.upload(
        file=pdf_path,
        config={"mime_type": "application/pdf", "display_name": f"{symbol}_AR_{fy}"},
    )

    try:
        logger.info(f"  Generating analysis for {symbol}…")
        response = client.models.generate_content(
            model="gemini-2.0-flash",
            contents=[
                types.Content(parts=[
                    types.Part(file_data=types.FileData(
                        file_uri=uploaded.uri,
                        mime_type="application/pdf",
                    )),
                    types.Part(text=_make_prompt(symbol, sector, fy)),
                ])
            ],
            config=types.GenerateContentConfig(
                system_instruction=_SYSTEM_PROMPT,
                response_mime_type="application/json",
                temperature=0.1,
            ),
        )
        raw = response.text.strip()
        # Strip markdown fences if model ignores the instruction
        if raw.startswith("```"):
            raw = raw.split("\n", 1)[1].rsplit("```", 1)[0]
        return json.loads(raw)
    finally:
        # Clean up uploaded file to avoid storage accumulation
        try:
            client.files.delete(name=uploaded.name)
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Staleness helpers
# ---------------------------------------------------------------------------

def _expected_fy() -> str:
    """Current expected FY (Indian Apr-Mar). FY2026 available from ~Jun 1 2026."""
    today = date.today()
    fy_available = date(today.year, 6, 1)
    return str(today.year if today >= fy_available else today.year - 1)


def _current_analysis_fy(symbol: str) -> str | None:
    """Return the FY stored in the company JSON's annual_report_analysis, or None."""
    path = COMPANIES_DIR / f"{symbol}.json"
    if not path.exists():
        return None
    try:
        with open(path) as f:
            data = json.load(f)
        return data.get("annual_report_analysis", {}).get(
            "company_metadata", {}
        ).get("fiscal_year")
    except Exception:
        return None


def _find_pdf(symbol: str, fy: str) -> Path | None:
    """Return path to the best available annual report PDF for symbol/fy."""
    symbol_dir = REPORTS_DIR / symbol
    if not symbol_dir.exists():
        return None
    candidates = list(symbol_dir.glob(f"*{fy}*.pdf")) or list(symbol_dir.glob("*.pdf"))
    if not candidates:
        return None
    return max(candidates, key=lambda p: p.stat().st_mtime)


def _get_sector(symbol: str) -> str:
    path = COMPANIES_DIR / f"{symbol}.json"
    if not path.exists():
        return "Unknown"
    try:
        with open(path) as f:
            return json.load(f).get("sector", "Unknown")
    except Exception:
        return "Unknown"


# ---------------------------------------------------------------------------
# Core: analyze one symbol
# ---------------------------------------------------------------------------

def analyze_symbol(symbol: str, fy: str | None = None, *, force: bool = False) -> dict:
    """
    Download (if needed) and analyze the annual report for a symbol.
    Returns {"symbol", "status", "error"?}.
    """
    target_fy = fy or _expected_fy()

    if not force and _current_analysis_fy(symbol) == target_fy:
        return {"symbol": symbol, "status": "skipped", "reason": f"already have FY{target_fy}"}

    # Find or download PDF
    pdf = _find_pdf(symbol, target_fy)
    if pdf is None:
        logger.info(f"  {symbol}: no PDF found, downloading…")
        from fetch_annual_reports import fetch_and_download_reports, get_session
        session = get_session()
        result = fetch_and_download_reports(symbol, session, max_reports=1)
        if not result.get("downloaded"):
            return {"symbol": symbol, "status": "failed", "error": "PDF download failed"}
        pdf = Path(result["downloaded"][0])

    sector = _get_sector(symbol)

    try:
        analysis = _analyze_pdf(pdf, symbol, sector, target_fy)
    except Exception as e:
        logger.error(f"  {symbol}: Gemini analysis failed — {e}")
        return {"symbol": symbol, "status": "failed", "error": str(e)}

    # Stamp metadata fields we control
    analysis.setdefault("company_metadata", {})
    analysis["company_metadata"]["fiscal_year"] = target_fy
    analysis["company_metadata"]["sector"] = sector
    analysis["company_metadata"]["report_analysis_date"] = date.today().isoformat()

    # Write into company JSON
    company_path = COMPANIES_DIR / f"{symbol}.json"
    try:
        with open(company_path) as f:
            company_data = json.load(f)
    except Exception:
        company_data = {"symbol": symbol}

    company_data["annual_report_analysis"] = analysis

    with open(company_path, "w") as f:
        json.dump(company_data, f, indent=2)

    logger.info(f"  {symbol}: analysis saved (FY{target_fy})")
    return {"symbol": symbol, "status": "ok", "fy": target_fy}


# ---------------------------------------------------------------------------
# Batch helpers
# ---------------------------------------------------------------------------

def get_stale_symbols() -> list[str]:
    """Return NSE500 symbols that are missing or have outdated AR analysis."""
    target_fy = _expected_fy()
    stale = []
    for path in sorted(COMPANIES_DIR.glob("*.json")):
        sym = path.stem
        if _current_analysis_fy(sym) != target_fy:
            stale.append(sym)
    return stale


def analyze_symbols(
    symbols: list[str],
    *,
    fy: str | None = None,
    force: bool = False,
    delay: float = 1.0,
    log_fn=print,
) -> tuple[list[str], list[tuple[str, str]]]:
    """Analyze a list of symbols. Returns (succeeded, failed)."""
    succeeded, failed = [], []
    target_fy = fy or _expected_fy()

    for i, sym in enumerate(symbols, 1):
        log_fn(f"[{i}/{len(symbols)}] {sym}")
        result = analyze_symbol(sym, fy=target_fy, force=force)
        if result["status"] == "ok":
            succeeded.append(sym)
        elif result["status"] == "skipped":
            log_fn(f"  skipped: {result.get('reason')}")
            succeeded.append(sym)
        else:
            failed.append((sym, result.get("error", "unknown")))
            log_fn(f"  FAILED: {result.get('error')}")
        if i < len(symbols):
            time.sleep(delay)

    return succeeded, failed


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    parser = argparse.ArgumentParser(description="Analyze annual reports with Gemini")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--symbol", metavar="SYM")
    group.add_argument("--symbols", nargs="+", metavar="SYM")
    group.add_argument("--stale", action="store_true", help="All symbols missing current FY analysis")
    parser.add_argument("--fy", metavar="YEAR", help="Override target FY, e.g. 2025")
    parser.add_argument("--force", action="store_true", help="Re-analyze even if already current")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    if args.symbol:
        symbols = [args.symbol.upper()]
    elif args.symbols:
        symbols = [s.upper() for s in args.symbols]
    else:
        symbols = get_stale_symbols()
        print(f"Found {len(symbols)} symbols needing analysis")

    if args.dry_run:
        print(f"Would analyze: {', '.join(symbols[:20])}")
        if len(symbols) > 20:
            print(f"  … and {len(symbols) - 20} more")
        return

    succeeded, failed = analyze_symbols(symbols, fy=args.fy, force=args.force)
    print(f"\nDone — {len(succeeded)} ok, {len(failed)} failed")
    if failed:
        for sym, err in failed:
            print(f"  {sym}: {err}")


if __name__ == "__main__":
    main()
