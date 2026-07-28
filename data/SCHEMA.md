# Data schemas

Reference for the shapes under `data/`. Two tiers: **curated** (`data/nse/`,
`data/snp/`) is what you should Read directly for most questions; **raw**
(`data/raw/`) backs the curated tier and is for drill-down only — see
"Raw tier" below for extraction recipes instead of reading files whole.

## Units

- Ratios and margins are decimals: `0.15` = 15%. Percentiles (`*_percentile`) and
  shareholding fields are whole numbers: `52.3` = 52.3%.
- Market cap / revenue / enterprise value (`market_cap`, `enterprise_value`,
  `total_revenue`) are absolute currency, one unsuffixed field for both markets —
  check the top-level `currency` field ("INR" or "USD") to know which.
- NSE fiscal years end March 31; S&P500 fiscal years vary by company (EDGAR's own `fy`/`fp`).

## Curated tier: `data/{nse,snp}/companies/{SYMBOL}.json`

Both markets share this top-level shape, but with real differences below.

| Field | NSE | S&P500 |
|---|---|---|
| `symbol`, `company_name`, `sector`, `industry`, `currency` | ✓ | ✓ |
| `isin` / `nse_industry` | ✓ (NSE-only) | — |
| `gics_sector`, `gics_industry`, `cik` | — | ✓ (S&P-only; `cik` links to `data/raw/snp/edgar_cache/{SYMBOL}.json`) |
| `current_snapshot` | ✓ | ✓ (no `per_share`; has `financial_health.beta`) |
| `historical_trends` | ✓ | ✓ (different metric set — see below) |
| `key_insights` | ✓ (list of strings) | ✓ |
| `industry_comparison` | ✓ | ✓ |
| `shareholding` | ✓ (NSE-only) | — |
| `credit_ratings` | ✓ (NSE-only) | — |
| `institutional_ownership` | — | ✓ (S&P-only) |

### `current_snapshot`

```
as_of                     # ISO date, fetch date
price_metrics:  trailing_pe, forward_pe, price_to_book, peg_ratio, price_to_sales,
                enterprise_to_ebitda, enterprise_to_revenue
profitability:  profit_margin, gross_margin, operating_margin, ebitda_margin,
                return_on_equity, return_on_assets
financial_health: debt_to_equity, current_ratio, quick_ratio [+ beta, S&P only]
size:           market_cap, enterprise_value, total_revenue, employees
                # absolute currency -- see top-level `currency` field
per_share:      trailing_eps, forward_eps, book_value, revenue_per_share   # NSE only
dividends:      dividend_rate, dividend_yield, payout_ratio
growth:         revenue_growth, earnings_growth, earnings_quarterly_growth
```

### `historical_trends`

NSE (yfinance-sourced, ~4-5 years) and S&P500 (`source: "edgar_xbrl"`, ~6 years) track
an overlapping but non-identical metric set:

| Metric | NSE | S&P500 |
|---|---|---|
| `revenue`, `net_income`, `eps` | ✓ (`values`, `yoy_growth`, `cagr_3yr`, `trend`) | ✓ (`values`, `cagr_3yr`, `trend`; no `yoy_growth`) |
| `operating_margin` | ✓ (`values`, `direction`, `change_3yr`) | ✓ (`values` only) |
| `roe` | ✓ (`values`, `direction`, `avg_3yr`) | — |
| `free_cash_flow` | ✓ (`values`, `trend`, `fcf_positive_years`) | — |
| `debt_to_equity` | ✓ (`values`, `trend`) | — |
| `gross_profit` | — | ✓ (`values_usd`) |
| `operating_cash_flow` | — | ✓ (`values_usd`, `positive_years`, `trend`) |

Don't assume a field exists on both markets — check before writing code/queries that
touch both. Zero in a historical series usually means "not reported," not zero.

### Both-market section

- **`industry_comparison`**: `industry`, `peer_count`, `metrics.{trailing_pe, forward_pe,
  price_to_book, profit_margin, operating_margin, roe, roa, debt_to_equity, ev_to_ebitda,
  revenue_cagr_3yr, eps_cagr_3yr}`, each `{value, industry_median, percentile, vs_median}`
  or `null` for a metric with fewer than 2 peer values in that industry (common for S&P's
  fine-grained GICS sub-industries — some have a single constituent). `vs_median` is a
  relative difference (`(value - median) / abs(median)`), comparable across metrics of
  different scale. Computed against `industry_stats.json`'s percentile bands (below).

### NSE-only sections

- **`shareholding`**: `updated_at`, `quarters` (list), `promoter`/`fii`/`dii`/`public`
  (parallel lists of % by quarter), `num_shareholders`, `trends` (per-holder-type
  `stable`/`increasing`/`decreasing`). Scraped from Screener.in.
- **`credit_ratings`**: `updated_at`, `has_ratings`, `latest_date`, `latest_agency`,
  `latest_action`, `agencies` (list), `recent_entries` (list of `{date, agency, action, url}`).
  Scraped from Screener.in.

### S&P500-only section

- **`institutional_ownership`**: `updated_at`, `pct_insider`, `pct_institutional`,
  `top_holders` (list of `{holder, shares, pct_out}`).

## Curated tier: `data/{nse,snp}/indices/`

- **`screening_summary.json`**: `{generated_at, total_companies, companies: [...]}`.
  Each company row is flat: `symbol, company_name, sector, industry, market_cap, currency,
  trailing_pe, forward_pe, price_to_book, roe, profit_margin, debt_to_equity, beta,
  revenue_cagr_3yr, net_income_cagr_3yr, eps_cagr_3yr, cik, pct_insider, pct_institutional,
  pe_percentile, forward_pe_percentile, price_to_book_percentile, margin_percentile,
  operating_margin_percentile, roe_percentile, roa_percentile, debt_to_equity_percentile,
  ev_to_ebitda_percentile, revenue_cagr_3yr_percentile, eps_cagr_3yr_percentile,
  promoter_latest, promoter_trend, fii_latest, fii_trend, dii_latest, dii_trend,
  public_latest, public_trend`. (`cik`/`pct_insider`/`pct_institutional` are S&P-only,
  `promoter_*`/`fii_*`/`dii_*`/`public_*` are NSE-only — both present as keys with null
  values on the other market's rows.) The schema is declared once in
  `screener/summary.py` (`TEXT_COLUMNS`/`NUMERIC_COLUMNS`/`METRICS_FOR_PERCENTILE`). This
  is the `nse`/`snp` table in `data/screener.db` — see `data/SQL.md`.
- **`industry_stats.json`**: `{industry_name: {company_count, metrics: {metric_name:
  {median, mean, std, p25, p75, min, max, count}}}}` — the percentile bands
  `industry_comparison` in each profile is computed against. Built for both markets;
  S&P's finer-grained GICS sub-industries mean many have `company_count: 1` and empty
  per-metric stats (need 2+ peer values — see `screener.summary.compute_industry_stats`).

Always check `generated_at` before treating the data as current — see AGENTS.md's
Freshness note.

## Raw tier: `data/raw/{nse,snp}/`

**Rule: never Read a raw file whole. Extract the specific field with jq/python.** These
files exist to answer questions the curated trend series can't — they are not meant to
be loaded into context wholesale.

### `data/raw/nse/`

- `current_metrics.csv`, `historical_annual.csv` — the flat tabular inputs the curated
  NSE JSONs are built from (~40 statement line items per company-year in the annual file).
- `quarterly_raw.json` — `{"{SYMBOL}.NS": {"info": {...full yfinance info blob...}}}`
  for all 311 currently-fetched NSE symbols. Business summaries, addresses, and other
  yfinance fields not retained in the curated profile.
- `annual_reports/{SYMBOL}/` — scraped Screener.in annual report PDFs.
- `failed_tickers.txt` — symbols that failed the last fetch; retry with `--symbols`.

### `data/raw/snp/`

- `edgar_cache/{SYMBOL}.json` — full SEC XBRL companyfacts payload per company (~4MB+
  each, 503 tags for a mature filer like AAPL). Shape: `{cik, entityName, facts: {"us-gaap":
  {TAG: {units: {USD: [{start, end, val, accn, fy, fp, form, filed}, ...]}}}, "dei": {...}}}`.
  Full quarterly + annual history back to ~2016, with filing-level provenance — this is
  where to look for any GAAP tag not in `historical_trends` (e.g. inventory, capex,
  interest expense, share counts).

  Extraction example (single company, one tag):
  ```bash
  jq '.facts["us-gaap"].Revenues.units.USD[] | {end, val, form}' \
    data/raw/snp/edgar_cache/AAPL.json
  ```

  For cross-company queries, prefer DuckDB over shell loops — it can query the JSON
  files in place with no derived artifact:
  ```sql
  SELECT symbol, val, end
  FROM read_json_auto('data/raw/snp/edgar_cache/*.json', filename=true)
  -- (flattening facts.us-gaap.<tag>.units.USD requires UNNESTing the nested struct;
  --  see the screen-stocks skill for a worked recipe once Phase 3 lands)
  ```
- `sp500_universe.json` — `{updated_at, total, companies: [{symbol, company_name,
  gics_sector, gics_industry}]}`. The Wikipedia-sourced constituent list.

No `cik_map.json`: CIK is only needed at fetch time to build the SEC URL, never used
as a lookup key elsewhere in the pipeline, so it's resolved fresh in memory each run
instead of cached to disk.
