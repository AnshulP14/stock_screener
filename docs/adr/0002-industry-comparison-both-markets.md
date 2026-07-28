# 0002: `industry_comparison` computed for both markets; `vs_median` as a relative difference

**Status:** Accepted (Phase 6)

## Context

`industry_comparison` (per-company percentile-vs-peers: `industry`,
`peer_count`, `metrics.{trailing_pe, ..., eps_cagr_3yr}` each with `value`/
`industry_median`/`percentile`/`vs_median`) was fully specified in
`data/SCHEMA.md`, marked NSE-only, but had zero writers anywhere —
`build_company_json`'s call site always passed `None`. Computing it needs
every company in a market loaded first (the percentile bands come from the
whole population), so it can't happen at per-symbol fetch time the way the
rest of the company JSON is built — it has to be a second pass, after
`industry_stats` is known.

By the time this was addressed, `screener.index.build_indices` already
computed `industry_stats` (percentile bands) for **both** markets
unconditionally — S&P's `snp_industry_stats` table already existed in
`screener.db` (112 industries) purely as a side effect of `build_indices`
being market-agnostic, even though `data/SCHEMA.md` said "No S&P500 equivalent
exists yet." That made "NSE-only" a documentation choice at this point, not a
technical constraint — the data to compute it for S&P was already sitting
there unused.

## Decision

Populate `industry_comparison` for both markets, and update
`data/SCHEMA.md` to drop the NSE-only restriction (moved from "NSE-only
sections" to "Both-market section").

`vs_median` is defined as `(value - median) / abs(median)` — a relative
difference, not an absolute one. Chosen so a single number is comparable
across metrics of very different scale (a PE ratio around 10-40 vs. a margin
around 0.05-0.3); an absolute difference would make cross-metric comparison
meaningless without knowing each metric's typical scale.

`screener/index.py`'s `build_indices` writes `industry_comparison` back onto
each company's own JSON file (atomic tmp+rename) after computing
`industry_stats` — a real write to the curated tier, not just an in-memory
value used for the flat table.

## Consequences

- S&P's fine-grained GICS sub-industries mean many have `company_count: 1`
  (or 2-3) — `compute_industry_stats` requires 2+ peer values per metric, so
  a lot of S&P companies get `peer_count: 1` and all-null `metrics` in their
  `industry_comparison`. This is correct behavior (no peers to compare
  against), not missing data — SCHEMA.md documents it explicitly so an agent
  doesn't mistake a thin industry for a fetch failure.
- `screener/summary.py` centralizes the metric list
  (`METRICS_FOR_PERCENTILE`) that both the flat table's `<key>_percentile`
  columns and `industry_comparison`'s metrics are computed from. A second,
  independent copy of this list with `industry_comparison`'s own metric names
  (`trailing_pe`/`profit_margin` instead of `pe`/`margin`) was tried first and
  immediately produced a real bug: it looked itself up in `industry_stats`
  under its own names, which don't exist there (`industry_stats` is keyed by
  `METRICS_FOR_PERCENTILE`'s names), so every metric silently came back
  `None`. Fixed by keeping one list plus a two-entry rename map
  (`_INDUSTRY_COMPARISON_NAMES`) instead of two lists that can drift apart.
