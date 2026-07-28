# Domain glossary

Terms used consistently across `screener/`, `data/`, and the skills. Add a term here
when a deepened module names a concept that isn't yet written down; sharpen an entry
here the moment its meaning gets clarified in conversation.

- **Curated tier** — `data/{nse,snp}/` — small, agent-facing JSON meant to be `Read`
  directly. Everything a screening query needs without touching the raw tier.
- **Raw tier** — `data/raw/{nse,snp}/` — large source-of-truth artifacts (full
  yfinance payloads, SEC EDGAR XBRL company facts, scraped PDFs). Drill-down only —
  never `Read` whole, always extracted with jq/DuckDB.
- **Company profile** — `data/{nse,snp}/companies/{SYMBOL}.json` — one company's full
  curated record: current snapshot, historical trends, insights, and (NSE-only)
  shareholding/credit ratings, or (S&P-only) institutional ownership.
- **Screening summary** — `data/{nse,snp}/indices/screening_summary.json` — one flat
  row per company across the whole market, for fast simple screens. Built by
  `build_indices` in `screener/index.py`.
- **Industry stats** — `data/{nse,snp}/indices/industry_stats.json` — per-industry
  percentile bands (median/mean/std/p25/p75) that `screening_summary`'s
  `*_percentile` columns and each company's own `industry_comparison` are computed
  against. Built for both markets; S&P's finer GICS sub-industries mean many have
  only 1-2 constituents, so a lot of per-metric bands are `null` (need 2+ peer
  values — see `screener/summary.py`'s `compute_industry_stats`).
- **Market pipeline** — a market's (`nse` or `snp`) end-to-end run: fetch → transform →
  enrich/EDGAR → build indices → (separately) rebuild `screener.db`. `markets/nse.py`
  and `markets/snp.py` are thin wrappers over the one shared orchestrator,
  `run_pipeline` in `screener/markets/__init__.py`, which is where every
  market-specific behavior (from `MarketConfig`, `screener/market.py`) actually
  gets applied.
- **Enrichment** — the Screener.in-scraped datasets (`shareholding`, `credit_ratings`)
  layered onto NSE company profiles after the core yfinance fetch, via
  `screener/enrich.py`'s staleness-driven batch (`MarketConfig.enrichment_datasets`).
  S&P's `institutional_ownership` looks similar but isn't the same mechanism — it's
  fetched inline during the main per-symbol fetch (`MarketConfig.fetch_institutional_holders`
  → `fetch.fetch_ticker_data`), not a separate staleness-checked pass.
- **Annual statements adapter** — `screener/statements.py`'s `AnnualStatements`: a
  typed year→line-item shape (`AnnualLineItems`) built by two interchangeable
  classmethods reading two very different raw sources into the same shape —
  `from_yfinance` (NSE, three annual DataFrames) and `from_edgar` (S&P, SEC XBRL
  company-facts, keyed by EDGAR's own `fy`/`fp`, not a date-derived fiscal year).
  Same output shape either way is what lets `transform.build_historical_trends`/
  `build_historical_trends_edgar` each just read `.revenue`/`.net_income`/etc.
  without caring where the data came from.
- **TrendVerdict** — the closed vocabulary a `historical_trends.*` field's `trend`/
  `direction` value is drawn from, and the pure classifiers that produce it, all in
  `screener/trends.py`: `GrowthTrend` (revenue/EPS/FCF direction over ≥3 points),
  `MarginDirection` (expanding/contracting/stable off a two-point delta), `LeverageBand`
  (debt-free/low/moderate/high off the *latest* debt/equity ratio — a level, not a
  delta, which is why it needs its own classifier rather than reusing
  `MarginDirection`). `generate_insights` in `screener/transform.py` matches on these
  values to produce `key_insights`. Not yet unified: `screener/enrich.py`'s
  shareholding `_holding_trend` (increasing/decreasing/stable) is a fourth, separate
  three-state vocabulary for a similar-shaped idea — worth folding in if it ever grows
  a second consumer, not before.

## Package structure

`screener/` is the installable package holding every module above; `scripts/*.py` are
thin CLI wrappers over it (see AGENTS.md's "Package structure" section for the map).
This split exists so the pipeline is importable — by tests, by future tooling —
without the `sys.path` hacks the wrapper scripts used to need.
