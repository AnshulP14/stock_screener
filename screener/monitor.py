"""Deterministic screening/briefings. Research escalation remains an agent task."""

from datetime import UTC, datetime

from .notifications import send_telegram
from .technicals import fetch_history, snapshot


def refresh(desk):
    targets = {(w["market"], w["symbol"]) for w in desk.watched()}
    targets.update((h["market"], h["symbol"]) for h in desk.portfolio()["holdings"])
    results, errors = [], []
    # One sequential stream, including benchmarks. No burst fetch across a universe.
    benchmarks = {}
    for market, symbol in sorted(targets):
        try:
            if market not in benchmarks:
                try:
                    benchmarks[market] = fetch_history(
                        market, "^NSEI" if market == "nse" else "SPY"
                    )
                except Exception:
                    benchmarks[market] = None
            result = snapshot(
                market, symbol, fetch_history(market, symbol), benchmark=benchmarks[market]
            )
            desk.save_snapshot(result)
            results.append(result)
        except Exception as exc:
            # Do not silently overwrite last good data or expose arbitrary upstream URLs.
            errors.append({"market": market, "symbol": symbol, "error": type(exc).__name__})
    return {"snapshots": results, "errors": errors}


def check(desk, *, do_refresh=False, telegram=False, briefing=False):
    fetched = refresh(desk) if do_refresh else {"snapshots": [], "errors": []}
    portfolio = desk.portfolio()
    targets = {(w["market"], w["symbol"]) for w in desk.watched()}
    targets.update((h["market"], h["symbol"]) for h in portfolio["holdings"])
    failures = {(e["market"], e["symbol"]) for e in fetched["errors"]}
    today = datetime.now(UTC).date()
    cards = []
    for error in fetched["errors"]:
        cards.append(
            {
                "key": f"health:{today}:{error['market']}:{error['symbol']}:refresh_failed",
                "text": (
                    f"DATA CHECK FAILED — {error['market']}:{error['symbol']} ({error['error']})\n"
                    "Cannot evaluate current technical conditions. Cached portfolio marks may be stale; "
                    "do not treat missing alerts as an all-clear."
                ),
            }
        )
    for s in desk.snapshots():
        key = s["market"], s["symbol"]
        # Never re-alert cached data after a failed refresh; cache itself also ages.
        if key not in targets or key in failures:
            continue
        age = (today - datetime.fromisoformat(s["fetched_at"]).date()).days
        candle_age = (today - datetime.fromisoformat(s["signal_date"]).date()).days
        if s["stale"] or age > 1 or candle_age > 4:
            continue
        for signal in s["signals"]:
            text = (
                f"INVESTIGATE — {s['market']}:{s['symbol']} — {signal}\n"
                f"Completed daily candle: {s['signal_date']}; retrieved: {s['fetched_at']}\n"
                "Technical screen only, not a trade recommendation. Read dossier, verify catalyst and contrary evidence."
            )
            delivery_key = f"signal:{s['market']}:{s['symbol']}:{s['signal_date']}:{signal}"
            cards.append({"key": delivery_key, "text": text})
    if briefing:
        lines = [
            f"ADVISORY DESK — {today}",
            f"Portfolio: {'active' if portfolio['active'] else 'inactive — awaiting starting holdings'}",
            f"Watching {len(targets)} instruments. Prices are daily closes/partial daily bars, not live quotes.",
        ]
        for h in portfolio["holdings"]:
            mark = h["mark"]
            lines.append(
                f"{h['market']}:{h['symbol']} qty {h['quantity']} | value {h['market_value']} {h['currency']} | "
                f"price date {mark['bar_date'] if mark else 'MISSING'} | "
                f"share of marked {h['currency']} equities: {h['share_of_marked_equities_in_currency']}"
            )
        for s in desk.snapshots():
            if (s["market"], s["symbol"]) in targets:
                lines.append(
                    f"{s['market']}:{s['symbol']} signal date {s['signal_date']}; fetched {s['fetched_at']}; "
                    f"{'STALE' if s['stale'] or (today - datetime.fromisoformat(s['signal_date']).date()).days > 4 else 'daily data'}"
                )
        for error in fetched["errors"]:
            lines.append(
                f"REFRESH FAILED {error['market']}:{error['symbol']} ({error['error']}); cached prices may be stale"
            )
        lines.append(
            "No broker connection. Report actual fills/corporate actions. Cash flows are not verified balances."
        )
        cards.append({"key": f"briefing:{today}", "text": "\n".join(lines)})
    for card in cards:
        card["delivery"] = send_telegram(desk, card["key"], card["text"]) if telegram else "dry_run"
    return {
        "cards": cards,
        "refresh_errors": fetched["errors"],
        "portfolio_active": portfolio["active"],
    }
