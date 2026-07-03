"use client";

// PH-DATA-6: the "계산 근거" panel for a self-computed figure (valuation / backtest / screener).
// Our figures are either a single sourced datum (→ the evidence viewer opens the real page) OR the
// OUTPUT of a formula over sourced inputs — for those there is no source *page* to open, so the
// trust envelope is showing the math: what data was queried, what was assumed, the formula, and the
// intermediate steps that produced the number. Collapsed by default; never a forecast (assumptions
// are the user's, base figures are sourced — same honesty contract as the valuation disclaimer).

import { useState } from "react";
import type { CalcRow, Computation } from "../lib/types";

function RowList({ title, rows }: { title: string; rows?: CalcRow[] }) {
  if (!rows || rows.length === 0) return null;
  return (
    <div className="cp-sec">
      <div className="cp-sec-h mono">{title}</div>
      <dl className="cp-rows">
        {rows.map((r, i) => (
          <div className="cp-row" key={i}>
            <dt className="cp-label">{r.label}</dt>
            <dd className="cp-val mono">
              {r.value}
              {r.source ? <span className="cp-src"> · {r.source}</span> : null}
            </dd>
          </div>
        ))}
      </dl>
    </div>
  );
}

export function ComputationPanel({ comp }: { comp?: Computation | null }) {
  const [open, setOpen] = useState(false);
  if (!comp) return null;
  return (
    <div className={`cp ${open ? "open" : ""}`}>
      <button type="button" className="cp-toggle mono" onClick={() => setOpen((o) => !o)}
        aria-expanded={open}>
        🧮 계산 근거 <span className="cp-method">{comp.method}</span>
        <span className="cp-chevron">{open ? "▾" : "▸"}</span>
      </button>
      {open && (
        <div className="cp-body">
          {comp.formula ? <div className="cp-formula mono">{comp.formula}</div> : null}
          <RowList title="사용한 데이터" rows={comp.inputs} />
          <RowList title="가정" rows={comp.assumptions} />
          <RowList title="계산 단계" rows={comp.steps} />
          {comp.note ? <div className="cp-note">{comp.note}</div> : null}
        </div>
      )}
    </div>
  );
}
