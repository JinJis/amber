"""KakaoTalk adapter — 알림톡/비즈메시지 needs business onboarding + OAuth; until that's linked we
simulate. A BYO relay webhook (config: {webhook_url}) is honored if present."""

from __future__ import annotations

from studioapi.channels._http import post_json


def send(config: dict, payload: dict, text: str) -> str:
    webhook = config.get("webhook_url")
    if not webhook:
        return "simulated"
    return "sent" if post_json(webhook, {"text": text}) else "failed"
