"""Screener.in enrichment: shareholding patterns + credit ratings."""

import json
import re
from datetime import date
from pathlib import Path

import requests
from bs4 import BeautifulSoup, Tag

from .annual_reports import parse_annual_report_links
from .config import CREDIT_RATINGS_STALE_DAYS
from .freshness import AgeDays, QuarterLag, is_stale
from .index import iter_companies, load_company, merge_company
from .runner import SCREENER_LIMITER

_QUARTER_POLICY = QuarterLag(field=("shareholding", "quarters", -1))
_RATINGS_POLICY = AgeDays(field=("credit_ratings", "updated_at"), days=CREDIT_RATINGS_STALE_DAYS)


# ── Helpers ─────────────────────────────────────────────────────────

def _fetch_soup(
    symbol: str, session: requests.Session | None = None
) -> tuple[BeautifulSoup | None, list[int | None]]:
    getter = session or requests
    statuses: list[int | None] = []
    for view in ("consolidated", ""):
        url = f"https://www.screener.in/company/{symbol}/{view}/"
        SCREENER_LIMITER.acquire()
        try:
            resp = getter.get(url, timeout=20)
            statuses.append(resp.status_code)
            if resp.status_code == 429:
                SCREENER_LIMITER.penalize()
            else:
                SCREENER_LIMITER.reward()
            if resp.status_code == 200:
                return BeautifulSoup(resp.text, "html.parser"), statuses
        except requests.RequestException:
            statuses.append(None)
    return None, statuses


def _load_company(symbol: str, dir_path: Path) -> dict:
    try:
        return load_company(dir_path, symbol)
    except (FileNotFoundError, json.JSONDecodeError):
        return {"symbol": symbol}


# ── Shareholding ─────────────────────────────────────────────────────

def parse_shareholding(soup: BeautifulSoup) -> dict | None:
    section = soup.find("section", id="shareholding")
    table = section.find("table") if section else None
    rows = table.find_all("tr") if table else []
    if not rows:
        return None

    headers = [th.get_text(strip=True) for th in rows[0].find_all(["th", "td"])]
    parsed: dict[str, list] = {}

    for row in rows[1:]:
        cells = [td.get_text(strip=True) for td in row.find_all("td")]
        if len(cells) < 2:
            continue
        label = cells[0].replace("\xa0", " ").rstrip("+").strip()
        parsed[label] = [_parse_val(c) for c in cells[1:len(headers)+1] if c]

    if not parsed:
        return None

    return {
        "updated_at": date.today().isoformat(),
        "quarters": headers,
        "promoter": parsed.get("Promoters", []),
        "fii": parsed.get("FIIs", []),
        "dii": parsed.get("DIIs", []),
        "public": parsed.get("Public", []),
        "num_shareholders": parsed.get("No. of Shareholders", []),
        "trends": {
            "promoter": _holding_trend(parsed.get("Promoters", [])),
            "fii": _holding_trend(parsed.get("FIIs", [])),
            "dii": _holding_trend(parsed.get("DIIs", [])),
        },
    }


def _parse_val(s: str) -> float | None:
    try:
        return float(s.replace("%", "").replace(",", "").strip())
    except (ValueError, AttributeError):
        return None


def _holding_trend(values: list[float | None], window: int = 4) -> str:
    clean = [v for v in values if v is not None]
    if len(clean) < window + 1:
        return "insufficient_data"
    delta = clean[-1] - clean[-window - 1]
    if delta > 1.0:
        return "increasing"
    if delta < -1.0:
        return "decreasing"
    return "stable"


# ── Credit Ratings ──────────────────────────────────────────────────

_CRISIL_ACTION = {
    "RR": "Reaffirmed", "RU": "Upgraded", "RD": "Downgraded",
    "RA": "Assigned", "RW": "Watch", "RS": "Suspended",
    "RWN": "Watch-Negative", "RWP": "Watch-Positive",
}

def parse_credit_ratings(soup: BeautifulSoup) -> dict:
    cr_div = soup.find("div", class_=lambda c: bool(c and "credit-ratings" in c))
    links = cr_div.find_all("a") if isinstance(cr_div, Tag) else []

    entries = []
    for a in links:
        if not isinstance(a, Tag):
            continue
        date_div = a.find("div", class_="smaller")
        href = str(a.get("href", ""))
        entries.append({
            "date": _parse_date_text(date_div.get_text(strip=True) if date_div else ""),
            "agency": _detect_agency(href),
            "action": _parse_action(href),
            "url": href,
        })

    if not entries:
        return {
            "updated_at": date.today().isoformat(),
            "has_ratings": False,
            "latest_date": None, "latest_agency": None, "latest_action": None,
            "agencies": [], "recent_entries": [],
        }

    latest = entries[0]
    return {
        "updated_at": date.today().isoformat(),
        "has_ratings": True,
        "latest_date": latest["date"],
        "latest_agency": latest["agency"],
        "latest_action": latest["action"],
        "agencies": sorted({e["agency"] for e in entries}),
        "recent_entries": entries,
    }


def _parse_action(url: str) -> str | None:
    m = re.search(r"_([A-Z]{2,4})_\d+\.html", url)
    return _CRISIL_ACTION.get(m.group(1)) if m else None


def _detect_agency(url: str) -> str:
    u = url.lower()
    if "crisil" in u: return "CRISIL"
    if "icra" in u: return "ICRA"
    if "careratings" in u or "careedge" in u: return "CARE"
    if "indiaratings" in u or "fitchratings" in u: return "India Ratings"
    if "brickworkratings" in u: return "Brickwork"
    return "Other"


def _parse_date_text(text: str) -> str:
    m = re.match(r"(\d{1,2}\s+\w{3})(?:\s+(\d{4}))?\s+from", text)
    if m:
        return f"{m.group(1)} {m.group(2) or date.today().year}"
    return text.split("from")[0].strip()


# ── Staleness checks ───────────────────────────────────────────────

def _is_shareholding_stale(company: dict) -> bool:
    return is_stale(company, _QUARTER_POLICY)


def _is_ratings_stale(company: dict) -> bool:
    return is_stale(company, _RATINGS_POLICY)


_STALE_CHECKS = {
    "shareholding": _is_shareholding_stale,
    "credit_ratings": _is_ratings_stale,
}


def get_stale_symbols(dataset: str, dir_path: Path) -> list[str]:
    is_stale_fn = _STALE_CHECKS[dataset]
    return [path.stem for path, company in iter_companies(dir_path) if is_stale_fn(company)]


def process_symbol_full(
    sym: str, dir_path: Path, session: requests.Session, *, need_report: bool = False,
) -> list[dict]:
    """Fetch and merge stale enrichment, returning annual-report links."""
    company = _load_company(sym, dir_path)
    need_shareholding = _is_shareholding_stale(company)
    need_ratings = _is_ratings_stale(company)
    if not (need_shareholding or need_ratings or need_report):
        return []

    soup, _statuses = _fetch_soup(sym, session)
    if soup is None:
        return []

    if need_shareholding:
        result = parse_shareholding(soup)
        if result is not None:
            merge_company(dir_path, sym, {"shareholding": result})
    if need_ratings:
        merge_company(dir_path, sym, {"credit_ratings": parse_credit_ratings(soup)})

    return parse_annual_report_links(soup) if need_report else []
