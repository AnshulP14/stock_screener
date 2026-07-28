# 0001: S&P `historical_trends` sourced from SEC EDGAR, not yfinance

**Status:** Accepted (Phase 5)

## Context

Before this decision, both markets built `historical_trends` the same way:
`screener.statements.AnnualStatements.from_yfinance` over yfinance's
`income_stmt`/`balance_sheet`/`cashflow` DataFrames. That's a reasonable single
source for NSE, but for S&P it meant `historical_trends.source` was always
`"yfinance"` even though `screener/fetch.py` already had a working, cached
`fetch_edgar_facts` that was imported by `markets/snp.py`'s ancestry but never
actually called — `cik_map` was computed and then discarded in the
pre-unification `us.py`. `data/SCHEMA.md` had already been written to document
S&P's `historical_trends` as EDGAR-sourced (`source: "edgar_xbrl"`, a smaller
metric set than NSE's) well before any code produced that shape — the schema
was aspirational, not descriptive.

yfinance's annual statements and SEC's own XBRL company-facts don't cover
identical ground: yfinance typically has ~4-6 years and no per-filing
provenance; EDGAR has full filing history back to ~2016+ (in practice, back to
2009 for mature filers like Apple) with `form`/`fy`/`fp`/`filed` on every
datapoint, and is the canonical source the company itself filed under.

## Decision

Add `AnnualStatements.from_edgar(facts)` as a second adapter alongside
`from_yfinance`, both producing the same `AnnualLineItems`/`by_year` shape.
Key selection: only `form == "10-K"` and `fp == "FY"` entries count as annual;
keyed by EDGAR's own `fy` field, not a date-derived fiscal year — SEC's fy/fp
already reflects each filer's actual fiscal calendar (including non-calendar
year-ends), which a single `MarketConfig.fiscal_year` rule can't. Per-field
GAAP tag fallback lists handle filers renaming tags mid-history (e.g. Apple's
revenue: `SalesRevenueNet` through FY2017, then
`RevenueFromContractWithCustomerExcludingAssessedTax` from FY2018) — a later
tag fills years the earlier one doesn't have, without overwriting years the
earlier tag already covered.

`MarketConfig.uses_edgar` (`True` for `SNP`, `False` for `NSE`) routes
`markets/__init__.py`'s per-symbol `handle()` to
`build_historical_trends_edgar` instead of `build_historical_trends` — a
config field, not a hardcoded per-market branch, consistent with every other
market-specific behavior in `MarketConfig`.

## Consequences

- S&P's `historical_trends` now has a genuinely different, smaller metric set
  than NSE's (documented in `data/SCHEMA.md`): no `yoy_growth`, no
  `roe`/`debt_to_equity`/`free_cash_flow` (no balance-sheet tags extracted),
  plus `operating_cash_flow`, which NSE doesn't track. Code touching both
  markets' `historical_trends` must not assume the same keys exist on both.
  `gross_profit`/`operating_cash_flow` use a `values_usd` key on the S&P side
  (an existing schema convention, not introduced by this decision).
- A CIK is required per company; `screener.fetch.build_cik_map` does one bulk
  fetch of SEC's full ticker→CIK table per pipeline run (not cached to disk —
  see its docstring), not a per-symbol lookup.
- Real-world consequence discovered during rollout, not a bug: SEC's own
  ticker map can point a familiar ticker at a *new* CIK after a corporate
  restructuring (XOM → a new ExxonMobil holding-company CIK) or a spinoff
  (FDXF/HONA — FedEx Freight, Honeywell Aerospace) with little or no 10-K
  history yet under that CIK. `historical_trends.years_available` is
  legitimately empty for these, which is why `edgar_coverage` (ADR 0004)
  checks for non-empty years, not just a resolved CIK.
- This is what made ADR 0003 possible: once S&P's trends stopped depending on
  yfinance's annual statements, fetching them for S&P became pure waste.
