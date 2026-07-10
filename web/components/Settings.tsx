"use client";

// 설정 — profile · 요금제 · 사용량 · 계정. The account hub every LLM chat app has: edit your
// display name/avatar, see your plan and what each tier offers, and your metered usage.

import { useEffect, useState } from "react";

import BillingPanel from "./BillingPanel";
import InvitePanel from "./InvitePanel";

type Me = { email: string; name: string; image?: string | null; plan: string };
type ByConn = { connector_id: string; calls: number; cost_units: number };
type Usage = {
  plan: string;
  usage: { total_calls?: number; total_cost_units?: number; by_connector?: ByConn[] };
  // PLAN-5: 턴(분석) 쿼터 스냅샷 — 프로그레스 바 표시용
  turns?: { plan: string; label?: string; daily_used: number; daily_limit: number | null;
    monthly_used: number; monthly_limit: number | null; degrade_over_monthly?: boolean;
    daily_reset_at?: string; monthly_reset_at?: string };
};

const PLANS = [
  { id: "free", name: "Free", price: "₩0", tagline: "가볍게 둘러보기",
    features: ["하루 5회 · 월 80회 분석", "핵심 데이터 소스(공시·거시·뉴스·히스토리)", "공유 링크"] },
  { id: "pro", name: "Pro", price: "₩19,900 / 월", tagline: "개인 리서처", featured: true,
    features: ["월 200회 심층 분석(이후에도 표준 모델로 계속)", "프리미엄 데이터 — KIS 실시간 수급 · 컨센서스 · 어닝콜", "심층 리서치 모델 + 멀티 에이전트 분석", "히스토리 랩 · 스탠딩 알림"] },
  { id: "team", name: "Team", price: "문의", tagline: "팀 · 기관",
    features: ["팀 워크스페이스", "공유 워크스페이스", "SSO 로그인", "전용 지원"] },
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
  const [tab, setTab] = useState<"profile" | "plan" | "usage" | "invite">("profile");
  const [editName, setEditName] = useState(name);
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);
  // UXQ-1: 테마 — 쿠키(vg_theme) + <html data-theme> 즉시 적용. auto=속성 제거(시스템 추종).
  const [theme, setTheme] = useState<"auto" | "light" | "dark">(() => {
    if (typeof document === "undefined") return "auto";
    const m = document.cookie.match(/(?:^|; )vg_theme=(light|dark)/);
    return (m?.[1] as "light" | "dark") ?? "auto";
  });
  function applyTheme(v: "auto" | "light" | "dark") {
    setTheme(v);
    const root = document.documentElement;
    if (v === "auto") {
      root.removeAttribute("data-theme");
      document.cookie = "vg_theme=; Max-Age=0; path=/";
    } else {
      root.setAttribute("data-theme", v);
      document.cookie = `vg_theme=${v}; Max-Age=31536000; path=/; SameSite=Lax`;
    }
  }

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
        {(["profile", "plan", "usage", "invite"] as const).map((t) => (
          <button key={t} type="button" className={`st-tab ${tab === t ? "on" : ""}`} onClick={() => setTab(t)}>
            {t === "profile" ? "프로필" : t === "plan" ? "요금제" : t === "usage" ? "사용량" : "친구 초대"}
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
          <div className="st-field">
            <span className="st-label">테마</span>
            <div className="st-theme-row">
              {([["auto", "자동"], ["light", "라이트"], ["dark", "다크"]] as const).map(([v, l]) => (
                <button key={v} type="button" className={`chip ${theme === v ? "on" : ""}`}
                  onClick={() => applyTheme(v)}>{l}</button>
              ))}
            </div>
            <p className="st-note mono">자동은 기기 설정(라이트/다크)을 따라가요.</p>
          </div>
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
          <BillingPanel />{/* BILL-2: 카드 등록 → Pro · 구독 관리 */}
        </section>
      )}

      {tab === "usage" && (
        <section className="st-panel">
          <div className="st-usage-hero">
            <div className="st-usage-big"><b className="mono">{calls.toLocaleString()}</b><span>총 도구 호출</span></div>
            <div className="st-usage-big"><b className="mono">{plan.toUpperCase()}</b><span>현재 플랜</span></div>
          </div>
          {usage?.turns && (usage.turns.daily_limit != null || usage.turns.monthly_limit != null) && (
            <div className="st-usage-list">
              <div className="st-usage-h mono">분석(턴) 사용량</div>
              {usage.turns.daily_limit != null && (
                <div className="st-usage-row">
                  <span className="st-usage-name">오늘 {usage.turns.daily_used}/{usage.turns.daily_limit}회를 사용했어요</span>
                  <span className="st-usage-bar"><span style={{ width: `${Math.min(100, (usage.turns.daily_used / Math.max(1, usage.turns.daily_limit)) * 100)}%` }} /></span>
                  <span className="st-usage-n mono">내일 0시 충전</span>
                </div>
              )}
              {usage.turns.monthly_limit != null && (
                <div className="st-usage-row">
                  <span className="st-usage-name">이번 달 {usage.turns.monthly_used}/{usage.turns.monthly_limit}회를 사용했어요</span>
                  <span className="st-usage-bar"><span style={{ width: `${Math.min(100, (usage.turns.monthly_used / Math.max(1, usage.turns.monthly_limit)) * 100)}%` }} /></span>
                  <span className="st-usage-n mono">
                    {usage.turns.degrade_over_monthly ? "초과해도 표준 모델로 계속" : "다음 달 1일 충전"}
                  </span>
                </div>
              )}
            </div>
          )}
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

      {tab === "invite" && <InvitePanel />}{/* REF-4: 친구 초대 */}
    </div>
  );
}
