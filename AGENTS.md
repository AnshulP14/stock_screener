# Stock Screener — data + tools for agent-driven equity research

This repo has no application. You (the agent) are the application: `data/` holds
curated fundamentals for NSE500 (India) and S&P500 (US), gitignored and regenerable
from public sources (offer to bootstrap if it's missing — see Skills below).
`data/screener.db` (DuckDB) is the only query surface, no CLI. `screener/` + `scripts/`
is the pipeline that produces both.

## Repo structure

`data/` has two tiers: curated (small, agent-facing) and raw (large, drill-down only).
`screener/` is a real installable package (`uv sync` installs it editable) — no
`sys.path` hacks needed to import it, including from tests. `scripts/*.py` are thin
CLI entry points over it; every command in this file targets `scripts/`, and that
interface doesn't change when the package internals do.

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
        ├── annual_reports/            # 10-K filing documents (htm) from SEC EDGAR
        └── sp500_universe.json

screener/
├── __init__.py
├── annual_reports.py     # NSE (screener.in PDFs) + S&P500 (SEC EDGAR 10-Ks)
│                         #   download logic, shared single/batch engine
├── cli.py                # data refresh CLI
├── config.py             # constants, paths, URLs
├── db.py                 # DuckDB: rebuild, query, drop tables
├── enrich.py             # screener.in enrichment
├── fetch.py              # data fetching (yfinance, EDGAR)
├── filings/              # filing navigation (PDF + HTML + shared backend)
│   ├── __init__.py
│   ├── backend.py        # FilingBackend dataclass + navigation logic
│   ├── pdf_filings.py    # PDF parser + PDF_BACKEND
│   └── html_filings.py   # HTML parser + HTML_BACKEND
├── freshness.py          # staleness policies
├── index.py              # screening summary + industry stats + manifest + store helpers
├── market.py             # MarketConfig, NSE/SNP instances
├── pipeline.py           # orchestrator (was markets/__init__.py)
├── runner.py             # concurrent fetch engine
├── statements.py         # AnnualStatements adapter
├── summary.py            # screening summary schema
└── transform.py          # snapshot, trends, insights (includes former trends.py)

scripts/
├── data_refresh.py             # → screener.cli.main()   `python scripts/data_refresh.py --market nse --mode full-sync`
├── build_db.py                 # → screener.db.rebuild()
├── query.py                    # → screener.db.query()
├── screener_in.py              # → screener.enrich (shareholding & credit ratings scraper CLI)
├── fetch_annual_reports.py     # → screener.annual_reports -- `--market nse` (screener.in PDFs)
│                               #   or `--market snp` (SEC EDGAR 10-Ks)
└── filings.py                  # navigate filings on disk (outline/grep/read) -- `--market nse`
                                #   (screener.filings.pdf_filings) or `--market snp`
                                #   (screener.filings.html_filings)
```

`tests/` covers the package with pytest (`uv run pytest`) — focused on pure seams
(classifiers, staleness rules, DB rebuild helpers), not network calls.

**Query surface:** SQL against `data/screener.db` (see `data/SCHEMA.md`) — six tables
(`nse`/`snp` flat summaries, `nse_companies`/`snp_companies` full nested profiles,
`nse_industry_stats`/`snp_industry_stats` percentile bands) covering everything under
`data/nse/` and `data/snp/`, from simple screens to single-company drill-down — no jq
needed. Rebuilt automatically at the end of every `data_refresh.py` run; only rebuild
it by hand (`python scripts/build_db.py --market all`) after a hand-edit to curated
JSON. Only reach into `data/raw/` for what the curated tier can't answer (e.g. a
specific GAAP tag's full quarterly history) — use jq/python there, don't Read a raw
file whole. Run `DESCRIBE {table}` for exact column shapes; NSE↔S&P500 schema deltas
and semantics are in `data/SCHEMA.md`.

**Units:** ratios and margins are decimals (0.15 = 15%); shareholding percentages are
whole numbers (52.3 = 52.3%). Market cap / revenue (`market_cap`, `enterprise_value`,
`total_revenue`) are absolute currency, one unsuffixed field for both markets — check
the top-level `currency` field ("INR" or "USD") to know which. NSE fiscal years end
March 31; US fiscal years vary by company.

**Freshness:** check `data/manifest.json` first — one file, per-market `generated_at`,
`total_companies`, and enrichment coverage (e.g. `shareholding_coverage`,
`edgar_coverage`), written by each pipeline run. Each market also carries a `db`
sub-key (`rebuilt_at`, `tables`), refreshed automatically at the end of every
`data_refresh.py` run — it only lags `generated_at` if the DB was rebuilt by hand
mid-way. Falls back to `generated_at` in each market's `screening_summary.json` if the
manifest predates this change. NSE annual results land ~60 days after Mar 31;
quarterly results ~45 days after quarter end. If data looks stale, offer to run an
incremental update (`--mode quick-sync`, see the `refresh-data` skill).

## Skills

Each `SKILL.md` in `.agents/skills/` is the source of truth for its area — commands,
modes, and recipes live there, not here.

| Skill | Use for |
|-------|---------|
| `refresh-data` | Bootstrapping, updating, or fixing stale data (both markets) |
| `screen-stocks` | SQL patterns and strategy recipes (value/growth/quality/GARP) — also see `data/SCHEMA.md` |
| `analyse-statements` | Reading and comparing 10-K / annual report filings |

## Web research

For news/qualitative context use WebSearch/WebFetch. Prefer these sources — India:
livemint.com, business-standard.com, economictimes.indiatimes.com, moneycontrol.com,
thehindubusinessline.com, financialexpress.com, nseindia.com, bseindia.com, sebi.gov.in.
US/global: reuters.com, bloomberg.com, sec.gov, wsj.com. Pass them as `allowed_domains`
for news searches; keep citation links in answers.

## House rules

- Never `git add data/`.
- All shared logic lives in `screener/`; market-specific orchestration in `screener/markets/`.
  `scripts/*.py` delegate to it — don't put logic back into the wrappers.
