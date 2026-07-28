# Architecture Decision Records

Lightweight ADRs for decisions in this repo that aren't obvious from reading the
code alone — the *why*, not the *what*. Format: Context / Decision / Consequences.
Numbered sequentially; never renumber or delete a superseded one — mark its
status instead and link to whatever replaced it.

| ADR | Title | Status |
|---|---|---|
| [0001](0001-snp-historical-trends-from-edgar.md) | S&P `historical_trends` sourced from SEC EDGAR, not yfinance | Accepted |
| [0002](0002-industry-comparison-both-markets.md) | `industry_comparison` computed for both markets; `vs_median` as a relative difference | Accepted |
| [0003](0003-drop-dead-statement-fetches.md) | Drop unused quarterly/annual yfinance statement fetches | Accepted |
| [0004](0004-manifest-enrichment-coverage.md) | Manifest enrichment-coverage metrics, config-driven | Accepted |
