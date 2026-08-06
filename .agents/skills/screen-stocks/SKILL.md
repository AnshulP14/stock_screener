---
name: screen-stocks
description: Screen and analyze NSE500 / S&P500 stocks using the local data set — SQL recipes, historical-series drill-downs, industry percentiles, and industry-aware metric interpretation.
---

# Screening stocks

Query `data/screener.db` through `scripts/query.py`. Check `data/manifest.json`
before treating results as current, then run `DESCRIBE nse` or `DESCRIBE snp` for
the live flat schema and one-line metric definitions.

```bash
uv run python scripts/query.py "DESCRIBE nse"
uv run python scripts/query.py "SELECT * FROM nse LIMIT 5"
uv run python scripts/query.py --csv "SELECT symbol, trailing_pe, roce FROM snp"
```

Batch symbols in one query with `IN`, joins, or grouping. NSE and S&P are separate
markets: compare percentiles only within their own table and compare absolute currency
values only when `currency` matches.

## Query surfaces

| Table | Use |
|---|---|
| `nse`, `snp` | Shared flat layout for screening and ranking |
| `nse_companies`, `snp_companies` | Nested snapshots, aligned annual series, ownership, ratings, and peer detail |
| `nse_industry_stats`, `snp_industry_stats` | Per-industry metric distributions |

Flat annual metrics always use `fundamentals_fy`; a missing value remains null. Flat
three-year signals require exact fiscal-year spacing. `industry_peer_count` excludes the
subject company. A `*_percentile` is present only with at least five valid same-industry
peers and means the percentage of those peers with a lower value. It is position, not a
goodness score: low valuation percentiles are usually attractive, while high quality
percentiles are usually attractive.

Ratios and margins are decimals (`0.15` = 15%). Percentiles are on a 0–100 scale.
Market cap is in the row's `currency`.

## Screening recipes

```sql
-- Value with operating quality
SELECT symbol, trailing_pe, enterprise_to_ebitda, operating_margin, roce
FROM nse
WHERE trailing_pe > 0 AND pe_percentile <= 35
  AND operating_margin_percentile >= 60
ORDER BY pe_percentile LIMIT 20;

-- Growth: exact three-year endpoints
SELECT symbol, revenue_cagr_3yr, eps_cagr_3yr, operating_margin_change_3yr
FROM snp
WHERE revenue_cagr_3yr >= 0.12 AND eps_cagr_3yr >= 0.12
ORDER BY revenue_cagr_3yr_percentile DESC LIMIT 20;

-- Capital efficiency and cash quality
SELECT symbol, roce, roce_avg_3yr, fcf_yield, fcf_positive_years_3yr
FROM nse
WHERE roce >= 0.15 AND roce_avg_3yr >= 0.15
  AND fcf_positive_years_3yr = 3
ORDER BY roce_percentile DESC LIMIT 20;

-- Bank health in the same shared view
SELECT symbol, price_to_book, roa, nonperforming_loans_ratio, cet1_ratio
FROM nse
WHERE nonperforming_loans_ratio IS NOT NULL
ORDER BY nonperforming_loans_ratio, cet1_ratio DESC;

-- Drawdown context
SELECT symbol, drawdown_52w, revenue_cagr_3yr, roe
FROM snp
WHERE drawdown_52w <= -0.20
ORDER BY drawdown_52w;
```

## Historical-series drill-down

Every list under `historical_trends` aligns positionally with `fiscal_years`; nulls keep
their place. `[-1]` is therefore the latest fiscal year, not the latest non-null value.

```sql
SELECT symbol,
       historical_trends.fiscal_years[-1] AS fiscal_year,
       historical_trends.revenue[-1] AS revenue,
       historical_trends.operating_margin[-1] AS operating_margin,
       historical_trends.roce[-1] AS roce,
       historical_trends.free_cash_flow[-1] AS free_cash_flow
FROM nse_companies
WHERE symbol IN ('RELIANCE', 'TCS');
```

For peer context, use `industry_comparison.metrics.<metric>`: it carries the subject
value, peer median, relative median gap, percentile, and metric-specific valid peer
count. Ownership, NSE shareholding, and credit ratings remain nested profile details.

Use `data/raw/` only when curated tables lack the requested detail. Extract the smallest
needed subset; annual reports use the `analyse-statements` skill.

## Industry interpretation

- Banks: emphasize price/book, ROA, nonperforming loans, and CET1. Generic ROCE, FCF
  yield, and net-debt/EBITDA may be null or economically unhelpful.
- Capital-intensive businesses: emphasize ROCE, its three-year average, FCF yield,
  capex history, and net-debt/EBITDA.
- Asset-light businesses: emphasize operating margin, ROE/ROA, growth, FCF conversion,
  and share-count change.
- REITs and other specialized structures require their own accounting context; use the
  flat view for discovery and the nested profile or filings for the decision.

For current news, prices, management changes, or regulatory actions, supplement the
local snapshot with web research.
