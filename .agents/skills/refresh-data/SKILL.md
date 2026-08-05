---
name: refresh-data
description: Refresh, update, or bootstrap NSE500 / S&P500 data — unified command, modes, first-time setup, expected runtimes, and failure handling. Use when data is missing or stale, on a fresh clone with no data/ directory, or after an index rebalance.
---

# Refreshing the data

One unified entry point, two pipelines under the hood, for both routine updates and
first-time setup — a fresh clone with no `data/` directory isn't a different tool, it's
just `--mode full-sync` with nothing to skip. `data_refresh.py` does everything:
fundamentals (yfinance/EDGAR), shareholding/credit-ratings (NSE) or institutional
ownership (S&P), annual report / 10-K download + indexing, curated JSON, and
`screener.db` — no separate `build_db.py` / `fetch_annual_reports.py` /
`filings.py index` steps needed for routine use. Long runs go in the background
(`run_in_background`); resumable/idempotent — re-running only fetches what's still
missing or stale.

## First-time setup (no `data/` yet)

```bash
# Install the project (editable, gives you `uv run python scripts/...`)
uv sync

# One-time: a real contact email for SEC EDGAR (S&P only; skip this and S&P still
# works, just with a generic User-Agent SEC is more likely to throttle)
export SEC_EDGAR_CONTACT='you@example.com'          # or write it once to ~/.screener_edgar_email

# Full bootstrap, both markets -- fundamentals, enrichment, annual reports, DB, all in
# one run (~60-120 min total — run in background)
uv run python scripts/data_refresh.py --mode full-sync

# Faster partial start if you just need something to query soon:
uv run python scripts/data_refresh.py --market nse --mode quick-sync --skip-reports   # NSE top-50, ~5 min
```

Both modes sync the universe first (fetch the live constituent list, delete anything
dropped from it) — there's no separate "just sync" mode, a symbol with no file yet is
automatically stale either way. `--mode` then picks *which survivors* get fetched:
`full-sync` fetches all of them; `quick-sync` fetches only stale ones, capped at the
top 50 by market cap (so it stays fast on a routine run *and* on a cold bootstrap, where
"stale" means everything). `--skip-reports` is a separate switch for the slow
annual-report/10-K leg, independent of mode — combine `--mode quick-sync --skip-reports`
for the fastest possible run; without it, even `quick-sync` still downloads/indexes
reports for its 50 symbols. Shareholding/credit-rating enrichment (NSE) and annual
reports (both markets) scrape external sites and may partially fail on a big first run —
the core yfinance/EDGAR dataset is still fully usable without them; a later quick-sync
run fills any gaps. Don't treat a partial enrichment/report failure as a reason to redo
the whole bootstrap.

## Unified command

```bash
# Both markets, quick-sync (default)
uv run python scripts/data_refresh.py

# Both markets, bootstrap from empty (~60-120 min total — run in background)
uv run python scripts/data_refresh.py --mode full-sync

# Single market
uv run python scripts/data_refresh.py --market nse --mode full-sync      # NSE bootstrap, ~60-90 min
uv run python scripts/data_refresh.py --market snp --mode full-sync      # S&P500 bootstrap, varies (EDGAR throttled)
uv run python scripts/data_refresh.py --market nse --mode quick-sync --skip-reports  # NSE top-50, ~5 min
uv run python scripts/data_refresh.py --market nse --symbols RELIANCE TCS  # Specific stocks
uv run python scripts/data_refresh.py --skip-reports  # Any mode, skip the slow report leg
```

### Modes (per market)

| Mode | NSE500 | S&P500 |
|------|--------|--------|
| `quick-sync` | Sync universe + fetch stale symbols, capped at top 50 by market cap | Same |
| `full-sync` | Sync universe + re-fetch every current constituent | Same |

## Rebuild the query DB manually

`data_refresh.py` rebuilds `data/screener.db` at the end of every run automatically —
no separate step needed. Only reach for this if you've hand-edited curated JSON, or
want to sync the DB without a full refresh:

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
  `edgar_coverage` for S&P), refreshed by every pipeline run.
  `edgar_coverage` below 1.0 isn't necessarily a failure — a resolved CIK with no real
  10-K history yet (a recent spinoff/restructuring) counts as uncovered, correctly.
- Missing/wrong `SEC_EDGAR_CONTACT` doesn't error, it just degrades silently (generic
  User-Agent, more likely to get throttled) — see First-time setup above.
