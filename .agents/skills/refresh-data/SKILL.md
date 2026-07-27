---
name: refresh-data
description: Update or bootstrap NSE500 / S&P500 data — unified command, modes, expected runtimes, and failure handling. Use when data is missing or stale, after index rebalances, or when the user asks to refresh/update data.
---

# Refreshing the data

One unified entry point, two pipelines under the hood. Long runs go in the background
(`run_in_background`); both are resumable/idempotent — re-running only fetches what's
still missing or stale.

## Unified command

```bash
# Both markets, incremental (default)
python scripts/data_refresh.py

# Both markets, bootstrap from empty (~60-120 min total — run in background)
python scripts/data_refresh.py --mode full

# Single market
python scripts/data_refresh.py --market nse --mode full      # NSE bootstrap, ~60-90 min
python scripts/data_refresh.py --market us --mode full       # S&P500 bootstrap, varies (EDGAR throttled)
python scripts/data_refresh.py --market nse --mode quick     # NSE top-50, ~5 min
python scripts/data_refresh.py --market nse --mode sync-universe  # NSE index rebalance
python scripts/data_refresh.py --market us --mode rebuild    # S&P500 indices only
python scripts/data_refresh.py --market nse --symbols RELIANCE TCS  # Specific stocks
python scripts/data_refresh.py --market us --dry-run         # Preview only
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
python scripts/build_db.py --market all   # or --market nse / snp
```

Cheap — JSON → DB only, no re-fetch.

## Failure handling

- Failed NSE tickers land in `data/raw/nse/failed_tickers.txt` — retry with `--symbols`.
- Screener.in scraping (shareholding, credit ratings) can rate-limit or break; the core
  yfinance dataset is still valid without it. Report what's missing, don't retry in a loop.
- tqdm progress bars render poorly in non-TTY output — ignore the noise, check the final
  summary block ("Fetched/Failed" counts) for success.
- Success check: `data/manifest.json` — per-market `generated_at`, `total_companies`,
  and enrichment coverage, refreshed by every pipeline run (transform/rebuild included).
- **S&P500 requires `$SEC_EDGAR_CONTACT` env var** (real email for SEC EDGAR User-Agent).
  If the script exits with an error about this, set it before running:
  `export SEC_EDGAR_CONTACT='sp500-screener-bot user@example.com'`
