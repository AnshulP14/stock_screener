"""Shared staleness policies for market data and enrichment."""

from __future__ import annotations

import json
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

_SENTINEL = object()


@dataclass(frozen=True)
class QuarterLag:
    """Stale when `field` is behind the latest expected NSE quarter."""
    field: tuple[str | int, ...]


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


def expected_latest_quarter(today: date | None = None) -> str:
    """Latest fiscal quarter whose data should be available, given NSE's reporting lag."""
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
    if value is _SENTINEL or value is None or not isinstance(value, str):
        return True
    if isinstance(policy, QuarterLag):
        expected = expected_latest_quarter(today=today)
        value_key, expected_key = _quarter_key(value), _quarter_key(expected)
        # Only behind-expected data is stale; malformed labels use exact match.
        if value_key is None or expected_key is None:
            return value != expected
        return value_key < expected_key
    try:
        return (today or date.today()) - date.fromisoformat(value) > timedelta(days=policy.days)
    except (TypeError, ValueError):
        return True


def stale_symbols(
    companies_dir: Path,
    policy: Policy | list[Policy],
    *,
    symbols: Iterable[str] | None = None,
    today: date | None = None,
) -> list[str]:
    """Return symbols stale under any policy; missing files are stale."""
    policies = list(policy) if isinstance(policy, (list, tuple)) else [policy]
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
        if any(is_stale(company, p, today) for p in policies):
            stale.append(sym)
    return stale
