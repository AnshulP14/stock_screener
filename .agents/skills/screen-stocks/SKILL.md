---
name: screen-stocks
description: Screen and analyze NSE500 / S&P500 stocks using the local data set — SQL reference, strategy recipes (value/growth/quality/GARP), sector caveats, and data-schema notes. Use for any stock screening, ranking, comparison, or company-profile question.
---

# Screening stocks

All structured queries go through SQL against `data/screener.db` (DuckDB).
For deep drill-downs beyond the curated trend series (full quarterly history, any of
~500 GAAP tags), see `data/raw/snp/edgar_cache/{SYMBOL}.json` — extract with jq/python,
never read one of these files whole (they run 4MB+ each).

## Query idioms — batch, don't loop

One query replaces several tool calls: use `WHERE symbol IN (...)`, joins, and
`GROUP BY` to answer in one shot instead of looping per-symbol calls. Run every
query through `scripts/query.py`:

```bash
uv run python scripts/query.py "SELECT * FROM nse LIMIT 5"
uv run python scripts/query.py --csv "SELECT symbol, sector, trailing_pe FROM nse"
uv run python scripts/query.py --market snp "SELECT * FROM snp WHERE pe_forward < 20"
```

Show column names: `uv run python scripts/query.py "DESCRIBE nse"`

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
       current_snapshot.profitability.return_on_equity,
       shareholding.promoter[-1] AS latest_promoter_pct
FROM nse_companies WHERE symbol = 'RELIANCE';
```

**Aggregation** (harder to express any other way):
```sql
SELECT sector, count(*) AS n, round(median(roe), 3) AS med_roe
FROM nse GROUP BY sector ORDER BY med_roe DESC;
```

More examples, full column shapes, units, and NSE↔S&P schema deltas: `data/SCHEMA.md`
(run `DESCRIBE {table}` for the live column list — don't trust a written one, it drifts).

## Nested table query patterns

The nested tables (`nse_companies`, `snp_companies`) use DuckDB **structs** and
**arrays** — dot-notation into structs, `[-1]` for the latest entry in an array
(see the drill-down example above). Two patterns worth knowing beyond that:

`sector` in nested tables is JSON-encoded — use
`json_extract_path_text(sector, '')` to filter by sector name (the flat `nse`/`snp`
tables have a plain-string `sector`, no extraction needed).

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

## Units

Ratios/margins are decimals (`roe = 0.15` → 15%); shareholding percentages are whole
numbers (`promoter_latest = 52.3` → 52.3%) — the strategy recipes below assume this.
Full unit/market-difference reference: `data/SCHEMA.md`.

## Strategy recipes

- **VALUE**: `trailing_pe <= 15 AND price_to_book <= 2 AND roe >= 0.12`
- **GROWTH**: `revenue_cagr_3yr >= 0.15 AND eps_cagr_3yr >= 0.15 AND profit_margin >= 0.10`
- **QUALITY**: `roe >= 0.18 AND profit_margin >= 0.12 AND debt_to_equity <= 0.5`
- **GARP**: `trailing_pe <= 25 AND revenue_cagr_3yr >= 0.15 AND eps_cagr_3yr >= 0.15`
- **RELATIVE VALUE**: `pe_percentile <= 35 AND margin_percentile >= 60`
  (cheap vs industry peers but higher quality — percentile columns compare within
  industry, so they work across sectors where absolute P/E cutoffs don't)

## Interpretation caveats

- **Banks/financials**: P/B matters more than P/E; D/E is structurally high and mostly
  meaningless — don't filter Financial Services on `debt_to_equity`.
- **Capital-intensive sectors** (Industrials, Utilities, Real Estate, Basic Materials,
  Energy): check D/E and P/B alongside P/E.
- Very low P/E often signals cyclical peak earnings or a declining business, not a bargain.
- NSE shareholding signals: rising promoter + rising FII holdings are generally positive;
  falling promoter holding warrants a news check (pledging, stake sales).

For anything price-sensitive or news-dependent (recent results, management changes,
regulatory actions), supplement local data with a web research. Local data is a snapshot — check `data/manifest.json` (per-market
`generated_at` + enrichment coverage) and flag staleness in your answer.
