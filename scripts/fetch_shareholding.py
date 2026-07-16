#!/usr/bin/env python3
"""
Shareholding Pattern Fetcher

Scrapes quarterly promoter/FII/DII/public holding data from Screener.in
and stores it in each company's JSON file.

Usage:
    python fetch_shareholding.py --symbol RELIANCE
    python fetch_shareholding.py --symbols TCS INFY HDFCBANK
    python fetch_shareholding.py --stale       # all companies with outdated data
    python fetch_shareholding.py --dry-run
"""

import argparse
import json
import logging
import sys
import time
from datetime import date, datetime
from pathlib import Path

import requests
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

ROOT = Path(__file__).parent.parent
COMPANIES_DIR = ROOT / "data" / "companies"

SCREENER_URL = "https://www.screener.in/company/{symbol}/{view}/"
USER_AGENT = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"
RATE_LIMIT = 1.2  # seconds between requests


# ---------------------------------------------------------------------------
# Quarter helpers
# ---------------------------------------------------------------------------

def _expected_latest_quarter() -> str:
    """Latest quarter whose data should be available (45-day lag after quarter end)."""
    today = date.today()
    ends = [
        date(today.year - 1, 9, 30), date(today.year - 1, 12, 31),
        date(today.year, 3, 31),      date(today.year, 6, 30),
        date(today.year, 9, 30),      date(today.year, 12, 31),
    ]
    available = [e for e in ends if (today - e).days >= 45]
    q = max(available)
    month = {3: "Mar", 6: "Jun", 9: "Sep", 12: "Dec"}[q.month]
    return f"{month} {q.year}"


def _parse_quarter_value(s: str) -> float | None:
    try:
        return float(s.replace("%", "").replace(",", "").strip())
    except (ValueError, AttributeError):
        return None


def _trend(values: list[float | None], window: int = 4) -> str:
    """Compare latest value vs window quarters ago."""
    clean = [v for v in values if v is not None]
    if len(clean) < window + 1:
        return "insufficient_data"
    delta = clean[0] - clean[window]
    if delta > 1.0:
        return "increasing"
    if delta < -1.0:
        return "decreasing"
    return "stable"


# ---------------------------------------------------------------------------
# Scraper
# ---------------------------------------------------------------------------

def _get_session() -> requests.Session:
    s = requests.Session()
    s.headers.update({"User-Agent": USER_AGENT, "Accept-Language": "en-US,en;q=0.9"})
    return s


def fetch_shareholding(symbol: str, session: requests.Session) -> dict | None:
    """Fetch shareholding table from Screener.in. Returns parsed dict or None."""
    for view in ("consolidated", ""):
        url = SCREENER_URL.format(symbol=symbol, view=view)
        try:
            r = session.get(url, timeout=20)
            if r.status_code == 404:
                continue
            if r.status_code != 200:
                logger.debug(f"{symbol}: HTTP {r.status_code} from {url}")
                continue

            soup = BeautifulSoup(r.text, "html.parser")
            section = soup.find("section", id="shareholding")
            if not section:
                continue

            table = section.find("table")
            if not table:
                continue

            rows = table.find_all("tr")
            if not rows:
                continue

            # Header row → quarter labels
            headers = [th.get_text(strip=True) for th in rows[0].find_all(["th", "td"])]
            quarters = [h for h in headers if h]  # drop empty first col

            parsed: dict[str, list] = {}
            for row in rows[1:]:
                cells = [td.get_text(strip=True) for td in row.find_all("td")]
                if len(cells) < 2:
                    continue
                label = cells[0].replace("\xa0", " ").rstrip("+").strip()
                values = [_parse_quarter_value(c) for c in cells[1:len(quarters) + 1]]
                parsed[label] = values

            if not quarters or not parsed:
                continue

            promoter = parsed.get("Promoters", [])
            fii      = parsed.get("FIIs", [])
            dii      = parsed.get("DIIs", [])
            public   = parsed.get("Public", [])

            raw_shareholders = parsed.get("No. of Shareholders", [])
            num_shareholders = []
            for v in raw_shareholders:
                try:
                    num_shareholders.append(int(str(v).replace(",", "")) if v is not None else None)
                except (ValueError, TypeError):
                    num_shareholders.append(None)

            return {
                "updated_at": date.today().isoformat(),
                "quarters": quarters,
                "promoter": promoter,
                "fii": fii,
                "dii": dii,
                "public": public,
                "num_shareholders": num_shareholders,
                "trends": {
                    "promoter": _trend(promoter),
                    "fii": _trend(fii),
                    "dii": _trend(dii),
                },
            }

        except requests.RequestException as e:
            logger.debug(f"{symbol}: request error — {e}")

    return None


# ---------------------------------------------------------------------------
# Staleness
# ---------------------------------------------------------------------------

def _stored_latest_quarter(symbol: str) -> str | None:
    path = COMPANIES_DIR / f"{symbol}.json"
    if not path.exists():
        return None
    try:
        with open(path) as f:
            data = json.load(f)
        quarters = data.get("shareholding", {}).get("quarters", [])
        return quarters[-1] if quarters else None
    except Exception:
        return None


def is_stale(symbol: str) -> bool:
    return _stored_latest_quarter(symbol) != _expected_latest_quarter()


def get_stale_symbols() -> list[str]:
    return [p.stem for p in sorted(COMPANIES_DIR.glob("*.json")) if is_stale(p.stem)]


# ---------------------------------------------------------------------------
# Write to company JSON
# ---------------------------------------------------------------------------

def _update_company_json(symbol: str, shareholding: dict) -> None:
    path = COMPANIES_DIR / f"{symbol}.json"
    try:
        with open(path) as f:
            data = json.load(f)
    except Exception:
        data = {"symbol": symbol}
    data["shareholding"] = shareholding
    with open(path, "w") as f:
        json.dump(data, f, indent=2)


# ---------------------------------------------------------------------------
# Core: process one symbol
# ---------------------------------------------------------------------------

def process_symbol(symbol: str, session: requests.Session, *, force: bool = False) -> str:
    """Fetch and store shareholding for one symbol. Returns 'ok'|'skipped'|'failed'."""
    if not force and not is_stale(symbol):
        return "skipped"

    result = fetch_shareholding(symbol, session)
    if result is None:
        logger.warning(f"  {symbol}: no shareholding data found on Screener.in")
        return "failed"

    _update_company_json(symbol, result)
    latest = result["quarters"][-1] if result["quarters"] else "?"
    logger.info(
        f"  {symbol}: saved {len(result['quarters'])} quarters "
        f"(latest {latest}) | "
        f"promoter {result['trends']['promoter']} "
        f"FII {result['trends']['fii']}"
    )
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

    parser = argparse.ArgumentParser(description="Fetch shareholding patterns from Screener.in")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--symbol", metavar="SYM")
    group.add_argument("--symbols", nargs="+", metavar="SYM")
    group.add_argument("--stale", action="store_true", help="All companies with outdated data")
    parser.add_argument("--force", action="store_true", help="Re-fetch even if already current")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    if args.symbol:
        symbols = [args.symbol.upper()]
    elif args.symbols:
        symbols = [s.upper() for s in args.symbols]
    else:
        symbols = get_stale_symbols()
        print(f"Found {len(symbols)} companies with stale shareholding data")

    if args.dry_run:
        print(f"Would fetch: {', '.join(symbols[:20])}")
        if len(symbols) > 20:
            print(f"  … and {len(symbols) - 20} more")
        return

    ok, skipped, failed = process_symbols(symbols, force=args.force)
    print(f"\nDone — {ok} updated  {skipped} skipped  {failed} failed")


if __name__ == "__main__":
    main()
