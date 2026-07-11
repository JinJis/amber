"""BILL-1~4 — 토스페이먼츠 빌링키 구독 결제 + REF-2/3 크레딧 적용.

흐름: 카드 등록(웹 SDK requestBillingAuth) → ``POST /billing/register``(빌링키 발급·암호화
저장, 구독 생성, 첫 인보이스 청구) → 매시간 ``billing_tick``(기간 만료 갱신·던닝 D+1/3/5·
소진 시 free 강등) → 웹훅(멱등, 바디 불신·재조회 검증). 모든 플랜 전환은
``plans.apply_plan`` 단일 경유 — 결제 성공 없이는 절대 pro가 열리지 않는다.

이중과금 3중 방어: ① invoices.order_id UNIQUE(같은 기간 재시도 = 같은 order_id, 토스가
멱등 처리) ② pg_advisory_lock(복수 레플리카 동시 tick 차단) ③ webhook_events.event_id
UNIQUE. 크레딧(REF-2/3): 인보이스 생성 시 잔액 차감, **결제 확정 시** 추천인에게 20% 적립,
환불 시 clawback — 전부 원장 UNIQUE로 멱등.
"""

from __future__ import annotations

import base64
import logging
import math
import uuid
from datetime import datetime, timedelta
from typing import Protocol

import httpx
from sqlalchemy import func, select

from studioapi.config import settings
from studioapi.db import SessionLocal
from studioapi.models import BillingCustomer, CreditLedger, Invoice, Subscription, User

logger = logging.getLogger(__name__)

PERIOD_DAYS = 30
RETRY_DAYS = (1, 2, 2)          # D+1, D+3, D+5 (누적)
REFEREE_FIRST_DISCOUNT = 0.30   # 피추천 첫 달 30% 할인
KICKBACK_RATE = 0.20            # 추천인: 결제액의 20% 크레딧


def plan_price_krw(plan: str = "pro") -> int:
    return int(settings.plan_price_pro_krw) if plan == "pro" else 0


# --- 빌링키 암호화 (Fernet, BILLING_ENC_KEY) -------------------------------------------------
def _fernet():
    from cryptography.fernet import Fernet

    key = settings.billing_enc_key
    if not key:
        # dev 편의: 고정 dev 키(경고). production은 assert_production_secrets가 기동을 막는다.
        key = base64.urlsafe_b64encode(b"dev-billing-key-32bytes-padding!").decode()
        logger.warning("billing: BILLING_ENC_KEY 미설정 — dev 키 사용(운영 금지)")
    return Fernet(key)


def encrypt_key(billing_key: str) -> str:
    return _fernet().encrypt(billing_key.encode()).decode()


def decrypt_key(enc: str) -> str:
    return _fernet().decrypt(enc.encode()).decode()


# --- PaymentGateway 심 (Toss / Fake) ---------------------------------------------------------
class PaymentGateway(Protocol):
    async def issue_billing_key(self, auth_key: str, customer_key: str) -> dict: ...
    async def charge(self, billing_key: str, customer_key: str, order_id: str,
                     amount: int, order_name: str) -> dict: ...
    async def get_payment(self, payment_key: str) -> dict: ...
    async def cancel(self, payment_key: str, reason: str) -> dict: ...


class TossGateway:
    """토스페이먼츠 v1 빌링 API. 시크릿 키는 Basic 인증(base64("{secret}:"))."""

    BASE = "https://api.tosspayments.com/v1"

    def _auth(self) -> dict:
        token = base64.b64encode(f"{settings.toss_secret_key}:".encode()).decode()
        return {"Authorization": f"Basic {token}", "Content-Type": "application/json"}

    async def _post(self, path: str, body: dict) -> dict:
        async with httpx.AsyncClient(timeout=15.0) as c:
            r = await c.post(f"{self.BASE}{path}", json=body, headers=self._auth())
        d = r.json()
        if r.status_code // 100 != 2:
            raise RuntimeError(f"toss {r.status_code}: {d.get('code')} {d.get('message')}")
        return d

    async def issue_billing_key(self, auth_key: str, customer_key: str) -> dict:
        return await self._post("/billing/authorizations/issue",
                                {"authKey": auth_key, "customerKey": customer_key})

    async def charge(self, billing_key: str, customer_key: str, order_id: str,
                     amount: int, order_name: str) -> dict:
        return await self._post(f"/billing/{billing_key}", {
            "customerKey": customer_key, "orderId": order_id, "orderName": order_name,
            "amount": amount})

    async def get_payment(self, payment_key: str) -> dict:
        async with httpx.AsyncClient(timeout=15.0) as c:
            r = await c.get(f"{self.BASE}/payments/{payment_key}", headers=self._auth())
        if r.status_code // 100 != 2:
            raise RuntimeError(f"toss get_payment {r.status_code}")
        return r.json()

    async def cancel(self, payment_key: str, reason: str) -> dict:
        return await self._post(f"/payments/{payment_key}/cancel", {"cancelReason": reason})


class FakeGateway:
    """테스트/로컬용 — 상태기계를 실키 없이 전수 검증한다. fail_next로 실패 주입."""

    def __init__(self) -> None:
        self.fail_next = 0
        self.charges: list[dict] = []

    async def issue_billing_key(self, auth_key: str, customer_key: str) -> dict:
        if not auth_key:
            raise RuntimeError("no auth key")
        return {"billingKey": f"bk_{auth_key}", "card": {"issuerCode": "지금은없음",
                "number": "1234-****-****-5678"}, "cardCompany": "테스트카드"}

    async def charge(self, billing_key: str, customer_key: str, order_id: str,
                     amount: int, order_name: str) -> dict:
        if self.fail_next > 0:
            self.fail_next -= 1
            raise RuntimeError("card declined")
        rec = {"paymentKey": f"pay_{order_id}", "orderId": order_id, "status": "DONE",
               "totalAmount": amount}
        self.charges.append(rec)
        return rec

    async def get_payment(self, payment_key: str) -> dict:
        for c in reversed(self.charges):   # 최신 상태 우선 (환불이 DONE을 덮는다)
            if c["paymentKey"] == payment_key:
                return c
        raise RuntimeError("unknown payment")

    async def cancel(self, payment_key: str, reason: str) -> dict:
        return {"paymentKey": payment_key, "status": "CANCELED"}


_gateway: PaymentGateway | None = None


def gateway() -> PaymentGateway:
    global _gateway
    if _gateway is None:
        _gateway = TossGateway() if settings.toss_secret_key else FakeGateway()
    return _gateway


# --- REF-2: 크레딧 원장 ------------------------------------------------------------------------
def credit_balance(email: str) -> int:
    with SessionLocal() as db:
        return int(db.execute(select(func.coalesce(func.sum(CreditLedger.amount_krw), 0)).where(
            CreditLedger.user_email == email)).scalar() or 0)


def _ledger_add(db, email: str, amount: int, kind: str, invoice_id: str | None = None,
                related_user: str | None = None, note: str | None = None) -> bool:
    """멱등 적립/차감 — (kind, invoice, user) UNIQUE 충돌은 이미 처리된 것(무해)."""
    from sqlalchemy.exc import IntegrityError

    try:
        db.add(CreditLedger(user_email=email, amount_krw=amount, kind=kind,
                            related_invoice_id=invoice_id, related_user=related_user, note=note))
        db.commit()
        return True
    except IntegrityError:
        db.rollback()
        return False


# --- 인보이스 생성·청구 ------------------------------------------------------------------------
def _build_invoice(db, user: User, sub: Subscription, period_start: datetime,
                   period_end: datetime) -> Invoice:
    """REF-2/3: 정가 − (피추천 첫 결제 30% 할인) − (크레딧 잔액 차감) = 청구액.
    order_id는 (구독, 기간)으로 결정적 — 같은 기간 재시도는 같은 주문."""
    amount = plan_price_krw(sub.plan)
    prior_paid = db.execute(select(func.count()).select_from(Invoice).where(
        Invoice.user_email == user.email, Invoice.status == "paid")).scalar() or 0
    discount = int(amount * REFEREE_FIRST_DISCOUNT) if (user.referred_by and prior_paid == 0) else 0
    balance = int(db.execute(select(func.coalesce(func.sum(CreditLedger.amount_krw), 0)).where(
        CreditLedger.user_email == user.email)).scalar() or 0)
    credit = min(max(balance, 0), amount - discount)
    # 기간 시작 시각(μs 포함)으로 결정적 — 같은 기간의 재시도는 같은 주문, 다른 기간은 항상 다른 주문
    order_id = f"{sub.id}_{period_start.strftime('%Y%m%d%H%M%S%f')}"[:64]
    existing = db.execute(select(Invoice).where(Invoice.order_id == order_id)).scalars().first()
    if existing is not None:   # 같은 기간의 재시도/레이스 → 기존 인보이스 재사용 (멱등)
        return existing
    inv = Invoice(user_email=user.email, subscription_id=sub.id, order_id=order_id,
                  amount=amount, discount=discount, credit_applied=credit,
                  total=max(0, amount - discount - credit),
                  period_start=period_start, period_end=period_end)
    db.add(inv)
    db.commit()
    if credit > 0:  # 사용분을 원장에 즉시 마이너스 기록 (인보이스와 1:1 멱등)
        _ledger_add(db, user.email, -credit, "invoice_application", inv.id,
                    note="다음 결제 자동 차감")
    return inv


async def _charge_invoice(inv_id: str) -> bool:
    """인보이스 1건 청구 + 성공 시 확정 처리(pro 플립·킥백). 실패는 False(상태 전환은 호출부)."""
    from studioapi.plans import apply_plan

    with SessionLocal() as db:
        inv = db.get(Invoice, inv_id)
        cust = db.get(BillingCustomer, inv.user_email) if inv else None
        user = db.get(User, inv.user_email) if inv else None
    if inv is None or cust is None or not cust.billing_key_enc or user is None:
        return False
    if inv.status == "paid":
        return True

    payment_key = None
    if inv.total > 0:
        try:
            res = await gateway().charge(decrypt_key(cust.billing_key_enc), cust.customer_key,
                                         inv.order_id, inv.total, "ValueGraph Pro 구독")
            payment_key = res.get("paymentKey")
        except Exception as exc:  # noqa: BLE001 — 카드 거절 등: 호출부가 던닝 처리
            logger.warning("billing: charge failed inv=%s: %s", inv.id, exc)
            with SessionLocal() as db:
                row = db.get(Invoice, inv_id)
                row.attempts += 1
                row.status = "failed"
                db.commit()
            return False

    with SessionLocal() as db:
        row = db.get(Invoice, inv_id)
        row.status = "paid"
        row.paid_at = datetime.utcnow()
        row.toss_payment_key = payment_key
        db.commit()
        # REF-3: 결제 확정 시 추천인 킥백(20%, 월 상한) — 멱등(원장 UNIQUE)
        # REF-4: 같은 카드(마스킹 라벨 일치)로 결제하는 추천인↔피추천은 자기추천으로 보고
        # 킥백을 건너뛴다 (할인·구독 자체는 정상 — 어뷰즈 이득만 제거).
        same_card = False
        if user.referred_by:
            ref_cust = db.get(BillingCustomer, user.referred_by)
            me_cust = db.get(BillingCustomer, user.email)
            same_card = bool(ref_cust and me_cust and ref_cust.card_label
                             and ref_cust.card_label == me_cust.card_label)
            if same_card:
                logger.warning("billing: same-card referral kickback blocked (%s ← %s)",
                               user.referred_by, user.email)
        if user.referred_by and inv.total > 0 and not same_card:
            kick = math.floor(inv.total * KICKBACK_RATE)
            month_start = datetime.utcnow().replace(day=1, hour=0, minute=0, second=0, microsecond=0)
            earned = int(db.execute(select(func.coalesce(func.sum(CreditLedger.amount_krw), 0)).where(
                CreditLedger.user_email == user.referred_by,
                CreditLedger.kind == "referral_kickback",
                CreditLedger.created_at >= month_start)).scalar() or 0)
            cap = int(settings.referral_kickback_monthly_cap_krw)
            kick = max(0, min(kick, cap - earned))
            if kick > 0:
                _ledger_add(db, user.referred_by, kick, "referral_kickback", inv.id,
                            related_user=user.email, note="친구 결제 적립 (20%)")
    await apply_plan(inv.user_email, "pro")
    return True


# --- 공개 플로우 -------------------------------------------------------------------------------
async def register_and_subscribe(user: User, auth_key: str) -> dict:
    """BILL-2: 카드 등록(빌링키 발급) → 구독 생성 → 첫 인보이스 청구 → 성공 시 pro."""
    with SessionLocal() as db:
        cust = db.get(BillingCustomer, user.email)
        if cust is None:
            cust = BillingCustomer(user_email=user.email, customer_key=uuid.uuid4().hex)
            db.add(cust)
            db.commit()
        customer_key = cust.customer_key
    issued = await gateway().issue_billing_key(auth_key, customer_key)
    card = issued.get("card") or {}
    label = f"{issued.get('cardCompany') or ''} {card.get('number') or ''}".strip()
    with SessionLocal() as db:
        cust = db.get(BillingCustomer, user.email)
        cust.billing_key_enc = encrypt_key(issued["billingKey"])
        cust.card_label = label[:64] or None
        cust.status = "active"
        # 활성 구독이 없으면 생성
        sub = db.execute(select(Subscription).where(
            Subscription.user_email == user.email,
            Subscription.status.in_(("active", "past_due")))).scalars().first()
        now = datetime.utcnow()
        if sub is None:
            sub = Subscription(user_email=user.email, plan="pro", status="active",
                               current_period_start=now,
                               current_period_end=now + timedelta(days=PERIOD_DAYS))
            db.add(sub)
        db.commit()
        u = db.get(User, user.email)
        inv = _build_invoice(db, u, sub, sub.current_period_start, sub.current_period_end)
        inv_id, sub_id = inv.id, sub.id
    ok = await _charge_invoice(inv_id)
    if not ok:
        with SessionLocal() as db:
            db.get(Subscription, sub_id).status = "past_due"
            db.commit()
        raise RuntimeError("첫 결제에 실패했어요. 카드 정보를 확인해 주세요.")
    return {"subscribed": True, "card": label,
            "message": "Pro가 시작됐어요. 이제 더 깊은 분석과 실시간 데이터를 쓸 수 있어요."}


async def refund_invoice(invoice_id: str, reason: str = "운영 환불",
                         already_canceled: bool = False) -> bool:
    """BILL-4/5: 환불 확정 — 토스 취소(결제 존재 시) → refunded → 킥백 clawback.
    clawback은 그 인보이스로 **실제 적립된 금액만** 회수한다(같은 카드 차단으로 적립이
    없었으면 회수도 없음). 웹훅·admin 환불 버튼이 공용하는 단일 경로. 멱등."""
    with SessionLocal() as db:
        inv = db.get(Invoice, invoice_id)
    if inv is None:
        return False
    if inv.status == "refunded":
        return True
    if inv.toss_payment_key and not already_canceled:   # 웹훅 경로는 이미 취소된 결제 — 재취소 금지
        try:
            await gateway().cancel(inv.toss_payment_key, reason)
        except Exception as exc:  # noqa: BLE001 — 이미 취소된 결제 등은 재조회로 확인될 것
            logger.error("billing: refund cancel failed inv=%s: %s", invoice_id, exc)
            return False
    with SessionLocal() as db:
        inv = db.get(Invoice, invoice_id)
        inv.status = "refunded"
        db.commit()
        u = db.get(User, inv.user_email)
        if u is not None and u.referred_by:
            kick_row = db.execute(select(CreditLedger).where(
                CreditLedger.kind == "referral_kickback",
                CreditLedger.related_invoice_id == inv.id,
                CreditLedger.user_email == u.referred_by)).scalars().first()
            if kick_row:
                _ledger_add(db, u.referred_by, -int(kick_row.amount_krw), "clawback", inv.id,
                            related_user=inv.user_email, note="환불 회수")
    return True


async def cancel_at_period_end(user: User) -> dict:
    with SessionLocal() as db:
        sub = db.execute(select(Subscription).where(
            Subscription.user_email == user.email,
            Subscription.status.in_(("active", "past_due")))).scalars().first()
        if sub is None:
            raise ValueError("활성 구독이 없어요.")
        sub.cancel_at_period_end = True
        db.commit()
        end = sub.current_period_end.strftime("%m월 %d일")
    return {"canceled": True,
            "message": f"해지가 예약됐어요. 이번 결제 기간이 끝나는 {end}까지 Pro를 계속 쓸 수 있어요."}


async def billing_tick(now: datetime | None = None) -> int:
    """BILL-3: 시간별 틱 — 기간 만료 구독 갱신·던닝 재시도. 처리 건수 반환.
    복수 레플리카는 pg_advisory_lock으로 직렬화(SQLite/단일 프로세스는 no-op)."""
    from studioapi.plans import apply_plan

    now = now or datetime.utcnow()
    processed = 0
    with SessionLocal() as db:
        locked = True
        if db.bind.dialect.name == "postgresql":
            locked = bool(db.execute(
                func.pg_try_advisory_lock(0x76674249)).scalar())  # 'vgBI'
        if not locked:
            return 0
        try:
            due = db.execute(select(Subscription).where(
                Subscription.status.in_(("active", "past_due")),
                Subscription.current_period_end <= now)).scalars().all()
            due_ids = [s.id for s in due]
            retry = db.execute(select(Invoice).where(
                Invoice.status == "failed", Invoice.next_retry_at.isnot(None),
                Invoice.next_retry_at <= now)).scalars().all()
            retry_ids = [i.id for i in retry]
        finally:
            if db.bind.dialect.name == "postgresql":
                db.execute(func.pg_advisory_unlock(0x76674249))

    for sid in due_ids:
        processed += 1
        with SessionLocal() as db:
            sub = db.get(Subscription, sid)
            user = db.get(User, sub.user_email)
            if sub.cancel_at_period_end:   # 예약 해지 — 기간 종료로 free 강등
                sub.status = "canceled"
                db.commit()
                await apply_plan(sub.user_email, "free")
                continue
            new_start, new_end = sub.current_period_end, sub.current_period_end + timedelta(days=PERIOD_DAYS)
            inv = _build_invoice(db, user, sub, new_start, new_end)
            inv_id = inv.id
        if await _charge_invoice(inv_id):
            with SessionLocal() as db:
                sub = db.get(Subscription, sid)
                sub.status = "active"
                sub.current_period_start, sub.current_period_end = new_start, new_end
                db.commit()
        else:
            with SessionLocal() as db:
                sub = db.get(Subscription, sid)
                sub.status = "past_due"
                inv = db.get(Invoice, inv_id)
                inv.next_retry_at = now + timedelta(days=RETRY_DAYS[0])
                db.commit()
            await _dunning_mail(sid, attempt=1)

    for iid in retry_ids:
        processed += 1
        if await _charge_invoice(iid):
            with SessionLocal() as db:
                inv = db.get(Invoice, iid)
                sub = db.get(Subscription, inv.subscription_id)
                sub.status = "active"
                sub.current_period_start, sub.current_period_end = inv.period_start, inv.period_end
                inv.next_retry_at = None
                db.commit()
        else:
            with SessionLocal() as db:
                inv = db.get(Invoice, iid)
                if inv.attempts >= 1 + len(RETRY_DAYS):    # 재시도 소진 → 해지 + free
                    sub = db.get(Subscription, inv.subscription_id)
                    sub.status = "canceled"
                    inv.next_retry_at = None
                    db.commit()
                    await apply_plan(inv.user_email, "free")
                else:
                    inv.next_retry_at = now + timedelta(days=RETRY_DAYS[min(inv.attempts - 1, len(RETRY_DAYS) - 1)])
                    db.commit()
                    await _dunning_mail(inv.subscription_id, attempt=inv.attempts)
    return processed


async def _dunning_mail(sub_id: str, attempt: int) -> None:
    """던닝 안내 — email_verified 유저에게만(카카오 무이메일 센티널 차단)."""
    from studioapi.mailer import send_email

    with SessionLocal() as db:
        sub = db.get(Subscription, sub_id)
        user = db.get(User, sub.user_email) if sub else None
    if user is None or not getattr(user, "email_verified", False):
        return
    await send_email(user.email, "ValueGraph 결제가 실패했어요",
                     "<p>Pro 구독 결제가 실패했어요. 카드 정보를 확인해 주세요 — "
                     "며칠 안에 다시 시도하고, 계속 실패하면 Pro가 일시 해제돼요.</p>")
