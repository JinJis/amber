"""Email adapter — a transactional relay webhook (config: {webhook_url, to}) is honored; SMTP
creds aren't shipped through the browser-facing path, so without a relay we simulate."""

from __future__ import annotations

from studioapi.channels._http import post_json


def send(config: dict, payload: dict, text: str) -> str:
    webhook, to = config.get("webhook_url"), config.get("to")
    if not webhook or not to:
        return "simulated"
    ok = post_json(webhook, {"to": to, "subject": payload.get("title", ""), "text": text})
    return "sent" if ok else "failed"
