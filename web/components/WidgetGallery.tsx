"use client";

// 위젯 추가 갤러리 — 탐색에서 핀한 데이터 에셋(라이브러리)에서 위젯을 고른다. 위젯은 언제나 "탐색" 채팅에서
// 발굴해 핀한 것이다(원자료가 아닌 카탈로그 나열이 아님). 핀이 하나도 없으면 탐색에서 핀할 에셋을 찾아보라고
// 자연스럽게 안내한다. 추가하면 선택한 핀의 spec을 현재 보드에 위젯으로 올리고, 주기성 데이터면 알림도 켤 수 있다.

import { useEffect, useState } from "react";
import { Button, CadenceTag, FreshnessDot, Modal } from "./ui";
import { isPeriodic } from "@/lib/alerts";

type LibPin = { id: string; title: string; spec: any; board_id: string | null };

export type AddedWidget = { pinId: string; spec: any; withAlert: boolean };

// human label for a pinned asset's nature (chart / table·value / source / narrative)
function kindLabel(spec: any): string {
  const k = spec?.kind;
  if (k === "source") return "출처";
  if (k === "table" || k === "kpi") return "표·값";
  if (k === "narrative") return "내러티브";
  if (k === "candlestick" || k === "timeseries" || k === "compare") return "차트";
  return "데이터";
}

export default function WidgetGallery({
  boardId, boardName, onClose, onAdded,
}: {
  boardId: string;
  boardName?: string;
  onClose: () => void;
  onAdded: (w: AddedWidget) => void;
}) {
  const [pins, setPins] = useState<LibPin[]>([]);
  const [loading, setLoading] = useState(true);
  const [sel, setSel] = useState<LibPin | null>(null);
  const [withAlert, setWithAlert] = useState(false);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    (async () => {
      try {
        const r = await fetch("/api/board/library");
        const ps = (r.ok ? (await r.json()).pins : []) as LibPin[];
        setPins(ps);
        if (ps.length) setSel(ps[0]);
      } catch {} finally { setLoading(false); }
    })();
  }, []);

  const periodic = sel ? isPeriodic(sel.spec) : false;

  async function add() {
    if (!sel) return;
    setBusy(true);
    try {
      // place a copy of the pinned asset onto the current board (RGL auto-flows it)
      const r = await fetch("/api/board", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ spec: sel.spec, board_ids: [boardId] }),
      });
      if (!r.ok) throw new Error();
      const pin = (await r.json()).pinned?.[0];
      // refresh tool-backed assets so they show live data immediately (honest gap otherwise)
      if (pin?.id && sel.spec?.tool) { try { await fetch(`/api/board/${pin.id}/refresh`, { method: "POST" }); } catch {} }
      onAdded({ pinId: pin?.id, spec: sel.spec, withAlert: withAlert && periodic });
    } catch { setBusy(false); }
  }

  return (
    <Modal
      className="widget-gallery"
      onClose={onClose}
      title="위젯 추가"
      footer={
        <>
          <span className="grow" />
          <Button variant="ghost" onClick={onClose} disabled={busy}>{pins.length === 0 ? "닫기" : "취소"}</Button>
          {pins.length > 0 && (
            <Button onClick={add} disabled={busy || !sel}>{busy ? "추가 중…" : "대시보드에 추가"}</Button>
          )}
        </>
      }
    >
      <div className="wg-sub">대시보드{boardName ? `: ${boardName}` : ""} · 탐색에서 핀한 데이터로 위젯을 만듭니다</div>

        {loading ? (
          <div className="wg-empty"><div className="muted-note">불러오는 중…</div></div>
        ) : pins.length === 0 ? (
          <div className="wg-empty">
            <div className="wg-empty-ic" aria-hidden>📌</div>
            <h4>아직 핀한 데이터가 없어요</h4>
            <p>위젯은 <b>탐색</b>에서 채팅으로 찾은 데이터를 핀해서 만듭니다.<br />
               탐색에서 궁금한 것을 물어보고, 답변의 <b>차트·표·출처</b>를 <b>＋ 대시보드</b>로 핀해 보세요.</p>
          </div>
        ) : (
          <div className="wg-body wg-lib">
            {/* the pin library — every asset pinned across 탐색 conversations */}
            <div className="wg-libcol">
              <div className="wg-cat-desc">핀한 데이터 {pins.length}</div>
              {pins.map((p) => (
                <button key={p.id} type="button" className={`wg-libitem ${sel?.id === p.id ? "on" : ""}`}
                  onClick={() => setSel(p)}>
                  <FreshnessDot f={p.spec?.freshness ?? "fresh"} />
                  <span className="wg-libitem-title">{p.title}</span>
                  <span className="wg-libitem-kind mono">{kindLabel(p.spec)}</span>
                </button>
              ))}
            </div>

            {/* preview + add */}
            <div className="wg-config">
              <div className="wg-cfg-l">위젯 미리보기</div>
              {sel && (
                <>
                  <div className="wg-preview">
                    <div className="wg-prev-head">{sel.title}
                      <span className="wg-prev-src mono">{sel.spec?.source || "출처"}</span>
                      <FreshnessDot f={sel.spec?.freshness ?? "fresh"} />
                    </div>
                    <div className="wg-libmeta">
                      <span className="wg-libchip">{kindLabel(sel.spec)}</span>
                      {sel.spec?.cadence && <CadenceTag c={sel.spec.cadence} />}
                      {sel.spec?.as_of && <span className="mono muted-note">as_of {sel.spec.as_of}</span>}
                    </div>
                    <div className="wg-prev-foot mono">출처: {sel.spec?.source || "—"}</div>
                  </div>
                  {periodic ? (
                    <label className="wg-alert">
                      <span>🔔 이 위젯에 알림 — 주기성 데이터</span>
                      <input type="checkbox" checked={withAlert} onChange={(e) => setWithAlert(e.target.checked)} />
                    </label>
                  ) : (
                    <div className="muted-note" style={{ marginTop: 10 }}>단발성 데이터 — 값으로 표시 (알림 없음)</div>
                  )}
                </>
              )}
            </div>
          </div>
        )}

    </Modal>
  );
}
