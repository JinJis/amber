"""Channel adapters for notification alerts (F3 messengers).

One adapter per messenger. Credentials are **server-side only** (on the user's
:class:`ChannelConnection`), never in the browser. With no usable credentials a send returns
``simulated`` — we record the exact message we *would* have sent so the alert→delivery flow is
verifiable end-to-end without external accounts. Real sends retry once with backoff and degrade
to ``failed`` on error (never a fake ``sent``).

Every message carries the trust envelope: source · as_of · a "대시보드/탐색에서 보기" deep link.
The body is built upstream from sourced facts only (no advice/forecast) — see ``alerts.py``.
"""

from __future__ import annotations

import logging

from studioapi.channels import email, kakao, slack, telegram

log = logging.getLogger("studioapi.channels")

SUPPORTED = ("telegram", "slack", "kakao", "email")
_ADAPTERS = {"telegram": telegram, "slack": slack, "kakao": kakao, "email": email}


def format_text(payload: dict) -> str:
    """Render the trust envelope as plain text — title, body, then source · as_of · deep link.

    That trailing line is the same trust signature an artifact carries; it must always be present.
    """
    title = (payload.get("title") or "").strip()
    body = (payload.get("body") or "").strip()
    src = payload.get("source") or "—"
    as_of = payload.get("as_of") or "—"
    link = payload.get("deeplink") or "/"
    lines = [l for l in (title, body) if l]
    lines.append(f"{src} · as_of {as_of} · 대시보드에서 보기 ↗ {link}")
    return "\n".join(lines)


def send(channel: str, config: dict | None, payload: dict) -> str:
    """Dispatch ``payload`` to ``channel`` using server-side ``config``.

    Returns ``sent`` | ``simulated`` | ``failed``.
    """
    adapter = _ADAPTERS.get(channel)
    if adapter is None:
        log.warning("unknown channel: %s", channel)
        return "failed"
    text = format_text(payload)
    try:
        return adapter.send(config or {}, payload, text)
    except Exception as exc:  # never let a channel error crash dispatch
        log.warning("channel %s send failed: %s", channel, exc)
        return "failed"
