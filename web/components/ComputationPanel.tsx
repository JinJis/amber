"use client";

// PH-DATA-6: the "계산 근거" panel for a self-computed figure (valuation / backtest / screener).
// Our figures are either a single sourced datum (→ the evidence viewer opens the real page) OR the
// OUTPUT of a formula over sourced inputs — for those there is no source *page* to open, so the
// trust envelope is showing the math: what data was queried, what was assumed, the formula, and the
// intermediate steps that produced the number. Collapsed by default; never a forecast (assumptions
// are the user's, base figures are sourced — same honesty contract as the valuation disclaimer).

import { useState } from "react";
import type { CalcRow, Computation } from "../lib/types";
import { DerivationCard } from "./DerivationCard";

export function ComputationPanel({ comp, onEvidence }: {
  comp?: Computation | null;
  onEvidence?: (evidenceUrl: string, row: CalcRow) => void;
}) {
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
          {/* DRV-3: the Derivation Card (symbol chips ↔ rows, numbered steps, evidence links) */}
          <DerivationCard comp={comp} onEvidence={onEvidence} />
        </div>
      )}
    </div>
  );
}
