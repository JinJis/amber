"use client";

// AUTH-4: 카카오 무이메일(센티널) 계정의 이메일 연결 승격 — OTP 2단계 후 서버가 계정을
// 새 이메일로 리네임한다. 세션은 JWT라 즉시 못 바꾸므로 성공 시 재로그인으로 안내.
import { useState } from "react";
import { signOut } from "next-auth/react";

export default function EmailLink() {
  const [step, setStep] = useState<"email" | "code" | "done">("email");
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

  async function link(e: React.FormEvent) {
    e.preventDefault();
    if (busy || !/^\d{6}$/.test(code)) return;
    setBusy(true); setNote(null);
    try {
      const r = await fetch("/api/auth/link-email", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ email: email.trim().toLowerCase(), code }),
      });
      const d = await r.json().catch(() => ({}));
      if (r.ok) {
        setStep("done");
        setNote(d.message || "이메일이 연결됐어요. 보안을 위해 다시 로그인해 주세요.");
        setTimeout(() => void signOut({ callbackUrl: "/" }), 2500);
      } else setNote(d.detail || "코드를 확인해 주세요.");
    } catch { setNote("연결에 실패했어요. 잠시 후 다시 시도해 주세요."); }
    setBusy(false);
  }

  return (
    <div className="st-field email-link">
      <span className="st-label">이메일 연결</span>
      <p className="st-note">이메일을 연결하면 어디서든 로그인할 수 있고, 결제·알림 안내도 받을 수 있어요.</p>
      {step === "email" && (
        <form className="st-namerow" onSubmit={requestCode}>
          <input className="input" type="email" placeholder="you@example.com" value={email}
            onChange={(e) => setEmail(e.target.value)} required />
          <button className="btn" type="submit" disabled={busy}>{busy ? "보내는 중…" : "코드 받기"}</button>
        </form>
      )}
      {step === "code" && (
        <form className="st-namerow" onSubmit={link}>
          <input className="input mono" inputMode="numeric" maxLength={6} placeholder="6자리 코드" value={code}
            onChange={(e) => setCode(e.target.value.replace(/\D/g, ""))} required autoFocus />
          <button className="btn" type="submit" disabled={busy}>{busy ? "연결 중…" : "연결하기"}</button>
        </form>
      )}
      {note && <p className="st-note">{note}</p>}
    </div>
  );
}
