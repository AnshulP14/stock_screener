"""Opt-in outbound Telegram only. Delivery uncertainty never causes automatic retries."""

import hashlib
import os

import requests

from .desk import now_iso


def send_telegram(desk, key, text):
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    chat = os.environ.get("TELEGRAM_CHAT_ID", "")
    if not token or not chat:
        raise ValueError("Set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID in the environment")
    key = hashlib.sha256(f"{chat}:{key}".encode()).hexdigest()
    with desk.con:
        # Pending is durable before network I/O. Timeouts/crashes need manual reconciliation.
        inserted = desk.con.execute(
            "INSERT OR IGNORE INTO deliveries VALUES (?,'pending',?)", (key, now_iso())
        ).rowcount
    if not inserted:
        return "already_sent_or_pending"
    try:
        # Conservative bound also fits Telegram UTF-16 limits for astral characters.
        chunks = [text[i : i + 1800] for i in range(0, len(text), 1800)] or ["(empty)"]
        for chunk in chunks:
            response = requests.post(
                f"https://api.telegram.org/bot{token}/sendMessage",
                json={
                    "chat_id": chat,
                    "text": chunk,
                    "link_preview_options": {"is_disabled": True},
                },
                timeout=20,
            )
            response.raise_for_status()
            if response.json().get("ok") is not True:
                raise ValueError("Telegram rejected message")
    except (requests.RequestException, ValueError):
        # Never propagate a requests exception: its URL includes the bot secret.
        raise RuntimeError(
            "Telegram delivery uncertain/failed; inspect delivery status and chat before retrying"
        ) from None
    with desk.con:
        desk.con.execute(
            "UPDATE deliveries SET status='sent',updated_at=? WHERE key=?", (now_iso(), key)
        )
    return "sent"
