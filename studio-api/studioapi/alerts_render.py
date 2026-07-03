"""Alert domain: message rendering + firing + scheduling (RF-12, split from alerts.py).

The pure, HTTP-free core of the notification-bot feature — what a due alert SAYS and DOES:
``render_message`` composes a sourced, facts-only message (board digest of periodic widgets, or a
single widget's fresh figure), ``fire_alert`` sends it across the user's channels (one
``NotificationDelivery`` each) and reschedules, and ``compute_next_fire`` advances the schedule. The
routes (alerts.py) and the scheduler (scheduler.py) both call into here; it imports neither, so the
domain is unit-testable without FastAPI.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta

import httpx
from sqlalchemy import select

from studioapi import channels as channels_mod
from studioapi.config import settings
from studioapi.models import (
    ChannelConnection,
    NotificationAlert,
    NotificationDelivery,
    PinnedArtifact,
    User,
)

log = logging.getLogger("studioapi.alerts")


TRIGGER_TYPES = ("earnings", "rate", "macro_indicator", "filing_news", "price_threshold", "digest")

# Per-trigger defaults: label + the upstream source line every message carries. Sources map to the
# real upstreams the trigger is checked against.
_TRIGGER_META: dict[str, dict] = {
    "earnings": {"label": "실적 발표", "source": "DART · SEC EDGAR"},
    "rate": {"label": "미 연준/한은 금리", "source": "FOMC · CME FedWatch · 한은"},
    "macro_indicator": {"label": "경제지표(CPI·고용)", "source": "FRED · BLS"},
    "filing_news": {"label": "공시·뉴스", "source": "DART · SEC EDGAR"},
    "price_threshold": {"label": "가격·밸류 임계치", "source": "Yahoo Finance · KIS"},
    "digest": {"label": "정기 요약", "source": "ValueGraph"},
}
_FORBIDDEN = ("매수", "매도", "목표가", "사세요", "파세요", "전망", "예측", "target price")


def _target_label(params: dict) -> str:
    if params.get("target"):
        return str(params["target"])
    if params.get("ticker"):
        return str(params["ticker"])
    if params.get("group"):
        return "@" + str(params["group"]).lstrip("@")
    return "관심 대상"


def _factual_guard(text: str) -> str:
    low = text.lower()
    if any(tok and tok.lower() in low for tok in _FORBIDDEN):
        log.warning("factual guard tripped; downgrading alert message")
        return "출처 기반 업데이트가 있습니다. 대시보드에서 원문과 근거를 확인하세요."
    return text


def compute_next_fire(schedule: dict | None, *, now: datetime | None = None) -> datetime | None:
    """Next run from a schedule dict {freq: daily|weekly|monthly|interval, time?, every_minutes?}.
    ``realtime``/``event`` (price thresholds, filings) are checked continuously → None."""
    now = now or datetime.utcnow()
    schedule = schedule or {}
    freq = schedule.get("freq", "daily")
    if freq == "interval":
        return now + timedelta(minutes=max(1, int(schedule.get("every_minutes", 60))))
    if freq in ("realtime", "event"):
        return None
    hh, mm = 8, 0
    t = schedule.get("time")
    if isinstance(t, str) and ":" in t:
        try:
            hh, mm = (int(x) for x in t.split(":", 1))
        except ValueError:
            pass
    nxt = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
    if nxt <= now:
        nxt += timedelta(days=1)
    if freq == "weekly":
        nxt += timedelta(days=7)
    elif freq == "monthly":
        nxt += timedelta(days=30)
    return nxt


def _board_periodic_specs(alert: NotificationAlert, db) -> list[dict]:
    """The board's PERIODIC pinned-widget specs (cadence != one_shot) — one-shot values carry no
    recurring update, so the root digest excludes them. Returns the JSON specs (tool/args/source…)."""
    if not alert.board_id:
        return []
    pins = db.execute(
        select(PinnedArtifact).where(
            PinnedArtifact.user_email == alert.user_email,
            PinnedArtifact.board_id == alert.board_id,
        )
    ).scalars().all()
    out: list[dict] = []
    for p in pins:
        try:
            spec = json.loads(p.spec) if p.spec else {}
        except (ValueError, TypeError):
            continue
        cad = spec.get("cadence")
        if cad and cad != "one_shot":
            spec.setdefault("title", p.title)
            out.append(spec)
    return out


def _fetch_artifact(tool: str | None, args: dict | None, api_key: str | None,
                    title: str | None = None) -> dict | None:
    """Re-run a widget's tool through the agent-engine refresh endpoint → a FRESH artifact dict —
    the exact path the dashboard '↻' uses — authenticated with the user's tenant key (so entitlement
    + metering apply). This is how the bot fetches up-to-date data on schedule. Sync (the fire path
    is sync, offloaded to a thread); returns None on any failure → caller degrades to a template."""
    if not (tool and api_key):
        return None
    try:
        with httpx.Client(timeout=settings.http_timeout_seconds) as client:
            resp = client.post(
                f"{settings.agent_engine_url}/agent/artifact/refresh",
                json={"tool": tool, "args": args or {}, "title": title},
                headers={"X-API-KEY": api_key},
            )
        if resp.status_code == 200:
            return (resp.json() or {}).get("artifact")
        log.warning("alert fetch %s → HTTP %s", tool, resp.status_code)
    except Exception:  # noqa: BLE001 — never let a fetch failure stop the alert
        log.warning("alert fetch failed (tool=%s)", tool, exc_info=True)
    return None


def _latest_figure(art: dict | None) -> str:
    """A one-line 'latest value' pulled from a freshly-fetched artifact (presentation only, not
    reasoning): newest candle close, else newest series point, else the first table data row."""
    if not isinstance(art, dict):
        return ""
    candles = art.get("candles") or []
    if candles and isinstance(candles[-1], dict) and candles[-1].get("close") is not None:
        c = candles[-1]
        return f"{c.get('x', '')} 종가 {c.get('close')}".strip()
    for s in (art.get("series") or []):
        pts = (s or {}).get("points") or []
        if pts:
            p = pts[-1]
            nm = s.get("name") or art.get("title") or ""
            return f"{p.get('x', '')} {nm} {p.get('y')}".strip()
    tbl = art.get("table") or []
    if len(tbl) >= 2 and tbl[1]:
        return " · ".join(str(x) for x in tbl[1][:4])
    return ""


def _widget_fresh_payload(alert: NotificationAlert, spec: dict, api_key: str | None,
                          now: datetime) -> dict | None:
    """Fetch the widget's latest data via its tool+args and render a sourced message from the FRESH
    value, noting the change vs the previously-delivered value (tracked in source_spec). None when
    there's no fetchable tool / the fetch failed → caller uses the trigger template."""
    art = _fetch_artifact(spec.get("tool"), spec.get("args"), api_key, spec.get("title"))
    if not art:
        return None
    title = art.get("title") or spec.get("title") or alert.name
    fig = _latest_figure(art)
    prev = spec.get("_last_fig")
    if fig and prev and fig != prev:
        change = f"\n변동: 이전 «{prev}» → 현재 «{fig}»"
    elif fig and prev and fig == prev:
        change = "\n(직전 알림 대비 변동 없음)"
    else:
        change = ""
    spec["_last_fig"] = fig  # remember for the next fire's change detection
    alert.source_spec = json.dumps(spec, ensure_ascii=False)
    body = f"{fig}{change}" if fig else "데이터를 최신으로 갱신했어요 — 자세한 값은 대시보드에서 확인하세요."
    deeplink = spec.get("deeplink") or (
        f"/?board={alert.board_id}&widget={alert.pin_id}" if alert.pin_id else f"/?alert={alert.id}")
    return {
        "title": f"🔔 {title} — 최신값",
        "body": _factual_guard(body),
        "source": art.get("source") or spec.get("source") or "ValueGraph",
        "as_of": str(art.get("as_of") or now.date().isoformat())[:10],
        "deeplink": deeplink,
    }


def render_message(alert: NotificationAlert, *, now: datetime | None = None, db=None,
                   api_key: str | None = None) -> dict:
    """Build the sourced message for one fire: title/body/source/as_of/deeplink.

    On schedule the bot RE-FETCHES live data via each widget's tool+args (``api_key`` = the user's
    tenant key) so the message carries the LATEST value, not a template:
      * a board-scope ``digest`` fetches every periodic widget and summarizes the fresh values;
      * a widget alert fetches its one widget and reports the value + change since the last fire.
    When there's no fetchable tool / the fetch fails, it degrades to the trigger-type template.
    Facts-shaped only (never advice/forecast); the deeplink always points back to the evidence.
    """
    now = now or datetime.utcnow()
    params = json.loads(alert.params) if alert.params else {}
    meta = _TRIGGER_META.get(alert.trigger_type, {"label": alert.trigger_type, "source": "ValueGraph"})
    target = _target_label(params)
    spec = json.loads(alert.source_spec) if alert.source_spec else {}

    # 1) board digest → fetch each periodic widget fresh and summarize the latest values
    if alert.scope == "board" and alert.trigger_type == "digest" and db is not None:
        specs = _board_periodic_specs(alert, db)
        if specs:
            lines = []
            for s in specs[:8]:
                art = _fetch_artifact(s.get("tool"), s.get("args"), api_key, s.get("title")) if s.get("tool") else None
                src = art or s
                bits = [f"• {src.get('title') or '위젯'}"]
                fig = _latest_figure(art) if art else ""
                if fig:
                    bits.append(fig)
                if src.get("source"):
                    bits.append(src["source"])
                if src.get("as_of"):
                    bits.append(f"as_of {str(src['as_of'])[:10]}")
                lines.append(" · ".join(bits))
            return {
                "title": f"🔔 {alert.name} — 주기성 위젯 {len(specs)}개 최신값",
                "body": _factual_guard("이 보드의 주기성 위젯 최신값입니다 (사실·출처만):\n" + "\n".join(lines)),
                "source": "ValueGraph 대시보드",
                "as_of": now.date().isoformat(),
                "deeplink": spec.get("deeplink") or (f"/?board={alert.board_id}" if alert.board_id else f"/?alert={alert.id}"),
            }

    # 2) a widget-backed alert → fetch its widget fresh and report the latest value (+ change)
    if spec.get("tool"):
        fresh = _widget_fresh_payload(alert, spec, api_key, now)
        if fresh:
            return fresh

    # 3) fallback template (no fetchable tool, or the fetch failed)
    if alert.trigger_type == "rate":
        title, body = f"🔔 {target} — 금리 발표 모니터", "예정된 금리 결정과 직전/직후 사실을 추적합니다. 점도표·성명서 원문은 대시보드에서."
    elif alert.trigger_type == "earnings":
        title, body = f"🔔 {target} 실적 — 공시 추적", "실적 발표가 가까워지면 접수된 공시의 사실을 정리해 보냅니다."
    elif alert.trigger_type == "macro_indicator":
        title, body = f"🔔 {target} — 경제지표 업데이트", "발표된 지표 수치를 원 출처(FRED·BLS) 그대로 전달합니다."
    elif alert.trigger_type == "filing_news":
        title, body = f"🔔 {target} — 새 공시·뉴스", "새 공시/뉴스 제목과 원문 링크를 그대로 전달합니다."
    elif alert.trigger_type == "price_threshold":
        thr = params.get("threshold") or params.get("level")
        title = f"🔔 {target} — 가격·밸류 임계치"
        body = f"설정한 임계치({thr}) 도달 여부를 감시합니다." if thr else "설정한 임계치 도달 여부를 감시합니다."
    else:
        title, body = f"🔔 {alert.name} — 정기 요약", f"{target} 관련 사실을 주기적으로 정리해 보냅니다."

    return {
        "title": title,
        "body": _factual_guard(body),
        "source": spec.get("source") or meta["source"],
        "as_of": now.date().isoformat(),
        "deeplink": spec.get("deeplink") or f"/?alert={alert.id}",
    }


def fire_alert(alert: NotificationAlert, db, *, now: datetime | None = None) -> list[NotificationDelivery]:
    """Render the message and push to every channel, recording one delivery each. Resolves each
    channel's server-side :class:`ChannelConnection` for credentials (missing → ``simulated``).
    Updates last/next_fire_at. Caller commits."""
    now = now or datetime.utcnow()
    # the user's tenant key (server-side) authorizes the live data fetch through the gateway
    user = db.get(User, alert.user_email)
    payload = render_message(alert, now=now, db=db, api_key=user.api_key if user else None)
    kinds = json.loads(alert.channels or "[]") or []
    creds = {
        c.channel: json.loads(c.config or "{}")
        for c in db.execute(
            select(ChannelConnection).where(ChannelConnection.user_email == alert.user_email)
        ).scalars().all()
    }
    out: list[NotificationDelivery] = []
    for kind in kinds:
        status = channels_mod.send(kind, creds.get(kind), payload)
        d = NotificationDelivery(
            alert_id=alert.id, user_email=alert.user_email, channel=kind, status=status,
            title=payload["title"][:240], body=payload["body"],
            as_of=payload["as_of"], source=payload["source"], deeplink=payload["deeplink"],
        )
        db.add(d)
        out.append(d)
    alert.last_fired_at = now
    alert.next_fire_at = compute_next_fire(json.loads(alert.schedule) if alert.schedule else {}, now=now)
    return out


# --------------------------------------------------------------------------- /alerts
