"""BILL-1~4 + REF-2/3 — FakeGateway로 상태기계·크레딧·킥백 전수 테스트."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta

import httpx
import pytest
import respx
from fastapi.testclient import TestClient
from sqlalchemy import select

from studioapi import billing
from studioapi.config import settings
from studioapi.db import SessionLocal, init_db
from studioapi.main import app
from studioapi.models import BillingCustomer, CreditLedger, Invoice, Subscription, User

client = TestClient(app)


def setup_module(_module):
    init_db()


@pytest.fixture(autouse=True)
def _fake_gateway(monkeypatch):
    """모든 테스트는 FakeGateway + apply_plan 무력화(respx 없이 users.plan만 갱신)."""
    fake = billing.FakeGateway()
    monkeypatch.setattr(billing, "_gateway", fake)

    async def _fake_apply(email: str, plan: str) -> bool:
        with SessionLocal() as db:
            u = db.get(User, email)
            if u is not None:
                u.plan = plan
                db.commit()
        return True

    import studioapi.plans as plans_mod
    monkeypatch.setattr(plans_mod, "apply_plan", _fake_apply)
    yield fake
    monkeypatch.setattr(billing, "_gateway", None)


def _user(email: str, referred_by: str | None = None) -> User:
    with SessionLocal() as db:
        for t in (CreditLedger, Invoice, Subscription):
            db.query(t).filter(t.user_email == email).delete(synchronize_session=False)
        db.query(BillingCustomer).filter(BillingCustomer.user_email == email).delete(synchronize_session=False)
        u = db.get(User, email) or User(email=email, project_id="p", api_key="k")
        u.plan = "free"
        u.referred_by = referred_by
        db.merge(u)
        db.commit()
        return db.get(User, email)


def test_register_and_first_charge_flips_pro(_fake_gateway):
    u = _user("pay1@u.com")
    out = asyncio.run(billing.register_and_subscribe(u, "authkey1"))
    assert out["subscribed"] is True
    with SessionLocal() as db:
        assert db.get(User, u.email).plan == "pro"
        sub = db.execute(select(Subscription).where(Subscription.user_email == u.email)).scalars().one()
        assert sub.status == "active"
        inv = db.execute(select(Invoice).where(Invoice.user_email == u.email)).scalars().one()
        assert inv.status == "paid" and inv.total == settings.plan_price_pro_krw
        cust = db.get(BillingCustomer, u.email)
        assert cust.billing_key_enc and "bk_authkey1" not in cust.billing_key_enc  # 암호화 저장
        assert billing.decrypt_key(cust.billing_key_enc) == "bk_authkey1"


def test_first_charge_failure_never_flips_pro(_fake_gateway):
    u = _user("pay2@u.com")
    _fake_gateway.fail_next = 1
    with pytest.raises(RuntimeError):
        asyncio.run(billing.register_and_subscribe(u, "authkey2"))
    with SessionLocal() as db:
        assert db.get(User, u.email).plan == "free"        # 결제 없이는 절대 pro가 안 열린다
        sub = db.execute(select(Subscription).where(Subscription.user_email == u.email)).scalars().one()
        assert sub.status == "past_due"


def _pending_invoice(email: str, order_id: str, status: str = "pending") -> str:
    from datetime import datetime
    with SessionLocal() as db:
        inv = Invoice(user_email=email, subscription_id="sub_cr2", order_id=order_id,
                      amount=19900, discount=0, credit_applied=0, total=19900,
                      period_start=datetime.utcnow(), period_end=datetime.utcnow(), status=status)
        db.add(inv)
        db.commit()
        return inv.id


def test_cr2_claim_guard_blocks_already_charging_invoice(_fake_gateway):
    """CR-2/ME-15: an invoice another path already claimed ('charging') is NOT re-charged — the
    atomic claim (status IN pending/failed → charging) matches 0 rows and _charge_invoice bails."""
    u = _user("cr2a@u.com")
    asyncio.run(billing.register_and_subscribe(u, "authkeyCR2a"))
    inv_id = _pending_invoice(u.email, "cr2_o1", status="charging")
    n = len(_fake_gateway.charges)
    assert asyncio.run(billing._charge_invoice(inv_id)) is False    # can't claim → no charge
    assert len(_fake_gateway.charges) == n                          # gateway was NOT called
    with SessionLocal() as db:   # don't leave a 'charging' invoice for another test's tick to sweep
        db.query(Invoice).filter(Invoice.order_id == "cr2_o1").delete(synchronize_session=False)
        db.commit()


def test_cr2_concurrent_charge_is_single(_fake_gateway):
    """CR-2: two concurrent _charge_invoice on the same invoice → exactly ONE reaches the gateway."""
    u = _user("cr2b@u.com")
    asyncio.run(billing.register_and_subscribe(u, "authkeyCR2b"))
    inv_id = _pending_invoice(u.email, "cr2_o2")

    async def _both():
        return await asyncio.gather(billing._charge_invoice(inv_id), billing._charge_invoice(inv_id))

    asyncio.run(_both())
    charges = [c for c in _fake_gateway.charges if c.get("orderId") == "cr2_o2"]
    assert len(charges) == 1                                        # no double-charge


def test_referee_discount_and_referrer_kickback(_fake_gateway):
    referrer = _user("refhost@u.com")
    referee = _user("refguest@u.com", referred_by=referrer.email)
    asyncio.run(billing.register_and_subscribe(referee, "authkey3"))
    with SessionLocal() as db:
        inv = db.execute(select(Invoice).where(Invoice.user_email == referee.email)).scalars().one()
        assert inv.discount == int(settings.plan_price_pro_krw * 0.30)   # 첫 달 30% 할인
        assert inv.total == settings.plan_price_pro_krw - inv.discount
    # 추천인 킥백 = 실결제액의 20% (크레딧, 멱등)
    assert billing.credit_balance(referrer.email) == int((settings.plan_price_pro_krw - int(settings.plan_price_pro_krw * 0.30)) * 0.20)
    # 같은 인보이스로 재확정해도 이중 적립 없음
    with SessionLocal() as db:
        inv = db.execute(select(Invoice).where(Invoice.user_email == referee.email)).scalars().one()
    before = billing.credit_balance(referrer.email)
    asyncio.run(billing._charge_invoice(inv.id))   # already paid → True, 킥백 스킵
    assert billing.credit_balance(referrer.email) == before


def test_renewal_applies_credit_and_dunning_downgrade(_fake_gateway):
    u = _user("pay4@u.com")
    asyncio.run(billing.register_and_subscribe(u, "authkey4"))
    # 크레딧 5,000원 적립 → 다음 갱신 인보이스에서 자동 차감
    with SessionLocal() as db:
        first_inv_id = db.execute(select(Invoice.id).where(Invoice.user_email == u.email)).scalar()
        db.add(CreditLedger(user_email=u.email, amount_krw=5000, kind="admin_adjust", note="test"))
        sub = db.execute(select(Subscription).where(Subscription.user_email == u.email)).scalars().one()
        sub.current_period_end = datetime.utcnow() - timedelta(minutes=1)   # 만료시켜 갱신 유도
        db.commit()
        sid = sub.id
    asyncio.run(billing.billing_tick())
    with SessionLocal() as db:
        inv2 = db.execute(select(Invoice).where(Invoice.user_email == u.email,
                                                Invoice.id != first_inv_id)).scalars().one()
        assert inv2.credit_applied == 5000 and inv2.total == settings.plan_price_pro_krw - 5000
        assert inv2.status == "paid"
        assert db.get(Subscription, sid).status == "active"
    assert billing.credit_balance(u.email) == 0            # 사용분 마이너스 원장

    # 갱신 실패 → past_due + 재시도 예약; 소진 → canceled + free 강등
    with SessionLocal() as db:
        sub = db.get(Subscription, sid)
        sub.current_period_end = datetime.utcnow() - timedelta(minutes=1)
        db.commit()
    _fake_gateway.fail_next = 99
    asyncio.run(billing.billing_tick())
    with SessionLocal() as db:
        assert db.get(Subscription, sid).status == "past_due"
        inv3 = db.execute(select(Invoice).where(Invoice.user_email == u.email,
                                                Invoice.status == "failed")).scalars().one()
        assert inv3.next_retry_at is not None
        iid = inv3.id
    for _ in range(4):   # 재시도 전부 실패 → 소진
        with SessionLocal() as db:
            db.get(Invoice, iid).next_retry_at = datetime.utcnow() - timedelta(minutes=1)
            db.commit()
        asyncio.run(billing.billing_tick())
    with SessionLocal() as db:
        assert db.get(Subscription, sid).status == "canceled"
        assert db.get(User, u.email).plan == "free"        # Pro 해제


def test_cancel_at_period_end_keeps_pro_until_end(_fake_gateway):
    u = _user("pay5@u.com")
    asyncio.run(billing.register_and_subscribe(u, "authkey5"))
    out = asyncio.run(billing.cancel_at_period_end(u))
    assert "까지 Pro를 계속" in out["message"]
    with SessionLocal() as db:
        assert db.get(User, u.email).plan == "pro"          # 기간 중엔 유지
        sub = db.execute(select(Subscription).where(Subscription.user_email == u.email)).scalars().one()
        sub.current_period_end = datetime.utcnow() - timedelta(minutes=1)
        db.commit()
    asyncio.run(billing.billing_tick())
    with SessionLocal() as db:
        assert db.get(User, u.email).plan == "free"         # 기간 종료 → free


def test_webhook_refund_claws_back_kickback(_fake_gateway, monkeypatch):
    referrer = _user("refhost2@u.com")
    referee = _user("refguest2@u.com", referred_by=referrer.email)
    asyncio.run(billing.register_and_subscribe(referee, "authkey6"))
    kick = billing.credit_balance(referrer.email)
    assert kick > 0
    with SessionLocal() as db:
        inv = db.execute(select(Invoice).where(Invoice.user_email == referee.email)).scalars().one()
        pk, oid = inv.toss_payment_key, inv.order_id
    _fake_gateway.charges.append({"paymentKey": pk, "orderId": oid, "status": "CANCELED",
                                  "totalAmount": 0})
    hdr = {"X-Service-Token": "dev-service-token"}
    r = client.post(f"/billing/webhook/{settings.toss_webhook_secret}", headers=hdr,
                    json={"eventType": "PAYMENT_STATUS_CHANGED", "data": {"paymentKey": pk}})
    assert r.status_code == 200
    with SessionLocal() as db:
        inv = db.execute(select(Invoice).where(Invoice.order_id == oid)).scalars().one()
        assert inv.status == "refunded"
    assert billing.credit_balance(referrer.email) == 0      # clawback
    # 중복 배달은 멱등 (event_id UNIQUE)
    r2 = client.post(f"/billing/webhook/{settings.toss_webhook_secret}", headers=hdr,
                     json={"eventType": "PAYMENT_STATUS_CHANGED", "data": {"paymentKey": pk}})
    assert r2.json().get("duplicate") is True
    # 시크릿 불일치 → 404
    assert client.post("/billing/webhook/wrong", headers=hdr, json={}).status_code == 404


def test_same_card_referral_kickback_blocked(_fake_gateway):
    """REF-4: 추천인과 피추천이 같은 카드(마스킹 라벨 동일)면 킥백을 건너뛴다 — 자기추천 어뷰즈.
    FakeGateway는 항상 같은 카드 라벨을 반환하므로 '둘 다 카드 등록' = 같은 카드 시나리오."""
    referrer = _user("samecard_host@u.com")
    asyncio.run(billing.register_and_subscribe(referrer, "authkeyH"))     # 추천인도 카드 등록
    referee = _user("samecard_guest@u.com", referred_by=referrer.email)
    before = billing.credit_balance(referrer.email)
    asyncio.run(billing.register_and_subscribe(referee, "authkeyG"))
    # 할인·구독은 정상, 킥백만 차단
    with SessionLocal() as db:
        inv = db.execute(select(Invoice).where(Invoice.user_email == referee.email)).scalars().one()
        assert inv.status == "paid" and inv.discount > 0
    assert billing.credit_balance(referrer.email) == before               # 적립 없음


def test_admin_billing_ops(_fake_gateway):
    """BILL-5: admin 재시도·환불·플랜 오버라이드 — X-Admin-Token 게이트."""
    ADMIN = {"X-Admin-Token": "dev-admin-token"}
    u = _user("adminops@u.com")
    _fake_gateway.fail_next = 1
    with pytest.raises(RuntimeError):
        asyncio.run(billing.register_and_subscribe(u, "authkeyA"))        # 첫 결제 실패 → past_due
    with SessionLocal() as db:
        inv = db.execute(select(Invoice).where(Invoice.user_email == u.email)).scalars().one()
        iid = inv.id
    # 토큰 없으면 401
    assert client.post(f"/admin/billing/invoices/{iid}/retry").status_code == 401
    # 수동 재시도 → 결제 성공 + 구독 정상화 + pro
    r = client.post(f"/admin/billing/invoices/{iid}/retry", headers=ADMIN)
    assert r.status_code == 200 and r.json()["ok"] is True
    with SessionLocal() as db:
        assert db.get(Invoice, iid).status == "paid"
        sub = db.execute(select(Subscription).where(Subscription.user_email == u.email)).scalars().one()
        assert sub.status == "active"
        assert db.get(User, u.email).plan == "pro"
    # 환불 → refunded (없는 인보이스 404)
    assert client.post("/admin/billing/invoices/inv_nope/refund", headers=ADMIN).status_code in (404, 502)
    r2 = client.post(f"/admin/billing/invoices/{iid}/refund", headers=ADMIN)
    assert r2.status_code == 200
    with SessionLocal() as db:
        assert db.get(Invoice, iid).status == "refunded"
    # 플랜 오버라이드 (apply_plan 단일 경로 — 픽스처가 users.plan만 갱신)
    r3 = client.post(f"/admin/users/{u.email}/plan", headers=ADMIN, json={"plan": "free"})
    assert r3.status_code == 200
    with SessionLocal() as db:
        assert db.get(User, u.email).plan == "free"
    assert client.post("/admin/users/ghost@u.com/plan", headers=ADMIN,
                       json={"plan": "pro"}).status_code == 404
    assert client.post(f"/admin/users/{u.email}/plan", headers=ADMIN,
                       json={"plan": "vip"}).status_code == 422
