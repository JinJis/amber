"use client";

// LG (근거 패널 v3) — 스트리밍→완료가 "정리"로 읽히는 2단계 플로우.
//
// 수집 중 (streaming): 위에서부터
//   리서치 과정 (live 타임라인 — 도구 호출, 마지막이 스피너)
//   차트·표 (도착 순)
//   수집한 출처 (도착 순 + "답변이 완성되면 인용된 출처 [n]과 참고만 한 출처로 정리돼요" 안내)
//
// 완료 (done): 읽는 순서대로, 섹션마다 한 줄 설명
//   ① 판정 (TrustStrip)      — QT-2 숫자 검증이 헤드라인
//   ② 차트·표                — 답변이 그린 시각 자료
//   ③ 인용한 출처 [n]         — 본문 [n]과 1:1, 번호순. [n] 클릭/호버 → 이 카드로 (remote control)
//   ④ 참고만 한 출처 (접힘)   — 살펴봤지만 인용하지 않은 출처 (버리지 않고 흐리게 보관)
//   ⑤ 리서치 과정 (접힘)      — 수집 중의 타임라인이 그대로 접혀 내려온다 (아무것도 사라지지 않음)
//
// 수치 원장은 패널에서 본문 속으로 옮겨졌다 (LG-4): 답변의 숫자가 하이라이트되고 hover 팝업이
// 원자료 대조를 보여준다. 카드 key는 출처 identity라 스트리밍→완료 전환에도 카드가 유지된다.

import { useEffect, useRef, useState, type MouseEvent as ReactMouseEvent } from "react";
import { ArtifactCard } from "./ArtifactCard";
import { SourceCard } from "./SourceCard";
import { evidenceOf, trustSummary, type TrustSummary } from "../lib/evidence";
import type { Artifact, Citation, Msg, ToolUse } from "../lib/types";

function uniqueTools(tools?: ToolUse[]): ToolUse[] {
  const seen = new Map<string, ToolUse>();
  for (const t of tools || []) seen.set(t.label || t.name, t);
  return [...seen.values()];
}
export { evidenceOf, uniqueTools };

// ── ① 판정 ──────────────────────────────────────────────────────────────────────
export function TrustStrip({ s }: { s: TrustSummary }) {
  if (s.conceptual) {
    return <div className="ctx-trust quiet" data-testid="trust-strip">개념 설명 — 검증할 수치 없음</div>;
  }
  return (
    <div className={`ctx-trust ${s.allClear ? "" : "warn"}`} data-testid="trust-strip">
      <span className="ct-verdict mono">
        {s.checked > 0
          ? (s.allClear
              ? <><b>✓ 검증</b> 수치 {s.checked}/{s.checked} 원자료 대조</>
              : <><b className="ct-warn">⚠ 확인 필요</b> 수치 {s.checked}개 중 미확인 {s.unsupported}건</>)
          : <><b>✓</b> 출처 기반</>}
      </span>
      <span className="ct-meta mono">
        출처 {s.sources}
        {s.freshness.fresh > 0 && <span className="ct-f"><i className="fdot fresh" />{s.freshness.fresh}</span>}
        {s.freshness.aging > 0 && <span className="ct-f"><i className="fdot aging" />{s.freshness.aging}</span>}
        {s.freshness.stale > 0 && <span className="ct-f"><i className="fdot stale" />{s.freshness.stale}</span>}
      </span>
    </div>
  );
}

// 섹션 머리 — 제목 + (개수) + 한 줄 설명. "뭘 어떻게 봐야 하는지"를 라벨이 직접 말해준다.
function SectionHead({ title, count, desc }: { title: string; count?: number; desc?: string }) {
  return (
    <>
      <div className="ctx-label">{title}{count != null ? ` ${count}` : ""}</div>
      {desc ? <div className="ctx-desc">{desc}</div> : null}
    </>
  );
}

// ── ⑥ 리서치 과정 — 도구 호출 타임라인 (수집 중엔 live, 완료 후엔 접힘으로 보존) ──
function ProcessRows({ tools, live }: { tools: ToolUse[]; live: boolean }) {
  return (
    <div className="ctx-proc" data-testid="proc-rows">
      {tools.map((t, j) => {
        const active = live && j === tools.length - 1;
        return (
          <div key={j} className={`ctx-proc-row ${active ? "active" : "done"}`}>
            <span className="ctx-proc-ic">{active ? <span className="tl-spin" /> : "✓"}</span>
            <span className="ctx-proc-lbl">{t.label || t.name}</span>
          </div>
        );
      })}
    </div>
  );
}

// ── the panel ────────────────────────────────────────────────────────────────────
export function ContextPanel(
  { msg, streaming, onEvidence, onPinArtifact, onPinCitation, onShareArtifact, onResizeStart,
    hoverCite, setHoverCite, flashCite, onCloseMobile }:
  {
    msg: Msg | null; streaming: boolean;
    onEvidence: (c: Citation) => void;
    onPinArtifact?: (a: Artifact) => void;
    onShareArtifact?: (a: Artifact) => void;
    onPinCitation?: (c: Citation) => void;
    onResizeStart: (e: ReactMouseEvent) => void;
    onCloseMobile?: () => void;   // mobile bottom-sheet: a close (✕) affordance in the head
    hoverCite: number | null;
    setHoverCite: (n: number | null) => void;
    flashCite: { n: number; ts: number } | null;   // [n] clicked in prose → scroll+flash here
  },
) {
  const arts = msg?.artifacts ?? [];
  const cites = msg?.citations ?? [];
  // 인용한 출처 = 본문 [n]이 가리키거나 차트·표를 뒷받침한 출처 — [n] 번호순으로.
  const used = (msg ? evidenceOf(msg) : [])
    .slice().sort((a, b) => (a.index ?? 999) - (b.index ?? 999));
  const usedKeys = new Set(used.map((c) => `${c.source}|${c.url}`));
  const others = cites.filter((c) => !usedKeys.has(`${c.source}|${c.url}`));
  const tools = uniqueTools(msg?.tools);
  const hasAny = arts.length || cites.length || tools.length;
  const summary = trustSummary(msg);

  // 참고만 한 출처 fold — [n] 클릭이 그 안의 카드를 가리키면 펼친다.
  const [othersOpen, setOthersOpen] = useState(false);
  useEffect(() => { setOthersOpen(false); }, [msg]);

  // [n] click → scroll+flash that card (인용한 출처는 항상 펼쳐져 있다).
  const cardRefs = useRef(new Map<number, HTMLDivElement>());
  useEffect(() => {
    if (!flashCite) return;
    if (others.some((c) => c.index === flashCite.n)) setOthersOpen(true);
    const el = cardRefs.current.get(flashCite.n);
    if (el) {
      el.scrollIntoView({ block: "center", behavior: "smooth" });
      el.classList.add("flash");
      const t = setTimeout(() => el.classList.remove("flash"), 1600);
      return () => clearTimeout(t);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [flashCite]);

  // 스트리밍→완료 전환에도 같은 카드가 유지되도록 key는 출처 identity로.
  const card = (c: Citation, j: number) => (
    <div key={`${c.source}|${c.url}|${j}`}
      ref={(el) => { if (el && c.index != null) cardRefs.current.set(c.index, el); }}
      className={`ctx-card-wrap ${hoverCite != null && c.index === hoverCite ? "hot" : ""}`}
      onMouseEnter={() => c.index != null && setHoverCite(c.index)}
      onMouseLeave={() => setHoverCite(null)}>
      <SourceCard c={c} onExpand={onEvidence} onPin={onPinCitation} />
    </div>
  );

  const artsSection = arts.length > 0 && (
    <div className="ctx-section">
      <SectionHead title="차트·표" count={arts.length}
        desc={streaming ? undefined : "답변이 그린 시각 자료 — 값마다 출처가 붙어요."} />
      <div className="artifacts">
        {arts.map((a, j) => <ArtifactCard key={a.title || `a${j}`} a={a} onPin={onPinArtifact} onShare={onShareArtifact} onEvidence={onEvidence} />)}
      </div>
    </div>
  );

  return (
    <aside className="ctxpane">
      <div className="ctx-resize" onMouseDown={onResizeStart} title="끌어서 패널 너비를 조절할 수 있어요" aria-hidden />
      <div className="ctxpane-head">
        <span className="ctx-title">근거 패널</span>
        {streaming && <span className="ctx-live"><span className="tl-spin" />수집 중</span>}
        {onCloseMobile && (
          <button type="button" className="ctx-sheet-x" onClick={onCloseMobile} aria-label="근거 패널 닫기">✕</button>
        )}
      </div>
      <span className="live-label">원자료와 출처만 보여드려요 — 예측이나 매매 의견은 없어요.</span>
      {!hasAny ? (
        <div className="ctx-empty">
          {streaming
            ? "답변을 작성하며 차트·표·출처를 모으고 있어요…"
            : "답변을 누르면 그 답에 쓰인 차트·표·출처가 여기에 모여요."}
        </div>
      ) : streaming ? (
        // ── 수집 중: 과정이 주인공 — 도구 타임라인 + 도착 순서 그대로의 출처 ──
        <>
          {tools.length > 0 && (
            <div className="ctx-section" data-testid="ctx-collecting">
              <SectionHead title="리서치 과정" count={tools.length} />
              <ProcessRows tools={tools} live />
            </div>
          )}
          {artsSection}
          {cites.length > 0 && (
            <div className="ctx-section">
              <SectionHead title="수집한 출처" count={cites.length} />
              <div className="ctx-note">
                답변이 완성되면 <b>인용한 출처 [n]</b>과 <b>참고만 한 출처</b>로 정리돼요.
              </div>
              <div className="ctx-cards">{cites.map(card)}</div>
            </div>
          )}
        </>
      ) : (
        // ── 완료: 판정 → 차트·표 → 인용한 출처 [n] → 수치 원장 → 참고만 (접힘) → 과정 (접힘) ──
        <>
          {msg && <TrustStrip s={summary} />}
          {artsSection}
          {used.length > 0 && (
            <div className="ctx-section" data-testid="ctx-used">
              <SectionHead title="인용한 출처" count={used.length}
                desc="답변 속 [n] 번호와 1:1이에요 — 본문의 [n]을 누르면 그 카드로 이동해요." />
              <div className="ctx-cards">{used.map(card)}</div>
            </div>
          )}
          {others.length > 0 && (
            <details className="ctx-section ctx-more" data-testid="ctx-others" open={othersOpen}
              onToggle={(e) => setOthersOpen((e.target as HTMLDetailsElement).open)}>
              <summary className="ctx-label">참고만 한 출처 {others.length} — 답변엔 인용 안 됨</summary>
              <div className="ctx-cards ctx-dim">{others.map(card)}</div>
            </details>
          )}
          {tools.length > 0 && (
            <details className="ctx-section ctx-more" data-testid="ctx-process">
              <summary className="ctx-label">리서치 과정 — 도구 {tools.length}개</summary>
              <ProcessRows tools={tools} live={false} />
            </details>
          )}
        </>
      )}
    </aside>
  );
}
