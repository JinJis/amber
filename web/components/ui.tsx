"use client";

// ── finnote design-system primitives ────────────────────────────────────────
// Single source of truth for the recurring UI patterns from the wireframes
// (see docs/deprecate/DESIGN_SYSTEM.md — design docs being rewritten). Every screen composes these instead of re-deriving
// markup/classes, so the visual language stays unified. Tokens live in globals.css
// :root; these primitives own the structural classNames that consume them.

import { ButtonHTMLAttributes, ReactNode, useEffect, useRef, useState } from "react";
import { cadenceLabel } from "@/lib/alerts";

// ── Button ──────────────────────────────────────────────────────────────────
// primary = ink fill · ghost = hairline · danger = light red outline.
type ButtonProps = ButtonHTMLAttributes<HTMLButtonElement> & {
  variant?: "primary" | "ghost" | "danger";
  size?: "md" | "sm";
};
export function Button({ variant = "primary", size = "md", className = "", ...rest }: ButtonProps) {
  const cls = ["btn",
    variant === "ghost" ? "ghost" : variant === "danger" ? "danger" : "",
    size === "sm" ? "sm" : "", className].filter(Boolean).join(" ");
  return <button className={cls} {...rest} />;
}

// ── Chip / Pill ───────────────────────────────────────────────────────────--
// default = hairline · accent = @group indigo · ink = filled. Optional freshness dot.
export function Chip(
  { tone = "default", dot, onClick, title, children }:
  { tone?: "default" | "accent" | "ink"; dot?: string; onClick?: () => void; title?: string; children: ReactNode },
) {
  const cls = ["chip-ui", tone !== "default" ? `chip-${tone}` : "", onClick ? "chip-btn" : ""].filter(Boolean).join(" ");
  return (
    <span className={cls} onClick={onClick} title={title} role={onClick ? "button" : undefined}>
      {dot ? <FreshnessDot f={dot} /> : null}{children}
    </span>
  );
}

// ── Surface card ──────────────────────────────────────────────────────────--
// White elevated surface with an optional hairline-separated header / footer.
export function Card(
  { head, foot, children, className = "", elevated = true }:
  { head?: ReactNode; foot?: ReactNode; children?: ReactNode; className?: string; elevated?: boolean },
) {
  return (
    <div className={`card-ui ${elevated ? "elevated" : ""} ${className}`.trim()}>
      {head != null ? <div className="card-ui-head">{head}</div> : null}
      {children != null ? <div className="card-ui-body">{children}</div> : null}
      {foot != null ? <div className="card-ui-foot">{foot}</div> : null}
    </div>
  );
}

// ── Trust signals ───────────────────────────────────────────────────────────
// Freshness is computed (fresh <30d · aging <90d · stale). The ONLY saturated color.
export const FRESH_LABEL: Record<string, string> = {
  fresh: "최신 (30일 이내)",
  aging: "업데이트 필요",
  stale: "오래됨",
  gap: "자료 없음",
};
export function FreshnessDot({ f }: { f?: string }) {
  if (!f) return null;
  const label = FRESH_LABEL[f] || f;
  return <span className={`fdot ${f}`} title={label} aria-label={label} />;
}
// Periodicity tag — a periodic datasource (cadence != one_shot) is alertable once pinned; a
// one-shot value is just a figure. Cadence labels come from lib/alerts (single source — FE-03).
export function CadenceTag({ c }: { c?: string | null }) {
  if (!c) return null;
  const periodic = c !== "one_shot";
  const label = cadenceLabel(c);
  return (
    <span className={`cad-tag ${periodic ? "periodic" : "oneshot"}`}
      title={periodic ? `새 값이 나오면 자동으로 업데이트되는 데이터예요 (${label})` : "이 시점의 값이에요"}>
      {periodic ? `↻ ${label}` : "1회성"}
    </span>
  );
}
// One legend, reused everywhere a freshness dot appears (the signature legend).
export function TrustLegend() {
  return (
    <div className="legend" aria-label="데이터 최신 상태 안내">
      <span><i className="fdot fresh" /> 최신</span>
      <span><i className="fdot aging" /> 업데이트 필요</span>
      <span><i className="fdot stale" /> 오래됨</span>
    </div>
  );
}

// ── Guardrail label ──────────────────────────────────────────────────────────
// The trust brand, shown not hidden (invariant #5). 가드레일 라벨(warn 톤, 근거색 아님).
export function GuardrailLabel({ icon = "🛡", children }: { icon?: string; children: ReactNode }) {
  return <div className="guard">{icon} {children}</div>;
}

// ── Historical label (M1 / HL-7, UX_SPEC §6.4) ───────────────────────────────
// The descriptive-statistics badge for History Lab artifacts. Deliberately NOT the evidence
// yellow — it marks safe-by-design content (aggregates of the historical record), not a refusal. Fixed copy,
// non-dismissable; base_rates/analogue renderers show it unconditionally (ROADMAP §2 invariant).
export function HistoricalLabel() {
  return <span className="histlabel" title="과거에 있었던 사례를 정리한 통계예요 — 미래 예측이 아니에요">⏳ 과거 기록 · 전망 아님</span>;
}

// ── Brand mark (finnote) — compact contexts use the mark, never a redrawn variant ──
import { Logo } from "./Logo";
export function Mascot({ size }: { size?: number }) {
  return <Logo variant="mark" size={size ?? 18} />;
}

// ── Kebab menu (⋯) ─────────────────────────────────────────────────────────--
// 한 답변의 액션(복사·재생성·공유)을 오른쪽 끝 "⋯" 안으로 모은다. 바깥 클릭·Esc로 닫힘.
// 별도 팝오버 프리미티브가 없어 자체 구현(Modal은 전체 화면 백드롭이라 인라인 메뉴엔 과함).
export type KebabItem = { key: string; label: string; onClick: () => void };
export function KebabMenu({ items, label = "더보기" }: { items: KebabItem[]; label?: string }) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!open) return;
    const onDoc = (e: MouseEvent) => { if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false); };
    const onEsc = (e: KeyboardEvent) => { if (e.key === "Escape") setOpen(false); };
    document.addEventListener("mousedown", onDoc);
    document.addEventListener("keydown", onEsc);
    return () => { document.removeEventListener("mousedown", onDoc); document.removeEventListener("keydown", onEsc); };
  }, [open]);
  if (!items.length) return null;
  return (
    <div className="kebab" ref={ref}>
      <button type="button" className="kebab-btn" aria-haspopup="menu" aria-expanded={open}
        title={label} aria-label={label}
        onClick={(e) => { e.stopPropagation(); setOpen((o) => !o); }}>⋯</button>
      {open && (
        <div className="kebab-menu" role="menu">
          {items.map((it) => (
            <button key={it.key} type="button" role="menuitem" className="kebab-item"
              onClick={(e) => { e.stopPropagation(); setOpen(false); it.onClick(); }}>
              {it.label}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}

// ── Modal shell ───────────────────────────────────────────────────────────--
// Backdrop + centered panel + head with close. Click-outside / esc closes.
// The single modal shell (FE-06): backdrop-click + ✕ close, plus a `className` for per-modal
// styling (alert-sheet / widget-gallery) and a11y (role=dialog · aria-modal · Escape-to-close).
export function Modal(
  { title, onClose, wide, className, children, footer }:
  { title: ReactNode; onClose: () => void; wide?: boolean; className?: string; children: ReactNode; footer?: ReactNode },
) {
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") onClose(); };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [onClose]);
  const cls = ["modal", wide && "wide", className].filter(Boolean).join(" ");
  return (
    <div className="modal-backdrop" onClick={onClose}>
      <div className={cls} role="dialog" aria-modal="true"
        aria-label={typeof title === "string" ? title : undefined}
        onClick={(e) => e.stopPropagation()}>
        <div className="modal-head">
          <h3>{title}</h3>
          <button className="x" onClick={onClose} aria-label="닫기">✕</button>
        </div>
        {children}
        {footer != null ? <div className="modal-foot">{footer}</div> : null}
      </div>
    </div>
  );
}
