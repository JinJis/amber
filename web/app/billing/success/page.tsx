"use client";

// BILL-2: 토스 requestBillingAuth 성공 착지 — authKey를 서버로 넘겨 빌링키 발급 + 첫 결제.
import { useEffect, useState } from "react";

export default function BillingSuccess() {
  const [msg, setMsg] = useState("결제를 진행하고 있어요…");
  const [failed, setFailed] = useState(false);
  useEffect(() => {
    (async () => {
      const authKey = new URLSearchParams(window.location.search).get("authKey");
      if (!authKey) { setFailed(true); setMsg("결제 정보를 찾지 못했어요. 다시 시도해 주세요."); return; }
      try {
        const r = await fetch("/api/billing/register", {
          method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ auth_key: authKey }),
        });
        const d = await r.json().catch(() => ({}));
        if (r.ok) { setMsg(d.message || "Pro가 시작됐어요!"); setTimeout(() => { window.location.href = "/"; }, 1500); }
        else { setFailed(true); setMsg(d.detail || "결제에 실패했어요. 카드 정보를 확인해 주세요."); }
      } catch { setFailed(true); setMsg("결제에 실패했어요. 잠시 후 다시 시도해 주세요."); }
    })();
  }, []);
  return (
    <main className="signin"><div className="signin-card">
      <div className="signin-brand"><span className="mascot" aria-hidden /><b>ValueGraph</b></div>
      <h1 className="signin-h">{failed ? "결제 실패" : "Pro 시작"}</h1>
      <p className="signin-sub">{msg}</p>
      {failed && <a className="btn ghost" href="/">돌아가기</a>}
    </div></main>
  );
}
