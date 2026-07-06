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
