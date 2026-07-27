# Stock Screener

A fundamental data set and screening toolkit for **NSE500 (India)** and **S&P500 (US)**
stocks, designed to be driven by a coding agent (Claude Code / pi). There is no app: you
open the repo in the agent, ask questions in natural language, and the agent queries the
local DuckDB file (`data/screener.db`) with SQL and uses web search to do the research.

## Layout

```
├── AGENTS.md            # agent entry point: data map, commands, conventions
│                        #   (CLAUDE.md symlinks here — same file, both names auto-load)
├── data/                # gitignored — built locally by scripts/ (see Setup)
│   ├── nse/             # curated NSE500: companies/, indices/
│   ├── snp/             # curated S&P500 mirror: companies/, indices/
│   ├── raw/             # deep-dive tier: edgar_cache, raw CSVs/JSON, annual reports
│   ├── screener.db      # DuckDB file over the curated data — see data/SQL.md
│   ├── SQL.md           # table reference + example queries
│   └── SCHEMA.md        # field-by-field JSON shapes
├── screener/            # the pipeline package: config, fetch, transform, enrich, index,
│   │                    #   markets/ (nse.py, us.py), cli.py, db.py, query.py
├── scripts/             # thin CLI wrappers over screener/ (yfinance, NSE archives,
│   │                    #   SEC EDGAR, Screener.in)
│   ├── cli.py                 # unified entry: --market nse/us/all --mode full|incremental
│   ├── build_db.py            # rebuild data/screener.db from curated JSON, no re-fetch
│   ├── query.py                # run SQL against data/screener.db
│   ├── data_refresh.py         # documented entry point (same interface as cli.py)
│   ├── screener_in.py         # Screener.in scraper (shareholding + credit ratings)
│   └── fetch_annual_reports.py # annual report PDFs
├── tests/               # pytest — pure seams only, no network calls
└── .agents/skills/      # screen-stocks, refresh-data (canonical; .claude/skills/ symlinks here)
```

## Setup

```bash
uv sync
uv run pytest                                                # run the test suite

# Build the data set (public sources; no keys needed)
python scripts/data_refresh.py --market nse --mode full     # NSE500, ~60-90 min
python scripts/data_refresh.py --market us --mode full      # S&P500 (requires SEC_EDGAR_CONTACT)

# Or a quick partial start:
python scripts/data_refresh.py --market nse --mode quick    # top 50 NSE stocks, ~5 min

# Rebuild data/screener.db — not automatic, run after any refresh above:
python scripts/build_db.py --market all
```

## Usage

```sql
-- SQL against data/screener.db (see data/SQL.md)
SELECT symbol, sector, trailing_pe, roe FROM nse
WHERE trailing_pe < 15 AND roe > 0.15 ORDER BY roe DESC LIMIT 20;
```

Keeping data fresh:

```bash
python scripts/data_refresh.py                          # Both markets, incremental
python scripts/data_refresh.py --market nse --mode full # NSE full bootstrap
python scripts/data_refresh.py --market us --mode rebuild # S&P500 indices only
python scripts/data_refresh.py --market nse --symbols RELIANCE TCS  # Specific stocks
```

But mostly: open the repo in Claude Code and ask. The `screen-stocks` and `refresh-data`
skills teach the agent the strategy recipes and update workflow.
