"use client";

// History Lab artifact renderers (M1 / HL-7, UX_SPEC §4/§6): `base_rates` (event sentence +
// horizon stat table + histogram strip + event-date chips) and `analogue` (day-offset multi-path
// SVG — current path in ink, historical matches in muted grays, aftermath dashed right of day 0).
// Both render <HistoricalLabel /> UNCONDITIONALLY (ROADMAP §2 invariant) and never draw an
// averaged/consensus path (that would manufacture a forecast line). TradeChart is time-axis-only,
// so the analogue overlay is a purpose-built lightweight SVG.

import { useState } from "react";
import type { AnalogueData, Artifact, BaseRatesData } from "@/lib/types";
import { FreshnessDot, HistoricalLabel } from "./ui";

function Shell({ a, children, bare, hideTitle, onPin, onShare, onRemove }: {
  a: Artifact; children: React.ReactNode; bare?: boolean; hideTitle?: boolean;
  onPin?: (spec: Artifact) => void; onShare?: (spec: Artifact) => void; onRemove?: () => void;
}) {
  const [pinned, setPinned] = useState(false);
  const body = (
    <>
      {children}
      <div className="artifact-foot">
        <HistoricalLabel />
        <span className="artifact-src">
          {a.source || "출처"}
          {a.as_of ? <span className="mono"> · as of {a.as_of}</span> : null}
        </span>
      </div>
    </>
  );
  if (bare) return <div className="hist-bare">{body}</div>;
  return (
    <div className="artifact">
      <div className="artifact-head">
        {!hideTitle && <span className="artifact-title">{a.title}</span>}
        <FreshnessDot f={a.freshness ?? undefined} />
        <span style={{ flex: 1 }} />
        {onPin && (
          <button type="button" className="artifact-toggle" disabled={pinned}
            onClick={() => { onPin(a); setPinned(true); }}>{pinned ? "✓ 대시보드" : "＋ 대시보드"}</button>
        )}
        {onShare && <button type="button" className="artifact-toggle" onClick={() => onShare(a)}>↗ 공유</button>}
        {onRemove && <button type="button" className="artifact-toggle" onClick={onRemove}>제거</button>}
      </div>
      {body}
    </div>
  );
}

// ── base_rates ────────────────────────────────────────────────────────────────
const MAX_CHIPS = 12;

export function BaseRatesArtifact({ a, ...shell }: {
  a: Artifact; bare?: boolean; hideTitle?: boolean;
  onPin?: (spec: Artifact) => void; onShare?: (spec: Artifact) => void; onRemove?: () => void;
}) {
  const d = a.base_rates as BaseRatesData;
  const [showAll, setShowAll] = useState(false);
  const chips = showAll ? d.event_dates : d.event_dates.slice(0, MAX_CHIPS);
  const bins = d.histogram?.bins ?? [];
  const maxCount = Math.max(1, ...bins.map((b) => b.count));
  const fmt = (v: number | null) => (v == null ? "—" : `${v > 0 ? "+" : ""}${v.toFixed(2)}%`);

  return (
    <Shell a={a} {...shell}>
      <div className="br-head mono">
        사건: {d.event.text} · n={d.n}
        {d.raw_n != null && d.raw_n !== d.n ? ` (원자료 ${d.raw_n}건 군집화)` : ""}
      </div>
      <table className="artifact-table">
        <thead><tr><th style={{ textAlign: "left" }}>이후</th><th>n</th><th>중앙값</th><th>p25–p75</th><th>최악·최고</th><th>상승 마감 비율(과거)</th></tr></thead>
        <tbody>
          {d.horizons.map((h) => (
            <tr key={h.h}>
              <td style={{ textAlign: "left" }} className="mono">{h.h}거래일</td>
              <td className="mono">{h.n}</td>
              <td className="mono">{fmt(h.median)}</td>
              <td className="mono">{fmt(h.p25)} ~ {fmt(h.p75)}</td>
              <td className="mono">{fmt(h.min)} · {fmt(h.max)}</td>
              <td className="mono">{h.pos_share == null ? "—" : `${h.pos_share}%`}</td>
            </tr>
          ))}
        </tbody>
      </table>
      {bins.length > 0 && (
        <div className="br-hist" title={`분포 (${d.histogram!.h_ref}거래일 수익률)`}>
          {bins.map((b, i) => (
            <div key={i} className={`br-bin ${b.hi <= 0 ? "neg" : b.lo >= 0 ? "pos" : ""}`}
              style={{ height: `${Math.max(8, (b.count / maxCount) * 100)}%` }}
              title={`${b.lo}% ~ ${b.hi}% · ${b.count}건`} />
          ))}
        </div>
      )}
      {d.event_dates.length > 0 && (
        <div className="br-dates">
          {chips.map((dt) => <span key={dt} className="br-chip mono">{dt}</span>)}
          {d.event_dates.length > MAX_CHIPS && (
            <button type="button" className="br-chip more" onClick={() => setShowAll(!showAll)}>
              {showAll ? "접기" : `+${d.event_dates.length - MAX_CHIPS}건 더`}
            </button>
          )}
        </div>
      )}
    </Shell>
  );
}

// ── analogue ─────────────────────────────────────────────────────────────────
// day-offset multi-path SVG: x = trading-day offset (matches aligned at their window start;
// aftermath continues past the day-0 divider = the window's end, i.e. "지금" 시점).
const GRAYS = ["#8A8A90", "#9B9BA1", "#ACACB2", "#BDBDC3", "#CECED4"];

export function AnalogueArtifact({ a, ...shell }: {
  a: Artifact; bare?: boolean; hideTitle?: boolean;
  onPin?: (spec: Artifact) => void; onShare?: (spec: Artifact) => void; onRemove?: () => void;
}) {
  const d = a.analogue as AnalogueData;
  const [hover, setHover] = useState<number | null>(null);
  const W = 560, H = 220, PAD = 8;
  const win = Math.max(d.window || 0, d.current.path.length, ...d.matches.map((m) => m.path.length));
  const maxAfter = Math.max(0, ...d.matches.map((m) => (m.aftermath?.length ?? 0)));
  const totalX = win + maxAfter;
  const all = [
    ...d.current.path,
    ...d.matches.flatMap((m) => [...m.path, ...(m.aftermath ?? [])]),
  ].filter((v) => v != null && isFinite(v));
  const lo = Math.min(...all), hi = Math.max(...all);
  const x = (i: number) => PAD + (i / Math.max(1, totalX - 1)) * (W - 2 * PAD);
  const y = (v: number) => hi === lo ? H / 2 : PAD + (1 - (v - lo) / (hi - lo)) * (H - 2 * PAD);
  const line = (path: number[], offset = 0) =>
    path.map((v, i) => `${i === 0 ? "M" : "L"}${x(i + offset).toFixed(1)},${y(v).toFixed(1)}`).join(" ");
  const divider = x(win - 1);

  return (
    <Shell a={a} {...shell}>
      <svg viewBox={`0 0 ${W} ${H}`} className="ana-svg" role="img"
        aria-label={`${a.title} — 현재 경로와 과거 유사 구간 ${d.matches.length}개`}>
        {/* day-0 divider: right of it = the matches' AFTERMATH (drawn as history, dashed) */}
        {maxAfter > 0 && (
          <>
            <line x1={divider} y1={0} x2={divider} y2={H} stroke="var(--line-2)" strokeWidth={1} />
            <text x={divider + 4} y={12} className="ana-d0">D0</text>
          </>
        )}
        {d.matches.map((m, i) => (
          <g key={i} opacity={hover == null || hover === i ? 1 : 0.25}
            onMouseEnter={() => setHover(i)} onMouseLeave={() => setHover(null)}>
            <path d={line(m.path)} fill="none" stroke={GRAYS[i % GRAYS.length]} strokeWidth={1.4} />
            {(m.aftermath?.length ?? 0) > 1 && (
              <path d={line(m.aftermath!, win - 1)} fill="none" stroke={GRAYS[i % GRAYS.length]}
                strokeWidth={1.4} strokeDasharray="4 3" />
            )}
          </g>
        ))}
        <path d={line(d.current.path)} fill="none" stroke="var(--ink)" strokeWidth={2.2} />
      </svg>
      <div className="ana-legend">
        <span className="ana-key"><i className="ana-swatch now" /> {d.current.label || "현재"}
          {d.current.depth_pct != null ? <span className="mono"> ({d.current.depth_pct}%)</span> : null}
        </span>
        {d.matches.map((m, i) => (
          <span key={i} className={`ana-key ${hover === i ? "on" : ""}`}
            onMouseEnter={() => setHover(i)} onMouseLeave={() => setHover(null)}>
            <i className="ana-swatch" style={{ background: GRAYS[i % GRAYS.length] }} />
            {m.ticker} <span className="mono">{m.start_date?.slice(0, 7)}{m.end_date ? `~${m.end_date.slice(0, 7)}` : ""}
              {m.score != null ? ` · 유사도 ${m.score}` : ""}{m.depth_pct != null ? ` · ${m.depth_pct}%` : ""}</span>
          </span>
        ))}
        {maxAfter > 0 && <span className="ana-key muted">점선 = 그 뒤 실제로 일어난 일 (과거)</span>}
      </div>
    </Shell>
  );
}
