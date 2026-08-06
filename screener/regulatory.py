"""Raw NSE banking XBRL and FFIEC FR Y-9C downloads."""

from __future__ import annotations

import csv
import io
import os
import tempfile
import time
import zipfile
from datetime import date
from pathlib import Path
from xml.etree import ElementTree

from curl_cffi import requests as curl_requests

from .config import FETCH_TIMEOUT, RAW_DIR
from .statements import safe_float

NSE_FILINGS_API = "https://www.nseindia.com/api/integrated-filing-results"
NSE_FILINGS_PAGE = "https://www.nseindia.com/companies-listing/corporate-integrated-filing"
FFIEC_ZIP_URL = "https://www.ffiec.gov/npw/FinancialReport/ReturnBHCFZipFiles"

# Reviewed against FFIEC National Information Center holding-company records.
_RSSD_BY_TICKER = {
    "BAC": 1073757,
    "BNY": 3587146,
    "C": 1951350,
    "CFG": 1132449,
    "FITB": 1070345,
    "HBAN": 1068191,
    "JPM": 1039502,
    "KEY": 1068025,
    "MTB": 1037003,
    "PNC": 1069778,
    "RF": 3242838,
    "TFC": 1074156,
    "USB": 1119794,
    "WFC": 1120754,
}

FFIEC_REQUIRED_COLUMNS = {
    "RSSD9001", "RSSD9999", "BHCA7205", "BHCAP793",
    "BHCK1403", "BHCK1407", "BHCK2122",
}


def rssd_id(symbol: str) -> int | None:
    return _RSSD_BY_TICKER.get(symbol.upper())


def is_yahoo_bank(info: dict) -> bool:
    return str(info.get("industry", "")).startswith("Banks")


def completed_december_years(today: date | None = None, count: int = 5) -> list[int]:
    today = today or date.today()
    latest = today.year - (today <= date(today.year, 12, 31))
    return list(range(latest, latest - count, -1))


def _fresh(path: Path, days_old: int, now: float | None = None) -> bool:
    return path.exists() and ((now or time.time()) - path.stat().st_mtime) <= days_old * 86400


def _atomic_bytes(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
    try:
        with os.fdopen(fd, "wb") as tmp:
            tmp.write(content)
        os.replace(tmp_name, path)
    except Exception:
        try:
            os.unlink(tmp_name)
        except FileNotFoundError:
            pass
        raise


def _session():
    return curl_requests.Session(impersonate="chrome", timeout=FETCH_TIMEOUT)


def _rows(payload) -> list[dict]:
    if isinstance(payload, list):
        return payload
    if not isinstance(payload, dict):
        return []
    for key in ("data", "content", "results"):
        value = payload.get(key)
        if isinstance(value, list):
            return value
        if isinstance(value, dict):
            nested = _rows(value)
            if nested:
                return nested
    return []


def _filing_date(row: dict) -> date | None:
    raw = row.get("qe_Date") or row.get("periodEndDate") or row.get("toDate")
    if not raw:
        return None
    for fmt in ("%d-%b-%Y", "%d-%m-%Y", "%Y-%m-%d", "%d/%m/%Y"):
        try:
            parsed = time.strptime(str(raw).strip(), fmt)
            return date(parsed.tm_year, parsed.tm_mon, parsed.tm_mday)
        except ValueError:
            pass
    return None


def select_nse_bank_filings(payload, limit: int = 5) -> list[tuple[int, str]]:
    """Select newest audited standalone March year-end XBRLs, one revision per year."""
    candidates = []
    for row in _rows(payload):
        period = _filing_date(row)
        url = row.get("xbrl") or row.get("xbrlUrl") or row.get("xbrl_url")
        if (
            period is None or (period.month, period.day) != (3, 31) or not url
            or str(row.get("consolidated", "")).strip().casefold() != "standalone"
            or str(row.get("audited", "")).strip().casefold() != "audited"
        ):
            continue
        try:
            sequence = int(row.get("seq_Id") or 0)
        except (TypeError, ValueError):
            sequence = 0
        revision = str(
            row.get("revised_Date") or row.get("broadcast_Date")
            or row.get("creation_Date") or row.get("broadcastDateTime")
            or row.get("submissionDate") or row.get("createdDate") or ""
        )
        candidates.append((period.year, sequence, revision, str(url)))

    selected: dict[int, str] = {}
    for year, _, _, url in sorted(candidates, reverse=True):
        selected.setdefault(year, url)
    return list(selected.items())[:limit]


def _validate_xml(content: bytes) -> None:
    if not content.lstrip().startswith(b"<"):
        raise ValueError("NSE XBRL response is not XML")
    ElementTree.fromstring(content)


def download_nse_bank_filings(
    symbol: str,
    *,
    days_old: int,
    cache_root: Path | None = None,
    session=None,
) -> list[Path]:
    """Cache up to five audited standalone March-31 NSE banking XBRLs."""
    session = session or _session()
    session.get(NSE_FILINGS_PAGE).raise_for_status()
    response = session.get(
        NSE_FILINGS_API,
        params={
            "symbol": symbol,
            "index": "equities",
            "type": "Integrated Filing- Financials",
            "page": 1,
            "size": 100,
        },
        headers={"Referer": NSE_FILINGS_PAGE},
    )
    response.raise_for_status()
    filings = select_nse_bank_filings(response.json())
    if not filings:
        raise ValueError("no audited standalone March-31 banking XBRL found")

    root = cache_root or RAW_DIR / "nse" / "bank_xbrl"
    paths = []
    for position, (year, url) in enumerate(filings):
        path = root / symbol / f"{year}.xml"
        if path.exists() and (position > 0 or _fresh(path, days_old)):
            paths.append(path)
            continue
        download = session.get(url, headers={"Referer": NSE_FILINGS_PAGE})
        download.raise_for_status()
        _validate_xml(download.content)
        _atomic_bytes(path, download.content)
        paths.append(path)
    return paths


def _zip_member_and_header(content: bytes) -> tuple[str, list[str]]:
    if not content.startswith(b"PK"):
        raise ValueError("FFIEC response is not a ZIP file")
    with zipfile.ZipFile(io.BytesIO(content)) as archive:
        members = [name for name in archive.namelist() if name.lower().endswith(".txt")]
        if not members:
            raise ValueError("FFIEC ZIP has no text data file")
        member = members[0]
        with archive.open(member) as data:
            header = next(csv.reader(io.TextIOWrapper(data, encoding="latin-1"), delimiter="^"))
    missing = FFIEC_REQUIRED_COLUMNS - set(header)
    if missing:
        raise ValueError(f"FFIEC ZIP missing required columns: {', '.join(sorted(missing))}")
    return member, header


def validate_ffiec_zip(content: bytes) -> None:
    _zip_member_and_header(content)


def ffiec_rssd_ids(path: Path) -> set[int]:
    """Return the RSSD IDs present in a validated cached bulk file."""
    with zipfile.ZipFile(path) as archive:
        member = next(name for name in archive.namelist() if name.lower().endswith(".txt"))
        with archive.open(member) as data:
            rows = csv.DictReader(io.TextIOWrapper(data, encoding="latin-1"), delimiter="^")
            return {
                int(row["RSSD9001"])
                for row in rows
                if row.get("RSSD9001", "").strip().isdigit()
            }


def parse_nse_bank_history(
    symbol: str, *, cache_root: Path | None = None,
) -> dict[int, dict[str, float | None]]:
    """Parse cached NSE banking XBRL into annual profile inputs."""
    root = cache_root or RAW_DIR / "nse" / "bank_xbrl"
    history = {}
    tags = {
        "PercentageOfGrossNpa": "nonperforming_loans_ratio",
        "PercentageOfNpa": "net_npa_ratio",
        "CET1Ratio": "cet1_ratio",
        "Advances": "loans",
        "Deposits": "deposits",
    }
    for path in sorted((root / symbol).glob("*.xml")):
        try:
            year = int(path.stem)
            elements = sorted(
                ElementTree.parse(path).getroot().iter(),
                key=lambda element: not element.attrib.get("contextRef", "").startswith("One"),
            )
        except (OSError, ValueError, ElementTree.ParseError):
            continue
        values: dict[str, float | None] = {}
        for element in elements:
            local_name = element.tag.rsplit("}", 1)[-1]
            output_name = tags.get(local_name)
            if output_name and output_name not in values:
                values[output_name] = safe_float(element.text)
        if values:
            history[year] = {output: values.get(output) for output in tags.values()}
    return history


def parse_ffiec_history(
    rssd: int, *, cache_dir: Path | None = None,
) -> dict[int, dict[str, float | None]]:
    """Parse one holding company's annual rows from cached December BHCF files."""
    cache_dir = cache_dir or RAW_DIR / "snp" / "ffiec"
    history = {}
    for path in sorted(cache_dir.glob("BHCF*1231.zip")):
        try:
            with zipfile.ZipFile(path) as archive:
                member = next(
                    name for name in archive.namelist() if name.lower().endswith(".txt")
                )
                with archive.open(member) as data:
                    rows = csv.DictReader(
                        io.TextIOWrapper(data, encoding="latin-1"), delimiter="^",
                    )
                    row = next(
                        (item for item in rows if safe_float(item.get("RSSD9001")) == rssd),
                        None,
                    )
            if row is None:
                continue
            report_date = str(row.get("RSSD9999") or "")
            year = int(report_date[:4] or path.name[4:8])
            nonaccrual = safe_float(row.get("BHCK1403"))
            past_due = safe_float(row.get("BHCK1407"))
            loans = safe_float(row.get("BHCK2122"))
            npl_ratio = (
                (nonaccrual + past_due) / loans
                if nonaccrual is not None and past_due is not None and loans and loans > 0
                else None
            )
            cet1 = safe_float(row.get("BHCAP793"))
            history[year] = {
                "nonperforming_loans_ratio": npl_ratio,
                "cet1_ratio": cet1 / 100 if cet1 is not None else None,
                "loans": loans,
                "deposits": None,
            }
        except (OSError, ValueError, zipfile.BadZipFile, StopIteration):
            continue
    return history


def download_ffiec_years(
    years: list[int],
    *,
    days_old: int,
    cache_dir: Path | None = None,
    session=None,
) -> tuple[dict[int, Path], list[tuple[int, str]]]:
    """Cache December BHCF bulk ZIPs; return successes and per-year errors."""
    session = session or _session()
    cache_dir = cache_dir or RAW_DIR / "snp" / "ffiec"
    latest = max(years) if years else None
    paths: dict[int, Path] = {}
    errors: list[tuple[int, str]] = []
    for year in years:
        path = cache_dir / f"BHCF{year}1231.zip"
        if path.exists() and (year != latest or _fresh(path, days_old)):
            paths[year] = path
            continue
        try:
            response = session.get(
                FFIEC_ZIP_URL,
                params={"zipfilename": f"BHCF{year}1231.ZIP"},
            )
            response.raise_for_status()
            validate_ffiec_zip(response.content)
            _atomic_bytes(path, response.content)
            paths[year] = path
        except Exception as exc:
            errors.append((year, str(exc)))
    return paths, errors
