"""Small agent-facing commands for the advisory desk."""

import argparse
import fcntl
import json
import sqlite3
import sys
from pathlib import Path

from .desk import Desk


def load_json(path):
    path = Path(path)
    if path.stat().st_size > 65536:
        raise ValueError("Import one record at a time (maximum 64 KiB)")
    with path.open() as f:
        result = json.load(f)
    if not isinstance(result, dict):
        raise TypeError("Expected one JSON object")
    return result


def parser():
    p = argparse.ArgumentParser(
        description="Advisory only: technicals, ledger, research and Telegram. No execution."
    )
    p.add_argument(
        "--state-dir", help="Private durable directory (default private/ or SCREENER_DESK_DIR)"
    )
    sub = p.add_subparsers(dest="command", required=True)
    sub.add_parser("init")
    for cmd in ("watch", "dossier"):
        c = sub.add_parser(cmd)
        c.add_argument("market", choices=["nse", "snp"])
        c.add_argument("symbol")
        if cmd == "watch":
            c.add_argument("--remove", action="store_true")
        else:
            c.add_argument("--kind", choices=["source", "note", "ticket"])
            c.add_argument("--limit", type=int, default=10)
            c.add_argument("--offset", type=int, default=0)
    for cmd in ("watchlist", "portfolio", "refresh", "deliveries"):
        sub.add_parser(cmd)
    c = sub.add_parser("screen")
    c.add_argument("--signal", choices=["uptrend", "volume_breakout", "below_200dma"])
    c.add_argument("--limit", type=int, default=20)
    c.add_argument(
        "--include-stale", action="store_true", help="Include explicitly marked stale snapshots"
    )
    for cmd in ("event", "source", "note", "ticket"):
        c = sub.add_parser(cmd)
        c.add_argument("path", help="JSON record; see ADVISORY.md")
    c = sub.add_parser("record")
    c.add_argument("id")
    c.add_argument("--offset", type=int, default=0, help="Character offset in the stored JSON")
    c.add_argument("--chars", type=int, default=6000)
    c = sub.add_parser("search")
    c.add_argument("query", help="SQLite FTS5 expression; quote literal phrases")
    c.add_argument("--limit", type=int, default=10)
    c = sub.add_parser("check")
    c.add_argument("--refresh", action="store_true")
    c.add_argument(
        "--telegram", action="store_true", help="Explicitly send notifications; default dry run"
    )
    c.add_argument("--briefing", action="store_true")
    c = sub.add_parser("notify-ticket")
    c.add_argument("id")
    c.add_argument("--telegram", action="store_true", help="Default is dry-run preview")
    c = sub.add_parser("backup")
    c.add_argument("path")
    return p


def dispatch(desk, args):
    cmd = args.command
    if cmd == "init":
        return {
            "database": str(desk.path),
            "advisory_only": True,
            "portfolio_active": desk.portfolio()["active"],
        }
    if cmd == "watch":
        desk.watch(args.market, args.symbol, args.remove)
        return desk.watched()
    if cmd == "watchlist":
        return desk.watched()
    if cmd == "portfolio":
        return desk.portfolio()
    if cmd == "event":
        return {"inserted": desk.add_event(load_json(args.path))}
    if cmd in ("source", "note", "ticket"):
        return {"inserted": desk.put_record(cmd, load_json(args.path))}
    if cmd == "dossier":
        rows = desk.records(args.market, args.symbol, args.kind, args.limit, args.offset)
        return {
            "records": [
                {k: r.get(k) for k in ("id", "kind", "stored_at", "title", "horizon", "source_ids")}
                | {"summary": str(r.get("thesis", r.get("excerpt", "")))[:300]}
                for r in rows
            ],
            "next_offset": args.offset + len(rows) if len(rows) == args.limit else None,
        }
    if cmd == "record":
        if not 1 <= args.chars <= 10000 or args.offset < 0:
            raise ValueError("chars must be 1..10000; offset must be nonnegative")
        row = desk.con.execute("SELECT body FROM records WHERE id=?", (args.id,)).fetchone()
        if not row:
            raise ValueError("Record not found")
        body = json.dumps(json.loads(row[0]), indent=2, ensure_ascii=False)
        end = args.offset + args.chars
        return {"text": body[args.offset : end], "next_offset": end if end < len(body) else None}
    if cmd == "search":
        return desk.search(args.query, args.limit)
    if cmd == "screen":
        if not 1 <= args.limit <= 50:
            raise ValueError("limit must be 1..50")
        rows = desk.snapshots()
        return [
            r
            for r in rows
            if (args.include_stale or not r["stale"])
            and (not args.signal or args.signal in r["signals"])
        ][: args.limit]
    if cmd in ("refresh", "check"):
        from .monitor import check, refresh

        # POSIX single-run lock: cron/manual runs must not overlap fetch/notifications.
        with (desk.path.parent / "monitor.lock").open("w") as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise ValueError("Another monitoring run is in progress") from None
            if cmd == "refresh":
                result = refresh(desk)
                return {
                    "refreshed": [
                        {"market": s["market"], "symbol": s["symbol"]} for s in result["snapshots"]
                    ],
                    "errors": result["errors"],
                }
            return check(
                desk, do_refresh=args.refresh, telegram=args.telegram, briefing=args.briefing
            )
    if cmd == "notify-ticket":
        from .notifications import send_telegram
        from .tickets import render_ticket, validate_ticket

        row = desk.con.execute(
            "SELECT body FROM records WHERE id=? AND kind='ticket'", (args.id,)
        ).fetchone()
        if not row:
            raise ValueError("Ticket not found")
        ticket = json.loads(row[0])
        validate_ticket(ticket)  # An expired stored ticket must never be re-promoted.
        text = render_ticket(ticket)
        for sid in ticket["source_ids"]:
            source = json.loads(
                desk.con.execute("SELECT body FROM records WHERE id=?", (sid,)).fetchone()[0]
            )
            text += f"\n{sid}: {source['url']} ({source['locator']})"
        result = send_telegram(desk, f"ticket:{args.id}", text) if args.telegram else "dry_run"
        return {"text": text, "delivery": result}
    if cmd == "deliveries":
        return [
            dict(r)
            for r in desk.con.execute("SELECT * FROM deliveries ORDER BY updated_at DESC LIMIT 30")
        ]
    if cmd == "backup":
        return {"backup": desk.backup(args.path)}
    raise ValueError("Unknown command")


def main(argv=None):
    args = parser().parse_args(argv)
    desk = None
    try:
        desk = Desk(args.state_dir)
        result = dispatch(desk, args)
        print(json.dumps(result, indent=2, ensure_ascii=False, allow_nan=False))
        if isinstance(result, dict) and (result.get("refresh_errors") or result.get("errors")):
            return 2  # Partial/total data-refresh failure must be visible to a scheduler.
    except (TypeError, ValueError, RuntimeError, OSError, sqlite3.Error) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    finally:
        if desk:
            desk.close()
    return 0
