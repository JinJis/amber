"use client";

// 설정 — profile · 요금제 · 사용량 · 계정. The account hub every LLM chat app has: edit your
// display name/avatar, see your plan and what each tier offers, and your metered usage.

import { useEffect, useState } from "react";

type Me = { email: string; name: string; image?: string | null; plan: string };
type ByConn = { connector_id: string; calls: number; cost_units: number };
type Usage = { plan: string; usage: { total_calls?: number; total_cost_units?: number; by_connector?: ByConn[] } };

const PLANS = [
  { id: "free", name: "Free", price: "₩0", tagline: "가볍게 둘러보기",
    features: ["하루 질문 한도", "핵심 데이터 소스", "공유 링크"] },
  { id: "pro", name: "Pro", price: "₩19,000 / 월", tagline: "개인 리서처", featured: true,
    features: ["질문 무제한", "전체 데이터 소스", "히스토리 랩 · 노트북", "우선 응답 속도"] },
  { id: "team", name: "Team", price: "문의", tagline: "팀 · 기관",
    features: ["팀 워크스페이스", "공유 노트북", "SSO 로그인", "전용 지원"] },
];

function Avatar({ image, name, size = 72 }: { image?: string | null; name: string; size?: number }) {
  const [failed, setFailed] = useState(false);
  const px = { width: size, height: size } as const;
  if (image && !failed) {
    // eslint-disable-next-line @next/next/no-img-element
    return <img className="avatar" src={image} alt="" style={px} width={size} height={size}
      onError={() => setFailed(true)} referrerPolicy="no-referrer" />;
  }
  const initial = (name || "?").trim().charAt(0).toUpperCase() || "?";
  return <span className="avatar mg" style={{ ...px, fontSize: Math.round(size * 0.42) }} aria-hidden>{initial}</span>;
}

export function Settings({ name, email, image }: { name: string; email: string; image?: string | null }) {
  const [me, setMe] = useState<Me | null>(null);
  const [usage, setUsage] = useState<Usage | null>(null);
  const [tab, setTab] = useState<"profile" | "plan" | "usage">("profile");
  const [editName, setEditName] = useState(name);
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);

  useEffect(() => {
    (async () => {
      try { const r = await fetch("/api/me"); if (r.ok) { const j = await r.json(); setMe(j); setEditName(j.name); } } catch {}
      try { const u = await fetch("/api/me/usage"); if (u.ok) setUsage(await u.json()); } catch {}
    })();
  }, []);

  async function saveName() {
    setSaving(true);
    try {
      const r = await fetch("/api/me", { method: "PATCH", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name: editName.trim() }) });
      if (r.ok) { setMe(await r.json()); setSaved(true); setTimeout(() => setSaved(false), 1500); }
    } finally { setSaving(false); }
  }

  const plan = me?.plan ?? "free";
  const img = me?.image ?? image;
  const dispName = me?.name ?? name;
  const calls = usage?.usage?.total_calls ?? 0;
  const byConn = (usage?.usage?.by_connector ?? []).slice().sort((a, b) => b.calls - a.calls).slice(0, 8);

  return (
    <div className="settings">
      <header className="settings-head"><h2>설정</h2></header>
      <div className="settings-tabs">
        {(["profile", "plan", "usage"] as const).map((t) => (
          <button key={t} type="button" className={`st-tab ${tab === t ? "on" : ""}`} onClick={() => setTab(t)}>
            {t === "profile" ? "프로필" : t === "plan" ? "요금제" : "사용량"}
          </button>
        ))}
      </div>

      {tab === "profile" && (
        <section className="st-panel">
          <div className="st-profile">
            <Avatar image={img} name={dispName} size={72} />
            <div className="st-profile-meta">
              <div className="st-email mono">{email}</div>
              <span className={`st-plan-badge plan-${plan}`}>{plan.toUpperCase()}</span>
            </div>
          </div>
          <label className="st-field">
            <span className="st-label">이름</span>
            <div className="st-namerow">
              <input className="input" value={editName} maxLength={120}
                onChange={(e) => setEditName(e.target.value)} placeholder="표시할 이름" />
              <button className="btn" onClick={saveName} disabled={saving || !editName.trim() || editName.trim() === dispName}>
                {saved ? "저장됨 ✓" : saving ? "저장 중…" : "저장"}
              </button>
            </div>
          </label>
          <p className="st-note mono">프로필 사진은 로그인한 계정(Google·카카오·네이버)에서 가져와요.</p>
          <div className="st-account">
            <a className="btn ghost" href="/api/auth/signout">로그아웃</a>
          </div>
        </section>
      )}

      {tab === "plan" && (
        <section className="st-panel">
          <div className="st-plans">
            {PLANS.map((p) => (
              <div key={p.id} className={`st-plancard ${p.featured ? "featured" : ""} ${plan === p.id ? "current" : ""}`}>
                {p.featured && <span className="st-plan-flag mono">추천</span>}
                <div className="st-plan-n">{p.name}</div>
                <div className="st-plan-tag">{p.tagline}</div>
                <div className="st-plan-price mono">{p.price}</div>
                <ul className="st-plan-feats">{p.features.map((f, i) => <li key={i}>{f}</li>)}</ul>
                {plan === p.id
                  ? <div className="st-plan-cur mono">현재 플랜</div>
                  : <button className="btn" disabled title="결제는 곧 제공돼요">{p.id === "team" ? "문의하기" : "업그레이드"}</button>}
              </div>
            ))}
          </div>
          <p className="st-note mono">결제 연동은 준비 중이에요 — 지금은 플랜 안내만 제공해요.</p>
        </section>
      )}

      {tab === "usage" && (
        <section className="st-panel">
          <div className="st-usage-hero">
            <div className="st-usage-big"><b className="mono">{calls.toLocaleString()}</b><span>총 도구 호출</span></div>
            <div className="st-usage-big"><b className="mono">{plan.toUpperCase()}</b><span>현재 플랜</span></div>
          </div>
          {byConn.length > 0 ? (
            <div className="st-usage-list">
              <div className="st-usage-h mono">소스별 사용</div>
              {byConn.map((c) => (
                <div key={c.connector_id} className="st-usage-row">
                  <span className="st-usage-name mono">{c.connector_id}</span>
                  <span className="st-usage-bar"><span style={{ width: `${Math.min(100, (c.calls / Math.max(1, calls)) * 100)}%` }} /></span>
                  <span className="st-usage-n mono">{c.calls.toLocaleString()}</span>
                </div>
              ))}
            </div>
          ) : <p className="st-note mono">아직 사용 기록이 없어요 — 질문을 시작하면 여기에 쌓여요.</p>}
        </section>
      )}
    </div>
  );
}
