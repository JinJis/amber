// 문서유형 badge + 지느러미 로딩(fin-swim) — design template §badge / §로딩 반영.
// DocBadge: 국내 공시=오션(기본), 미국 공시=자주(b-us), 실적콜=초록(b-call), 모델 계산=회색(b-est),
// 뉴스=뱃지 없음. 근거색(노랑)은 절대 쓰지 않는다. FinLoading: 스피너 대신 fin-swim 모션.
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import { DocBadge, docBadgeOf, FinLoading } from "../components/ui";

afterEach(cleanup);

describe("docBadgeOf (문서유형 → 색상 매핑)", () => {
  it("미국 SEC 공시 → b-us(자주)", () => {
    expect(docBadgeOf({ doc_type: "10-K", kind: "filing" })).toEqual({ label: "10-K", cls: "b-us" });
    expect(docBadgeOf({ doc_type: "8-K", kind: "filing" })?.cls).toBe("b-us");
    expect(docBadgeOf({ doc_type: "20-F", kind: "filing" })?.cls).toBe("b-us");
  });
  it("실적발표 콜/트랜스크립트 → b-call(초록)", () => {
    expect(docBadgeOf({ doc_type: "실적발표 콜", kind: "filing" })?.cls).toBe("b-call");
    expect(docBadgeOf({ doc_type: "earnings call transcript", kind: "filing" })?.cls).toBe("b-call");
  });
  it("모델이 계산·도출한 값 → b-est(회색), 근거색 아님", () => {
    expect(docBadgeOf({ computation: { foo: 1 } as any })).toEqual({ label: "모델 계산", cls: "b-est" });
    expect(docBadgeOf({ doc_type: "모델 추정", kind: "data" })?.cls).toBe("b-est");
  });
  it("국내 공시(사업/분기/공정공시) → 기본 오션(cls 없음)", () => {
    expect(docBadgeOf({ doc_type: "분기보고서", kind: "filing" })).toEqual({ label: "분기보고서", cls: "" });
    expect(docBadgeOf({ doc_type: "사업보고서", kind: "filing" })?.cls).toBe("");
  });
  it("뉴스·doc_type 없음 → 뱃지 없음(null)", () => {
    expect(docBadgeOf({ doc_type: "news", kind: "news" })).toBeNull();
    expect(docBadgeOf({ kind: "news" })).toBeNull();
    expect(docBadgeOf({})).toBeNull();
  });
});

describe("DocBadge (렌더)", () => {
  it("문서유형을 .badge로, 색상 클래스와 함께 렌더", () => {
    const { container } = render(<DocBadge c={{ doc_type: "10-K", kind: "filing" }} />);
    const b = container.querySelector("span.badge");
    expect(b).toBeTruthy();
    expect(b!.className).toContain("b-us");
    expect(b!.textContent).toBe("10-K");
  });
  it("뉴스는 아무것도 렌더하지 않는다", () => {
    const { container } = render(<DocBadge c={{ kind: "news" }} />);
    expect(container.querySelector("span.badge")).toBeNull();
  });
});

describe("FinLoading (지느러미 로딩)", () => {
  it("fin-swim 모션 + 라벨을 status 역할로 렌더", () => {
    render(<FinLoading label="원문 불러오는 중…" />);
    const box = screen.getByTestId("fin-loading");
    expect(box.getAttribute("role")).toBe("status");
    expect(box.querySelector(".fin-swim")).toBeTruthy();   // 스피너가 아니라 지느러미
    expect(box.textContent).toContain("원문 불러오는 중…");
  });
  it("testid를 덮어써 기존 로더 훅을 보존한다", () => {
    render(<FinLoading testid="tk-loading" label="훑는 중…" />);
    expect(screen.getByTestId("tk-loading").querySelector(".fin-swim")).toBeTruthy();
  });
});
