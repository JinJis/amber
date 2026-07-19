"""AUTH-2 — 트랜잭션 메일 발송 (Resend).

단일 키(``RESEND_API_KEY``) + 보낸이(``EMAIL_FROM``)만 있으면 되는 가장 가벼운 경로.
키가 없으면 **dev 모드**: 실제 발송 대신 코드를 로그로 남긴다(로컬 로그인 개발용) — 발송한
척 하지 않는다(정직성 원칙; alerts의 channels/email.py 'simulated' 철학과 동일).
alerts의 email 채널 어댑터는 유저별 웹훅이라 모양이 달라 재사용하지 않는다.
"""

from __future__ import annotations

import logging

import httpx

from studioapi.config import settings

logger = logging.getLogger(__name__)

_RESEND_URL = "https://api.resend.com/emails"


async def send_email(to: str, subject: str, html: str) -> bool:
    """발송 성공 여부. 키 없음 → dev 모드 로그(False 아님 — 흐름은 계속되게 True)."""
    if not settings.resend_api_key:
        logger.warning("mailer[dev]: RESEND_API_KEY 없음 — 발송 생략. to=%s subject=%r body=%r",
                       to, subject, html[:200])
        return True
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            r = await client.post(_RESEND_URL, json={
                "from": settings.email_from, "to": [to], "subject": subject, "html": html,
            }, headers={"Authorization": f"Bearer {settings.resend_api_key}"})
        if r.status_code // 100 == 2:
            return True
        logger.error("mailer: resend %s — %s", r.status_code, r.text[:200])
        return False
    except Exception as exc:  # noqa: BLE001 — 메일 실패가 500으로 번지지 않게
        logger.error("mailer: send failed: %s", exc)
        return False


async def send_otp(to: str, code: str) -> bool:
    return await send_email(
        to, f"Amber 로그인 코드 {code}",
        f"<p>아래 6자리 코드를 입력해 주세요. <b>10분</b> 동안만 유효해요.</p>"
        f"<p style=\"font-size:28px;letter-spacing:6px\"><b>{code}</b></p>"
        f"<p>본인이 요청하지 않았다면 이 메일은 무시해도 돼요.</p>")
