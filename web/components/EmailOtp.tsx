"use client";

import { useState } from "react";
import { signIn } from "next-auth/react";

// AUTH-2: 이메일 6자리 코드 로그인 — 같은 탭에서 완결(인앱 브라우저에서도 안전).
// step 1: 이메일 → /api/otp/request(발송) · step 2: 코드 → signIn("email-otp") 검증.
export default function EmailOtp({ callbackUrl = "/" }: { callbackUrl?: string }) {
  const [step, setStep] = useState<"email" | "code">("email");
  const [email, setEmail] = useState("");
  const [code, setCode] = useState("");
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState<string | null>(null);

  async function requestCode(e: React.FormEvent) {
    e.preventDefault();
    if (busy || !email.includes("@")) return;
    setBusy(true); setNote(null);
    try {
      const r = await fetch("/api/otp/request", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ email: email.trim().toLowerCase() }),
      });
      const d = await r.json().catch(() => ({}));
      if (r.ok) { setStep("code"); setNote(d.message || "이메일로 6자리 코드를 보냈어요."); }
      else setNote(d.detail || "코드를 보내지 못했어요. 잠시 후 다시 시도해 주세요.");
    } catch { setNote("코드를 보내지 못했어요. 잠시 후 다시 시도해 주세요."); }
    setBusy(false);
  }

  async function verify(e: React.FormEvent) {
    e.preventDefault();
    if (busy || !/^\d{6}$/.test(code)) return;
    setBusy(true); setNote(null);
    const res = await signIn("email-otp", { email: email.trim().toLowerCase(), code, callbackUrl, redirect: true })
      .catch(() => null);
    // redirect:true면 성공 시 이동 — 여기 남아 있다면 실패
    if (res !== undefined) setNote("코드가 맞지 않거나 만료됐어요. 다시 확인해 주세요.");
    setBusy(false);
  }

  return step === "email" ? (
    <form className="signin-dev" onSubmit={requestCode}>
      <input className="input" type="email" placeholder="이메일로 계속하기" value={email}
        onChange={(e) => setEmail(e.target.value)} required aria-label="이메일" />
      <button className="btn ghost" type="submit" disabled={busy}>{busy ? "보내는 중…" : "코드 받기"}</button>
      {note && <p className="muted otp-note">{note}</p>}
    </form>
  ) : (
    <form className="signin-dev" onSubmit={verify}>
      <input className="input mono" inputMode="numeric" pattern="\d{6}" maxLength={6} placeholder="6자리 코드"
        value={code} onChange={(e) => setCode(e.target.value.replace(/\D/g, ""))} required aria-label="인증 코드" autoFocus />
      <button className="btn ghost" type="submit" disabled={busy}>{busy ? "확인 중…" : "로그인"}</button>
      <p className="muted otp-note">
        {note || `${email}로 코드를 보냈어요.`}{" "}
        <button type="button" className="linklike" onClick={() => { setStep("email"); setCode(""); setNote(null); }}>
          다른 이메일로
        </button>
      </p>
    </form>
  );
}
