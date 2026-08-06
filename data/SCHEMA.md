# Data schema and SQL semantics

`data/screener.db` is the query surface for curated NSE500 and S&P500 data. Run
`DESCRIBE nse` or `DESCRIBE snp` through `scripts/query.py` for the live flat columns,
types, and one-line definitions; this file records semantics that SQL cannot express.

## Tables

| Table | Shape |
|---|---|
| `nse`, `snp` | Shared one-row-per-company screening layout |
| `nse_companies`, `snp_companies` | Nested company profiles and annual series |
| `nse_industry_stats`, `snp_industry_stats` | Per-industry distributions |

NSE and S&P tables are separate. Industry statistics and percentiles never mix markets.

## Time and missingness

- `snapshot_as_of` dates market-data observations.
- `fundamentals_fy` is the latest completed fiscal year used by every annual flat metric.
- A missing latest-year value stays null; an older non-null observation is not substituted.
- Three-year CAGRs and changes require endpoints exactly three fiscal years apart.
- Three-year averages and counts require all three consecutive fiscal-year observations.

Nested `historical_trends` is retained as the legacy storage key, but its contents are
aligned historical series rather than trend labels. `fiscal_years` is ascending and every
metric list has the same positional alignment. Historical drill-downs should select the
full year list and full metric arrays. Use `[-1]` only for an explicitly latest-value query;
it preserves a final null rather than backfilling an older observation.

## Units and formulas

- Ratios, margins, growth, drawdown, and yields are decimals (`0.15` = 15%).
- Percentiles are 0–100 and equal the percentage of valid peers strictly below the
  subject value.
- Market cap and statement amounts are absolute values in `currency` (`INR` or `USD`).
- Capex is stored as a positive cash-outflow magnitude; FCF is operating cash flow minus
  capex.
- ROE, ROA, and ROCE use consecutive-year average balance-sheet denominators.
- S&P FR Y-9C absolute balances are normalized from USD thousands to USD.

## Industry context

`industry_peer_count` counts other companies in the same market and industry regardless
of metric availability. A flat percentile requires at least five valid peers. Nested
`industry_comparison.metrics.<metric>` also exposes `valid_peer_count`, peer median,
relative median gap, and percentile.

Percentiles are neutral positions. Lower is commonly preferable for valuation and
nonperforming loans; higher is commonly preferable for margins, returns, cash yield,
growth, and CET1. Industry economics still determine relevance.

## Market-specific profile detail

The flat layouts are identical. Market-specific information remains nested:

- NSE: ISIN, NSE industry, shareholding, credit ratings, and net NPA history.
- S&P: CIK, RSSD where mapped, GICS metadata, and institutional ownership.

S&P deposits remain null because no validated consolidated FR Y-9C deposit field is
contracted. Shared total-capital ratio and S&P interest coverage are absent rather than
represented as always-null flat columns.

## Examples

```sql
SELECT symbol, fundamentals_fy, trailing_pe, roce, fcf_yield
FROM nse
WHERE pe_percentile <= 35 AND roce_percentile >= 60
ORDER BY fcf_yield DESC;

SELECT symbol,
       historical_trends.fiscal_years,
       historical_trends.revenue,
       historical_trends.roce
FROM snp_companies
WHERE symbol = 'AAPL';
```

`data_refresh.py` rebuilds the selected market's tables after refreshing. Use
`scripts/build_db.py` only after hand-editing curated JSON. Raw filings and EDGAR caches
are drill-down inputs, not query tables; extract only the needed section or tag.
