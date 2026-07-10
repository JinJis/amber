"use client";

// BILL-2/4 — 요금제 탭의 결제 액션: 토스 카드 등록(requestBillingAuth) → /billing/success에서
// 첫 결제, 구독 중이면 상태·다음 결제·해지 예약. NEXT_PUBLIC_TOSS_CLIENT_KEY 없으면 안내만.
import { useEffect, useState } from "react";

declare global { interface Window { TossPayments?: (key: string) => { requestBillingAuth: (m: string, o: Record<string, string>) => Promise<void> } } }

type BillingMe = {
  plan: string; customer_key?: string | null; card?: string | null; credit_balance?: number;
  subscription?: { status: string; current_period_end: string; cancel_at_period_end: boolean } | null;
  next_invoice_preview?: { amount: number; credit: number; total: number };
};

const CLIENT_KEY = process.env.NEXT_PUBLIC_TOSS_CLIENT_KEY;

export default function BillingPanel() {
  const [me, setMe] = useState<BillingMe | null>(null);
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState<string | null>(null);

  async function load() {
    try { const r = await fetch("/api/billing/me"); if (r.ok) setMe(await r.json()); } catch {}
  }
  useEffect(() => { void load(); }, []);

  async function start() {
    if (!CLIENT_KEY || !me?.customer_key || busy) return;
    setBusy(true); setNote(null);
    try {
      if (!window.TossPayments) {
        await new Promise<void>((res, rej) => {
          const s = document.createElement("script");
          s.src = "https://js.tosspayments.com/v1/payment";
          s.onload = () => res(); s.onerror = () => rej(new Error("sdk"));
          document.head.appendChild(s);
        });
      }
      await window.TossPayments!(CLIENT_KEY).requestBillingAuth("카드", {
        customerKey: me.customer_key,
        successUrl: `${location.origin}/billing/success`,
        failUrl: `${location.origin}/billing/fail`,
      });
    } catch { setNote("결제창을 열지 못했어요. 잠시 후 다시 시도해 주세요."); }
    setBusy(false);
  }

  async function cancel() {
    if (busy) return;
    setBusy(true); setNote(null);
    try {
      const r = await fetch("/api/billing/cancel", { method: "POST" });
      const d = await r.json().catch(() => ({}));
      setNote(d.message || d.detail || null);
      await load();
    } catch {}
    setBusy(false);
  }

  const sub = me?.subscription;
  return (
    <div className="billing-panel">
      {sub ? (
        <div className="st-usage-list">
          <div className="st-usage-h mono">구독 관리</div>
          <p className="st-note">
            {sub.status === "past_due" ? "결제가 실패해 재시도 중이에요 — 카드 정보를 확인해 주세요." :
              sub.cancel_at_period_end
                ? `해지가 예약됐어요 — ${sub.current_period_end.slice(0, 10)}까지 Pro를 계속 쓸 수 있어요.`
                : `다음 결제일은 ${sub.current_period_end.slice(0, 10)}이에요.`}
            {me?.card ? ` · ${me.card}` : ""}
            {me?.credit_balance ? ` · 크레딧 ₩${me.credit_balance.toLocaleString()} (다음 결제에서 자동 차감)` : ""}
          </p>
          {!sub.cancel_at_period_end && (
            <button className="btn ghost" type="button" disabled={busy} onClick={() => void cancel()}>해지 예약</button>
          )}
        </div>
      ) : CLIENT_KEY ? (
        <button className="btn" type="button" disabled={busy || !me?.customer_key === undefined} onClick={() => void start()}>
          {busy ? "여는 중…" : "Pro 시작하기 — 카드 등록"}
        </button>
      ) : (
        <p className="st-note mono">결제 연동은 준비 중이에요 — 지금은 플랜 안내만 제공해요.</p>
      )}
      {note && <p className="st-note">{note}</p>}
    </div>
  );
}
