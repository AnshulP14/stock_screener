# Stock Screener — agent-driven equity research data and tools

There is no user-facing application. Agents query curated NSE500 (India) and S&P 500
(US) fundamentals in `data/screener.db`, navigate annual reports through
`scripts/filings.py`, and use `screener/` plus the thin `scripts/` entry points to
refresh or rebuild those artifacts. `data/` is gitignored and regenerable; if it is
missing, offer to bootstrap it with the `refresh-data` skill.

## Repository map

`screener/` is an installable package (`uv sync` installs it editable). Keep reusable
logic there; keep `scripts/*.py` as thin command-line wrappers.

```
data/
├── manifest.json                    # per-market freshness, coverage, and DB metadata
├── screener.db                      # DuckDB query surface for curated data
├── SCHEMA.md                        # SQL semantics, units, and market differences
├── nse/
│   ├── companies/{SYMBOL}.json      # snapshot, trends, shareholding, ratings, peers
│   └── indices/
│       ├── screening_summary.json   # flat screening rows
│       └── industry_stats.json      # per-industry percentile bands
├── snp/
│   ├── companies/{SYMBOL}.json      # snapshot, trends, ownership, GICS, peers
│   └── indices/                     # same index files as NSE
└── raw/                             # large drill-down tier; extract, never read whole
    ├── nse/
    │   ├── current_metrics.csv
    │   ├── historical_annual.csv
    │   ├── annual_reports/{SYMBOL}/
    │   │   ├── {SYMBOL}_AR_{YEAR}.pdf
    │   │   ├── {SYMBOL}_AR_{YEAR}.txt
    │   │   └── {SYMBOL}_AR_{YEAR}.index.json
    │   └── failed_tickers.txt
    └── snp/
        ├── edgar_cache/{SYMBOL}.json
        ├── annual_reports/{SYMBOL}/
        │   ├── {SYMBOL}_10K_{YEAR}.htm
        │   ├── {SYMBOL}_10K_{YEAR}.txt
        │   └── {SYMBOL}_10K_{YEAR}.index.json
        ├── sp500_universe.json
        └── failed_tickers.txt

screener/
├── annual_reports.py     # Screener.in PDF and SEC 10-K downloads
├── cli.py                # data-refresh CLI implementation
├── config.py             # paths, URLs, and constants
├── db.py                 # DuckDB rebuild/query/drop operations
├── edgar.py              # SEC CIK, submissions, XBRL facts, and document fetching
├── enrich.py             # Screener.in shareholding and credit-rating enrichment
├── fetch.py              # market universes and yfinance fundamentals
├── filings/
│   ├── backend.py        # shared text/index/navigation behavior
│   ├── pdf_filings.py    # NSE PDF extraction
│   └── html_filings.py   # S&P HTML extraction
├── freshness.py          # staleness policies
├── index.py              # company store, curated indices, and manifest
├── market.py             # MarketConfig plus NSE/SNP definitions
├── pipeline.py           # shared refresh orchestration
├── runner.py             # concurrent fetch engine and rate limiting
├── statements.py         # normalized annual-statement adapter
├── summary.py            # flat summary and industry-comparison schema
└── transform.py          # snapshots, trends, insights, and classifiers

scripts/
├── data_refresh.py       # unified refresh for either or both markets
├── build_db.py           # rebuild DuckDB from curated JSON
├── query.py              # execute SQL against DuckDB
└── filings.py            # index, list, outline, grep, or read filings
```

Run tests with `uv run pytest`. Tests cover the package's parsing, transformation,
freshness, indexing, DB, and pipeline seams; network behavior is mocked.

## Curated-data queries

Use SQL against `data/screener.db`, normally through `scripts/query.py`. Its six tables
are `nse`, `snp`, `nse_companies`, `snp_companies`, `nse_industry_stats`, and
`snp_industry_stats`. The first pair is flat, the second pair preserves nested company
profiles, and the last pair contains industry percentile bands.

```bash
uv run python scripts/query.py "DESCRIBE nse"
uv run python scripts/query.py "SELECT symbol, trailing_pe, roe FROM nse LIMIT 20"
```

Treat `DESCRIBE {table}` as the source of truth for current column shapes. See
`data/SCHEMA.md` for field semantics and NSE/S&P differences. Use `data/raw/` only when
the curated tables cannot answer the question, such as a particular GAAP tag's full
filing history; extract only the needed subset with SQL, `jq`, or Python.

Ratios and margins are decimals (`0.15` = 15%); shareholding percentages are whole
numbers (`52.3` = 52.3%). Market cap, enterprise value, and revenue are absolute values
in the row's `currency` (`INR` or `USD`). NSE fiscal years end March 31; US fiscal years
vary by company.

## Refresh and filing behavior

`data_refresh.py` refreshes fundamentals and enrichment, rebuilds curated indices and
DuckDB, then waits for annual-report jobs before reporting completion. Unless
`--skip-reports` is passed, stale/missing report sources are downloaded and the newest
report per affected symbol is converted to `.txt` and `.index.json`.

Report staleness currently checks only the source `.pdf`/`.htm`, not its sidecars. A
quick sync with no stale fundamentals exits before report checks. To create or repair
sidecars for reports already on disk without downloading anything, run:

```bash
uv run python scripts/filings.py --market nse index --all
uv run python scripts/filings.py --market snp index --all
```

Check `data/manifest.json` before treating data as current. Each market entry contains
`generated_at`, `total_companies`, enrichment coverage, and a `db` sub-key with
`rebuilt_at` and table row counts. `data_refresh.py` rebuilds the selected market's DB
tables automatically; use `scripts/build_db.py` only after hand-editing curated JSON.
NSE annual results generally arrive about 60 days after March 31 and quarterly results
about 45 days after quarter-end.

## Skills

The `SKILL.md` files in `.agents/skills/` are the command and workflow sources of truth.

| Skill | Use for |
|---|---|
| `refresh-data` | Bootstrap, update, or repair market data |
| `screen-stocks` | Screen, rank, compare, or profile companies with SQL |
| `analyse-statements` | Navigate and compare annual reports and 10-Ks |

## Web research

Use web research for current news and qualitative context. Prefer primary sources
(`nseindia.com`, `bseindia.com`, `sebi.gov.in`, `sec.gov`) and high-quality financial
reporting such as Reuters, Bloomberg, WSJ, Mint, Business Standard, Economic Times,
Moneycontrol, The Hindu BusinessLine, and Financial Express. Cite links in the answer.

## House rules

- Never stage or commit `data/`.
- Put shared and market-configured behavior in `screener/`; do not move logic into the
  `scripts/` wrappers.
- Never load a large raw filing or EDGAR payload wholesale; navigate or extract the
  smallest relevant section.
