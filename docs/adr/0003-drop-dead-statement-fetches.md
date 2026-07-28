# 0003: Drop unused quarterly/annual yfinance statement fetches

**Status:** Accepted (Phase 7)

## Context

`screener.fetch.fetch_ticker_data` fetched six yfinance statement DataFrames
per company: `quarterly_income`/`quarterly_balance`/`quarterly_cashflow` and
`annual_income`/`annual_balance`/`annual_cashflow`. `data/SCHEMA.md` also
documented a `data/raw/nse/quarterly_raw.json` file as if it existed.

Auditing consumers (grep, not assumption) found:

- The three `quarterly_*` DataFrames were consumed by **nothing**, for
  **either** market. `build_historical_trends` (NSE's yfinance-based trend
  builder) only ever reads `annual_*`. No other function in `screener/`
  references `quarterly_income`/`quarterly_balance`/`quarterly_cashflow` at
  all. This predates Phase 5 — it was already true before EDGAR existed.
- `quarterly_raw.json` was never written by any code — not even historically.
  `fetch_ticker_data` returns an in-memory dict per symbol; nothing in
  `screener/index.py`'s CSV export (`_save_raw_csvs`, NSE-only) or anywhere
  else serializes a quarterly JSON blob. `data/raw/nse/` on disk confirms it:
  no such file.
- Since ADR 0001, the three `annual_*` DataFrames are *also* dead weight for
  S&P specifically: `MarketConfig.uses_edgar` markets route through
  `build_historical_trends_edgar`, which never touches `raw["annual_*"]` —
  those DataFrames are fetched, then simply discarded for every S&P company.

Six yfinance calls per company, three of them (or six, for S&P) never read,
means real wasted work: extra requests against Yahoo's rate limiter, extra
time per company on every fetch run, extra exposure to throttling.

## Decision

- Remove the `quarterly_*` fetch entirely — no market ever used it, and nothing
  should preserve a fetch nobody reads "just in case." Removed the
  `quarterly_raw.json` doc entries from `data/SCHEMA.md`/`AGENTS.md` rather
  than build a writer for a file nothing has ever needed.
- Make the `annual_*` fetch conditional: `fetch_ticker_data` gained an
  `annual_statements: bool = True` parameter; `markets/__init__.py` passes
  `annual_statements=not market.uses_edgar`, so EDGAR-driven markets skip it.

## Consequences

- A full S&P run now makes 3 fewer yfinance calls per company than before
  Phase 5 made EDGAR the source of truth (annual statements skipped) plus 3
  fewer than every prior run (quarterly statements removed for both markets)
  — 6 fewer per company, ~3,000 fewer requests across a full ~503-company run.
- `fetch_ticker_data`'s return shape lost the three `quarterly_*` keys
  entirely (not just emptied) — any future code expecting them will fail
  fast with a `KeyError` rather than silently operating on an always-empty
  DataFrame.
- If a future need for quarterly detail arises (e.g. sub-annual trend lines),
  it should be re-added as an explicit, consumed feature — not restored as a
  standing fetch with no reader.
