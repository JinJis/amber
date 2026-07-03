"""Telegram adapter — Bot API sendMessage. config: {bot_token, chat_id}."""

from __future__ import annotations

from studioapi.channels._http import post_json


def send(config: dict, payload: dict, text: str) -> str:
    token, chat_id = config.get("bot_token"), config.get("chat_id")
    if not token or not chat_id:
        return "simulated"
    ok = post_json(f"https://api.telegram.org/bot{token}/sendMessage", {"chat_id": chat_id, "text": text})
    return "sent" if ok else "failed"
