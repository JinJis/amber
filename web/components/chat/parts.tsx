"use client";

// 챗 스트림의 프리젠테이션 파트 — Chat.tsx에서 분리(FE-2). 순수 렌더 컴포넌트라 상태 없이
// props만 받는다: 분석 과정 스트림·되묻기 칩·하위 에이전트 카드.

import { useState } from "react";
import { Button } from "../ui";
import type { Clarify, SubAgent, Think } from "../../lib/types";

// PH-THINK: the live reasoning stream — foldable so it doesn't stack up. COLLAPSED (default)
// shows only the latest step (spinning); click to EXPAND the full analyze→fetch→found→synthesize
// trace. The latest one spins, earlier ones are checked.
export function ThinkingLive({ steps }: { steps: Think[] }) {
  const [open, setOpen] = useState(false);
  if (!steps.length) return null;
  const latest = steps[steps.length - 1];
  return (
    <div className={`thinking-live ${open ? "open" : ""}`} aria-live="polite">
      <button type="button" className="tl-bar" onClick={() => setOpen((o) => !o)}
        aria-expanded={open} title={open ? "접기" : "분석 과정 전체 보기"}>
        <span className="tl-chev">{open ? "▾" : "▸"}</span>
        <span className="tl-bar-lbl">분석 과정 · {steps.length}단계</span>
      </button>
      {open
        ? steps.map((s, j) => {
            const last = j === steps.length - 1;
            return (
              <div key={j} className={`tl-step ${last ? "active" : "done"}`}>
                <span className="tl-ic">{last ? <span className="tl-spin" /> : "✓"}</span>{s.text}
              </div>
            );
          })
        : (
          <div className="tl-step active">
            <span className="tl-ic"><span className="tl-spin" /></span>{latest.text}
          </div>
        )}
    </div>
  );
}

// CLARIFY-WITH-OPTIONS: render the agent's choices as chips. Single-pick → click runs it;
// multi-pick → toggle several then confirm. Picks compose a refined follow-up question.
export function ClarifyChips(
  { clarify, disabled, onSubmit }:
  { clarify: Clarify; disabled?: boolean; onSubmit: (labels: string[]) => void },
) {
  const [sel, setSel] = useState<Set<number>>(new Set());
  const toggle = (i: number) =>
    setSel((prev) => { const n = new Set(prev); n.has(i) ? n.delete(i) : n.add(i); return n; });
  return (
    <div className="clarify">
      <div className="clarify-opts">
        {clarify.options.map((o, i) => (
          <button key={i} type="button" disabled={disabled}
            className={`clarify-chip ${clarify.multi && sel.has(i) ? "on" : ""}`}
            title={o.description || undefined}
            onClick={() => (clarify.multi ? toggle(i) : onSubmit([o.label]))}>
            <span className="clarify-label">{o.label}</span>
            {o.description ? <span className="clarify-desc">{o.description}</span> : null}
          </button>
        ))}
      </div>
      {clarify.multi && (
        <Button size="sm" disabled={disabled || sel.size === 0}
          onClick={() => onSubmit([...sel].sort((a, b) => a - b).map((i) => clarify.options[i].label))}>
          선택한 내용으로 진행 →
        </Button>
      )}
    </div>
  );
}

// A2A: live cards for the sub-agents researching each facet of a complex request in parallel.
export function SubAgentCards({ subs }: { subs: SubAgent[] }) {
  if (!subs.length) return null;
  return (
    <div className="subagents">
      {subs.map((s) => (
        <div key={s.id} className={`subagent ${s.status}`}>
          <span className="sa-ic">{s.status === "done" ? "✓" : <span className="tl-spin" />}</span>
          <span className="sa-title">{s.title}</span>
          {s.status === "done" && <span className="sa-meta">{s.sources ?? 0} 근거</span>}
        </div>
      ))}
    </div>
  );
}
