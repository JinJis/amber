"use client";

// M-DERIV (DRV-3) — the Derivation Card: a derived figure's trust envelope rendered as
// 공식(symbol chips) + 출처 있는 입력 + 가정 + 번호 단계(최종값 강조) + note. UX_SPEC §6.6.
// Two-way binding: hovering a symbol chip in the formula highlights its input row and
// vice versa. An input carrying `evidence` deep-links into the /evidence cell-highlight
// viewer via onEvidence. Grayscale, mono, no math-rendering dependency.

import { useMemo, useState } from "react";
import type { CalcEvidence, CalcRow, Computation } from "../lib/types";

const STEP_NUM = ["①", "②", "③", "④", "⑤", "⑥", "⑦", "⑧", "⑨", "⑩"];

/** /evidence params URL for a sourced input row (same contract as Citation.evidence_image_url). */
export function evidenceUrlOf(ev?: CalcEvidence | null): string | null {
  if (!ev || !ev.accession) return null;
  const p = new URLSearchParams();
  if (ev.market) p.set("market", String(ev.market));
  p.set("accession", String(ev.accession));
  if (ev.concept) p.set("concept", String(ev.concept));
  if (ev.value !== undefined && ev.value !== null) p.set("value", String(ev.value));
  if (ev.cik) p.set("cik", String(ev.cik));
  return `/evidence?${p.toString()}`;
}

/** The whole derivation as quotable text (for notes / 공유 인용). */
export function derivationText(comp: Computation): string {
  const line = (r: CalcRow) => `  ${r.symbol ? `${r.symbol} = ` : ""}${r.label}: ${r.value}${r.source ? ` (${r.source})` : ""}`;
  const parts = [`계산 근거 · ${comp.method}`];
  if (comp.formula) parts.push(comp.formula);
  if (comp.inputs?.length) parts.push("입력:", ...comp.inputs.map(line));
  if (comp.assumptions?.length) parts.push("가정:", ...comp.assumptions.map(line));
  if (comp.steps?.length) parts.push("단계:", ...comp.steps.map((r, i) => `  ${STEP_NUM[i] ?? `${i + 1}.`} ${r.label}: ${r.value}`));
  if (comp.note) parts.push(comp.note);
  return parts.join("\n");
}

/** Split the formula into text + symbol chips (symbols = the rows' `symbol` bindings). */
function formulaParts(formula: string, symbols: string[]): { text: string; sym: string | null }[] {
  if (!symbols.length) return [{ text: formula, sym: null }];
  // longest-first so "EPS" wins over "E"; escape regex metacharacters in symbols
  const alts = [...symbols].sort((a, b) => b.length - a.length)
    .map((s) => s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")).join("|");
  const re = new RegExp(`(${alts})`, "g");
  return formula.split(re).filter((t) => t !== "").map((t) => ({ text: t, sym: symbols.includes(t) ? t : null }));
}

function Row({ r, i, kind, hover, setHover, onEvidence }: {
  r: CalcRow; i: number; kind: "input" | "assumption" | "step";
  hover: string | null; setHover: (s: string | null) => void;
  onEvidence?: (url: string, r: CalcRow) => void;
}) {
  const evUrl = kind === "input" ? evidenceUrlOf(r.evidence) : null;
  const hot = !!r.symbol && hover === r.symbol;
  return (
    <div className={`dc-row dc-${kind} ${hot ? "hot" : ""}`} data-testid={`dc-row-${kind}-${i}`}
      onMouseEnter={() => r.symbol && setHover(r.symbol)} onMouseLeave={() => setHover(null)}>
      {kind === "step" ? <span className="dc-stepno mono">{STEP_NUM[i] ?? `${i + 1}.`}</span> : null}
      {r.symbol ? <span className={`dc-sym mono ${hot ? "hot" : ""}`}>{r.symbol}</span> : null}
      <span className="dc-label">{r.label}</span>
      <span className="dc-val mono">{r.value}</span>
      {r.source ? <span className="dc-src">{r.source}</span> : null}
      {evUrl && onEvidence ? (
        <button type="button" className="dc-ev mono" data-testid={`dc-ev-${i}`}
          onClick={() => onEvidence(evUrl, r)} title="이 입력의 원문(공시 셀)을 하이라이트로 엽니다">
          원문↗
        </button>
      ) : null}
    </div>
  );
}

export function DerivationCard({ comp, onEvidence }: {
  comp: Computation;
  onEvidence?: (evidenceUrl: string, row: CalcRow) => void;
}) {
  const [hover, setHover] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);
  const symbols = useMemo(
    () => [...(comp.inputs || []), ...(comp.assumptions || [])].map((r) => r.symbol).filter((s): s is string => !!s),
    [comp],
  );
  const parts = useMemo(() => (comp.formula ? formulaParts(comp.formula, symbols) : []), [comp.formula, symbols]);
  const steps = comp.steps || [];

  async function copy() {
    try { await navigator.clipboard.writeText(derivationText(comp)); setCopied(true); setTimeout(() => setCopied(false), 1500); } catch {}
  }

  return (
    <div className="dc" data-testid="derivation-card">
      {comp.formula ? (
        <div className="dc-formula mono" data-testid="dc-formula">
          {parts.map((p, i) => p.sym ? (
            <span key={i} className={`dc-sym chip ${hover === p.sym ? "hot" : ""}`} data-testid={`dc-chip-${p.sym}`}
              onMouseEnter={() => setHover(p.sym)} onMouseLeave={() => setHover(null)}>{p.text}</span>
          ) : (
            <span key={i}>{p.text}</span>
          ))}
        </div>
      ) : null}
      {comp.inputs?.length ? (
        <div className="dc-sec">
          <div className="dc-sec-h mono">입력 (출처 있음)</div>
          {comp.inputs.map((r, i) => (
            <Row key={i} r={r} i={i} kind="input" hover={hover} setHover={setHover} onEvidence={onEvidence} />
          ))}
        </div>
      ) : null}
      {comp.assumptions?.length ? (
        <div className="dc-sec">
          <div className="dc-sec-h mono">가정 — 데이터가 아니라 조정값</div>
          {comp.assumptions.map((r, i) => (
            <Row key={i} r={r} i={i} kind="assumption" hover={hover} setHover={setHover} />
          ))}
        </div>
      ) : null}
      {steps.length ? (
        <div className="dc-sec">
          <div className="dc-sec-h mono">계산 단계</div>
          {steps.map((r, i) => (
            <div key={i} className={`dc-step-wrap ${i === steps.length - 1 ? "final" : ""}`}
              data-testid={i === steps.length - 1 ? "dc-final" : undefined}>
              <Row r={r} i={i} kind="step" hover={hover} setHover={setHover} />
              {i === steps.length - 1 ? <span className="dc-final-mark mono">◀ 최종값</span> : null}
            </div>
          ))}
        </div>
      ) : null}
      {comp.note ? <div className="dc-note" data-testid="dc-note">{comp.note}</div> : null}
      <div className="dc-actions">
        <button type="button" className="dc-copy mono" onClick={copy}>{copied ? "복사됨 ✓" : "도출 과정 복사"}</button>
      </div>
    </div>
  );
}
