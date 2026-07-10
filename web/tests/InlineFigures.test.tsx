// ARTICLE — 답변 본문 속 인라인 그림: {{figure:N}} 마커 자리에 실제 차트·표(ArtifactCard)가
// 본문 그림으로 렌더링된다. 미지/중복 마커는 조용히 드랍(옛 대화·날조 방지), 스트리밍 중
// 반쯤 도착한 마커는 원문으로 노출되지 않는다.
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { AnswerArticle, splitFigures } from "../components/Chat";
import type { Artifact } from "../lib/types";

afterEach(cleanup);

const TABLE_ART: Artifact = {
  kind: "table", title: "AAPL 연간 매출", source: "SEC EDGAR", as_of: "2026-07-01",
  table: [["연도", "매출"], ["FY25", "391.0B"], ["FY24", "383.3B"]],
} as Artifact;

describe("splitFigures", () => {
  it("마커 기준으로 텍스트/그림 세그먼트 분할", () => {
    const segs = splitFigures("리드 문단.\n\n{{figure:1}}\n\n다음 문단 [1].");
    expect(segs).toEqual([
      { text: "리드 문단.\n\n" }, { fig: 1 }, { text: "\n\n다음 문단 [1]." },
    ]);
  });

  it("스트리밍 중 반쯤 도착한 마커는 숨김 (원문 노출 없음)", () => {
    expect(splitFigures("문단 끝.\n\n{{figu", true)).toEqual([{ text: "문단 끝.\n\n" }]);
    // 완료 후에는 그대로 (마커가 아닌 이중중괄호 텍스트는 보존)
    expect(splitFigures("문단 끝.\n\n{{figu", false)).toEqual([{ text: "문단 끝.\n\n{{figu" }]);
  });

  it("마커 없는 글은 통짜 텍스트 하나", () => {
    expect(splitFigures("그냥 짧은 답 [1].")).toEqual([{ text: "그냥 짧은 답 [1]." }]);
  });
});

describe("AnswerArticle (인라인 그림)", () => {
  const md = { a: (p: any) => <a {...p} /> } as any;

  it("{{figure:1}} 자리에 실제 표 아티팩트가 본문 그림으로 렌더링", () => {
    render(<AnswerArticle mdComponents={md} artifacts={[TABLE_ART]}
      content={"매출이 늘었다 [1].\n\n{{figure:1}}\n\n마무리."} />);
    const fig = screen.getByTestId("fig-1");
    expect(fig.textContent).toContain("AAPL 연간 매출");
    expect(fig.textContent).toContain("391.0B");
    expect(fig.textContent).toContain("SEC EDGAR");       // 그림에도 출처가 붙는다
    expect(screen.getByText(/마무리/)).toBeTruthy();       // 그림 뒤 본문 계속
  });

  it("미지 번호·중복 번호는 조용히 드랍 (옛 대화 = 아티팩트 미보존)", () => {
    const { container } = render(<AnswerArticle mdComponents={md} artifacts={[TABLE_ART]}
      content={"본문.\n\n{{figure:7}}\n\n{{figure:1}}\n\n{{figure:1}}\n\n끝."} />);
    expect(screen.queryByTestId("fig-7")).toBeNull();
    expect(container.querySelectorAll("figure").length).toBe(1);   // 1번은 한 번만
    expect(container.textContent).not.toContain("{{figure");        // 원문 마커 노출 없음
  });

  it("그림 클릭은 상위(답변 포커스)로 전파되지 않는다", () => {
    const onOuter = vi.fn();
    render(<div onClick={onOuter}><AnswerArticle mdComponents={md} artifacts={[TABLE_ART]}
      content={"{{figure:1}}"} /></div>);
    fireEvent.click(screen.getByTestId("fig-1"));
    expect(onOuter).not.toHaveBeenCalled();
  });
});

// ── LG-4: 본문 속 수치 원장 — 하이라이트 + hover 팝업 ─────────────────────────────
import { makeMdComponents } from "../components/Chat";
import { annotateNumerals } from "../lib/evidence";
import type { Citation } from "../lib/types";

const ROWS = [
  { raw: "391.0B", value: 391e9, span: [4, 10] as [number, number], citation_idx: 1, supported: true },
  { raw: "999조", value: 999e12, span: [24, 28] as [number, number], citation_idx: null, supported: false },
];
const CONTENT = "매출은 391.0B [1]로 늘었고, 목표 999조는 검증 밖이다.";
const CITES: Citation[] = [{ index: 1, kind: "filing", source: "SEC EDGAR", as_of: "2026-06-30" }];

describe("annotateNumerals (수치 → #num-i 링크)", () => {
  it("스팬 자리의 수치를 [raw](#num-i)로 감싼다 (뒤에서부터 삽입해 좌표 보존)", () => {
    expect(annotateNumerals(CONTENT, ROWS as any))
      .toBe("매출은 [391.0B](#num-0) [1]로 늘었고, 목표 [999조](#num-1)는 검증 밖이다.");
  });
  it("스팬이 본문과 어긋난 행은 건너뛴다 (오래된 대화 방어)", () => {
    const drifted = [{ raw: "391.0B", value: 1, span: [0, 6] as [number, number], supported: true }];
    expect(annotateNumerals(CONTENT, drifted as any)).toBe(CONTENT);
  });
});

describe("NumHighlight (본문 하이라이트 + 팝업)", () => {
  const mk = (onEvidence = vi.fn()) =>
    makeMdComponents(null, vi.fn(), vi.fn(), { rows: ROWS as any, citations: CITES, onEvidence });

  it("검증 수치는 노란 하이라이트 + 팝업에 원자료 대조·출처 [n]·as_of", () => {
    render(<AnswerArticle mdComponents={mk()} content={annotateNumerals(CONTENT, ROWS as any)} />);
    const hls = screen.getAllByTestId("num-hl");
    expect(hls[0].textContent).toContain("391.0B");
    expect(hls[0].textContent).toContain("원자료 대조 확인");        // 팝업 내용 (hover 시 표시)
    expect(hls[0].textContent).toContain("[1] SEC EDGAR · 2026-06-30");
    expect(hls[0].textContent).toContain("누르면 원문을 볼 수 있어요");
  });

  it("미확인 수치는 앰버(warn) + 경고 팝업, 클릭 불가", () => {
    render(<AnswerArticle mdComponents={mk()} content={annotateNumerals(CONTENT, ROWS as any)} />);
    const warn = screen.getAllByTestId("num-hl")[1];
    expect(warn.className).toContain("warn");
    expect(warn.textContent).toContain("확인하지 못한 숫자");
    expect(warn.getAttribute("role")).toBeNull();
  });

  it("하이라이트 클릭 → 해당 인용으로 onEvidence", () => {
    const onEvidence = vi.fn();
    render(<AnswerArticle mdComponents={mk(onEvidence)}
      content={annotateNumerals(CONTENT, ROWS as any)} />);
    const hl = screen.getAllByTestId("num-hl")[0];
    fireEvent.click(hl);
    expect(onEvidence).toHaveBeenCalledWith(expect.objectContaining({ index: 1 }));
  });
});
