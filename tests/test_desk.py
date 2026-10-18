import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from screener.desk import Desk
from screener.desk_cli import main
from screener.tickets import render_ticket


@pytest.fixture
def desk(tmp_path):
    d = Desk(tmp_path / "private")
    yield d
    d.close()


def event(id="buy-1", type="BUY", **kwargs):
    return {
        "id": id,
        "type": type,
        "market": "nse",
        "symbol": "INFY",
        "quantity": "10",
        "price": "100",
        "occurred_at": "2025-01-01T10:00:00Z",
        **kwargs,
    }


def source(id="primary-1", **kwargs):
    return {
        "id": id,
        "market": "nse",
        "symbol": "INFY",
        "title": "Results",
        "url": "https://example.com/results",
        "category": "primary",
        "locator": "page 4",
        "retrieved_at": "2025-01-01T10:00:00Z",
        "published_at": None,
        "excerpt": "Revenue increased but margins declined.",
        **kwargs,
    }


def ticket(**kwargs):
    now = datetime.now(UTC)
    return {
        "id": "idea-1",
        "market": "nse",
        "symbol": "INFY",
        "horizon": "swing",
        "holding_period": "2–6 weeks",
        "researched_at": now.isoformat(),
        "price_timestamp": (now - timedelta(minutes=10)).isoformat(),
        "expires_at": (now + timedelta(days=1)).isoformat(),
        "price_source": "Manual verified quote",
        "tick_size": "0.05",
        "tick_size_source": "Verified instrument metadata",
        "variety": "regular",
        "entry": {
            "exchange": "NSE",
            "tradingsymbol": "INFY",
            "transaction_type": "BUY",
            "order_type": "LIMIT",
            "product": "CNC",
            "quantity": None,
            "price": "100",
            "validity": "DAY",
        },
        "stop": {
            "transaction_type": "SELL",
            "order_type": "SL",
            "quantity": None,
            "trigger_price": "95",
            "price": "94",
        },
        "targets": [
            {"transaction_type": "SELL", "order_type": "LIMIT", "quantity": None, "price": "115"}
        ],
        "thesis": "Illustrative only",
        "counterarguments": "Margin pressure",
        "invalidation": "Demand deteriorates",
        "catalysts": "Next results",
        "source_ids": ["primary-1"],
        **kwargs,
    }


def test_ledger_replay_fees_split_and_sale(desk):
    assert not desk.portfolio()["active"]
    assert desk.add_event(event(type="OPEN", fees="10"))
    assert not desk.add_event(event(type="OPEN", fees="10"))
    desk.add_event(event("split", "SPLIT", ratio="2", occurred_at="2025-01-02T10:00:00Z"))
    desk.add_event(
        event(
            "sale", "SELL", quantity="5", price="60", fees="2", occurred_at="2025-01-03T10:00:00Z"
        )
    )
    p = desk.portfolio()
    assert p["holdings"][0]["quantity"] == "15"
    assert Decimal(p["holdings"][0]["cost_basis"]) == Decimal("757.5")
    assert Decimal(p["realised_pnl"]["INR"]) == Decimal("45.5")
    assert p["net_recorded_cash_flows"]["INR"] == "298"


def test_equal_timestamps_preserve_import_order(desk):
    desk.add_event(event("z-buy"))
    desk.add_event(event("a-sale", "SELL", quantity="3"))
    assert desk.portfolio()["holdings"][0]["quantity"] == "7"


def test_nse_fractional_fills_rejected_us_allowed(desk):
    with pytest.raises(ValueError, match="whole shares"):
        desk.add_event(event(quantity="0.5"))
    desk.add_event(event(market="snp", symbol="AAPL", quantity="0.5"))
    assert desk.portfolio()["holdings"][0]["quantity"] == "0.5"


def test_event_conflict_oversell_backdate_atomic(desk):
    desk.add_event(event())
    for bad in (
        event(price="101"),
        event("sale", "SELL", quantity="11"),
        event("old-sale", "SELL", occurred_at="2024-01-01T10:00:00Z"),
    ):
        with pytest.raises(ValueError):
            desk.add_event(bad)
    assert len(desk.events()) == 1


@pytest.mark.parametrize("bad", ["NaN", "Infinity", "-1", 0, True])
def test_invalid_quantity(desk, bad):
    with pytest.raises((TypeError, ValueError)):
        desk.add_event(event(quantity=bad))
    assert desk.events() == []


def test_portfolio_marks_concentration_and_read_time_freshness(desk):
    desk.add_event(event())
    current = datetime.now(UTC).isoformat()
    desk.save_snapshot(
        {
            "market": "nse",
            "symbol": "INFY",
            "stale": False,
            "fetched_at": current,
            "signal_date": current[:10],
            "signals": [],
            "mark": {"price": 120, "bar_date": current[:10]},
        }
    )
    p = desk.portfolio()
    assert p["holdings"][0]["market_value"] == "1200"
    assert p["holdings"][0]["unrealised_pnl"] == "200"
    assert p["holdings"][0]["share_of_marked_equities_in_currency"] == "1"
    assert not p["holdings"][0]["mark"]["stale"]
    old = desk.snapshots()[0]
    old["fetched_at"] = "2020-01-01T00:00:00Z"
    desk.save_snapshot(old)
    assert desk.portfolio()["holdings"][0]["mark"]["stale"]


def test_unknown_basis_and_separate_cash(desk):
    desk.add_event(event(type="OPEN", price=None))
    desk.add_event(event("us", "DEPOSIT", currency="USD", amount="100"))
    desk.add_event(event("sale", "SELL", quantity="2", occurred_at="2025-01-02T10:00:00Z"))
    p = desk.portfolio()
    assert p["holdings"][0]["cost_basis"] is None
    assert p["realised_pnl"]["INR"] is None
    assert p["net_recorded_cash_flows"] == {"INR": "200", "USD": "100"}


def test_research_append_search_and_pagination(desk):
    desk.put_record("source", source())
    assert not desk.put_record("source", source())
    assert desk.search("margins")[0]["id"] == "primary-1"
    note = {
        "id": "note1",
        "market": "nse",
        "symbol": "INFY",
        "thesis": "Growth",
        "changes": "Margins declined",
        "counterarguments": "Competition",
        "invalidation": "Losses",
        "open_questions": "Can margins recover?",
        "source_ids": ["primary-1"],
    }
    desk.put_record("note", note)
    assert len(desk.records("nse", "INFY", limit=1)) == 1
    assert desk.records("nse", "INFY", offset=1)[0]["id"] == "primary-1"
    with pytest.raises(ValueError, match="new revision"):
        desk.put_record("note", note | {"thesis": "Changed"})


def test_forums_alone_and_wrong_instrument_rejected(desk):
    desk.put_record("source", source(category="forum"))
    with pytest.raises(ValueError, match="primary"):
        desk.put_record("ticket", ticket())
    with pytest.raises(ValueError, match="mismatch"):
        desk.put_record(
            "ticket", ticket(symbol="TCS", entry=ticket()["entry"] | {"tradingsymbol": "TCS"})
        )


def test_ticket_unsized_not_fill(desk):
    desk.put_record("source", source())
    t = ticket()
    assert desk.put_record("ticket", t)
    text = render_ticket(t)
    assert "not sized" in text and "BUY LIMIT ≤ 100" in text
    assert not desk.portfolio()["active"]


@pytest.mark.parametrize(
    "change",
    [
        {"expires_at": "2020-01-01T00:00:00Z"},
        {"tick_size": "0.3"},
        {"entry": ticket()["entry"] | {"order_type": "MARKET"}},
        {"entry": ticket()["entry"] | {"product": "MIS"}},
        {"entry": ticket()["entry"] | {"quantity": 5}},
        {"stop": ticket()["stop"] | {"trigger_price": "105"}},
    ],
)
def test_ticket_bad_fields_rejected(desk, change):
    desk.put_record("source", source())
    with pytest.raises(ValueError):
        desk.put_record("ticket", ticket(**change))


def test_backup_and_cli(tmp_path, capsys):
    state = tmp_path / "state"
    assert main(["--state-dir", str(state), "init"]) == 0
    assert json.loads(capsys.readouterr().out)["advisory_only"]
    assert main(["--state-dir", str(state), "watch", "nse", "INFY"]) == 0
    capsys.readouterr()
    assert main(["--state-dir", str(state), "check", "--briefing"]) == 0
    assert json.loads(capsys.readouterr().out)["cards"][-1]["delivery"] == "dry_run"
    d = Desk(state)
    backup = tmp_path / "backup.sqlite3"
    d.backup(backup)
    assert backup.exists() and backup.stat().st_mode & 0o077 == 0
    with pytest.raises(ValueError):
        d.backup(backup)
    d.close()
