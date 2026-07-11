"""GUEST-1 — 익명(게스트) 체험 배관.

공유 페이지에서 suggestion을 누른 방문자가 **로그인 없이 바로 살아있는 채팅**에 착지하도록,
게스트를 실제 ``User`` 행(``guest_{id}@guest.local``, plan="guest")으로 만든다 — 대화·런·
소유권·쿼터 로직이 전부 무변경으로 동작하고, 가입 시 대화 이어붙이기(GUEST-3)가 단일
UPDATE가 된다.

인프라는 **공유 게스트 테넌트/프로젝트/키 1개**를 lazy 프로비저닝(control-plane admin 재사용,
무료 커넥터만 활성화, plan=guest rate 티어). 게스트마다 키를 만들지 않는 이유: 키 스프롤 +
resolve_key 테이블 증식. 게이트웨이 인바리언트는 그대로 — 게스트 턴도 게이트웨이 엔타이틀먼트
·미터링·가드레일을 전부 통과한다.

캡: 디바이스(쿠키 id) 평생 ``GUEST_TURNS_MAX``(quotas의 lifetime 판정) + 같은 IP 하루
``GUEST_TURNS_PER_IP_DAY``(어뷰즈 백스톱, quotas가 판정).
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re

from fastapi import HTTPException

from studioapi.config import settings
from studioapi.db import SessionLocal
from studioapi.models import GuestSession, ServiceState, User
from studioapi.plans import FREE_CONNECTORS
from studioapi.provision import _admin

logger = logging.getLogger(__name__)

_STATE_KEY = "guest_project"
_GID_RE = re.compile(r"^[a-f0-9]{16,48}$")   # 쿠키 id = uuid hex (BFF가 발급)
_lock = asyncio.Lock()


def guest_email(gid: str) -> str:
    return f"guest_{gid}@guest.local"


def is_guest_email(email: str | None) -> bool:
    return bool(email) and email.startswith("guest_") and email.endswith("@guest.local")


def gid_of(email: str) -> str | None:
    """게스트 이메일 → 세션 id (아니면 None)."""
    if not is_guest_email(email):
        return None
    return email.removeprefix("guest_").split("@", 1)[0]


def ip_hash(ip: str | None) -> str | None:
    if not ip:
        return None
    return hashlib.sha256((settings.guest_ip_salt + ip).encode()).hexdigest()


async def _ensure_guest_project() -> dict:
    """공유 게스트 테넌트/프로젝트/키 — 최초 게스트 요청에서 1회 프로비저닝(락 + KV 캐시)."""
    with SessionLocal() as db:
        row = db.get(ServiceState, _STATE_KEY)
        if row:
            return json.loads(row.value)
    async with _lock:
        with SessionLocal() as db:   # double-check under the lock
            row = db.get(ServiceState, _STATE_KEY)
            if row:
                return json.loads(row.value)
        tenant = await _admin("POST", "/admin/tenants", {"name": "guest"})
        project = await _admin("POST", f"/admin/tenants/{tenant['id']}/projects", {"name": "guest"})
        key = await _admin("POST", f"/admin/projects/{project['id']}/keys", {"name": "guest"})
        for cid in FREE_CONNECTORS:  # 무료 셋만 — 프리미엄 커넥터는 게스트에게 절대 열지 않음
            try:
                await _admin("POST", f"/admin/projects/{project['id']}/activations", {"connector_id": cid})
            except Exception:  # noqa: BLE001 — 일부 실패해도 나머지는 활성화 (provision과 동일 철학)
                pass
        try:
            await _admin("PATCH", f"/admin/projects/{project['id']}", {"plan": "guest"})
        except Exception:  # noqa: BLE001 — rate 티어는 best-effort (기본값도 안전)
            logger.warning("guest project plan patch failed — global rate default applies")
        state = {"tenant_id": tenant["id"], "project_id": project["id"], "api_key": key["api_key"]}
        with SessionLocal() as db:
            db.merge(ServiceState(key=_STATE_KEY, value=json.dumps(state)))
            db.commit()
        logger.info("guest project provisioned: %s", project["id"])
        return state


async def ensure_guest(gid: str, ip: str | None = None) -> User:
    """게스트 세션 검증 + User 행 보장. 형식이 틀리거나 이미 가입으로 이어진(claimed) 세션 → 401."""
    if not settings.feature_guest:
        raise HTTPException(401, "게스트 체험이 꺼져 있어요. 로그인해 주세요.")
    if not _GID_RE.match(gid or ""):
        raise HTTPException(401, "잘못된 게스트 세션이에요.")
    email = guest_email(gid)
    with SessionLocal() as db:
        sess = db.get(GuestSession, gid)
        if sess is None:
            db.add(GuestSession(id=gid, ip_hash=ip_hash(ip)))
            db.commit()
        elif sess.claimed_by:
            raise HTTPException(401, "이미 가입으로 이어진 체험 세션이에요. 로그인해 주세요.")
        existing = db.get(User, email)
        if existing is not None:
            return existing
    state = await _ensure_guest_project()
    user = User(email=email, tenant_id=state["tenant_id"], project_id=state["project_id"],
                api_key=state["api_key"], plan="guest", name="게스트",
                onboarded=True, email_verified=False)
    with SessionLocal() as db:
        db.merge(user)
        db.commit()
    return user
