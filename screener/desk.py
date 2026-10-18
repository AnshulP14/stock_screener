"""Private, durable advisory state. No broker credentials or execution methods."""

import json
import os
import re
import sqlite3
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from zoneinfo import ZoneInfo

from .config import ROOT


def now_iso():
    return datetime.now(UTC).isoformat()


def timestamp(value):
    dt = datetime.fromisoformat(value)
    if dt.tzinfo is None:
        raise ValueError("Timestamp must include a UTC offset")
    return dt.astimezone(UTC).isoformat()


def instrument(market, symbol):
    if not isinstance(symbol, str):
        raise TypeError("Symbol must be text")
    symbol = symbol.strip().upper()
    if market not in ("nse", "snp") or not re.fullmatch(r"[A-Z0-9][A-Z0-9.&_-]{0,39}", symbol):
        raise ValueError("Expected market nse/snp and a plain exchange symbol (no .NS suffix)")
    if market == "nse" and symbol.endswith(".NS"):
        raise ValueError("Use the exchange symbol without .NS")
    return market, symbol


def number(value, *, positive=False):
    if isinstance(value, bool):
        raise TypeError("Expected a finite decimal")
    try:
        result = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ValueError("Expected a finite decimal") from exc
    if not result.is_finite() or result < 0 or (positive and result == 0):
        raise ValueError(
            "Expected a positive decimal" if positive else "Expected a nonnegative decimal"
        )
    return result


def required_text(obj, name, max_length=20000):
    value = obj.get(name)
    if not isinstance(value, str) or not value.strip() or len(value) > max_length:
        raise ValueError(f"{name} must be nonempty text (max {max_length} characters)")
    return value


class Desk:
    def __init__(self, directory=None):
        directory = Path(directory or os.environ.get("SCREENER_DESK_DIR", ROOT / "private"))
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.path = directory / "desk.sqlite3"
        self.con = sqlite3.connect(self.path, timeout=30)
        self.con.row_factory = sqlite3.Row
        self.con.execute("PRAGMA foreign_keys=ON")
        self.con.executescript("""
            CREATE TABLE IF NOT EXISTS watchlist (
                market TEXT NOT NULL, symbol TEXT NOT NULL,
                PRIMARY KEY(market, symbol));
            CREATE TABLE IF NOT EXISTS events (
                id TEXT PRIMARY KEY, occurred_at TEXT NOT NULL, body TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS records (
                id TEXT PRIMARY KEY, kind TEXT NOT NULL, market TEXT NOT NULL,
                symbol TEXT NOT NULL, created_at TEXT NOT NULL, body TEXT NOT NULL);
            CREATE INDEX IF NOT EXISTS records_lookup ON records(kind,market,symbol,created_at);
            CREATE VIRTUAL TABLE IF NOT EXISTS research_search USING fts5(id UNINDEXED, body);
            CREATE TABLE IF NOT EXISTS snapshots (
                market TEXT NOT NULL, symbol TEXT NOT NULL, body TEXT NOT NULL,
                PRIMARY KEY(market,symbol));
            CREATE TABLE IF NOT EXISTS deliveries (
                key TEXT PRIMARY KEY, status TEXT NOT NULL, updated_at TEXT NOT NULL);
        """)
        self.path.chmod(0o600)

    def close(self):
        self.con.close()

    def watch(self, market, symbol, remove=False):
        market, symbol = instrument(market, symbol)
        with self.con:
            if remove:
                self.con.execute(
                    "DELETE FROM watchlist WHERE market=? AND symbol=?", (market, symbol)
                )
            else:
                self.con.execute("INSERT OR IGNORE INTO watchlist VALUES (?,?)", (market, symbol))

    def watched(self):
        return [dict(r) for r in self.con.execute("SELECT * FROM watchlist ORDER BY market,symbol")]

    def events(self):
        return [
            json.loads(r[0])
            for r in self.con.execute("SELECT body FROM events ORDER BY occurred_at,rowid")
        ]

    def add_event(self, event):
        """Append an actual fill/corporate action. Replays ALL events before commit."""
        event = dict(event)
        required_text(event, "id", 100)
        event["occurred_at"] = timestamp(required_text(event, "occurred_at"))
        if event["occurred_at"] > now_iso():
            raise ValueError("Cannot record future fills/actions")
        kind = event.get("type")
        if kind not in ("OPEN", "BUY", "SELL", "SPLIT", "DIVIDEND", "DEPOSIT", "WITHDRAW", "FEE"):
            raise ValueError("Unknown ledger event type")
        if kind in ("DEPOSIT", "WITHDRAW", "FEE"):
            if event.get("currency") not in ("INR", "USD"):
                raise ValueError("Cash events need currency INR/USD")
            event["amount"] = str(number(event.get("amount"), positive=True))
        else:
            event["market"], event["symbol"] = instrument(
                event.get("market"), event.get("symbol", "")
            )
            event["currency"] = "INR" if event["market"] == "nse" else "USD"
            if kind == "DIVIDEND":
                event["amount"] = str(number(event.get("amount"), positive=True))
            elif kind == "SPLIT":
                event["ratio"] = str(number(event.get("ratio"), positive=True))
            else:
                quantity = number(event.get("quantity"), positive=True)
                if event["market"] == "nse" and quantity != quantity.to_integral_value():
                    raise ValueError("NSE cash-equity fills require whole shares")
                event["quantity"] = str(quantity)
                # Unknown opening cost basis must stay unknown, never become zero.
                event["price"] = (
                    None
                    if kind == "OPEN" and event.get("price") is None
                    else str(number(event.get("price"), positive=True))
                )
                event["fees"] = str(number(event.get("fees", 0)))
        body = json.dumps(event, sort_keys=True, allow_nan=False)
        with self.con:
            # Acquire the write lock BEFORE replay to prevent concurrent oversells.
            self.con.execute("BEGIN IMMEDIATE")
            previous = self.con.execute(
                "SELECT body FROM events WHERE id=?", (event["id"],)
            ).fetchone()
            if previous:
                if previous[0] != body:
                    raise ValueError("Event ID already exists with different content")
                return False
            self.con.execute(
                "INSERT INTO events VALUES (?,?,?)", (event["id"], event["occurred_at"], body)
            )
            replay(self.events())
        return True

    def portfolio(self):
        result = replay(self.events())
        snapshots = {(s["market"], s["symbol"]): s for s in self.snapshots()}
        for holding in result["holdings"]:
            snap = snapshots.get((holding["market"], holding["symbol"]))
            mark = snap.get("mark") if snap else None
            if mark:
                mark = {**mark, "stale": snap["stale"]}
            holding["mark"] = mark
            holding["market_value"] = (
                str(Decimal(holding["quantity"]) * Decimal(str(mark["price"]))) if mark else None
            )
            holding["unrealised_pnl"] = (
                str(Decimal(holding["market_value"]) - Decimal(holding["cost_basis"]))
                if mark and holding["cost_basis"] is not None
                else None
            )
        totals = {}
        for h in result["holdings"]:
            if h["market_value"] is not None:
                totals[h["currency"]] = totals.get(h["currency"], Decimal(0)) + Decimal(
                    h["market_value"]
                )
        for h in result["holdings"]:
            total = totals.get(h["currency"], Decimal(0))
            h["share_of_marked_equities_in_currency"] = (
                str(Decimal(h["market_value"]) / total)
                if h["market_value"] is not None and total
                else None
            )
        result["marked_equity_subtotals"] = {k: str(v) for k, v in totals.items()}
        result["valuation_complete"] = all(
            h["market_value"] is not None for h in result["holdings"]
        )
        result["warnings"].append(
            "Equity weights exclude cash and unpriced positions; they are not total-account weights."
        )
        return result

    def put_record(self, kind, record):
        record = dict(record)
        required_text(record, "id", 100)
        record["market"], record["symbol"] = instrument(
            record.get("market"), record.get("symbol", "")
        )
        if kind == "source":
            from urllib.parse import urlparse

            for field in ("title", "url", "excerpt", "locator"):
                required_text(record, field)
            url = urlparse(record["url"])
            if url.scheme not in ("http", "https") or not url.netloc:
                raise ValueError(
                    "Source needs an HTTP(S) URL; content is imported, never fetched here"
                )
            if record.get("category") not in ("primary", "reporting", "forum"):
                raise ValueError("Source category must be primary/reporting/forum")
            record["retrieved_at"] = timestamp(required_text(record, "retrieved_at"))
            # Publication date may be unknown; do not invent one.
        elif kind == "note":
            for field in (
                "thesis",
                "changes",
                "counterarguments",
                "invalidation",
                "open_questions",
            ):
                required_text(record, field)
            self.check_sources(record)
        elif kind == "ticket":
            from .tickets import validate_ticket

            validate_ticket(record)
            self.check_sources(record)
        else:
            raise ValueError("Unknown record kind")
        body = json.dumps(record, sort_keys=True, allow_nan=False)
        with self.con:
            old = self.con.execute(
                "SELECT kind,body FROM records WHERE id=?", (record["id"],)
            ).fetchone()
            if old:
                if tuple(old) != (kind, body):
                    raise ValueError("Record ID exists; append a new revision with a new ID")
                return False
            self.con.execute(
                "INSERT INTO records VALUES (?,?,?,?,?,?)",
                (record["id"], kind, record["market"], record["symbol"], now_iso(), body),
            )
            if kind in ("source", "note"):
                self.con.execute("INSERT INTO research_search VALUES (?,?)", (record["id"], body))
        return True

    def check_sources(self, record):
        ids = record.get("source_ids")
        if not isinstance(ids, list) or not ids or not all(isinstance(i, str) for i in ids):
            raise ValueError("At least one stored source_id is required")
        categories = set()
        for sid in ids:
            row = self.con.execute(
                "SELECT * FROM records WHERE id=? AND kind='source'", (sid,)
            ).fetchone()
            if not row or (row["market"], row["symbol"]) != (record["market"], record["symbol"]):
                raise ValueError(f"Missing source or instrument mismatch: {sid}")
            categories.add(json.loads(row["body"])["category"])
        if "primary" not in categories:
            raise ValueError(
                "Research/tickets require primary evidence; forums alone are insufficient"
            )

    def records(self, market, symbol, kind=None, limit=10, offset=0):
        market, symbol = instrument(market, symbol)
        if not 1 <= limit <= 50 or offset < 0:
            raise ValueError("limit must be 1..50; offset must be nonnegative")
        rows = self.con.execute(
            """SELECT kind,created_at,body FROM records
            WHERE market=? AND symbol=? AND (? IS NULL OR kind=?)
            ORDER BY rowid DESC LIMIT ? OFFSET ?""",
            (market, symbol, kind, kind, limit, offset),
        )
        return [
            {"kind": r["kind"], "stored_at": r["created_at"], **json.loads(r["body"])} for r in rows
        ]

    def search(self, query, limit=10):
        if not 1 <= limit <= 50:
            raise ValueError("limit must be 1..50")
        return [
            dict(r)
            for r in self.con.execute(
                """SELECT id,
            snippet(research_search,1,'[',']','…',40) AS passage
            FROM research_search WHERE research_search MATCH ? ORDER BY rank LIMIT ?""",
                (query, limit),
            )
        ]

    def save_snapshot(self, snapshot):
        market, symbol = instrument(snapshot["market"], snapshot["symbol"])
        with self.con:
            self.con.execute(
                "INSERT OR REPLACE INTO snapshots VALUES (?,?,?)",
                (market, symbol, json.dumps(snapshot, allow_nan=False)),
            )

    def snapshots(self):
        rows = [
            json.loads(r[0])
            for r in self.con.execute("SELECT body FROM snapshots ORDER BY market,symbol")
        ]
        now = datetime.now(UTC)
        for row in rows:
            local = now.astimezone(
                ZoneInfo("Asia/Kolkata" if row["market"] == "nse" else "America/New_York")
            )
            row["cache_age_hours"] = (
                now - datetime.fromisoformat(row["fetched_at"])
            ).total_seconds() / 3600
            row["stale"] = (
                row["stale"]
                or row["cache_age_hours"] > 36
                or (local.date() - datetime.fromisoformat(row["signal_date"]).date()).days > 4
            )
        return rows

    def backup(self, path):
        path = Path(path)
        if path.exists() or path.resolve() == self.path.resolve():
            raise ValueError("Backup destination must not already exist")
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        os.close(fd)
        with sqlite3.connect(path) as target:
            self.con.backup(target)
        return str(path)


def replay(events):
    """Long-only weighted-average accounting, separate currencies, no invented funding."""
    holdings, cash, realised = {}, {}, {}
    for e in events:
        kind, cur = e["type"], e["currency"]
        cash.setdefault(cur, Decimal(0))
        realised.setdefault(cur, Decimal(0))
        if kind in ("DEPOSIT", "WITHDRAW", "FEE", "DIVIDEND"):
            cash[cur] += Decimal(e["amount"]) * (1 if kind in ("DEPOSIT", "DIVIDEND") else -1)
            continue
        key = (e["market"], e["symbol"])
        h = holdings.setdefault(key, {"quantity": Decimal(0), "cost": Decimal(0)})
        if kind == "SPLIT":
            if h["quantity"] == 0:
                raise ValueError("Cannot split an empty position")
            h["quantity"] *= Decimal(e["ratio"])
            continue
        q, fees = Decimal(e["quantity"]), Decimal(e["fees"])
        value = q * Decimal(e["price"]) if e["price"] is not None else None
        if kind in ("BUY", "OPEN"):
            if kind == "OPEN" and h["quantity"] != 0:
                raise ValueError(
                    "OPEN requires an empty position; record subsequent purchases as BUY"
                )
            h["quantity"] += q
            h["cost"] = None if h["cost"] is None or value is None else h["cost"] + value + fees
            if kind == "BUY":
                cash[cur] -= value + fees
        else:
            if q > h["quantity"]:
                raise ValueError(f"SELL exceeds holdings: {key}")
            sold_cost = None if h["cost"] is None else h["cost"] * q / h["quantity"]
            realised[cur] = (
                None
                if realised[cur] is None or sold_cost is None
                else realised[cur] + value - fees - sold_cost
            )
            h["cost"] = None if sold_cost is None else h["cost"] - sold_cost
            h["quantity"] -= q
            if h["quantity"] == 0:
                h["cost"] = Decimal(0)
            cash[cur] += value - fees
    rows = [
        {
            "market": m,
            "symbol": s,
            "currency": "INR" if m == "nse" else "USD",
            "quantity": str(h["quantity"]),
            "cost_basis": str(h["cost"]) if h["cost"] is not None else None,
        }
        for (m, s), h in sorted(holdings.items())
        if h["quantity"]
    ]
    return {
        "active": bool(events),
        "holdings": rows,
        "net_recorded_cash_flows": {k: str(v) for k, v in cash.items()},
        "realised_pnl": {k: str(v) if v is not None else None for k, v in realised.items()},
        "warnings": [
            "Cash flows are NOT an account balance unless all starting cash and movements are recorded.",
            "Weighted-average research P&L, not tax/FIFO accounting. No FX consolidation.",
            "Corporate actions and actual fills must be reported manually.",
        ],
    }
