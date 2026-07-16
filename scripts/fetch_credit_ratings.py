#!/usr/bin/env python3
"""
Credit Rating Fetcher

Scrapes credit rating history from Screener.in (which aggregates CRISIL/ICRA/CARE links).
Stores latest rating metadata in each company's JSON file.

Usage:
    python fetch_credit_ratings.py --symbol RELIANCE
    python fetch_credit_ratings.py --symbols TCS INFY HDFCBANK
    python fetch_credit_ratings.py --stale       # companies with outdated data
    python fetch_credit_ratings.py --dry-run
"""

import argparse
import json
import logging
import re
import time
from datetime import date
from pathlib import Path

import requests
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

ROOT = Path(__file__).parent.parent
COMPANIES_DIR = ROOT / "data" / "companies"

SCREENER_URL = "https://www.screener.in/company/{symbol}/{view}/"
USER_AGENT = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"
RATE_LIMIT = 1.2
STALE_DAYS = 30


# ---------------------------------------------------------------------------
# Parsing helpers
# ---------------------------------------------------------------------------

_CRISIL_ACTION = {
    "RR": "Reaffirmed",
    "RU": "Upgraded",
    "RD": "Downgraded",
    "RA": "Assigned",
    "RW": "Watch",
    "RS": "Suspended",
    "RWN": "Watch-Negative",
    "RWP": "Watch-Positive",
}


def _parse_action(url: str) -> str | None:
    m = re.search(r"_([A-Z]{2,4})_\d+\.html", url)
    if m:
        return _CRISIL_ACTION.get(m.group(1))
    return None


def _detect_agency(url: str) -> str:
    u = url.lower()
    if "crisil" in u:
        return "CRISIL"
    if "icra" in u:
        return "ICRA"
    if "careratings" in u or "careedge" in u:
        return "CARE"
    if "indiaratings" in u or "fitchratings" in u:
        return "India Ratings"
    if "brickworkratings" in u:
        return "Brickwork"
    return "Other"


def _parse_date_text(text: str) -> str:
    """'30 Mar from crisil' → '30 Mar 2026'; '30 Oct 2025 from crisil' → '30 Oct 2025'"""
    m = re.match(r"(\d{1,2}\s+\w{3})(?:\s+(\d{4}))?\s+from", text)
    if m:
        day_month = m.group(1)
        year = m.group(2) or str(date.today().year)
        return f"{day_month} {year}"
    return text.split("from")[0].strip()


# ---------------------------------------------------------------------------
# Scraper
# ---------------------------------------------------------------------------

def _get_session() -> requests.Session:
    s = requests.Session()
    s.headers.update({"User-Agent": USER_AGENT, "Accept-Language": "en-US,en;q=0.9"})
    return s


def fetch_credit_ratings(symbol: str, session: requests.Session) -> dict:
    """Fetch credit rating entries from Screener.in. Always returns a dict."""
    for view in ("consolidated", ""):
        url = SCREENER_URL.format(symbol=symbol, view=view)
        try:
            r = session.get(url, timeout=20)
            if r.status_code == 404:
                continue
            if r.status_code != 200:
                continue

            soup = BeautifulSoup(r.text, "html.parser")
            cr_div = soup.find("div", class_=lambda c: c and "credit-ratings" in (c or ""))
            if not cr_div:
                continue

            links = cr_div.find_all("a")
            if not links:
                continue

            entries = []
            for a in links:
                date_div = a.find("div", class_="smaller")
                date_text = date_div.get_text(strip=True) if date_div else ""
                href = a.get("href", "")
                entries.append({
                    "date": _parse_date_text(date_text),
                    "agency": _detect_agency(href),
                    "action": _parse_action(href),
                    "url": href,
                })

            if not entries:
                continue

            latest = entries[0]
            agencies = sorted(set(e["agency"] for e in entries))

            return {
                "updated_at": date.today().isoformat(),
                "has_ratings": True,
                "latest_date": latest["date"],
                "latest_agency": latest["agency"],
                "latest_action": latest["action"],
                "agencies": agencies,
                "recent_entries": entries,
            }

        except requests.RequestException as e:
            logger.debug(f"{symbol}: request error — {e}")

    return {
        "updated_at": date.today().isoformat(),
        "has_ratings": False,
        "latest_date": None,
        "latest_agency": None,
        "latest_action": None,
        "agencies": [],
        "recent_entries": [],
    }


# ---------------------------------------------------------------------------
# Staleness
# ---------------------------------------------------------------------------

def _stored_updated_at(symbol: str) -> str | None:
    path = COMPANIES_DIR / f"{symbol}.json"
    if not path.exists():
        return None
    try:
        with open(path) as f:
            data = json.load(f)
        return data.get("credit_ratings", {}).get("updated_at")
    except Exception:
        return None


def is_stale(symbol: str) -> bool:
    updated_at = _stored_updated_at(symbol)
    if not updated_at:
        return True
    try:
        return (date.today() - date.fromisoformat(updated_at)).days >= STALE_DAYS
    except Exception:
        return True


def get_stale_symbols() -> list[str]:
    return [p.stem for p in sorted(COMPANIES_DIR.glob("*.json")) if is_stale(p.stem)]


# ---------------------------------------------------------------------------
# Write to company JSON
# ---------------------------------------------------------------------------

def _update_company_json(symbol: str, ratings: dict) -> None:
    path = COMPANIES_DIR / f"{symbol}.json"
    try:
        with open(path) as f:
            data = json.load(f)
    except Exception:
        data = {"symbol": symbol}
    data["credit_ratings"] = ratings
    with open(path, "w") as f:
        json.dump(data, f, indent=2)


# ---------------------------------------------------------------------------
# Core: process one symbol
# ---------------------------------------------------------------------------

def process_symbol(symbol: str, session: requests.Session, *, force: bool = False) -> str:
    if not force and not is_stale(symbol):
        return "skipped"

    result = fetch_credit_ratings(symbol, session)
    _update_company_json(symbol, result)

    if result["has_ratings"]:
        logger.info(
            f"  {symbol}: {', '.join(result['agencies'])} | "
            f"latest {result['latest_date']} "
            f"({result['latest_action'] or 'action unknown'})"
        )
    else:
        logger.info(f"  {symbol}: no rated instruments")
    return "ok"


# ---------------------------------------------------------------------------
# Batch
# ---------------------------------------------------------------------------

def process_symbols(
    symbols: list[str],
    *,
    force: bool = False,
    delay: float = RATE_LIMIT,
    log_fn=print,
) -> tuple[int, int, int]:
    """Returns (ok, skipped, failed)."""
    session = _get_session()
    ok = skipped = failed = 0

    for i, sym in enumerate(symbols, 1):
        status = process_symbol(sym, session, force=force)
        if status == "ok":
            ok += 1
        elif status == "skipped":
            skipped += 1
        else:
            failed += 1
        if i % 25 == 0:
            log_fn(f"  [{i}/{len(symbols)}] {ok} ok  {skipped} skipped  {failed} failed")
        if i < len(symbols):
            time.sleep(delay)

    return ok, skipped, failed


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    parser = argparse.ArgumentParser(description="Fetch credit ratings from Screener.in")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--symbol", metavar="SYM")
    group.add_argument("--symbols", nargs="+", metavar="SYM")
    group.add_argument("--stale", action="store_true")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    if args.symbol:
        symbols = [args.symbol.upper()]
    elif args.symbols:
        symbols = [s.upper() for s in args.symbols]
    else:
        symbols = get_stale_symbols()
        print(f"Found {len(symbols)} companies with stale credit rating data")

    if args.dry_run:
        print(f"Would fetch: {', '.join(symbols[:20])}")
        if len(symbols) > 20:
            print(f"  … and {len(symbols) - 20} more")
        return

    ok, skipped, failed = process_symbols(symbols, force=args.force)
    print(f"\nDone — {ok} updated  {skipped} skipped  {failed} failed")


if __name__ == "__main__":
    main()
