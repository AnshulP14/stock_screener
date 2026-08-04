---
name: analyse-statements
description: Analyse annual report filings — US 10-Ks and NSE (India) annual report PDFs. Navigate long filings by section, search across years, track changes. Use when asked to analyse 10-K filings, Indian annual reports, or annual report text.
---

# Analyse annual statements

Two markets, two tools, same loop:
- **S&P500 10-Ks** (HTML) → `scripts/filings.py`, addressed by **page number**.
- **NSE annual reports** (PDF) → `scripts/nse_filings.py`, addressed by **page number**.

Both markets are huge documents (10-Ks ~2.5MB / NSE PDFs 130-590 pages). **Never read
one whole.** Each filing has a durable navigation index next to it; drive it with the
corresponding `scripts/*.py` using the loop: **outline → grep → read one page → expand
only where there's signal.**

## S&P500 10-K filings (US)

```
data/raw/snp/annual_reports/{SYMBOL}/
  {SYMBOL}_10K_{YEAR}.htm         # source iXBRL filing
  {SYMBOL}_10K_{YEAR}.txt         # normalized text (offsets address this)
  {SYMBOL}_10K_{YEAR}.index.json  # page-range section map
```

Build the index if missing (no re-download):
```bash
uv run python scripts/filings.py index --symbols {SYMBOL}       # one/few
uv run python scripts/filings.py index --all                    # whole corpus
```
If the `.htm` itself is missing: `uv run python scripts/fetch_annual_reports_snp.py --symbol {SYMBOL}`.

American 10-K filings are inline XBRL (iXBRL) with no native page breaks — boundaries
come from CSS `page-break-after: always` and bare `<hr>` elements. They have font sizes
declared on parent elements (inherited) and boldness as the second axis; headings are
any line set apart from body by (size, bold). Three filters keep it honest: a line over
90 characters is a paragraph, a heading needs more than three letters, and consecutive
pages with the same heading merge into one section.

~2% of S&P filings have no page-break markers (degenerate case) — the entire document
becomes one page; the outline is just the document-level heading candidates. Degenerate
filingst have `pages` 1 in `outline` output — that's the tell.

### S&P500 commands

```bash
uv run python scripts/filings.py index --symbols PG              # build (no re-download)
uv run python scripts/filings.py filings AAPL                     # which years are on disk
uv run python scripts/filings.py outline AAPL --fy 2024          # sections + page ranges
uv run python scripts/filings.py grep AAPL "tariff|China"
uv run python scripts/filings.py grep AAPL "tariff" --start-page 10 --end-page 20
uv run python scripts/filings.py read AAPL 15                     # exactly one page
```

## NSE annual reports (India)

```
data/raw/nse/annual_reports/{SYMBOL}/
  {SYMBOL}_AR_{YEAR}.pdf         # source annual report (130-590 pages)
  {SYMBOL}_AR_{YEAR}.txt         # extracted text
  {SYMBOL}_AR_{YEAR}.index.json  # page-range section map
```

Build the index if missing (no re-download):
```bash
python scripts/nse_filings.py index --symbols {SYMBOL}       # one/few
python scripts/nse_filings.py index --all                    # whole corpus
```

### NSE commands

```bash
python scripts/nse_filings.py index --symbols RELIANCE      # build (no re-download)
python scripts/nse_filings.py filings RELIANCE              # which years are on disk
python scripts/nse_filings.py outline RELIANCE --fy 2024    # sections + page ranges
python scripts/nse_filings.py grep RELIANCE "capex|expansion"
python scripts/nse_filings.py grep RELIANCE "capex" --start-page 40 --end-page 55
python scripts/nse_filings.py read RELIANCE 47              # exactly one page
```

`grep --start-page/--end-page` narrows a search to a page range — typically one just read
off the outline — instead of searching the whole report.

`--fy` defaults to the newest report/filing on disk everywhere, so ask for a year only
when you want an older one.

**`filings` gives years, page counts and a section count — not the outlines.** One outline
is ~400-3,500 tokens at the median, so returning every year's would spend more context
answering "which years exist?" than on the actual analysis. Call `outline` per year, only
for the years you decided to look at.

**Sections are freeform — always `outline` first.** The outline holds at most one heading
per page (the line set apart from body by size and boldness for NSE, font-size for PDF)
and merges only consecutive repeats. The outline reports whatever titles *that document*
uses, verbatim — don't assume a section exists or guess its name.

**`read` returns exactly one page, deliberately.** A page is ~800 tokens (~1,400 at p90),
and an agent almost always needs one of the pages a wider window would have returned.
Paginate yourself with `next_page` when the page genuinely continues — but having read a
page you are usually better placed to jump somewhere else entirely, or to stop. Context
spent on unread pages cannot be reclaimed.

**`grep` paginates too.** It returns 10 hits with the page number and surrounding text
for each, plus `hit_count` (the *true* total) and `next_offset`. If `hit_count` is much
larger than what you got back, narrow the pattern rather than paging through hundreds of
matches — and never assume you saw every match when `next_offset` is non-null. Grep works
on the full text, so it finds things the outline never named.

**Standalone vs consolidated matters (NSE only).** Every NSE report contains both sets of
accounts (`Standalone Balance Sheet` and `Consolidated Balance Sheet` are separate sections,
kept distinct on purpose). Consolidated includes subsidiaries; for a group these are very
different numbers. Say which one you used.

**~0.5% of NSE reports are scans with no text layer** (BEML, MMTC, RHIM, SHYAMMETL,
SPLPETRO). The tell is a long report with a near-empty outline — a 387-page filing with 1
section. No tool can read these; say so rather than reporting the report as empty.

### Known outline noise — and how to read past it

The outline rule (one heading per page, merged consecutive repeats) works well on statutory
reports and financial statements, but front-matter has three recognisable failure shapes.
None of them break `read` or `grep` — the page range is still correct, only the *title* is
wrong or empty of meaning — so treat a front-matter title with suspicion rather than as
ground truth.

- **Branding repeated as a "heading."** A cover or full-bleed marketing page is often one
  large background image with no real text layer, so the only sizeable text left is the
  company's own logo wordmark or a masthead like "Annual Report 2021-22" — and because it
  repeats non-consecutively across many pages, it never merges into one entry the way a
  real running section does. Measured on a 40-report sample, this can dominate the outline:
  **ANANTRAJ FY2024 is 59% (61/103) "Anant Raj Limited"; ACE FY2021 is 39% (84/213) its
  own company name; SUNDARMFIN FY2025 is 58% (168/288) "Annual Report."** ADANIENT FY2022
  pp.5-6 is a concrete example — `read`ing those pages returns almost nothing but the
  company name and the three-booklet labels (`CORPORATE OVERVIEW`/`STATUTORY REPORTS`/`FINANCIAL
  STATEMENTS`). If a title repeats identically several times *non-consecutively* in the
  outline, it is very likely the company's own name or masthead, not a real section —
  treat it as noise, not as several distinct occurrences of one section.
- **Divider/blank pages become their own 1-page "section."** e.g. PERSISTENT FY2026 has
  six separate entries titled "This space is intentionally kept blank." Harmless, but don't
  mistake a run of these for content.
- **A large pull-quote figure is occasionally mistaken for a title** (~0.8% of all section
  rows) — e.g. a page whose most prominent element is a number like "70,433 H crore" set in
  large display type. `read` the page rather than trust the title if a heading looks like a
  number or currency figure.

**Most sections are one page — that's expected, not a defect.** Because the rule takes at
most one heading per page and merges only *consecutive* repeats, the median report has
~95% single-page sections. A visually "busy" outline with many 1-page rows is normal; it
is not evidence the detector is fragmenting a real section.

**The reliable core is unchanged: statutory reports and financial statements.** These are
typeset with a real, consistently-styled section title on the opening page and no
competing marketing images, so the rule is accurate there. The noise above is concentrated
in the front 10-40% of most reports (corporate overview / "who we are" booklets) — if you
only need the Board's Report, MD&A, or the financial statements, you are unlikely to hit
any of it.

## The navigation loop (both markets)

**1. Outline first — always.** Cheap, shows the section tree with page ranges. Decide
which sections matter before reading anything.
```bash
# S&P500
uv run python scripts/filings.py outline AAPL              # latest FY
uv run python scripts/filings.py outline AAPL --fy 2024
# NSE
python scripts/nse_filings.py outline RELIANCE --fy 2024
```

**2. Grep to locate.** Returns addressed hits with page numbers and surrounding context.
Restrict to a page range from the outline instead of searching the whole filing.
```bash
# S&P500
uv run python scripts/filings.py grep AAPL "tariff|supply chain"
uv run python scripts/filings.py grep AAPL "tariff" --start-page 10 --end-page 20
# NSE
python scripts/nse_filings.py grep RELIANCE "capex|expansion"
python scripts/nse_filings.py grep RELIANCE "capex" --start-page 40 --end-page 55
```

**3. Windowed read.** `read` returns exactly one page with `prev_page`/`next_page` for
pagination. Read only what you need.
```bash
# S&P500
uv run python scripts/filings.py read AAPL 15 --fy 2024
# NSE
python scripts/nse_filings.py read RELIANCE 47
```

All commands emit JSON.

## Analysing

Define what you're tracking, then use the loop. For cross-year theme/sentiment counting
over flat text, use `grep` to find relevant passages then `read` the surrounding context —
doesn't need section boundaries. Otherwise:

- Search MD&A (`7`) and Business (`1`) for narrative/sentiment — **avoid** legal boilerplate
  and the audit opinion in `8`, which skew "we believe / we may" counts.
- Risk Factors (`1A`) for risk-language and new-risk-dimension tracking.

Output findings to the conversation with citations (symbol, FY, page). Don't add report
generators to the repo.
