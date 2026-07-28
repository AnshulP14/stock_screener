---
name: screen-stocks
description: Screen and analyze NSE500 / S&P500 stocks using the local data set — SQL reference, strategy recipes (value/growth/quality/GARP), sector caveats, and data-schema notes. Use for any stock screening, ranking, comparison, or company-profile question.
---

# Screening stocks

All structured queries go through SQL against `data/screener.db` (DuckDB). Default
market is NSE (`nse`/`nse_companies` tables); use `snp`/`snp_companies` for S&P500.
All example queries and strategy recipes work on both markets — only column names
differ for market cap (INR vs USD) and ownership data.
For deep drill-downs beyond the curated trend series (full quarterly history, any of
~500 GAAP tags), see `data/raw/snp/edgar_cache/{SYMBOL}.json` — extract with jq/python,
never Read one of these files whole (they run 4MB+ each).

## Query idioms — batch, don't loop

One query replaces several tool calls — use `python3 scripts/query.py "..."`, or with
zero extra deps:

```bash
python3 -c "
import duckdb
con = duckdb.connect('data/screener.db')
for row in con.execute('SELECT * FROM nse LIMIT 5').fetchall():
    print(row)
"
```

Show column names: `python3 -c "import duckdb; [print(c[0]) for c in duckdb.connect('data/screener.db').execute('DESCRIBE nse').fetchall()]"`

**Multi-symbol comparison in one query:**
```sql
SELECT symbol, trailing_pe, roe, profit_margin
FROM nse WHERE symbol IN ('RELIANCE', 'TCS', 'BHEL');
```

**Sector sweep, ranked:**
```sql
SELECT symbol, company_name, roe, profit_margin
FROM nse WHERE sector = 'Healthcare' ORDER BY roe DESC LIMIT 10;
```

**Filter + rank + limit (one-shot shortlist):**
```sql
SELECT symbol, trailing_pe, roe, profit_margin
FROM nse
WHERE trailing_pe <= 15 AND roe >= 0.15 AND profit_margin >= 0.10
ORDER BY roe DESC LIMIT 5;
```

**Deep single-company drill-down** (nested table, dot-notation into structs,
`[-1]` for latest in a list):
```sql
SELECT symbol,
       current_snapshot.profitability.return_on_equity AS roe,
       shareholding.promoter[-1] AS latest_promoter_pct
FROM nse_companies WHERE symbol = 'RELIANCE';
```

**Aggregation** (harder to express any other way):
```sql
SELECT sector, count(*) AS n, round(median(roe), 3) AS med_roe
FROM nse GROUP BY sector ORDER BY med_roe DESC;
```

More examples in `data/SQL.md`.

## Nested table schema

The nested tables (`nse_companies`, `snp_companies`) use DuckDB **structs** and **arrays**.

| Field | Description |
|---|---|
| `current_snapshot.price_metrics.trailing_pe` | Trailing P/E |
| `current_snapshot.price_metrics.price_to_book` | P/B ratio |
| `current_snapshot.profitability.return_on_equity` | ROE |
| `current_snapshot.profitability.profit_margin` | Profit margin |
| `current_snapshot.growth.revenue_growth` | Single-period revenue growth % |
| `historical_trends.revenue.cagr_3yr` | 3-year revenue CAGR |
| `historical_trends.eps.cagr_3yr` | 3-year EPS CAGR |
| `historical_trends.roe.values` | Historical ROE array |
| `shareholding.promoter[-1]` | Latest promoter holding % (array, `[-1]` = newest) |
| `shareholding.fii[-1]` | Latest FII holding |
| `isin`, `nse_industry` | NSE-only, top-level (not nested) — `isin` from NSE's official CSV; `nse_industry` is NSE's own label, can differ from GICS-style `industry` |
| `gics_sector`, `gics_industry`, `cik` | S&P-only, top-level — GICS classification from Wikipedia's constituent table; `cik` links to `data/raw/snp/edgar_cache/{SYMBOL}.json` for full XBRL history beyond the curated trend series |
| `institutional_ownership.pct_institutional` | S&P: % held by institutions (`pct_insider` for insider %) |
| `institutional_ownership.top_holders` | S&P: `[{holder, shares, pct_out}]` — needs `UNNEST` (see below) |
| `industry_comparison.metrics.trailing_pe.vs_median` | Either market: relative diff vs industry median, `(value - median) / abs(median)` — `null` if the industry has fewer than 2 peers for that metric |
| `industry_comparison.peer_count` | How many companies a row's percentiles/`vs_median` were computed against — check this before trusting a percentile from a thin industry |

`sector` in nested tables is JSON-encoded — use
`json_extract_path_text(sector, '')` to filter by sector name.
Run `DESCRIBE nse_companies` to see the full column layout.

**Top institutional holders** (array of structs — needs `UNNEST`):
```sql
SELECT symbol, h.holder, h.pct_out
FROM snp_companies, UNNEST(institutional_ownership.top_holders) AS t(h)
WHERE symbol = 'AAPL' ORDER BY h.pct_out DESC LIMIT 5;
```

**Peer-checked relative value** (nested `industry_comparison`, not the flat
`*_percentile` columns — gives `peer_count` alongside the comparison, so a thin
industry doesn't get silently trusted):
```sql
SELECT symbol,
       industry_comparison.peer_count,
       industry_comparison.metrics.trailing_pe.percentile AS pe_pctile,
       industry_comparison.metrics.trailing_pe.vs_median AS pe_vs_median
FROM snp_companies WHERE symbol = 'AAPL';
```

## Tables

| Table | Shape |
|---|---|
| `nse`, `snp` | Flat, one row per company — fast simple screens |
| `nse_companies`, `snp_companies` | Nested, full profile — historical trends, shareholding, credit ratings |
| `nse_industry_stats`, `snp_industry_stats` | One row per industry — percentile bands |

## Units & market differences

Ratios/margins are decimals (`roe = 0.15` → 15%); shareholding percentages are whole
numbers (`promoter_latest = 52.3` → 52.3%). NSE fiscal years end March 31; US fiscal
years vary.

Both markets share one `market_cap` column (unsuffixed) — check `currency` ("INR" or
"USD") before comparing across markets, since raw market-cap/size values are never
directly comparable between them regardless. NSE additionally has shareholding
(promoter/FII/DII) and `isin`/`nse_industry`; S&P has institutional/insider %
(`institutional_ownership`) and `gics_sector`/`gics_industry`/`cik` instead. S&P has
`beta`; NSE has `net_income_cagr_3yr` and `ev_to_ebitda` percentiles.
`cik`/`pct_insider`/`pct_institutional` are flat `snp`-table columns too (S&P-only,
null on `nse`); `isin`/`nse_industry`/`gics_sector`/`gics_industry` are nested-table-only
(not flattened — use `nse_companies`/`snp_companies`).
All core screening columns (`trailing_pe`, `roe`, `profit_margin`, `price_to_book`,
`revenue_cagr_3yr`, `eps_cagr_3yr`) are identical across both markets.

## Strategy recipes

- **VALUE**: `trailing_pe <= 15 AND price_to_book <= 2 AND roe >= 0.12`
- **GROWTH**: `revenue_cagr_3yr >= 0.15 AND eps_cagr_3yr >= 0.15 AND profit_margin >= 0.10`
- **QUALITY**: `roe >= 0.18 AND profit_margin >= 0.12 AND debt_to_equity <= 0.5`
- **GARP**: `trailing_pe <= 25 AND revenue_cagr_3yr >= 0.15 AND eps_cagr_3yr >= 0.15`
- **RELATIVE VALUE**: `pe_percentile <= 35 AND margin_percentile >= 60`
  (cheap vs industry peers but higher quality — percentile columns compare within
  industry, so they work across sectors where absolute P/E cutoffs don't)
- **S&P INSTITUTIONAL CONFIDENCE** (`snp` only): `pct_institutional >= 70 AND
  pct_insider >= 1` — heavy institutional ownership plus some insider skin in the
  game. Check `institutional_ownership.top_holders` (nested table) for *who* —
  a handful of active managers reads differently than an all-indexer roster.
- Add `trailing_pe > 0` (or `IS NOT NULL`) to exclude loss-making companies from any screen.

## Interpretation caveats

- **Banks/financials**: P/B matters more than P/E; D/E is structurally high and mostly
  meaningless — don't filter Financial Services on `debt_to_equity`.
- **Capital-intensive sectors** (Industrials, Utilities, Real Estate, Basic Materials,
  Energy): check D/E and P/B alongside P/E.
- Very low P/E often signals cyclical peak earnings or a declining business, not a bargain.
- NULL `debt_to_equity` means "not reported" — many companies legitimately report none;
  don't treat it as zero.
- Zero revenue in historical trend arrays means "not reported", not zero sales.
- NSE shareholding signals: rising promoter + rising FII holdings are generally positive;
  falling promoter holding warrants a news check (pledging, stake sales).
- High `pct_institutional` isn't automatically a quality signal — S&P 500 membership
  itself pulls in mechanical index-fund ownership (most `top_holders` rosters lead
  with BlackRock/Vanguard/State Street for exactly this reason). It's more useful as
  a floor-level sanity check (near-zero institutional ownership on an S&P name would
  be unusual) than as a differentiator between S&P names.
- `pct_institutional` can legitimately exceed 100% (yfinance/13F double-counting from
  securities lending — shares lent out get reported by both the original holder and
  the borrower) — don't treat a value above 100 as a data error.
- `industry_comparison.peer_count` of 1-2 means "not enough peers to compare" —
  common for S&P's fine-grained GICS sub-industries. Don't read a `null`
  `industry_comparison` metric as the company being unusual; check `peer_count` first.

For anything price-sensitive or news-dependent (recent results, management changes,
regulatory actions), supplement local data with WebSearch using the preferred domains in
AGENTS.md. Local data is a snapshot — check `data/manifest.json` (per-market
`generated_at` + enrichment coverage) and flag staleness in your answer.
