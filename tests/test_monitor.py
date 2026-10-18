from datetime import UTC, datetime
from unittest.mock import Mock

import pytest
import requests

from screener.desk import Desk
from screener.monitor import check, refresh
from screener.notifications import send_telegram


@pytest.fixture
def desk(tmp_path):
    d = Desk(tmp_path)
    yield d
    d.close()


def test_telegram_dedup_and_no_secret_leak(desk, monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "top-secret")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "123")
    post = Mock(return_value=Mock(json=lambda: {"ok": True}))
    monkeypatch.setattr("screener.notifications.requests.post", post)
    assert send_telegram(desk, "one", "hello" * 800) == "sent"
    assert post.call_count == 3
    assert send_telegram(desk, "one", "hello") == "already_sent_or_pending"
    post.side_effect = requests.Timeout("https://api.telegram.org/bottop-secret/sendMessage")
    with pytest.raises(RuntimeError) as exc:
        send_telegram(desk, "two", "hello")
    assert "top-secret" not in str(exc.value)
    post.side_effect = None
    assert send_telegram(desk, "two", "hello") == "already_sent_or_pending"


def test_missing_telegram_credentials_no_delivery_claim(desk, monkeypatch):
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    with pytest.raises(ValueError):
        send_telegram(desk, "one", "hello")
    assert desk.con.execute("SELECT count(*) FROM deliveries").fetchone()[0] == 0


def cached(desk):
    today = datetime.now(UTC).date().isoformat()
    desk.watch("nse", "INFY")
    desk.save_snapshot(
        {
            "market": "nse",
            "symbol": "INFY",
            "stale": False,
            "signal_date": today,
            "fetched_at": datetime.now(UTC).isoformat(),
            "signals": ["volume_breakout"],
        }
    )


def test_dry_run_and_telegram_check(desk, monkeypatch):
    cached(desk)
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "secret")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "123")
    post = Mock(return_value=Mock(json=lambda: {"ok": True}))
    monkeypatch.setattr("screener.notifications.requests.post", post)
    assert check(desk)["cards"][0]["delivery"] == "dry_run"
    assert not post.called
    assert check(desk, telegram=True)["cards"][0]["delivery"] == "sent"
    assert check(desk, telegram=True)["cards"][0]["delivery"] == "already_sent_or_pending"
    assert post.call_count == 1


def test_failed_refresh_does_not_alert_previous_good_data(desk, monkeypatch):
    cached(desk)
    fetch = Mock(side_effect=RuntimeError("network failure"))
    monkeypatch.setattr("screener.monitor.fetch_history", fetch)
    result = check(desk, do_refresh=True, briefing=True)
    assert result["refresh_errors"]
    assert len(result["cards"]) == 2
    assert "DATA CHECK FAILED" in result["cards"][0]["text"]
    assert "REFRESH FAILED" in result["cards"][1]["text"]
    assert not any("INVESTIGATE" in card["text"] for card in result["cards"])


def test_aged_cache_and_unwatched_do_not_alert(desk):
    cached(desk)
    s = desk.snapshots()[0]
    s["fetched_at"] = "2020-01-01T00:00:00+00:00"
    desk.save_snapshot(s)
    assert check(desk)["cards"] == []
    desk.watch("nse", "INFY", remove=True)
    assert check(desk)["cards"] == []


def test_empty_refresh_does_not_use_network(desk, monkeypatch):
    fetch = Mock()
    monkeypatch.setattr("screener.monitor.fetch_history", fetch)
    assert refresh(desk) == {"snapshots": [], "errors": []}
    assert not fetch.called
