"""Single staleness definition shared by both market pipelines and the
enrichment batch. Previously seven separate, partly-inconsistent
implementations across markets/nse.py, markets/us.py, and enrich.py — see
the commit introducing this module for the full inventory and the two
behavioral discrepancies (inclusive vs exclusive day comparison, and how
"never fetched" was detected) that had to be reconciled to unify them.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, timedelta
from enum import StrEnum
from pathlib import Path
from typing import Iterable

_SENTINEL = object()


class Market(StrEnum):
    NSE = "nse"
    SNP = "snp"


@dataclass(frozen=True)
class QuarterLag:
    """Stale when the value at `field` isn't the latest quarter expected to
    be available `lag_days` after quarter-end."""
    field: tuple[str | int, ...]
    market: Market
    lag_days: int = 45


@dataclass(frozen=True)
class AgeDays:
    """Stale when the ISO date at `field` is more than `days` old (strict
    `>` — a value exactly `days` old is not yet stale)."""
    field: tuple[str | int, ...]
    days: int


Policy = QuarterLag | AgeDays

# NSE fiscal quarters end Mar/Jun/Sep/Dec.
_QUARTER_MONTH_NAME = {3: "Mar", 6: "Jun", 9: "Sep", 12: "Dec"}
_QUARTER_NAME_MONTH = {v: k for k, v in _QUARTER_MONTH_NAME.items()}


def _quarter_key(label: str) -> tuple[int, int] | None:
    """Parse a 'Mon YYYY' shareholding-quarter label into a (year, month) sort
    key, or None if it doesn't look like one."""
    parts = label.split()
    if len(parts) != 2 or parts[0] not in _QUARTER_NAME_MONTH:
        return None
    try:
        return (int(parts[1]), _QUARTER_NAME_MONTH[parts[0]])
    except ValueError:
        return None


def expected_latest_quarter(market: Market, today: date | None = None) -> str:
    """Latest fiscal quarter whose data should be available, given the
    market's reporting lag. Only NSE has quarter-based staleness today."""
    if market is not Market.NSE:
        raise NotImplementedError(f"expected_latest_quarter is not defined for {market}")
    today = today or date.today()
    ends = [
        date(today.year - 1, 9, 30), date(today.year - 1, 12, 31),
        date(today.year, 3, 31), date(today.year, 6, 30),
        date(today.year, 9, 30), date(today.year, 12, 31),
    ]
    q = max(e for e in ends if (today - e).days >= 45)
    return f"{_QUARTER_MONTH_NAME[q.month]} {q.year}"


def _dig(company: dict, field: tuple[str | int, ...]):
    value = company
    for key in field:
        if isinstance(key, int):
            if not isinstance(value, list) or not (-len(value) <= key < len(value)):
                return _SENTINEL
            value = value[key]
        else:
            if not isinstance(value, dict):
                return _SENTINEL
            value = value.get(key, _SENTINEL)
            if value is _SENTINEL:
                return _SENTINEL
    return value


def is_stale(company: dict, policy: Policy, today: date | None = None) -> bool:
    """Whether an already-loaded company dict is stale under `policy`."""
    value = _dig(company, policy.field)
    if value is _SENTINEL or value is None:
        return True
    if isinstance(policy, QuarterLag):
        expected = expected_latest_quarter(policy.market, today)
        value_key, expected_key = _quarter_key(value), _quarter_key(expected)
        # lag_days is a conservative floor -- real disclosed data is often
        # already ahead of it (e.g. NSE's actual ~21-day filing deadline vs.
        # the 45-day default here). Only behind-expected counts as stale;
        # an unparseable label falls back to exact match.
        if value_key is None or expected_key is None:
            return value != expected
        return value_key < expected_key
    try:
        return (today or date.today()) - date.fromisoformat(value) > timedelta(days=policy.days)
    except (TypeError, ValueError):
        return True


def stale_symbols(
    companies_dir: Path,
    policy: Policy,
    *,
    symbols: Iterable[str] | None = None,
    today: date | None = None,
) -> list[str]:
    """Symbols whose company JSON is stale under `policy`.

    If `symbols` is given, it's the full universe to check (a symbol with no
    file yet counts as stale — this is how a never-fetched ticker is caught).
    If omitted, falls back to globbing `companies_dir/*.json`.
    """
    if symbols is None:
        symbols = sorted(p.stem for p in companies_dir.glob("*.json"))

    stale = []
    for sym in symbols:
        path = companies_dir / f"{sym}.json"
        if not path.exists():
            stale.append(sym)
            continue
        try:
            with open(path) as f:
                company = json.load(f)
        except (OSError, json.JSONDecodeError):
            stale.append(sym)
            continue
        if is_stale(company, policy, today):
            stale.append(sym)
    return stale
