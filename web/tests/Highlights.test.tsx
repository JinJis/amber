// 하이라이트 3종(design template §MARK) — 근거·핵심·수치가 서로 다른 시각 언어로 렌더되는지.
// 이전에는 셋이 모두 앰버 씰 하나로 뭉개졌다: 수치=근거와 동일 앰버였고 핵심은 아예 없었다.
// 이제 수치=오션 칩(.figure-num), 핵심=연한 앰버 블록(.hl-block, ==…==), 근거=앰버 각주로 갈린다.
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { AnswerArticle, makeMdComponents, splitFigures } from "../components/Chat";
import { annotateNumerals } from "../lib/evidence";
import type { Citation } from "../lib/types";

afterEach(cleanup);

const md = () => makeMdComponents(null, vi.fn(), vi.fn());

describe("핵심 (==…== → .hl-block)", () => {
  it("문단 안 ==구절==을 연한 앰버 블록 <mark>로 감싼다", () => {
    const { container } = render(
      <AnswerArticle mdComponents={md()} content={"이번 분기 ==영업이익이 크게 늘었다==. 배경은…"} />);
    const mk = container.querySelector("mark.hl-block");
    expect(mk).toBeTruthy();
    expect(mk!.textContent).toBe("영업이익이 크게 늘었다");
    expect(container.textContent).not.toContain("==");   // 마커는 노출되지 않는다
  });

  it("핵심 구절이 [n] 인용을 품어도(노드 경계 넘어감) 블록으로 감싸고 각주는 살아있다", () => {
    const { container } = render(
      <AnswerArticle mdComponents={md()} content={"==매출은 391.0B로 늘었다 [1]== 그리고 이어진다."} />);
    const mk = container.querySelector("mark.hl-block");
    expect(mk).toBeTruthy();
    expect(mk!.textContent).toContain("매출은 391.0B로 늘었다");
    // 각주는 블록 안에서 여전히 클릭 가능한 cite-ref 버튼으로 렌더된다
    expect(mk!.querySelector("button.cite-ref")?.textContent).toBe("[1]");
  });

  it("답변당 정확히 한 번만 — 두 번째 ==는 그대로 둔다", () => {
    const { container } = render(
      <AnswerArticle mdComponents={md()} content={"==첫 핵심== 중간 ==둘째==."} />);
    expect(container.querySelectorAll("mark.hl-block").length).toBe(1);
    expect(container.querySelector("mark.hl-block")!.textContent).toBe("첫 핵심");
    expect(container.textContent).toContain("둘째");        // 두 번째는 마킹 안 됨(문단에 남음)
  });

  it("짝이 안 맞는 == 는 리터럴로 남긴다(오검출 방지)", () => {
    const { container } = render(
      <AnswerArticle mdComponents={md()} content={"수식 a == b 는 비교 연산이다."} />);
    expect(container.querySelector("mark.hl-block")).toBeNull();
  });
});

describe("수치 (검증 수치 = 오션 칩 .figure-num, 근거 앰버와 구분)", () => {
  const ROWS = [
    { raw: "391.0B", value: 391e9, span: [4, 10] as [number, number], citation_idx: 1, supported: true },
    { raw: "999조", value: 999e12, span: [24, 28] as [number, number], citation_idx: null, supported: false },
  ];
  const CONTENT = "매출은 391.0B [1]로 늘었고, 목표 999조는 검증 밖이다.";
  const CITES: Citation[] = [{ index: 1, kind: "filing", source: "SEC EDGAR", as_of: "2026-06-30" }];
  const mk = makeMdComponents(null, vi.fn(), vi.fn(), { rows: ROWS as any, citations: CITES });

  it("검증 수치는 .figure-num(오션), 옛 앰버 클래스(evidence-highlight)는 더 이상 아니다", () => {
    render(<AnswerArticle mdComponents={mk} content={annotateNumerals(CONTENT, ROWS as any)} />);
    const ok = screen.getAllByTestId("num-hl")[0];
    expect(ok.className).toContain("figure-num");
    expect(ok.className).not.toContain("evidence-highlight");
  });

  it("미확인 수치만 앰버 warn으로 남는다(신뢰 플래그)", () => {
    render(<AnswerArticle mdComponents={mk} content={annotateNumerals(CONTENT, ROWS as any)} />);
    const warn = screen.getAllByTestId("num-hl")[1];
    expect(warn.className).toContain("warn");
    expect(warn.className).not.toContain("figure-num");
  });
});

describe("셋 다 한 문장 — 뭉개지지 않고 각자 다른 시각 언어로", () => {
  it("핵심 블록 안에서도 수치=오션 칩, 근거=앰버 각주가 각각 살아있다", () => {
    const content = "핵심은 ==매출 391.0B 증가 [1]==라는 점.";
    const s = content.indexOf("391.0B");
    const rows = [{ raw: "391.0B", value: 391e9, span: [s, s + 6] as [number, number],
      citation_idx: 1, supported: true }];
    const cites: Citation[] = [{ index: 1, kind: "filing", source: "SEC EDGAR", as_of: "2026-06-30" }];
    const mk = makeMdComponents(null, vi.fn(), vi.fn(), { rows: rows as any, citations: cites });
    const { container } = render(
      <AnswerArticle mdComponents={mk} content={annotateNumerals(content, rows as any)} />);
    const block = container.querySelector("mark.hl-block");
    expect(block).toBeTruthy();
    expect(block!.querySelector(".num-hl.figure-num")).toBeTruthy();       // 오션 수치칩
    expect(block!.querySelector("button.cite-ref")?.textContent).toBe("[1]"); // 앰버 각주
    expect(container.textContent).not.toContain("==");
  });
});

describe("splitFigures — 스트리밍 중 진행 중인 ==핵심== 숨김", () => {
  it("닫히지 않은 == 는 완성 전까지 노출하지 않는다", () => {
    expect(splitFigures("리드. ==영업이익이", true)).toEqual([{ text: "리드. " }]);
  });
  it("== 짝이 맞으면 그대로 통과", () => {
    expect(splitFigures("리드 ==핵심== 계속", true)).toEqual([{ text: "리드 ==핵심== 계속" }]);
  });
});
