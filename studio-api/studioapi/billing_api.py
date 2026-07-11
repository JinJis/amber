"""BILL-2/4 + REF-4 — 결제·구독·레퍼럴 HTTP 표면 (로직은 billing.py / referrals.py)."""

from __future__ import annotations

import json
import logging
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from studioapi import billing
from studioapi.config import settings
from studioapi.db import SessionLocal
from studioapi.deps import current_user, require_admin, require_service
from studioapi.models import (
    BillingCustomer, CreditLedger, Invoice, Subscription, User,
)

logger = logging.getLogger(__name__)
router = APIRouter(dependencies=[Depends(require_service)])
# BILL-5: 운영 액션 — admin 패널이 X-Admin-Token으로 호출 (요청 경로 밖의 ops)
admin_router = APIRouter(prefix="/admin", dependencies=[Depends(require_admin)])


class RegisterIn(BaseModel):
    auth_key: str


@router.post("/billing/register", tags=["Billing"], summary="BILL-2: 카드 등록 + 첫 결제 → Pro")
async def billing_register(body: RegisterIn, user: User = Depends(current_user)) -> dict:
    if (user.plan or "") == "guest":
        raise HTTPException(403, "가입 후 이용할 수 있어요.")
    try:
        return await billing.register_and_subscribe(user, body.auth_key)
    except RuntimeError as exc:
        raise HTTPException(402, str(exc))


@router.get("/billing/me", tags=["Billing"], summary="구독·카드·크레딧·다음 청구 미리보기")
async def billing_me(user: User = Depends(current_user)) -> dict:
    with SessionLocal() as db:
        sub = db.execute(select(Subscription).where(
            Subscription.user_email == user.email,
            Subscription.status.in_(("active", "past_due")))).scalars().first()
        cust = db.get(BillingCustomer, user.email)
        invoices = db.execute(select(Invoice).where(Invoice.user_email == user.email)
                              .order_by(Invoice.created_at.desc()).limit(12)).scalars().all()
    amount = billing.plan_price_krw("pro")
    balance = billing.credit_balance(user.email)
    credit_next = min(max(balance, 0), amount)
    return {
        "plan": user.plan or "free",
        "customer_key": (cust.customer_key if cust else None),
        "card": (cust.card_label if cust else None),
        "subscription": ({"status": sub.status,
                          "current_period_end": sub.current_period_end.isoformat(),
                          "cancel_at_period_end": bool(sub.cancel_at_period_end)} if sub else None),
        "credit_balance": balance,
        "next_invoice_preview": {"amount": amount, "credit": credit_next,
                                 "total": max(0, amount - credit_next)},
        "invoices": [{"id": i.id, "total": i.total, "status": i.status,
                      "period_start": i.period_start.isoformat(),
                      "paid_at": i.paid_at.isoformat() if i.paid_at else None} for i in invoices],
    }


@router.post("/billing/cancel", tags=["Billing"], summary="BILL-4: 기간 말 해지 예약")
async def billing_cancel(user: User = Depends(current_user)) -> dict:
    try:
        return await billing.cancel_at_period_end(user)
    except ValueError as exc:
        raise HTTPException(404, str(exc))


class WebhookIn(BaseModel):
    eventType: str | None = None
    data: dict | None = None


@router.post("/billing/webhook/{secret}", tags=["Billing"], summary="BILL-4: 토스 웹훅 (멱등·재조회 검증)")
async def billing_webhook(secret: str, body: WebhookIn) -> dict:
    """웹은 공개 경로 /api/billing/webhook/{secret}로 받아 서비스 토큰을 끼워 프록시한다.
    바디는 신뢰하지 않는다 — paymentKey로 토스에 재조회한 상태만 반영. event_id UNIQUE 멱등."""
    if secret != settings.toss_webhook_secret:
        raise HTTPException(404, "not found")
    data = body.data or {}
    payment_key = data.get("paymentKey")
    if not payment_key:
        return {"ok": True, "ignored": True}
    event_id = f"{payment_key}:{body.eventType or 'unknown'}"
    from studioapi.models import WebhookEvent
    with SessionLocal() as db:
        try:
            db.add(WebhookEvent(provider="toss", event_id=event_id,
                                payload=json.dumps(data, ensure_ascii=False)[:4000]))
            db.commit()
        except IntegrityError:
            db.rollback()
            return {"ok": True, "duplicate": True}
    try:
        payment = await billing.gateway().get_payment(payment_key)   # 재조회 — 바디 불신
    except Exception:  # noqa: BLE001
        return {"ok": True, "unverified": True}
    order_id, status = payment.get("orderId"), payment.get("status")
    with SessionLocal() as db:
        inv = db.execute(select(Invoice).where(Invoice.order_id == order_id)).scalars().first()
        inv_id = inv.id if inv else None
    if inv_id is None:
        return {"ok": True, "unknown_order": True}
    if status == "CANCELED":
        await billing.refund_invoice(inv_id, reason="토스 웹훅 취소 반영", already_canceled=True)
    return {"ok": True}


# --- BILL-5: 운영(admin) 액션 -------------------------------------------------------------------
@admin_router.post("/billing/invoices/{invoice_id}/retry", tags=["Billing"],
                   summary="BILL-5: 실패 인보이스 수동 재시도")
async def admin_retry_invoice(invoice_id: str) -> dict:
    with SessionLocal() as db:
        inv = db.get(Invoice, invoice_id)
        if inv is None:
            raise HTTPException(404, "invoice not found")
        if inv.status == "paid":
            return {"ok": True, "already_paid": True}
    ok = await billing._charge_invoice(invoice_id)
    if ok:
        with SessionLocal() as db:   # 수동 회수 성공 → 구독 정상화 + 기간 반영
            inv = db.get(Invoice, invoice_id)
            sub = db.get(Subscription, inv.subscription_id)
            if sub is not None:
                sub.status = "active"
                sub.current_period_start, sub.current_period_end = inv.period_start, inv.period_end
                inv.next_retry_at = None
                db.commit()
    return {"ok": ok}


@admin_router.post("/billing/invoices/{invoice_id}/refund", tags=["Billing"],
                   summary="BILL-5: 환불 (토스 취소 + 킥백 회수)")
async def admin_refund_invoice(invoice_id: str) -> dict:
    ok = await billing.refund_invoice(invoice_id)
    if not ok:
        raise HTTPException(502, "환불 처리에 실패했어요 — 로그를 확인해 주세요.")
    return {"ok": True}


class AdminPlanIn(BaseModel):
    plan: str


@admin_router.post("/users/{email}/plan", tags=["Billing"],
                   summary="BILL-5: 플랜 수동 오버라이드 (apply_plan 단일 경로)")
async def admin_set_plan(email: str, body: AdminPlanIn) -> dict:
    from studioapi.plans import apply_plan

    if body.plan.lower() not in ("free", "pro", "guest"):
        raise HTTPException(422, "plan must be free|pro|guest")
    with SessionLocal() as db:
        if db.get(User, email) is None:
            raise HTTPException(404, "user not found")
    ok = await apply_plan(email, body.plan.lower())
    return {"ok": ok, "email": email, "plan": body.plan.lower()}


# --- REF-4: 친구 초대 화면 데이터 ---------------------------------------------------------------
@router.get("/referrals/me", tags=["Referrals"], summary="내 추천 코드·적립 내역")
async def referrals_me(user: User = Depends(current_user)) -> dict:
    from studioapi.referrals import ensure_referral_code

    code = ensure_referral_code(user.email)
    with SessionLocal() as db:
        invited = db.execute(select(func.count()).select_from(User).where(
            User.referred_by == user.email)).scalar() or 0
        rows = db.execute(select(CreditLedger).where(CreditLedger.user_email == user.email)
                          .order_by(CreditLedger.created_at.desc()).limit(50)).scalars().all()
    def _mask(e: str | None) -> str | None:
        if not e or "@" not in e:
            return None
        h, d = e.split("@", 1)
        return f"{h[:2]}***@{d}"
    return {"code": code,
            "share_url": f"{settings.public_base_url}/?ref={code}" if code else None,
            "invited": invited,
            "balance": billing.credit_balance(user.email),
            "ledger": [{"amount": r.amount_krw, "kind": r.kind, "note": r.note,
                        "from": _mask(r.related_user),
                        "at": r.created_at.isoformat() if r.created_at else None} for r in rows]}


class ReferralEnterIn(BaseModel):
    code: str


@router.post("/referrals/enter", tags=["Referrals"], summary="REF-1: 추천 코드 소급 입력 (가입 14일 내)")
async def referrals_enter(body: ReferralEnterIn, user: User = Depends(current_user)) -> dict:
    from datetime import timedelta

    from studioapi.referrals import attribute_signup

    if user.created_at and datetime.utcnow() - user.created_at > timedelta(days=14):
        raise HTTPException(422, "추천 코드는 가입 후 14일 안에만 입력할 수 있어요.")
    if user.referred_by:
        raise HTTPException(409, "이미 추천 코드가 등록돼 있어요.")
    if not attribute_signup(user.email, body.code):
        raise HTTPException(422, "코드를 확인해 주세요.")
    return {"ok": True, "message": "추천 코드가 등록됐어요."}
