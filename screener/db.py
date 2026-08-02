"""Rebuild data/screener.db from curated JSON — the logic behind scripts/build_db.py."""

import sys

from .index import drop_market_tables, rebuild_market_db
from .market import NSE, SNP, MarketConfig


def rebuild(market: str | MarketConfig = "all") -> dict:
    """
    Rebuild screener.db for one or both markets.

    Args:
        market: "nse", "snp", "all", or a MarketConfig instance

    Returns:
        dict with table names and row counts (only for markets actually rebuilt)
    """
    results = {}
    targets = _resolve_markets(market)

    for mc in targets:
        if not mc.indices_dir.exists():
            print(f"  No {mc.label} screening_summary.json found.", file=sys.stderr)
            drop_market_tables(mc.id)
        else:
            results[mc.id] = rebuild_market_db(
                market=mc.id,
                companies_dir=mc.companies_dir,
                indices_dir=mc.indices_dir,
            )

    return results


def _resolve_markets(market: str | MarketConfig) -> list[MarketConfig]:
    """Resolve a market specifier to a list of MarketConfig instances."""
    if isinstance(market, MarketConfig):
        return [market]
    MARKETS = {"nse": NSE, "snp": SNP}
    if market == "all":
        return list(MARKETS.values())
    return [MARKETS[market]]
