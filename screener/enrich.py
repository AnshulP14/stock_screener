"""Screener.in enrichment: shareholding patterns + credit ratings."""

import json
import re
from datetime import date
from pathlib import Path

import requests
from bs4 import BeautifulSoup, Tag

from .annual_reports import parse_annual_report_links
from .config import CREDIT_RATINGS_STALE_DAYS, SCREENER_USER_AGENT
from .freshness import NSE, AgeDays, QuarterLag, is_stale
from .runner import SCREENER_LIMITER
from .index import iter_companies, load_company, merge_company

_QUARTER_POLICY = QuarterLag(field=("shareholding", "quarters", -1), market=NSE)
_RATINGS_POLICY = AgeDays(field=("credit_ratings", "updated_at"), days=CREDIT_RATINGS_STALE_DAYS)


# ── Helpers ─────────────────────────────────────────────────────────

def _get_session() -> requests.Session:
    s = requests.Session()
    s.headers.update({"User-Agent": SCREENER_USER_AGENT, "Accept-Language": "en-US,en;q=0.9"})
    return s


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
    delta = clean[0] - clean[window]
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


# ── Batch processing ───────────────────────────────────────────────

DATASETS = {
    "shareholding": ("shareholding", parse_shareholding, _is_shareholding_stale),
    "credit_ratings": ("credit_ratings", parse_credit_ratings, _is_ratings_stale),
}


def get_stale_symbols(dataset: str, dir_path: Path, symbols: list[str] | None = None) -> list[str]:
    """Stale symbols for `dataset`.

    `symbols`, when given, restricts the check to that explicit set (a
    targeted `--symbols` run) instead of sweeping every company on disk --
    otherwise a single-symbol retry re-checks enrichment staleness for the
    whole universe.
    """
    _, _, is_stale_fn = DATASETS[dataset]
    stale = []
    for path, company in iter_companies(dir_path):
        if symbols is not None and path.stem not in symbols:
            continue
        if is_stale_fn(company):
            stale.append(path.stem)
    return stale


def process_symbols(symbols: list[str], dataset: str, dir_path: Path, *, force: bool = False, log_fn=print) -> tuple[int, int, int]:
    key, parse_fn, is_stale_fn = DATASETS[dataset]
    session = _get_session()
    ok = skipped = failed = rate_limited = 0

    for sym in symbols:
        company = _load_company(sym, dir_path)
        if not force and not is_stale_fn(company):
            skipped += 1
            continue

        soup, statuses = _fetch_soup(sym, session)
        result = parse_fn(soup) if soup else None
        if result is None:
            failed += 1
            if 429 in statuses:
                rate_limited += 1
                log_fn(f"  {sym}: rate-limited (429) by Screener.in")
            else:
                log_fn(f"  {sym}: fetch failed (status={statuses})")
        else:
            merge_company(dir_path, sym, {key: result})
            ok += 1

        if len(symbols) > 25 and len(symbols) % 25 == 0:
            suffix = f"  ({rate_limited} rate-limited)" if rate_limited else ""
            log_fn(f"  [{ok + skipped + failed}/{len(symbols)}] {ok} ok  {skipped} skipped  {failed} failed{suffix}")

    return ok, skipped, failed


def process_symbol_full(
    sym: str, dir_path: Path, session: requests.Session, *, need_report: bool = False,
) -> list[dict]:
    """One screener.in page fetch for `sym` -> shareholding + credit ratings
    (merged in if stale) + annual report links (parsed if `need_report`).

    Used by the combined NSE enrichment pass in run_pipeline, which folds
    what used to be two separate scrapes (shareholding/ratings, then a
    second pass for annual-report links) into a single request per symbol.
    Returns the discovered report links (possibly empty) for the caller to
    download -- this function never touches the filesystem for reports.
    """
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
