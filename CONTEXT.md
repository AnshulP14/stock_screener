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
- **Industry stats** — `data/nse/indices/industry_stats.json` — per-industry
  percentile bands (median/mean/std/p25/p75) that `screening_summary`'s
  `*_percentile` columns are computed against. NSE-only.
- **Market pipeline** — a market's (`nse` or `us`) end-to-end run: fetch → transform →
  enrich → build indices → (separately) rebuild `screener.db`. One module per market
  under `screener/markets/`, both built on the shared fetch/transform/index/enrich
  primitives in `screener/`.
- **Enrichment** — the Screener.in-scraped datasets (`shareholding`, `credit_ratings`)
  layered onto NSE company profiles after the core yfinance fetch. `screener/enrich.py`.
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
