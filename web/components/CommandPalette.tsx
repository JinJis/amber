"use client";

// UXQ-3 — ⌘K 커맨드 팔레트: 종목·대화·질문으로 즉시 점프. 단순 부분일치 스코어(결정적),
// Enter=실행 · ↑↓=이동 · Esc=닫기. 아무 것도 안 맞으면 입력 그대로 질문으로 흘려보낸다.

import { useEffect, useMemo, useRef, useState } from "react";
import { TickerLogo } from "./TickerLogo";
import type { Watchlist } from "./Watchlists";

type Item =
  | { kind: "conv"; id: string; label: string }
  | { kind: "ticker"; market: string; ticker: string; label: string }
  | { kind: "ask"; q: string; label: string };

export function CommandPalette({ open, onClose, convs, groups, onOpenConv, onAsk }: {
  open: boolean; onClose: () => void;
  convs: { id: string; title: string }[];
  groups: Watchlist[];
  onOpenConv: (id: string) => void;
  onAsk: (q: string) => void;
}) {
  const [q, setQ] = useState("");
  const [idx, setIdx] = useState(0);
  const inputRef = useRef<HTMLInputElement>(null);

  useEffect(() => { if (open) { setQ(""); setIdx(0); setTimeout(() => inputRef.current?.focus(), 30); } }, [open]);

  const items = useMemo<Item[]>(() => {
    const needle = q.trim().toLowerCase();
    const score = (s: string) => {
      const t = s.toLowerCase();
      if (!needle) return 1;
      if (t.startsWith(needle)) return 3;
      if (t.includes(needle)) return 2;
      return 0;
    };
    const tickers = groups.flatMap((g) => g.items ?? []);
    const seen = new Set<string>();
    const tk: Item[] = tickers
      .filter((t) => { const k = `${t.market}:${t.ticker}`; if (seen.has(k)) return false; seen.add(k); return true; })
      .map((t) => ({ kind: "ticker" as const, market: t.market, ticker: t.ticker,
                     label: `${t.name || t.ticker} · ${t.ticker}` }))
      .filter((i) => score(i.label) > 0)
      .slice(0, 5);
    const cv: Item[] = convs
      .map((c) => ({ kind: "conv" as const, id: c.id, label: c.title || "(제목 없음)" }))
      .filter((i) => score(i.label) > 0)
      .slice(0, 5);
    const ask: Item[] = needle ? [{ kind: "ask", q: q.trim(), label: `“${q.trim()}” 물어보기` }] : [];
    return [...tk, ...cv, ...ask];
  }, [q, convs, groups]);

  useEffect(() => { setIdx((i) => Math.min(i, Math.max(0, items.length - 1))); }, [items.length]);

  function run(it: Item | undefined) {
    if (!it) return;
    onClose();
    if (it.kind === "conv") onOpenConv(it.id);
    else if (it.kind === "ticker") onAsk(`${it.label.split(" · ")[0]} `);
    else onAsk(it.q);
  }

  if (!open) return null;
  return (
    <div className="sv-backdrop" onClick={onClose}>
      <div className="cmdk" role="dialog" aria-label="커맨드 팔레트" onClick={(e) => e.stopPropagation()}>
        <input ref={inputRef} className="cmdk-input" value={q} placeholder="종목·대화 검색, 또는 그냥 질문…"
          onChange={(e) => { setQ(e.target.value); setIdx(0); }}
          onKeyDown={(e) => {
            if (e.key === "ArrowDown") { e.preventDefault(); setIdx((i) => Math.min(i + 1, items.length - 1)); }
            else if (e.key === "ArrowUp") { e.preventDefault(); setIdx((i) => Math.max(i - 1, 0)); }
            else if (e.key === "Enter") { e.preventDefault(); run(items[idx]); }
            else if (e.key === "Escape") onClose();
          }} />
        <div className="cmdk-list">
          {items.map((it, i) => (
            <button key={i} type="button" className={`cmdk-item ${i === idx ? "on" : ""}`}
              onMouseEnter={() => setIdx(i)} onClick={() => run(it)}>
              {it.kind === "ticker"
                ? <TickerLogo market={it.market} ticker={it.ticker} size={18} />
                : <span className="cmdk-ic" aria-hidden>{it.kind === "conv" ? "💬" : "❓"}</span>}
              <span className="cmdk-label">{it.label}</span>
              <span className="cmdk-kind mono">{it.kind === "conv" ? "대화" : it.kind === "ticker" ? "종목" : "질문"}</span>
            </button>
          ))}
          {items.length === 0 && <div className="cmdk-empty mono">일치하는 항목이 없어요 — Enter로 그냥 물어볼 수 있어요</div>}
        </div>
        <div className="cmdk-foot mono">↑↓ 이동 · Enter 실행 · Esc 닫기</div>
      </div>
    </div>
  );
}
