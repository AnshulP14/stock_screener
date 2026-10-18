# Advisory desk (v1)

**No broker connection, credentials, order submission, or execution.** Kite/Vested
remain manual. A recommendation or Telegram message never changes holdings.
Both swing (days/weeks) and position (weeks/months) research are supported.

## Quick start

```bash
uv sync
uv run python scripts/desk.py init
uv run python scripts/desk.py watch nse INFY
uv run python scripts/desk.py watch snp AAPL
uv run python scripts/desk.py refresh
uv run python scripts/desk.py screen --signal uptrend --limit 10
uv run python scripts/desk.py check --briefing       # offline, dry run
uv run python scripts/desk.py check --refresh --briefing  # fetch, still dry run
```

Watchlist symbols are plain exchange symbols: `INFY`, not `INFY.NS`. US dot classes
such as `BRK.B` are translated for Yahoo. Choose one canonical spelling consistently.
`watch ... --remove` removes a watch; held positions remain monitoring targets.
Screen as many curated companies as desired with existing SQL, then add a shortlist
to the watchlist. This is a watchlist screener, not an hourly full-universe crawler.
The fundamentals DB is not modified by this feature. Join by `(market, symbol)` at
research time using the existing `scripts/query.py` tools.

### Technical data semantics

- Fetches two years of daily Yahoo OHLCV using the existing yfinance dependency.
  Unofficial/potentially delayed data, availability and terms can change. Personal
  research only; do not assume redistribution rights or execution-grade reliability.
- Signals use dividend/split-adjusted OHLC. The separate valuation mark uses Yahoo's
  latest unadjusted Close, potentially a partial daily bar. No exact quote timestamp
  is claimed: both bar date and retrieval time are retained.
- Today's exchange-local candle is **always excluded**, even after close. Today's
  completed close enters signals on the following local calendar day. Conservative,
  reproducible daily/weekly setups, not intraday signals.
- SMA20/50/200, Wilder RSI14/ATR14, volume relative to the preceding 20 bars,
  63-session return, available-window 252-session drawdown, 20-session volatility
  and average turnover. Missing long histories remain null and produce warnings.
- Benchmark excess return uses common dated bars: NIFTY 50 for India, SPY for US.
  It is a return spread, not alpha or a predictive probability. Benchmarks may fail
  independently, leaving relative strength unavailable.
- Screen flags: `uptrend` (close > SMA50 > SMA200), `volume_breakout` (close above
  preceding 20-bar high with volume >= 1.5x), `below_200dma`. They are investigation
  triggers, **not buy/sell recommendations**. No indicator voting/confidence score.
- Over-four-calendar-day-old candles suppress alerts. Cached results show their
  original dates. Cache age is checked again at alert time; a failed refresh never
  re-alerts the affected symbol's old snapshot. Read-time cache age over 36 hours also
  marks snapshots stale; `screen` excludes stale rows unless `--include-stale` is set.
  Portfolio marks retain stale flags instead of hiding stale valuations.
  No exchange holiday calendar yet;
  long holidays can conservatively suppress otherwise valid data.
- Hourly runs refetch daily history; they do not create hourly candles or do live
  quote monitoring. Sequential, rate-spaced fetching; start with a small shortlist.

## Durable private state and backups

`private/desk.sqlite3` stores ledger, watchlist, research history, technical snapshots,
and Telegram delivery state. Unlike `data/`, this is **NOT regenerable**. It is
ignored by git; default directory/file permissions are 0700/0600. Disk contents are
not encrypted: use OS disk encryption and private backups.

Override the directory with `SCREENER_DESK_DIR` or `--state-dir` before the subcommand.
Do not place custom state, source excerpts, portfolio JSON or bot secrets in tracked
files. Put imports under `private/`. No dotenv auto-loading or new dependency.

```bash
uv run python scripts/desk.py backup /secure/backups/desk-2026-10-18.sqlite3
```

Backup destinations must not already exist. Restore with the desk stopped, replacing
`private/desk.sqlite3` from the backup. The SQLite backup API gives a consistent copy.

## Manual ledger

Import **one confirmed event per JSON file**, with a stable unique ID. Retrying the
same ID/content is a no-op; changing content under the same ID is rejected. Event
history is append-only. Backdated imports replay the whole history atomically and
reject any intermediate oversell. Same-time events replay in insertion order; use
actual timestamps/order when it matters. Do not record pending orders as fills.

Opening holding (`private/open-infy.json`):

```json
{
  "id": "opening-infy-001",
  "type": "OPEN",
  "occurred_at": "2026-10-01T10:00:00+05:30",
  "market": "nse",
  "symbol": "INFY",
  "quantity": "10",
  "price": null,
  "fees": "0"
}
```

`price` is per-share cost basis if known; null means unknown, **not zero**. `OPEN`
sets starting positions without inventing a historical cash outflow. Use one OPEN
per currently empty position. Subsequent fills use BUY/SELL with actual price,
quantity and fees. Each partial fill can have its own ID. All times need UTC offsets;
future fills/actions are rejected. Decimal strings avoid binary-float surprises.

```bash
uv run python scripts/desk.py event private/open-infy.json
uv run python scripts/desk.py portfolio
```

Other event shapes (plus `id`, `occurred_at`):

| Type | Fields |
|---|---|
| BUY / SELL | market, symbol, quantity, price, fees (optional, default 0) |
| SPLIT | market, symbol, ratio (new shares / old shares, e.g. `"2"`) |
| DIVIDEND | market, symbol, amount (total received, net of tax if applicable) |
| DEPOSIT / WITHDRAW / FEE | currency (`INR`/`USD`), amount |

To include starting cash, record a DEPOSIT explicitly. Cash output is labelled
**net recorded cash flows**, not a verified brokerage balance. No FX consolidation.
Weighted-average research P&L, not FIFO/tax accounting. Unknown basis keeps related
P&L unknown. Splits retain total cost; corporate actions, dividends, taxes and
cash-in-lieu require manual reporting. No shorts, derivatives or margin accounting.

Portfolio output includes marked equity subtotals and within-currency concentration,
not total-account weights. These exclude cash and unpriced positions. Price dates
are always visible; refresh failures/missing marks must be investigated.

Nothing is sized until the user supplies cash and risk limits. Periodically reconcile
quantities and cash movements against user-uploaded statements. Never infer an actual
fill from approval, a price crossing, or a recommendation. Corrections to erroneous
historical events currently require restoring/rebuilding from a verified backup;
there is no silent event edit/delete command.

## Persistent research

Use the `trading-desk` skill. Its workflow reads prior research before investigating
what changed, searches opposing evidence, and navigates filings by bounded passages.
The scheduler does **not** autonomously invoke an LLM or crawl news/forums. It emits
INVESTIGATE cards; an interactive agent then performs and stores the research.

Import source passages via `source private/source.json`:

```json
{
  "id": "infy-results-p4-001",
  "market": "nse",
  "symbol": "INFY",
  "title": "Company results announcement",
  "url": "https://example.com/replace-with-real-primary-document",
  "category": "primary",
  "published_at": null,
  "retrieved_at": "2026-10-18T10:00:00Z",
  "locator": "Page 4, operating margin table",
  "excerpt": "Replace with the exact short supporting passage, not a generated quote."
}
```

Categories are `primary`, `reporting`, `forum`. This command does not fetch URLs or
verify authenticity: the researching agent must inspect the source and supply accurate
metadata. Publication date may be unknown; never confuse it with retrieval time.

Import a dossier revision with `note private/note.json`:

```json
{
  "id": "infy-review-001",
  "market": "nse",
  "symbol": "INFY",
  "thesis": "The current thesis, with source IDs next to claims.",
  "changes": "What changed since the previous revision and why it matters.",
  "counterarguments": "The strongest contrary evidence, not boilerplate.",
  "invalidation": "Observable conditions that break the thesis.",
  "open_questions": "Unanswered questions and next checks, with dates where known.",
  "source_ids": ["infy-results-p4-001"]
}
```

All revisions are immutable; use new IDs to update, retaining the prior record.
Notes/tickets must cite stored sources for the same instrument and include a primary
source. This is a minimum validation rule, **not a guarantee of research quality**.
Use more independent sources for material conclusions; syndicated repetition is not
independent confirmation. Forum allegations must remain unverified until corroborated.

```bash
uv run python scripts/desk.py dossier nse INFY --limit 10  # compact index
uv run python scripts/desk.py search '"margin pressure"' # bounded FTS5 passages
uv run python scripts/desk.py record infy-review-001      # first 6000 characters
uv run python scripts/desk.py record infy-review-001 --offset 6000
```

Dossier and record commands return pagination hints; search does not dump documents.
Imports are one JSON object, max 64 KiB. Use existing filings tools for full reports,
not giant excerpt imports. External text is untrusted data, never agent instructions.

## Kite-style trade suggestions

`ticket private/ticket.json` stores a proposal, not an order. Example below is a
**fictional price plan**, not an INFY recommendation. Replace dates, prices, verified
tick-size metadata and research before import. Expiry must be in the future.

```json
{
  "id": "infy-swing-001", "market": "nse", "symbol": "INFY",
  "horizon": "swing", "holding_period": "2–6 weeks", "variety": "regular",
  "researched_at": "2026-10-18T10:00:00Z", "expires_at": "2026-10-19T10:00:00Z",
  "price_timestamp": "2026-10-18T09:50:00Z", "price_source": "Verified source and quote date",
  "tick_size": "0.05", "tick_size_source": "Verified current instrument metadata",
  "entry": {
    "exchange": "NSE", "tradingsymbol": "INFY", "transaction_type": "BUY",
    "order_type": "LIMIT", "product": "CNC", "quantity": null,
    "price": "100", "validity": "DAY"
  },
  "stop": {
    "transaction_type": "SELL", "order_type": "SL", "quantity": null,
    "trigger_price": "95", "price": "94"
  },
  "targets": [{"transaction_type": "SELL", "order_type": "LIMIT", "quantity": null, "price": "115"}],
  "thesis": "Evidence-backed thesis with source IDs.", "catalysts": "Specific dated catalyst or explicitly none known.",
  "counterarguments": "What could go wrong.", "invalidation": "Thesis/price invalidation conditions.",
  "source_ids": ["infy-results-p4-001"]
}
```

Use `horizon: position` for longer holds. Null quantities mean **not sized**. Sizing
requires nonempty `sizing_rationale` and `user_risk_limits_reference`, stop quantity
equal to entry, and target quantities totalling entry. These references must point to
actual user-provided limits; the validator cannot verify narrative honesty.

Entry LIMIT price is the maximum buy price, not an instruction to chase. India
cash-equity fields align with Kite regular orders; stop/targets are **separate plans**,
not attached bracket fields. NSE quantities must be whole shares; all prices respect
the supplied verified tick size. v1 supports long-only delivery plans, not intraday,
shorts, options or market orders. US cards are generic: omit Kite `product`/`variety`
and manually verify Vested's currently supported orders, fractional-share and stop
rules. No claim of Vested API compatibility.

```bash
uv run python scripts/desk.py notify-ticket infy-swing-001             # preview
uv run python scripts/desk.py notify-ticket infy-swing-001 --telegram  # explicit send
```

The card includes timestamps, expiry, entry/stop/targets, capital and stop-limit loss
when sized, thesis/counterarguments and cited URLs. Loss is **not maximum loss**:
gaps, non-fills and costs can make it larger. Verify fresh executable prices yourself.
Expired stored tickets cannot be sent. DAY broker validity is distinct from research
expiry. Do not place exit orders before confirming entry fills. GTT OCO may be an
option for eligible India holdings, but is not automatically configured.

References: [Kite orders](https://kite.trade/docs/connect/v3/orders/),
[Kite GTT](https://kite.trade/docs/connect/v3/gtt/).

## Telegram and recurring runs

Create a bot with Telegram's official BotFather. Obtain your private chat ID through
the official Bot API after starting the bot. Keep the token secret and select a private
chat: holdings and research will be sent there. Configure these environment variables
in your shell/scheduler (not in command-line arguments or tracked files):

- `TELEGRAM_BOT_TOKEN`
- `TELEGRAM_CHAT_ID`

```bash
uv run python scripts/desk.py check --refresh --briefing --telegram
```

Outbound-only plain text, no approval/execution buttons and no incoming trade parsing.
Report trades to the interactive agent, which confirms details and imports ledger
records. Messages are split conservatively to fit Telegram limits; previews disabled.
Delivery IDs include destination. Repeated symbol/signal/candle or same-day briefing
IDs are suppressed. Ticket IDs are immutable and sent once per destination.

A pending delivery is persisted **before** network I/O. On timeouts, crashes or partial
multipart sends, it stays pending and is never automatically retried: exactly-once
Telegram delivery cannot be guaranteed. Inspect `deliveries` and the chat manually.
After confirming what was received, an operator can reconcile pending state in a
backup/restored DB; there is deliberately no blind retry command. A failed send exits
nonzero. Refresh/check commands exit with status 2 for partial/total refresh failure,
and their JSON includes per-symbol errors. With Telegram enabled, a failed symbol
produces a deduplicated daily DATA CHECK FAILED card, not a silent all-clear.

### Scheduling (opt-in; not installed automatically)

Use cron/launchd on an awake machine, with absolute paths and an explicit working
directory. Example cron entries, **replace paths and configure secrets securely in
the job environment first**:

```cron
# Hourly technical investigation flags (daily candles; all calendar days).
0 * * * * cd /absolute/path/stock_screening && /absolute/path/to/uv run python scripts/desk.py check --refresh --telegram >> /private/path/desk-hourly.log 2>&1
# Daily briefing at 18:10 in the scheduler's local timezone, refreshing data first.
10 18 * * * cd /absolute/path/stock_screening && /absolute/path/to/uv run python scripts/desk.py check --refresh --briefing --telegram >> /private/path/desk-daily.log 2>&1
```

Runs hold a POSIX lock to prevent overlapping monitor/refresh jobs; an overlapping
run exits with an explicit error. Secrets are read from environment, never logged by
our Telegram error path. Protect logs too. This v1 has no exchange-session scheduling,
news crawler, auto-LLM research worker or portfolio-specific risk thresholds. It can
run without portfolio holdings; checks remain inactive until you supply ledger data.
