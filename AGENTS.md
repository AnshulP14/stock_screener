# Stock Screener — data + tools for agent-driven equity research

This repo has no application. You (the agent) are the application. It provides:

1. **`data/`** — fundamental data for NSE500 (India) and S&P500 (US) stocks. Gitignored,
   regenerable from public sources. If it's missing, offer to bootstrap (see below).
2. **`data/screener.db`** — DuckDB file over the curated data. Query it with SQL for
   anything under `data/nse/` or `data/snp/` — see `data/SQL.md`. This is the only
   query surface; there's no CLI.
3. **`screener/`** — the pipeline package (shared utilities + NSE/S&P500 orchestration).
   **`scripts/`** — thin CLI wrappers over it; commands below are unaffected by that split.
4. **Skills** — `screen-stocks` (how to query + strategy recipes), `refresh-data`
   (first-time setup/bootstrap and when/how to update).

## Data map

Two tiers: curated (small, agent-facing) and raw (large, drill-down only).

```
data/
├── nse/                            # curated NSE500 (India)
│   ├── companies/{SYMBOL}.json     # per-company profile: snapshot, trends,
│   │                                #   shareholding, credit ratings, industry comparison
│   └── indices/
│       ├── screening_summary.json  # all NSE500 companies, flat screening metrics
│       └── industry_stats.json     # per-industry percentile bands
├── snp/                            # curated S&P500 (US), same shapes as nse/
│   ├── companies/{SYMBOL}.json     # (institutional_ownership instead of
│   │                                #   shareholding/credit_ratings; GICS fields)
│   └── indices/
└── raw/                            # deep-dive tier — never Read whole, always extract
    ├── nse/
    │   ├── current_metrics.csv     # raw fetch output (inputs to the curated JSONs)
    │   ├── historical_annual.csv
    │   ├── annual_reports/         # scraped Screener.in PDFs
    │   └── failed_tickers.txt
    └── snp/
        ├── edgar_cache/{SYMBOL}.json  # full SEC XBRL companyfacts (~500 tags,
        │                               #   full filing history) — 4MB+ each, extract don't Read
        └── sp500_universe.json
```

**Query curated data with SQL against `data/screener.db`** (see `data/SQL.md`) — six
tables (`nse`/`snp` flat summaries, `nse_companies`/`snp_companies` full nested
profiles, `nse_industry_stats`/`snp_industry_stats` percentile bands). **Not rebuilt
automatically by the pipeline** — after any fetch/transform run, rebuild it explicitly
with `python scripts/build_db.py --market all` (cheap, JSON → DB only, no re-fetch).
This covers everything under `data/nse/` and `data/snp/`,
from simple screens to deep single-company drill-down — no jq needed for curated data.
Only reach into `data/raw/` for questions the curated tier can't answer (e.g. a
specific GAAP tag's full quarterly history) — use jq/python there, don't Read a raw
file whole. Full field-by-field shapes for every file (including the NSE↔S&P500 schema
deltas) are in `data/SCHEMA.md`.

## Package structure

`screener/` is a real installable package (`uv sync` installs it editable) — no
`sys.path` hacks needed to import it, including from tests. `scripts/*.py` are thin
CLI entry points over it; every command in this file targets `scripts/`, and that
interface doesn't change when the package internals do.

```
screener/
├── config.py            # Paths, URLs, rate limits, staleness thresholds
├── market.py             # MarketConfig -- the per-market value object (currency,
│                         #   fiscal-year rule, uses_edgar, enrichment_datasets, ...)
├── freshness.py          # Staleness policies (QuarterLag/AgeDays) + is_stale
├── trends.py             # TrendVerdict classifiers (GrowthTrend/MarginDirection/LeverageBand)
├── statements.py         # AnnualStatements -- from_yfinance (NSE) / from_edgar (S&P)
├── fetch.py              # NSE/S&P500 tickers, yfinance fundamentals, SEC EDGAR + cache
├── transform.py          # Snapshot building, trends, insights, company JSON assembly
├── enrich.py             # Screener.in shareholding & credit ratings parsing + batch
├── summary.py            # Flat screening_summary schema + industry_comparison
├── index.py              # Screening summary, industry stats, percentiles, DB rebuild
├── runner.py             # Concurrent fetch→save engine shared by both market pipelines
├── cli.py                # Unified CLI implementation
├── db.py                 # screener.db rebuild logic
├── query.py              # DuckDB SQL query logic (read-only)
└── markets/
    ├── __init__.py       # run_pipeline -- the shared orchestrator both nse.py/snp.py
    │                     #   delegate to; every market-specific behavior comes from MarketConfig
    ├── nse.py            # NSE500: thin wrapper over run_pipeline(NSE, ...)
    └── snp.py            # S&P500: thin wrapper over run_pipeline(SNP, ...)

scripts/
├── cli.py                # → screener.cli.main()   `python scripts/cli.py --market nse --mode full`
├── data_refresh.py       # → screener.cli.main() (documented entry point, same interface)
├── build_db.py           # → screener.db.rebuild()
├── query.py              # → screener.query.query()
├── screener_in.py        # → screener.enrich (shareholding & credit ratings scraper CLI)
└── fetch_annual_reports.py  # standalone annual report PDF downloader
```

`tests/` covers the package with pytest (`uv run pytest`) — focused on pure seams
(classifiers, staleness rules, DB rebuild helpers), not network calls.

**Units:** ratios and margins are decimals (0.15 = 15%); shareholding percentages are
whole numbers (52.3 = 52.3%). Market cap / revenue (`market_cap`, `enterprise_value`,
`total_revenue`) are absolute currency, one unsuffixed field for both markets — check
the top-level `currency` field ("INR" or "USD") to know which. NSE fiscal years end
March 31; US fiscal years vary by company.

**Freshness:** check `data/manifest.json` first — one file, per-market `generated_at`,
`total_companies`, and enrichment coverage (e.g. `shareholding_coverage`,
`edgar_coverage`), written by each pipeline run. Each market also carries a `db`
sub-key (`rebuilt_at`, `tables`) written only by `scripts/build_db.py` — compare
`db.rebuilt_at` against the market's `generated_at` to tell whether `screener.db` is
behind the curated JSON (see the note above: the DB rebuild is a separate manual step).
Falls back to `generated_at` in each market's `screening_summary.json` if the manifest
predates this change. NSE annual results land ~60 days after Mar 31; quarterly results
~45 days after quarter end. If data looks stale, offer to run an incremental update
(see the `refresh-data` skill).

## Quick reference

```sql
-- SQL against data/screener.db (see data/SQL.md)
SELECT symbol, sector, trailing_pe, roe FROM nse
WHERE trailing_pe < 15 AND roe > 0.15 ORDER BY roe DESC LIMIT 20;
```

```bash
python scripts/build_db.py --market all                 # rebuild data/screener.db from JSON, no re-fetch
python scripts/data_refresh.py                          # Both markets, incremental
python scripts/data_refresh.py --market nse --mode full # NSE full bootstrap (~60-90 min)
python scripts/data_refresh.py --market snp --mode full # S&P500 full bootstrap
python scripts/data_refresh.py --market nse --symbols RELIANCE TCS  # Specific stocks
python scripts/data_refresh.py --market snp --dry-run          # Preview only
```

## Fresh clone (no data/)

Bootstrap: `python scripts/data_refresh.py --mode full` (both markets, ~60-120 min —
run in background), then `python scripts/build_db.py --market all` (never automatic).
The `refresh-data` skill is also the first-time setup guide — quick-start alternative,
SEC EDGAR contact setup, and partial-enrichment-failure handling all live there.

## Web research

For news/qualitative context use WebSearch/WebFetch. Prefer these sources — India:
livemint.com, business-standard.com, economictimes.indiatimes.com, moneycontrol.com,
thehindubusinessline.com, financialexpress.com, nseindia.com, bseindia.com, sebi.gov.in.
US/global: reuters.com, bloomberg.com, sec.gov, wsj.com. Pass them as `allowed_domains`
for news searches; keep citation links in answers.

## House rules

- Analysis output goes to the conversation (or files the user asks for) — don't add
  report generators, notebooks, or app code to the repo.
- Never `git add data/`.
- All shared logic lives in `screener/`; market-specific orchestration in `screener/markets/`.
  `scripts/*.py` delegate to it — don't put logic back into the wrappers.
