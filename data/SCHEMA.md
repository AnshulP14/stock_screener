# Data schema & SQL

`data/screener.db` (DuckDB) is the **only query surface** for anything under
`data/nse/` or `data/snp/` — six tables, three per market, covering both quick
screens and deep single-company drill-down. Write plain SQL; there's no
bespoke flag syntax or nested-path traversal to learn. `data/raw/` (large
per-symbol payloads, PDFs, EDGAR cache) is a different tier, not in the DB —
see "Raw tier" below, extract with jq/python, never Read a raw file whole.

**Column names and types are not repeated here — they'd be a second source of
truth that drifts from the code.** Run `DESCRIBE {table}` for the real,
current shape (DuckDB expands nested structs recursively, so it's a complete
field list, not just top-level columns):

```bash
uv run python scripts/query.py "DESCRIBE nse"             # flat table, ~40 columns
uv run python scripts/query.py "DESCRIBE nse_companies"   # nested, full struct tree
```

This file covers what `DESCRIBE` can't: what the fields *mean*, their units,
and where NSE and S&P500 genuinely differ.

## Setup (one-time)

```bash
uv sync   # installs duckdb + pandas, gives you `uv run python scripts/query.py`
```

`data_refresh.py` rebuilds `screener.db` automatically at the end of every run —
no separate step needed for routine use. Only rebuild it by hand after
hand-editing curated JSON:

```bash
uv run python scripts/build_db.py --market nse   # or snp, or all (default)
```

## Tables

NSE and S&P are **separate tables, not unioned** — the two markets are
analyzed independently (no cross-market ranking/decisions), so there's no
shared schema to reconcile and no risk of a query silently mixing INR and USD
figures.

| Table | Source | Shape |
|---|---|---|
| `nse`, `snp` | `{market}/indices/screening_summary.json` | flat, one row per company — fast simple screens |
| `nse_companies`, `snp_companies` | `{market}/companies/*.json` | one row per company, **nested** (structs/lists preserved) — full profile, deep drill-down |
| `nse_industry_stats`, `snp_industry_stats` | `{market}/indices/industry_stats.json` | one row per industry — percentile bands (median/mean/std/p25/p75/min/max) |

## Units

- Ratios and margins are decimals: `roe = 0.15` → 15%. Percentiles
  (`*_percentile`) and shareholding fields are whole numbers:
  `promoter_latest = 52.3` → 52.3%.
- Market cap / revenue / enterprise value (`market_cap`, `enterprise_value`,
  `total_revenue`) are absolute currency, one unsuffixed column for both
  markets — check `currency` ("INR" or "USD") before comparing across markets;
  raw values are never directly comparable between them regardless.
- NSE fiscal years end March 31; S&P500 fiscal years vary by company (EDGAR's
  own `fy`/`fp`).

## Market differences

- NSE has `shareholding` (promoter/FII/DII, scraped from Screener.in) and
  `credit_ratings` (also Screener.in); S&P has `institutional_ownership`
  (`pct_insider`/`pct_institutional`/`top_holders`, from yfinance) instead.
  `cik`/`pct_insider`/`pct_institutional` are flat `snp`-table columns too
  (null on `nse`).
- NSE has `isin`/`nse_industry` (top-level, NSE-only); S&P has
  `gics_sector`/`gics_industry` (top-level, S&P-only) — `cik` links to
  `data/raw/snp/edgar_cache/{SYMBOL}.json` for full XBRL history beyond the
  curated trend series. These four are nested/flat-table columns only, not
  flattened onto the other market's rows.
- `historical_trends` sourcing differs: NSE is yfinance-derived (~4-5 years,
  `source: "yfinance"`); S&P is SEC EDGAR XBRL (~6 years,
  `source: "edgar_xbrl"`). The metric set overlaps but isn't identical — e.g.
  NSE's `revenue`/`net_income`/`eps` carry `yoy_growth`, S&P's don't; NSE has
  `roe`/`free_cash_flow`/`debt_to_equity` series, S&P doesn't (no
  balance-sheet data in EDGAR's XBRL feed for these). Don't assume a field
  exists on both markets — check with `DESCRIBE` before writing a query that
  touches both. Zero in a historical series usually means "not reported," not
  zero.
- S&P's `beta` lives under `current_snapshot.financial_health` (and flat on
  `snp`); NSE has no `beta` but has `current_snapshot.per_share` (S&P
  doesn't) and NSE-only flat percentiles (`net_income_cagr_3yr`,
  `ev_to_ebitda_percentile`).
- All core screening columns (`trailing_pe`, `roe`, `profit_margin`,
  `price_to_book`, `revenue_cagr_3yr`, `eps_cagr_3yr`) are identical across
  both markets.

## Semantic notes (not visible from `DESCRIBE`)

- **`industry_comparison`** (both markets): `metrics.<name>` is `null` when
  the industry has fewer than 2 peer values for that metric — common for
  S&P's fine-grained GICS sub-industries, some of which have a single
  constituent. Check `peer_count` before trusting a `null` as "unusual" rather
  than "not enough peers to compare." `vs_median` is a relative difference
  (`(value - median) / abs(median)`), comparable across metrics of different
  scale. Computed against `nse_industry_stats`/`snp_industry_stats`'s
  percentile bands.
- **`screening_summary.json` / the flat `nse`/`snp` tables**: the column list
  is declared once in `screener/summary.py`
  (`TEXT_COLUMNS`/`NUMERIC_COLUMNS`/`METRICS_FOR_PERCENTILE`) — that's the
  actual source of truth if you need to know why a column exists, not this
  file.
- `sector` in the nested tables (`nse_companies`/`snp_companies`) is
  JSON-encoded — use `json_extract_path_text(sector, '')` to filter by
  sector name there (the flat `nse`/`snp` tables have a plain-string
  `sector` column, no extraction needed).
- Always check `generated_at` (in each table, or `data/manifest.json`) before
  treating results as current — see AGENTS.md's Freshness note.

## Example queries

```sql
-- Value + quality screen, flat table
SELECT symbol, sector, trailing_pe, roe
FROM nse WHERE trailing_pe < 15 AND roe > 0.15
ORDER BY roe DESC LIMIT 20;

-- Range + multi-condition + sector filter (harder to express as CLI flags)
SELECT symbol, trailing_pe, roe
FROM snp
WHERE trailing_pe BETWEEN 10 AND 20
  AND profit_margin > 0.20
  AND sector = 'Technology'
ORDER BY roe DESC;

-- Aggregation: median ROE per sector
SELECT sector, count(*) AS n, round(median(roe), 3) AS med_roe
FROM nse GROUP BY sector ORDER BY med_roe DESC;

-- Deep drill-down on the nested table: dot-notation into structs, [-1] for latest in a list
SELECT symbol, current_snapshot.profitability.return_on_equity AS roe
FROM nse_companies
WHERE current_snapshot.profitability.return_on_equity > 0.3
ORDER BY roe DESC LIMIT 10;

SELECT symbol, shareholding.promoter[-1] AS latest_promoter_pct
FROM nse_companies
WHERE shareholding.promoter[-1] > 70;

-- Top institutional holders (array of structs, needs UNNEST)
SELECT symbol, h.holder, h.pct_out
FROM snp_companies, UNNEST(institutional_ownership.top_holders) AS t(h)
WHERE symbol = 'AAPL' ORDER BY h.pct_out DESC LIMIT 5;

-- Industry percentile bands
SELECT industry, company_count, metrics.roe.median AS median_roe
FROM nse_industry_stats
ORDER BY company_count DESC LIMIT 10;
```

## Raw tier: `data/raw/{nse,snp}/`

Not in `screener.db`. **Rule: never Read a raw file whole. Extract the
specific field with jq/python.** These files exist to answer questions the
curated trend series can't — they are not meant to be loaded into context
wholesale.

### `data/raw/nse/`

- `current_metrics.csv`, `historical_annual.csv` — the flat tabular inputs the
  curated NSE JSONs are built from (~40 statement line items per
  company-year in the annual file).
- `annual_reports/{SYMBOL}/` — scraped Screener.in annual report PDFs.
- `failed_tickers.txt` — symbols that failed the last fetch; retry with
  `--symbols`.

### `data/raw/snp/`

- `edgar_cache/{SYMBOL}.json` — full SEC XBRL companyfacts payload per company
  (~4MB+ each, 503 tags for a mature filer like AAPL). Shape: `{cik,
  entityName, facts: {"us-gaap": {TAG: {units: {USD: [{start, end, val, accn,
  fy, fp, form, filed}, ...]}}}, "dei": {...}}}`. Full quarterly + annual
  history back to ~2016, with filing-level provenance — this is where to look
  for any GAAP tag not in `historical_trends` (e.g. inventory, capex,
  interest expense, share counts).

  Extraction example (single company, one tag):
  ```bash
  jq '.facts["us-gaap"].Revenues.units.USD[] | {end, val, form}' \
    data/raw/snp/edgar_cache/AAPL.json
  ```

  For cross-company queries, prefer DuckDB over shell loops — it can query
  the JSON files in place with no derived artifact:
  ```sql
  SELECT symbol, val, end
  FROM read_json_auto('data/raw/snp/edgar_cache/*.json', filename=true)
  -- flattening facts.us-gaap.<tag>.units.USD requires UNNESTing the nested struct
  ```
- `sp500_universe.json` — `{updated_at, total, companies: [{symbol,
  company_name, gics_sector, gics_industry}]}`. The Wikipedia-sourced
  constituent list.

No `cik_map.json`: CIK is only needed at fetch time to build the SEC URL,
never used as a lookup key elsewhere in the pipeline, so it's resolved fresh
in memory each run instead of cached to disk.

## Maintenance

- `run_pipeline` (`screener/markets/__init__.py`) rebuilds indices *and*
  `screener.db` at the end of every run, before its slower annual-report /
  10-K download step finishes -- DB freshness never waits on that.
- A single-market run only rebuilds that market's three tables
  (`CREATE OR REPLACE TABLE {market}...`) — the other market's tables are
  untouched.
- The JSON files remain the source of truth. Each market's
  `data/manifest.json` entry carries a `db` sub-key (`rebuilt_at`, `tables`) —
  compare it against that market's `generated_at` to tell whether the DB is
  behind the curated JSON (only possible if `screener.db` was rebuilt by hand
  mid-way, or a run was interrupted before finishing). Re-run
  `scripts/build_db.py --market {market}` to bring it current without
  re-fetching.
