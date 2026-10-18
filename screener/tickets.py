"""Validate manual, long-only cash-equity proposals; never submit orders."""

from datetime import UTC, datetime

from .desk import instrument, number, required_text, timestamp


def validate_ticket(ticket):
    market, _ = instrument(ticket.get("market"), ticket.get("symbol", ""))
    for field in ("thesis", "counterarguments", "invalidation", "holding_period", "catalysts"):
        required_text(ticket, field)
    if ticket.get("horizon") not in ("swing", "position"):
        raise ValueError("horizon must be swing or position")
    created = timestamp(required_text(ticket, "researched_at"))
    expires = timestamp(required_text(ticket, "expires_at"))
    price_at = timestamp(required_text(ticket, "price_timestamp"))
    if not price_at <= created < expires or expires <= datetime.now(UTC).isoformat():
        raise ValueError("Need price timestamp <= research timestamp < future expiry")
    if created > datetime.now(UTC).isoformat():
        raise ValueError("Research timestamp cannot be in the future")
    required_text(ticket, "price_source")
    tick = number(ticket.get("tick_size"), positive=True)
    required_text(ticket, "tick_size_source")
    entry = ticket.get("entry")
    stop = ticket.get("stop")
    targets = ticket.get("targets")
    if (
        not isinstance(entry, dict)
        or not isinstance(stop, dict)
        or not isinstance(targets, list)
        or not targets
    ):
        raise ValueError("Need entry, separate stop, and at least one target")
    if entry.get("transaction_type") != "BUY" or entry.get("order_type") != "LIMIT":
        raise ValueError("v1 supports long-only BUY LIMIT entries, not shorts or market orders")
    if entry.get("validity") != "DAY":
        raise ValueError("v1 proposals use DAY entries; recommendation expiry is separate")
    if market == "nse":
        expected = {"exchange": "NSE", "tradingsymbol": ticket["symbol"], "product": "CNC"}
        if any(entry.get(k) != v for k, v in expected.items()):
            raise ValueError("India cash-equity entries need NSE, exact tradingsymbol, CNC")
        if ticket.get("variety") != "regular":
            raise ValueError("Kite entry variety must be regular")
    elif "product" in entry or "variety" in ticket:
        raise ValueError("Do not imply Vested supports Kite product/variety semantics")
    if stop.get("order_type") != "SL" or stop.get("transaction_type") != "SELL":
        raise ValueError("Stop must specify SELL SL trigger and limit, separate from entry")
    prices = [entry.get("price"), stop.get("trigger_price"), stop.get("price")]
    for target in targets:
        if (
            not isinstance(target, dict)
            or target.get("transaction_type") != "SELL"
            or target.get("order_type") != "LIMIT"
        ):
            raise ValueError("Targets must be SELL LIMIT plans")
        prices.append(target.get("price"))
    prices = [number(p, positive=True) for p in prices]
    if any(p % tick for p in prices):
        raise ValueError("All prices must respect the verified instrument tick size")
    buy, trigger, limit = prices[:3]
    if not limit <= trigger < buy or any(p <= buy for p in prices[3:]):
        raise ValueError("For long entries: stop limit <= trigger < entry < each target")
    quantity = entry.get("quantity")
    if quantity is not None:
        q = number(quantity, positive=True)
        if market == "nse" and q != q.to_integral_value():
            raise ValueError("NSE cash quantities must be whole shares")
        required_text(ticket, "sizing_rationale")
        required_text(ticket, "user_risk_limits_reference")
        allocations = [number(t.get("quantity"), positive=True) for t in targets]
        if sum(allocations) != q or number(stop.get("quantity"), positive=True) != q:
            raise ValueError("Target quantities must total entry quantity; stop must cover it")
        if market == "nse" and any(n != n.to_integral_value() for n in allocations):
            raise ValueError("NSE target quantities must be whole shares")
    elif stop.get("quantity") is not None or any(t.get("quantity") is not None for t in targets):
        raise ValueError("Unsized tickets must have null quantities throughout")
    return ticket


def render_ticket(ticket):
    """Plain-text Telegram card; broker-shaped fields are suggestions, not API requests."""
    e, s = ticket["entry"], ticket["stop"]
    q = e.get("quantity")
    entry = number(e["price"], positive=True)
    stop = number(s["price"], positive=True)
    capital = str(entry * number(q, positive=True)) if q is not None else "not sized"
    risk = str((entry - stop) * number(q, positive=True)) if q is not None else "not sized"
    targets = ", ".join(
        f"{t['price']} (qty {t.get('quantity') or 'not sized'})" for t in ticket["targets"]
    )
    return "\n".join(
        [
            f"ADVISORY ONLY — {ticket['id']} — {ticket['market']}:{ticket['symbol']}",
            f"{ticket['horizon']} / {ticket['holding_period']}",
            f"BUY LIMIT ≤ {e['price']} | Qty: {q if q is not None else 'not sized'} | {e.get('product', 'verify Vested support')} | DAY",
            f"Separate SELL stop: trigger {s['trigger_price']}, limit {s['price']}",
            f"Targets: {targets}",
            f"Capital before costs: {capital}; loss at stop limit before costs: {risk}",
            "Gaps/non-fills can cause larger losses. Stop-limit is not guaranteed protection.",
            f"Price timestamp: {ticket['price_timestamp']} ({ticket['price_source']})",
            f"Recommendation expires: {ticket['expires_at']} (not broker validity)",
            f"Thesis: {ticket['thesis']}",
            f"Catalysts: {ticket['catalysts']}",
            f"Counterarguments: {ticket['counterarguments']}",
            f"Invalidation: {ticket['invalidation']}",
            "Sources: " + ", ".join(ticket["source_ids"]),
            "Manual plan: place entry yourself; confirm actual fills before configuring exits.",
            "No attached bracket assumed. For eligible India holdings, consider GTT OCO; verify current broker rules.",
        ]
    )
