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

One query can replace what would otherwise be several tool calls. Use the wrapper
`python3 scripts/query.py "..."` or the Python one-liner pattern below.

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

`sector` in nested tables is JSON-encoded — use
`json_extract_path_text(sector, '')` to filter by sector name.
Run `DESCRIBE nse_companies` to see the full column layout.

## Querying from the shell

**Wrapper script (recommended):**
```bash
python3 scripts/query.py "SELECT * FROM nse LIMIT 5"
```

**Python one-liner (zero extra deps):**
```bash
python3 -c "
import duckdb
con = duckdb.connect('data/screener.db')
for row in con.execute('SELECT * FROM nse LIMIT 5').fetchall():
    print(row)
"
```

**Show column names:**
```bash
python3 -c "import duckdb; [print(c[0]) for c in duckdb.connect('data/screener.db').execute('DESCRIBE nse').fetchall()]"
```

## Tables

| Table | Shape |
|---|---|
| `nse`, `snp` | Flat, one row per company — fast simple screens |
| `nse_companies`, `snp_companies` | Nested, full profile — historical trends, shareholding, credit ratings |
| `nse_industry_stats`, `snp_industry_stats` | One row per industry — percentile bands |

## Units & market notes

Ratios/margins are decimals (`roe = 0.15` → 15%); shareholding percentages are whole
numbers (`promoter_latest = 52.3` → 52.3%). NSE uses `market_cap_inr`, S&P uses
`market_cap_usd` — don't compare raw values across markets. NSE fiscal years end
March 31; US fiscal years vary.

## Strategy recipes

- **VALUE**: `trailing_pe <= 15 AND price_to_book <= 2 AND roe >= 0.12`
- **GROWTH**: `revenue_cagr_3yr >= 0.15 AND eps_cagr_3yr >= 0.15 AND profit_margin >= 0.10`
- **QUALITY**: `roe >= 0.18 AND profit_margin >= 0.12 AND debt_to_equity <= 0.5`
- **GARP**: `trailing_pe <= 25 AND revenue_cagr_3yr >= 0.15 AND eps_cagr_3yr >= 0.15`
- **RELATIVE VALUE**: `pe_percentile <= 35 AND margin_percentile >= 60`
  (cheap vs industry peers but higher quality — percentile columns compare within
  industry, so they work across sectors where absolute P/E cutoffs don't)
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

## Cross-checking & market differences

- NSE tables include shareholding (promoter/FII/DII); S&P uses institutional/insider %.
- S&P has `beta`; NSE has `net_income_cagr_3yr` and `ev_to_ebitda` percentiles.
- All core screening columns (`trailing_pe`, `roe`, `profit_margin`, `price_to_book`,
  `revenue_cagr_3yr`, `eps_cagr_3yr`) are identical across both markets.

For anything price-sensitive or news-dependent (recent results, management changes,
regulatory actions), supplement local data with WebSearch using the preferred domains in
AGENTS.md. Local data is a snapshot — check `data/manifest.json` (per-market
`generated_at` + enrichment coverage) and flag staleness in your answer.
