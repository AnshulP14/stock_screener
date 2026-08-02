---
name: refresh-data
description: Update, refresh, or bootstrap NSE500 / S&P500 data from scratch — unified command, modes, first-time setup, expected runtimes, and failure handling. Use when data is missing or stale, on a fresh clone with no data/ directory, after index rebalances, or when the user asks to refresh/update/set up the data.
---

# Refreshing the data

One unified entry point, two pipelines under the hood, for both routine updates and
first-time setup — a fresh clone with no `data/` directory isn't a different tool, it's
just `--mode full` with nothing to skip. Long runs go in the background
(`run_in_background`); both are resumable/idempotent — re-running only fetches what's
still missing or stale.

## First-time setup (no `data/` yet)

```bash
# Install the project (editable, gives you `uv run python scripts/...`)
uv sync

# One-time: a real contact email for SEC EDGAR (S&P only; skip this and S&P still
# works, just with a generic User-Agent SEC is more likely to throttle)
export SEC_EDGAR_CONTACT='you@example.com'          # or write it once to ~/.screener_edgar_email

# Full bootstrap, both markets (~60-120 min total — run in background)
uv run python scripts/data_refresh.py --mode full

# Faster partial start if you just need something to query soon:
uv run python scripts/data_refresh.py --market nse --mode quick   # NSE top-50 by market cap, ~5 min

# After any refresh, before querying:
uv run python scripts/build_db.py --market all             # data/screener.db is never built automatically

# Index the S&P 10-K corpus (outline→grep→read loop needs .index.json + .txt files):
uv run python scripts/filings.py index --all               # one-time after bootstrap

# (Optional) Download NSE annual report PDFs from screener.in:
uv run python scripts/fetch_annual_reports.py --all        # PDFs only, no parser yet
```

Shareholding/credit-rating enrichment (NSE) scrapes Screener.in and may partially
fail on a big first run — the core yfinance/EDGAR dataset is still fully usable
without it; a later incremental run fills any gaps. Don't treat a partial enrichment
failure as a reason to redo the whole bootstrap.

Shareholding/credit-rating enrichment (NSE) scrapes Screener.in and may partially
fail on a big first run — the core yfinance/EDGAR dataset is still fully usable
without it; a later incremental run fills any gaps. Don't treat a partial enrichment
failure as a reason to redo the whole bootstrap.

## Unified command

```bash
# Both markets, incremental (default)
uv run python scripts/data_refresh.py

# Both markets, bootstrap from empty (~60-120 min total — run in background)
uv run python scripts/data_refresh.py --mode full

# Single market
uv run python scripts/data_refresh.py --market nse --mode full      # NSE bootstrap, ~60-90 min
uv run python scripts/data_refresh.py --market snp --mode full      # S&P500 bootstrap, varies (EDGAR throttled)
uv run python scripts/data_refresh.py --market nse --mode quick     # NSE top-50, ~5 min
uv run python scripts/data_refresh.py --market nse --mode sync-universe  # NSE index rebalance
uv run python scripts/data_refresh.py --market snp --mode rebuild   # S&P500 indices only
uv run python scripts/data_refresh.py --market nse --symbols RELIANCE TCS  # Specific stocks
uv run python scripts/data_refresh.py --market snp --dry-run        # Preview only
```

### Modes (per market)

| Mode | NSE500 | S&P500 |
|------|--------|--------|
| `incremental` | Financial-calendar aware (missing FY/quarter) | Stale 7+ days |
| `full` | Re-fetch all 500 stocks | Re-fetch all companies |
| `quick` | Top 50 by market cap | Not applicable (falls back to incremental) |
| `sync-universe` | Sync NSE500 list, fetch newly added | Update constituent list |
| `transform-only` | Rebuild indices from existing CSVs | Not applicable |
| `rebuild` | Not applicable | Rebuild indices from existing JSONs |

## Rebuild the query DB

`data_refresh.py` (and the underlying pipelines) only regenerate the curated JSON
(`screening_summary.json`, `industry_stats.json`) — `data/screener.db` is **not**
rebuilt automatically. Run this after any refresh, before querying:

```bash
uv run python scripts/build_db.py --market all   # or --market nse / snp
```

Cheap — JSON → DB only, no re-fetch.

## Failure handling

- Failed NSE tickers land in `data/raw/nse/failed_tickers.txt` — retry with `--symbols`.
- Screener.in scraping (shareholding, credit ratings) can rate-limit or break; the core
  yfinance dataset is still valid without it. Report what's missing, don't retry in a loop.
- tqdm progress bars render poorly in non-TTY output — ignore the noise, check the final
  summary block ("Fetched/Failed" counts) for success.
- Success check: `data/manifest.json` — per-market `generated_at`, `total_companies`,
  and enrichment coverage (`shareholding_coverage`/`credit_ratings_coverage` for NSE,
  `edgar_coverage` for S&P), refreshed by every pipeline run (transform/rebuild included).
  `edgar_coverage` below 1.0 isn't necessarily a failure — a resolved CIK with no real
  10-K history yet (a recent spinoff/restructuring) counts as uncovered, correctly.
- Missing/wrong `SEC_EDGAR_CONTACT` doesn't error, it just degrades silently (generic
  User-Agent, more likely to get throttled) — see First-time setup above.
