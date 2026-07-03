"use client";

// 오늘의 데스크 (M-DESK / DK-2) — the turn-zero briefing in the empty chat. Renders the
// per-user desk feed (GET /api/desk-feed): sourced suggestion cards; tapping one pre-fills
// the composer (editable — never auto-sends). A user without @groups gets the watchlist
// nudge with an inline quick-add (shared presets). Every data card carries its provenance
// footer (source · as_of · freshness). When the feed is empty/degraded this renders nothing —
// the parent's capability examples remain, so the screen is never blank.

import { useCallback, useEffect, useState } from "react";
import { PRESETS } from "@/lib/presets";
import type { Citation } from "@/lib/types";
import { FreshnessDot } from "./ui";

export type DeskCard = {
  kind: string;
  question: string;
  hook: string;
  citations?: Citation[];
  deeplink?: string | null;
  ticker?: string | null;
};

type Feed = { cards: DeskCard[]; generated_at?: string | null; cached?: boolean; degraded?: boolean };

const ICONS: Record<string, string> = {
  price_move: "📈", filing_new: "📄", earnings_upcoming: "📅", econ_calendar: "🗓",
  news_cluster: "📰", market_pulse: "🌐", continue_thread: "↩", watchlist_nudge: "⭐",
};

export default function DeskHome({ onPick, onChanged }: {
  onPick: (question: string) => void;   // tap → composer pre-fill (never auto-send)
  onChanged?: () => void;               // quick-add created a watchlist → reload @handles
}) {
  const [feed, setFeed] = useState<Feed | null>(null);
  const [loading, setLoading] = useState(true);
  const [adding, setAdding] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const r = await fetch("/api/desk-feed");
      setFeed(r.ok ? await r.json() : null);
    } catch {
      setFeed(null);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { void load(); }, [load]);

  async function quickAdd(presetId: string) {
    const p = PRESETS.find((x) => x.id === presetId);
    if (!p || adding) return;
    setAdding(presetId);
    try {
      const r = await fetch("/api/watchlists", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name: p.name }),
      });
      if (r.ok) {
        const wl = await r.json();
        for (const it of p.items) {
          await fetch(`/api/watchlists/${wl.id}/items`, {
            method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(it),
          }).catch(() => {});
        }
        onChanged?.();
        await load(); // the edit invalidated the cache → a personalized feed comes back
      }
    } finally {
      setAdding(null);
    }
  }

  if (loading) {
    return (
      <div className="deskfeed" aria-busy="true">
        <div className="df-label">오늘의 데스크</div>
        <div className="df-grid">
          {[0, 1, 2, 3].map((i) => <div key={i} className="df-card df-skel" />)}
        </div>
      </div>
    );
  }

  const cards = feed?.cards ?? [];
  if (!cards.length) return null; // graceful: the parent's capability examples stay visible

  return (
    <div className="deskfeed">
      <div className="df-label">
        오늘의 데스크
        <button type="button" className="df-refresh" onClick={() => void load()} title="새로 고침">↻</button>
      </div>
      <div className="df-grid">
        {cards.map((c, i) => c.kind === "watchlist_nudge" ? (
          <div key={i} className="df-card df-nudge">
            <div className="df-hook"><span className="df-ic">{ICONS[c.kind] ?? "•"}</span>{c.hook}</div>
            <div className="df-presets">
              {PRESETS.map((p) => (
                <button key={p.id} type="button" className="chip" disabled={adding !== null}
                  onClick={() => void quickAdd(p.id)}>
                  {adding === p.id ? "만드는 중…" : `＋ ${p.name}`}
                </button>
              ))}
            </div>
          </div>
        ) : (
          <button key={i} type="button" className="df-card" onClick={() => onPick(c.question)}>
            <div className="df-hook"><span className="df-ic">{ICONS[c.kind] ?? "•"}</span>{c.hook}</div>
            <div className="df-q">“{c.question}” <span className="df-arrow">→</span></div>
            {(c.citations?.length ?? 0) > 0 && (
              <div className="df-foot mono">
                <FreshnessDot f={c.citations![0].freshness} />
                <span>{c.citations![0].source}</span>
                {c.citations![0].as_of && <span>· {c.citations![0].as_of}</span>}
              </div>
            )}
          </button>
        ))}
      </div>
    </div>
  );
}
