# 0004: Manifest enrichment-coverage metrics, config-driven

**Status:** Accepted (Phase 7)

## Context

`AGENTS.md`'s Freshness note named `shareholding_coverage` and
`edgar_coverage` as example fields in each market's `data/manifest.json`
entry, alongside `generated_at`/`total_companies`. Nothing wrote them —
`markets/__init__.py`'s `_write_manifest` only ever set `total_companies`.
An agent checking data health per the documented Freshness workflow would
never find these keys.

## Decision

Compute coverage in `_write_manifest`, driven by the same `MarketConfig`
fields that already gate the enrichment steps themselves — not a hardcoded
per-market list, so a new enrichment dataset or a new EDGAR-like market
gets coverage tracking automatically:

- One `<dataset>_coverage` per entry in `market.enrichment_datasets`
  (`shareholding_coverage`/`credit_ratings_coverage` for NSE today; empty
  tuple for SNP, so no NSE-only keys leak onto SNP's manifest entry).
- `edgar_coverage` when `market.uses_edgar` is set (SNP today), defined as
  the fraction of companies with a **non-empty**
  `historical_trends.years_available` — not merely a resolved `cik`. See
  ADR 0001's consequences: XOM/FDXF/HONA all have a real CIK but empty
  filing history, and counting a resolved-but-empty CIK as "covered" would
  overstate how much real EDGAR data actually landed.

## Consequences

- Coverage is a fraction in `[0, 1]`, rounded to 4 decimals, computed by
  reading every company file in `market.companies_dir` — real disk I/O
  (JSON reads, not network) on every pipeline run, comparable in cost to
  what `build_indices` already does for the same directory in the same run.
- Verified against real data at rollout: NSE's `shareholding_coverage` and
  `credit_ratings_coverage` were both `1.0` (fully enriched); S&P's
  `edgar_coverage` was `0.994` (500/503) — exactly the 3 companies (XOM,
  FDXF, HONA) with resolved CIKs but no real 10-K history, confirming the
  years-available definition does what it's meant to.
