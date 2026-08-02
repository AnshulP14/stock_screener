# Stock Screener

Fundamental data for **NSE500 (India)** and **S&P500 (US)** stocks, driven by [pi](https://pi.dev).

There is no app. You open this repo in pi, ask questions in natural language, and pi queries the local data and the web to answer them.

## Quick start

```bash
git clone https://github.com/AnshulP14/stock_screener.git
cd stock_screener
pi
```

Then ask pi anything:

> screen the NSE500 for value stocks with P/E under 15 and ROE above 15%

> how does TCS compare to INFY on profitability and growth?

> what are the risk factors Apple mentioned in their 2024 10-K?

Pi uses the skills below to know what data exists and how to query it.

---

## Setup

### 1. Install dependencies

```bash
uv sync
```

### 2. Bootstrap the data

The `data/` directory is gitignored — you need to fetch it once. The fastest path to something queryable:

```bash
python scripts/data_refresh.py --market nse --mode quick   # top 50 NSE stocks, ~5 min
python scripts/build_db.py --market nse                     # build the query database
```

For the full dataset (both markets, all 500+ companies, ~60-120 min):

```bash
python scripts/data_refresh.py --mode full
python scripts/build_db.py --market all
```

> **Tip:** Open pi and ask "set up the data" — the `refresh-data` skill will walk you through it.

---

## Skills

This repo ships three skills in `.agents/skills/`. Pi loads them automatically when you open the repo. Each skill is also available as a `/skill:name` command.

| Skill | Command | What it does |
|-------|---------|--------------|
| **screen-stocks** | `/skill:screen-stocks` | SQL queries, strategy recipes (value, growth, quality, GARP), sector caveats |
| **refresh-data** | `/skill:refresh-data` | Bootstrap, update, or fix stale data — both markets |
| **analyse-statements** | `/skill:analyse-statements` | Navigate 10-K filings: outline, grep, read sections, compare across years |

### screen-stocks

Use for any stock screening, ranking, or company-profile question. Pi queries `data/screener.db` with SQL and can also fetch news and qualitative context.

**Examples:**
- "find me GARP stocks in the NSE500"
- "show me the top 10 S&P500 companies by ROE"
- "compare the debt profiles of RELIANCE and TATASTEEL"
- "which S&P500 companies have heavy institutional ownership?"

Strategy recipes the skill knows: value, growth, quality, GARP, relative value, institutional confidence. See `data/SQL.md` for more SQL examples.

### refresh-data

Use when data is missing, stale, or you want to set up a fresh clone.

**Examples:**
- "set up the data for the first time"
- "refresh the NSE500 data"
- "update just AAPL and MSFT"
- "the data looks stale, what should I do?"

Rebuilding the query database (`build_db.py`) is **not** automatic — always run it after any refresh:

```bash
python scripts/build_db.py --market all
```

> **Tip:** Ask pi "is the data fresh?" — it will check `data/manifest.json` and tell you.

### analyse-statements

Use to read and compare 10-K annual reports (S&P500 only). 10-Ks are huge (~150k tokens each), so the skill provides a navigation loop: outline → grep → windowed read → compare.

**Examples:**
- "what risks did Apple mention in their latest 10-K?"
- "how has Tesla's risk discussion changed over the last 3 years?"
- "find mentions of tariffs in AMD's 2024 filing"

If the filings aren't downloaded yet:
```bash
python scripts/fetch_annual_reports_snp.py --symbol AAPL
python scripts/filings.py index --symbols AAPL
```

> NSE annual reports are PDFs — out of scope for this skill. Use web search for India-specific filings.

---

## How it works

```
data/
├── nse/companies/{SYMBOL}.json     # per-company profile (NSE500)
├── nse/indices/screening_summary.json  # flat screening data
├── snp/                            # same structure for S&P500
├── screener.db                     # DuckDB file — the only query surface
└── raw/                            # deep-dive tier (EDGAR cache, annual reports)
```

Pi queries `data/screener.db` with SQL for structured analysis, and uses web search for news and qualitative context. The curated JSON files (`nse/`, `snp/`) are the source of truth — `screener.db` is rebuilt from them.

## Keeping data fresh

Pi can handle this for you ("refresh the data"), or run it manually:

```bash
python scripts/data_refresh.py                          # both markets, incremental
python scripts/data_refresh.py --market nse --mode full # NSE full bootstrap
python scripts/data_refresh.py --market nse --symbols RELIANCE TCS
python scripts/build_db.py --market all                  # rebuild query DB
```

Check `data/manifest.json` for per-market timestamps and coverage.

---

## For agents

`AGENTS.md` is the canonical reference for agent-driven workflows: data map, package structure, SQL reference, house rules. If you're extending the pipeline or adding new markets, start there.
