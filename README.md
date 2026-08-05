# Stock Screener

Fundamental data for **NSE500 (India)** and **S&P500 (US)** stocks, driven by [pi](https://pi.dev).

There is no app. You open this repo in pi, ask questions in natural language, and pi queries the local data and the web to answer them.

## Quick start

```bash
git clone https://github.com/AnshulP14/stock_screener.git
cd stock_screener
pi
```

Then ask pi anything:

> screen the NSE500 for value stocks with P/E under 15 and ROE above 15%

> how does TCS compare to INFY on profitability and growth?

> what are the risk factors Apple mentioned in their 2024 10-K?

Pi uses the skills below to know what data exists and how to query it.

---

## Setup

```bash
uv sync
```

The `data/` directory is gitignored — fetch it once. Open pi and ask "/refresh-data
set up the data"; the `refresh-data` skill has the full first-time-setup walkthrough.

---

## Skills

This repo ships three skills in `.agents/skills/`. Pi loads them automatically when
you open the repo; each is also available as a `/skill:name` command. Each `SKILL.md`
is the source of truth for its area — this README doesn't restate it.

| Skill | Command | Use for |
|-------|---------|---------|
| **screen-stocks** | `/skill:screen-stocks` | Screening, ranking, comparisons, company profiles |
| **refresh-data** | `/skill:refresh-data` | Bootstrap, update, or fix stale data |
| **analyse-statements** | `/skill:analyse-statements` | Reading and comparing 10-K / annual report filings |

---

## For agents

`AGENTS.md` is the canonical technical reference: data layout, package structure, SQL
reference, house rules. Start there if you're extending the pipeline or adding a market.
