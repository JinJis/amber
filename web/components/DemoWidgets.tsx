"use client";

// PH-DEMO — high-impact dashboard widgets (Datadog-style) used by the live-demo board:
//   stat     — a row of BIG hero KPI tiles (PER · PBR · Forward PER · 탐욕지수 gauge)
//   heatmap  — sector tiles colored by % change (반도체 종목 현황)
//   feed     — a vertical LIVE news/event stream (right rail)
//   calendar — upcoming dated events with D-day badges
// When `spec.live` is set the numeric widgets tick on a timer (a client-side simulation — these are
// mock/demo specs). Real widgets omit `live` and render statically. Rendered by ArtifactCard.

import { useEffect, useRef, useState } from "react";
import type { Artifact, CalEvent, FeedItem, HeatCell, StatItem } from "../lib/types";

// ── live jitter: mean-reverting random walk around the original values (demo only) ────────────
function useJitter(base: number[], live?: boolean, amp = 0.004, ms = 2000): number[] {
  const baseRef = useRef(base);
  const [vals, setVals] = useState(base);
  useEffect(() => {
    if (!live) return;
    const id = setInterval(() => {
      setVals((prev) => prev.map((v, i) => {
        const target = baseRef.current[i];
        return v + (target - v) * 0.12 + v * (Math.random() - 0.5) * 2 * amp;
      }));
    }, ms);
    return () => clearInterval(id);
  }, [live, amp, ms]);
  return live ? vals : base;
}

function fmtStat(v: number, fmt?: string | null): string {
  if (fmt === "pct") return `${v.toFixed(1)}%`;
  if (fmt === "won") return `${Math.round(v).toLocaleString("en-US")}원`;
  if (fmt === "usd") return `$${v.toLocaleString("en-US", { maximumFractionDigits: 2 })}`;
  return v.toFixed(v >= 100 ? 0 : 2);
}

const GREED_LABEL = (v: number) =>
  v >= 75 ? "극단적 탐욕" : v >= 55 ? "탐욕" : v >= 45 ? "중립" : v >= 25 ? "공포" : "극단적 공포";
const greedColor = (v: number) => `hsl(${Math.round((v / 100) * 130)} 70% 45%)`;  // red→green

// ── stat: big hero KPI tiles ──────────────────────────────────────────────────────────────────
export function StatRow({ a }: { a: Artifact }) {
  const stats = a.stats ?? [];
  const live = useJitter(stats.map((s) => s.value), a.live, 0.0035, 2000);
  return (
    <div className="dw-stats">
      {stats.map((s, i) => {
        const v = live[i];
        const drift = s.value ? ((v - s.value) / s.value) * 100 : 0;
        const delta = (s.delta ?? 0) + drift;       // base day-change + live wiggle
        const up = delta >= 0;
        if (s.gauge) {
          return (
            <div className="dw-stat gauge" key={i}>
              <div className="dw-stat-l">{s.label}</div>
              <div className="dw-stat-v" style={{ color: greedColor(v) }}>{Math.round(v)}</div>
              <div className="dw-gauge"><i style={{ left: `${Math.max(0, Math.min(100, v))}%` }} /></div>
              <div className="dw-stat-sub" style={{ color: greedColor(v) }}>{GREED_LABEL(v)}</div>
            </div>
          );
        }
        return (
          <div className="dw-stat" key={i}>
            <div className="dw-stat-l">{s.label}</div>
            <div className="dw-stat-v">{fmtStat(v, s.fmt)}{s.unit ? <span className="dw-stat-u">{s.unit}</span> : null}</div>
            <div className={`dw-stat-d ${up ? "up" : "dn"}`}>{up ? "▲" : "▼"} {Math.abs(delta).toFixed(2)}%</div>
          </div>
        );
      })}
    </div>
  );
}

// ── heatmap: sector tiles colored by % change ─────────────────────────────────────────────────
function heatBg(pct: number): string {
  const x = Math.max(-3, Math.min(3, pct)) / 3;
  return x >= 0 ? `rgba(31,164,99,${(0.1 + x * 0.5).toFixed(3)})` : `rgba(217,72,58,${(0.1 - x * 0.5).toFixed(3)})`;
}

export function Heatmap({ a }: { a: Artifact }) {
  const cells = a.cells ?? [];
  const live = useJitter(cells.map((c) => c.pct), a.live, 0.06, 1800);
  return (
    <div className="dw-heat">
      {cells.map((c, i) => {
        const pct = live[i];
        return (
          <div className="dw-heat-cell" key={i} style={{ background: heatBg(pct) }} title={c.label}>
            <div className="dw-heat-name">{c.label}</div>
            {c.sub ? <div className="dw-heat-sub">{c.sub}</div> : null}
            <div className={`dw-heat-pct ${pct >= 0 ? "up" : "dn"}`}>{pct >= 0 ? "+" : ""}{pct.toFixed(2)}%</div>
          </div>
        );
      })}
    </div>
  );
}

// ── feed: a vertical LIVE news/event stream (rotates to simulate new items arriving) ──────────
export function NewsFeed({ a }: { a: Artifact }) {
  const [order, setOrder] = useState<FeedItem[]>(a.items ?? []);
  const [tick, setTick] = useState(0);
  useEffect(() => {
    if (!a.live || (a.items?.length ?? 0) < 2) return;
    const id = setInterval(() => {
      setOrder((prev) => {
        const next = [...prev];
        const moved = next.pop();           // last → front as the "newest"
        return moved ? [{ ...moved, time: "방금" }, ...next] : prev;
      });
      setTick((n) => n + 1);
    }, 7000);
    return () => clearInterval(id);
  }, [a.live, a.items]);
  return (
    <div className="dw-feed">
      <div className="dw-feed-hd"><span className="dw-live"><i />LIVE</span></div>
      <div className="dw-feed-list">
        {order.map((it, i) => (
          <div className={`dw-feed-item ${i === 0 ? "fresh" : ""}`} key={`${tick}-${it.text}`}>
            <div className="dw-feed-meta">
              {it.tag ? <span className="dw-feed-tag">{it.tag}</span> : null}
              <span className="dw-feed-time">{it.time || ""}</span>
            </div>
            <div className="dw-feed-text">{it.text}</div>
          </div>
        ))}
      </div>
    </div>
  );
}

// ── calendar: upcoming dated events with D-day badges ─────────────────────────────────────────
function ddays(date: string): number {
  const d = new Date(date + "T00:00:00");
  const now = new Date();
  now.setHours(0, 0, 0, 0);
  return Math.round((d.getTime() - now.getTime()) / 86400000);
}

export function Calendar({ a }: { a: Artifact }) {
  const events = [...(a.events ?? [])].sort((x: CalEvent, y: CalEvent) => (x.date < y.date ? -1 : 1));
  return (
    <div className="dw-cal">
      {events.map((e, i) => {
        const d = ddays(e.date);
        const dd = d === 0 ? "오늘" : d > 0 ? `D-${d}` : `D+${-d}`;
        return (
          <div className={`dw-cal-row ${d === 0 ? "today" : ""}`} key={i}>
            <span className={`dw-cal-dday ${d <= 3 && d >= 0 ? "soon" : ""}`}>{dd}</span>
            <span className="dw-cal-main">
              <span className="dw-cal-label">{e.label}</span>
              <span className="dw-cal-date">{e.date.slice(5)}{e.tag ? ` · ${e.tag}` : ""}</span>
            </span>
          </div>
        );
      })}
    </div>
  );
}

// dispatch helper — returns the right demo widget for a kind, or null if not a demo kind.
export function demoWidget(a: Artifact): React.ReactNode {
  if (a.kind === "stat" && a.stats?.length) return <StatRow a={a} />;
  if (a.kind === "heatmap" && a.cells?.length) return <Heatmap a={a} />;
  if (a.kind === "feed" && a.items?.length) return <NewsFeed a={a} />;
  if (a.kind === "calendar" && a.events?.length) return <Calendar a={a} />;
  return null;
}
