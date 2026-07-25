# Querying the data with SQL (DuckDB)

The curated screening data lives in `data/screener.db` — a DuckDB file with six
tables, three per market. This is the **only query surface** for anything under
`data/nse/` or `data/snp/`: it covers both quick screens and deep single-company
drill-down, so there's no separate jq/Read path to learn for curated data. Write
plain SQL — no bespoke flags, no nested-path traversal syntax.

`data/raw/` (large per-symbol payloads, PDFs, EDGAR cache) is a different tier —
still extract with jq/python there, never Read whole. See `data/SCHEMA.md`.

## Setup (one-time)

```bash
python3 -m pip install duckdb   # pandas is already a project dependency
```

The DB is **not** rebuilt automatically by `data_refresh.py` / the market pipelines —
they only regenerate the curated JSON (`screening_summary.json`, `industry_stats.json`).
Rebuild `screener.db` explicitly after any fetch/transform run:

```bash
python3 scripts/build_db.py --market nse   # or snp, or all (default)
```

## Tables

NSE and S&P are **separate tables, not unioned** — the two markets are analyzed
independently (no cross-market ranking/decisions), so there's no shared schema to
reconcile and no risk of a query silently mixing INR and USD figures.

| Table | Source | Shape |
|---|---|---|
| `nse`, `snp` | `{market}/indices/screening_summary.json` | flat, one row per company — fast simple screens |
| `nse_companies`, `snp_companies` | `{market}/companies/*.json` | one row per company, **nested** (structs/lists preserved) — full profile, deep drill-down |
| `nse_industry_stats`, `snp_industry_stats` | `{market}/indices/industry_stats.json` | one row per industry — percentile bands (median/mean/std/p25/p75/min/max) |

Run `DESCRIBE {table}` to see exact columns — flat tables have ~40+ columns
(percentiles/vs_industry for every metric), and nested tables mirror
`data/SCHEMA.md`'s per-company shape exactly (`current_snapshot.profitability.*`,
`historical_trends.*`, `shareholding.*`, etc.).

**Units unchanged from the JSON:** ratios/margins are decimals (`roe = 0.15` → 15%);
shareholding percentages are whole numbers (`promoter_latest = 52.3` → 52.3%);
market cap is absolute (`market_cap_inr` / `market_cap_usd`).

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

-- Industry percentile bands
SELECT industry, company_count, metrics.roe.median AS median_roe
FROM nse_industry_stats
ORDER BY company_count DESC LIMIT 10;
```

## Maintenance

- The DB is **not** rebuilt automatically by the fetch/transform pipeline (`core.index.build_indices`,
  called from `markets/nse.py` and `markets/us.py`) — only `core.index.rebuild_market_db`
  (via `python3 scripts/build_db.py`) touches `screener.db`.
- A single-market run only rebuilds that market's three tables
  (`CREATE OR REPLACE TABLE {market}...`) — the other market's tables are
  untouched.
- The JSON files remain the source of truth. Each market's `data/manifest.json`
  entry carries a `db` sub-key (`rebuilt_at`, `tables`) written only when
  `rebuild_market_db` runs — compare it against that market's `generated_at` to
  tell whether the DB is behind the curated JSON. Re-run `python3
  scripts/build_db.py --market {market}` to bring it current without re-fetching.
