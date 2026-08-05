---
name: analyse-statements
description: Analyse annual report filings — US 10-Ks and NSE (India) annual reports. Navigate huge filings by section instead of reading them whole, and search within or across years for themes, risk language, and sentiment. Use when asked to analyse annual reports.
---

# Analyse annual statements

One tool, two markets, same loop: `scripts/filings.py --market {snp,nse}`, addressed
by **page number** either way (`--market` goes before the subcommand).
- **S&P500 10-Ks** (HTML, `--market snp`)
- **NSE annual reports** (PDF, `--market nse`)

Both markets are huge documents (10-Ks ~2.5MB / NSE PDFs 130-590 pages). **Never read
one whole.** Each filing has a durable navigation index next to it; drive it with
`scripts/filings.py` using the loop: **outline → grep → read one page → expand only
where there's signal.**

## S&P500 10-K filings (US)

```
data/raw/snp/annual_reports/{SYMBOL}/
  {SYMBOL}_10K_{YEAR}.htm         # source iXBRL filing
  {SYMBOL}_10K_{YEAR}.txt         # normalized text (offsets address this)
  {SYMBOL}_10K_{YEAR}.index.json  # page-range section map
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
uv run python scripts/filings.py --market snp index --symbols {SYMBOL}       # one/few
uv run python scripts/filings.py --market snp index --all                    # whole corpus
```

### filing analysis commands

```bash
uv run python scripts/filings.py --market snp index --symbols PG              # build (no re-download)
uv run python scripts/filings.py --market nse filings RELIANCE                     # which years are on disk
uv run python scripts/filings.py --market snp outline AAPL --fy 2024          # sections + page ranges
uv run python scripts/filings.py --market nse grep ADANIPORT "tariff|China"
uv run python scripts/filings.py --market snp grep GOOG "tariff" --start-page 10 --end-page 20
uv run python scripts/filings.py --market snp read AAPL 15                     # exactly one page
```

`grep --start-page/--end-page` narrows a search to a page range — typically one just read
off the outline — instead of searching the whole report.

`--fy` defaults to the newest report/filing on disk everywhere, so ask for a year only
when you want an older one.

**`filings` gives years, page counts and a section count**

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

### Known outline noise — and how to read past it

The outline rule (one heading per page, merged consecutive repeats) works well at picking ip section names, but there are usually two kinds of factual innacuracies in the outlines
None of them break `read` or `grep` — the start page is still correct, and all sections are mostly picked up in the headline, but there is a lot of noise in the outline and the page range is usually misleading

**A lot of the heading names might sound weird and that's probably because they are not headings** The
  company's own logo wordmark or a masthead like "Annual Report 2021-22" can be the largest font item in a page and thus might be picked up as a heading — and because it
  repeats non-consecutively across many pages, it never merges into one entry the way a
  real running section does. Measured on a 40-report sample, this can dominate the outline. Or the largest font item can be something that looks random and it may be due to the same reason

**Most sections are shown as one page — that's expected but incorrect** Because the rule takes at most one heading per page and merges only *consecutive* repeats, the median report has
~95% single-page sections. If you can't find what you wanted, you can read the next page to find it incase the section is continuing beyond the current page

## The navigation loop (both markets)

The commands are in the S&P500/NSE sections above; this is the order and the reasoning
behind it.

**1. Outline first — always.** Cheap, shows the section tree with page numbers. Decide
which sections matter before reading anything.

**2. Grep to locate.** Returns addressed hits with page numbers and surrounding context.
Restrict to a page range from the outline (`--start-page`/`--end-page`) to reduce hits.

**3. Windowed read.** `read` returns exactly one page with `prev_page`/`next_page` for
pagination. Read only what you need — paginate where there's signal, stop where there isn't.

All commands emit JSON.
