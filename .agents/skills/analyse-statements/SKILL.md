---
name: analyse-statements
description: Analyse 10-K annual report filings — navigate long filings by section, search across years, track changes. Use when asked to analyse 10-K filings or annual report text.
---

# Analyse annual statements

10-K filings are huge (~2.5MB / ~150k tokens each). **Never read one whole.** Each
filing has a durable navigation index next to it; drive it with `scripts/filings.py`
using the loop: **outline → grep → windowed read → expand only where there's signal.**

## Data layout

```
data/raw/snp/annual_reports/{SYMBOL}/
  {SYMBOL}_10K_{YEAR}.htm         # source iXBRL filing
  {SYMBOL}_10K_{YEAR}.txt         # normalized text (offsets address this)
  {SYMBOL}_10K_{YEAR}.index.json  # section map + quality flag
```

If the `.txt` / `.index.json` are missing, build them (no re-download):
```bash
uv run python scripts/filings.py index --symbols {SYMBOL}       # one/few
uv run python scripts/filings.py index --all                    # whole corpus
```
If the `.htm` itself is missing: `uv run python scripts/fetch_annual_reports_snp.py --symbol {SYMBOL}`.

NSE reports are PDFs on the India side — out of scope here; no PDF parser installed.

## The navigation loop

**1. Outline first — always.** Cheap, shows the section tree + word counts + a
`quality` flag. Decide which Items matter before reading anything.
```bash
uv run python scripts/filings.py outline AAPL              # latest FY
uv run python scripts/filings.py outline AAPL --fy 2024
```

**2. Grep to locate.** Returns addressed hits (`item` + `offset`) you can drill into.
Restrict to a section with `--item` to avoid boilerplate.
```bash
uv run python scripts/filings.py grep AAPL "tariff|supply chain" --item 1A
uv run python scripts/filings.py grep AAPL "China"               # whole filing
```

**3. Windowed read.** Page through a section with `--offset`; the response's
`next_offset` / `remaining` tell you whether to continue. Read only what you need.
```bash
uv run python scripts/filings.py read AAPL 1A --fy 2024          # first 6k chars of Risk Factors
uv run python scripts/filings.py read AAPL 1A --offset 6000
```

**4. Compare across years.** One item's size/offsets across every filing on disk —
the entry point for "how did X change over time".
```bash
uv run python scripts/filings.py compare AAPL 1A
```

Items you'll use most: `1` Business, `1A` Risk Factors, `7` MD&A, `7A` Market Risk,
`8` Financial Statements. All commands emit JSON.

## The quality flag — read it

`outline` reports `quality`:
- **`ok`** — all core items (1, 1A, 7, 7A, 8) detected; section reads are reliable.
- **`degraded`** — some core items missing (`missing_core` lists them). Section
  detection couldn't fully map this filer (class-based heading styles, incorporation
  by reference, unusual structure). **`read_section` for the missing items won't work,
  but `grep` still searches the whole normalized text** — navigate by keyword instead.
- **`failed`** — no sections detected; treat the filing as flat text and use `grep`.

Detection is style-aware (real headers are bold/large; TOC rows and cross-references
are not) and covers the large majority of filers. Don't pretend a `degraded` filing
was fully parsed — say so, and fall back to grep.

## Analysing

Define what you're tracking, then use the loop. For cross-year theme/sentiment
counting over flat text, use `scripts/filings.py grep` to find relevant passages
then `read` the surrounding context — doesn't need section boundaries. Otherwise:

- Search MD&A (`7`) and Business (`1`) for narrative/sentiment — **avoid** legal
  boilerplate and the audit opinion in `8`, which skew "we believe / we may" counts.
- Risk Factors (`1A`) for risk-language and new-risk-dimension tracking.
- Use `compare` to spot which sections grew/shrank before reading them.

Output findings to the conversation with citations (symbol, FY, item). Don't add
report generators to the repo.
