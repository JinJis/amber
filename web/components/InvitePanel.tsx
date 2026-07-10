"use client";

// REF-4 — 친구 초대: 내 코드·공유 링크·적립/사용 내역. "친구가 결제하면 결제 금액의 20%가
// 크레딧으로 쌓여요. 다음 결제에서 자동으로 차감돼요." + 가입 14일 내 소급 입력.
import { useEffect, useState } from "react";

type Ledger = { amount: number; kind: string; note?: string | null; from?: string | null; at?: string | null };
type RefMe = { code?: string | null; share_url?: string | null; invited: number; balance: number; ledger: Ledger[] };

const KIND_LABEL: Record<string, string> = {
  referral_kickback: "친구 결제 적립",
  invoice_application: "결제 차감",
  clawback: "환불 회수",
  admin_adjust: "운영 조정",
};

export default function InvitePanel() {
  const [me, setMe] = useState<RefMe | null>(null);
  const [copied, setCopied] = useState(false);
  const [enterCode, setEnterCode] = useState("");
  const [note, setNote] = useState<string | null>(null);

  useEffect(() => {
    (async () => { try { const r = await fetch("/api/referrals/me"); if (r.ok) setMe(await r.json()); } catch {} })();
  }, []);

  async function copy() {
    if (!me?.share_url) return;
    try { await navigator.clipboard.writeText(me.share_url); setCopied(true); setTimeout(() => setCopied(false), 1500); } catch {}
  }

  async function enter(e: React.FormEvent) {
    e.preventDefault();
    if (!enterCode.trim()) return;
    const r = await fetch("/api/referrals/enter", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ code: enterCode.trim() }),
    });
    const d = await r.json().catch(() => ({}));
    setNote(d.message || d.detail || null);
  }

  return (
    <section className="st-panel">
      <p className="st-note">친구가 결제하면 결제 금액의 <b>20%</b>가 크레딧으로 쌓여요. 다음 결제에서 자동으로 차감돼요.
        초대받은 친구는 첫 달 <b>30% 할인</b>을 받아요.</p>
      <div className="st-usage-hero">
        <div className="st-usage-big"><b className="mono">{me?.code ?? "…"}</b><span>내 추천 코드</span></div>
        <div className="st-usage-big"><b className="mono">{me?.invited ?? 0}</b><span>초대한 친구</span></div>
        <div className="st-usage-big"><b className="mono">₩{(me?.balance ?? 0).toLocaleString()}</b><span>크레딧 잔액</span></div>
      </div>
      {me?.share_url && (
        <button className="btn ghost" type="button" onClick={() => void copy()}>
          {copied ? "✓ 복사했어요" : "초대 링크 복사"}
        </button>
      )}
      <form className="signin-dev" onSubmit={enter} style={{ marginTop: 12 }}>
        <input className="input mono" placeholder="추천 코드를 받았나요? (가입 14일 내)" value={enterCode}
          onChange={(e) => setEnterCode(e.target.value.toUpperCase())} maxLength={8} />
        <button className="btn ghost" type="submit">등록</button>
      </form>
      {note && <p className="st-note">{note}</p>}
      {(me?.ledger ?? []).length > 0 && (
        <div className="st-usage-list">
          <div className="st-usage-h mono">적립·사용 내역</div>
          {me!.ledger.map((l, i) => (
            <div key={i} className="st-usage-row">
              <span className="st-usage-name">{KIND_LABEL[l.kind] ?? l.kind}{l.from ? ` · ${l.from}` : ""}</span>
              <span className="st-usage-n mono">{l.amount > 0 ? "+" : ""}₩{l.amount.toLocaleString()}</span>
            </div>
          ))}
        </div>
      )}
    </section>
  );
}
