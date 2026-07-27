"""TrendVerdict — the closed vocabulary historical_trends.*.trend/direction
fields are drawn from, plus the pure classifiers that produce them.

StrEnum so JSON output is unchanged: `json.dump` serializes a member as its
string value, and equality against the plain strings already on disk in
existing company JSON still holds.
"""

from enum import StrEnum
from itertools import pairwise


class GrowthTrend(StrEnum):
    CONSISTENTLY_GROWING = "consistently_growing"
    MOSTLY_GROWING = "mostly_growing"
    DECLINING = "declining"
    VOLATILE = "volatile"
    INSUFFICIENT_DATA = "insufficient_data"


def classify_growth(values: list[float | None]) -> GrowthTrend:
    clean = [v for v in values if v is not None]
    if len(clean) < 3:
        return GrowthTrend.INSUFFICIENT_DATA
    ups = sum(1 for a, b in pairwise(clean) if b > a)
    if ups >= len(clean) - 1:
        return GrowthTrend.CONSISTENTLY_GROWING
    if ups == 0:
        return GrowthTrend.DECLINING
    if ups >= (len(clean) - 1) * 0.6:
        return GrowthTrend.MOSTLY_GROWING
    return GrowthTrend.VOLATILE


class MarginDirection(StrEnum):
    EXPANDING = "expanding"
    CONTRACTING = "contracting"
    STABLE = "stable"
    INSUFFICIENT_DATA = "insufficient_data"


def classify_margin_direction(values: list[float | None]) -> MarginDirection:
    clean = [v for v in values if v is not None]
    if len(clean) < 2:
        return MarginDirection.INSUFFICIENT_DATA
    change = clean[-1] - clean[0]
    if change > 0.02:
        return MarginDirection.EXPANDING
    if change < -0.02:
        return MarginDirection.CONTRACTING
    return MarginDirection.STABLE


class LeverageBand(StrEnum):
    DEBT_FREE = "debt_free"
    LOW = "low"
    MODERATE = "moderate"
    HIGH = "high"
    INSUFFICIENT_DATA = "insufficient_data"


def yoy(values: list[float | None]) -> list[float | None]:
    if len(values) < 2:
        return [None] * len(values)
    out = [None]
    for i in range(1, len(values)):
        a, b = values[i - 1], values[i]
        out.append((b - a) / abs(a) if a and b and a != 0 else None)
    return out


def cagr(values: list[float | None]) -> float | None:
    valid = [(i, v) for i, v in enumerate(values) if v is not None and v > 0]
    if len(valid) < 2:
        return None
    n = valid[-1][0] - valid[0][0]
    if n <= 0:
        return None
    return (valid[-1][1] / valid[0][1]) ** (1 / n) - 1


def average_roe(values: list[float | None], window: int = 3) -> float | None:
    """Mean ROE over the trailing `window` fiscal years. Previously computed
    as net-income CAGR standing in for an ROE average — a different metric
    entirely, just because both happened to be "a number about profitability
    over 3 years"."""
    trailing = [v for v in values[-window:] if v is not None]
    if not trailing:
        return None
    return sum(trailing) / len(trailing)


def classify_leverage(values: list[float | None]) -> LeverageBand:
    """Band the *level* of the most recent debt/equity ratio — not a delta
    over time like classify_margin_direction. `values` must be the ratio
    (debt/equity), not raw debt."""
    clean = [v for v in values if v is not None]
    if not clean:
        return LeverageBand.INSUFFICIENT_DATA
    latest = clean[-1]
    if latest < 0.05:
        return LeverageBand.DEBT_FREE
    if latest < 0.5:
        return LeverageBand.LOW
    if latest < 1.5:
        return LeverageBand.MODERATE
    return LeverageBand.HIGH
