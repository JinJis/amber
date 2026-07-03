"""Slack adapter — incoming webhook. config: {webhook_url}."""

from __future__ import annotations

from studioapi.channels._http import post_json


def send(config: dict, payload: dict, text: str) -> str:
    webhook = config.get("webhook_url")
    if not webhook:
        return "simulated"
    return "sent" if post_json(webhook, {"text": text}) else "failed"
