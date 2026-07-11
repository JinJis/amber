// ONB v3 — 서비스를 "직접 보여주는" 6스텝 온보딩: 정체성(진짜 데이터·교차검증) → 근거 프리뷰
// → 분석거리 카드 프리뷰 → 꼬리물기 프리뷰 → 관심종목(필수 3+) → 착륙. 스킵 없음.
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import Onboarding from "../components/Onboarding";

afterEach(() => { cleanup(); vi.restoreAllMocks(); });

function stubApis() {
  vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
    if (String(url) === "/api/watchlists" && init?.method === "POST") {
      return { ok: true, json: async () => ({ id: "wl_1", name: "내 관심" }) };
    }
    return { ok: true, json: async () => ({}) };
  }));
}

const next = () => fireEvent.click(screen.getByText("다음 →"));

describe("Onboarding v3", () => {
  it("① 정체성: 진짜 데이터 근거 + 교차검증 + 무전망이 첫 화면에 읽힌다 (스킵 없음)", () => {
    stubApis();
    render(<Onboarding onDone={vi.fn()} />);
    const intro = screen.getByTestId("onb-intro");
    expect(intro.textContent).toContain("진짜 데이터로 파고드는 리서치 데스크");
    expect(intro.textContent).toContain("SEC EDGAR");
    expect(intro.textContent).toContain("DART");
    expect(intro.textContent).toContain("교차검증");
    expect(intro.textContent).toContain("전망이나 매수 조언은 하지 않아요");
    expect(screen.queryByText("건너뛰기")).toBeNull();
  });

  it("② 근거 프리뷰: 판정 스트립 + 실제 SourceCard(공시·뉴스·데이터) + 하이라이트 수치", () => {
    stubApis();
    render(<Onboarding onDone={vi.fn()} />);
    next();
    const ev = screen.getByTestId("onb-evidence");
    expect(screen.getByTestId("trust-strip")).toBeInTheDocument();     // 진짜 TrustStrip
    expect(ev.textContent).toContain("SEC EDGAR · 10-Q");              // filing SourceCard
    expect(ev.textContent).toContain("Reuters");                       // news SourceCard
    expect(ev.textContent).toContain("재무제표 추출");                  // data SourceCard (표)
    expect(ev.querySelector(".num-hl")).toBeTruthy();                  // 하이라이트 수치
    expect(ev.textContent).toContain("예시 화면");                      // 정직한 배지
  });

  it("③ 분석거리 프리뷰: 종목 칩 + QCard(공시/밸류에이션/수급 각도)", () => {
    stubApis();
    render(<Onboarding onDone={vi.fn()} />);
    next(); next();
    const cards = screen.getByTestId("onb-cards");
    expect(cards.textContent).toContain("삼성전자");
    expect(cards.textContent).toContain("밸류에이션");                  // KIND 라벨
    expect(cards.textContent).toContain("수급·보유");
    expect(cards.textContent).toContain("들여다볼까요");                // QCard question
  });

  it("④ 꼬리물기 프리뷰: 후속 질문 칩", () => {
    stubApis();
    render(<Onboarding onDone={vi.fn()} />);
    next(); next(); next();
    const chain = screen.getByTestId("onb-chain");
    expect(chain.textContent).toContain("이어서 더 파고들기");
    expect(chain.querySelectorAll(".fu-chip").length).toBe(3);
    expect(chain.textContent).toContain("경쟁사와 비교하면");
  });

  it("⑤ 관심종목: 3종목 미만이면 다음으로 못 넘어간다 (필수 게이트)", () => {
    stubApis();
    render(<Onboarding onDone={vi.fn()} />);
    next(); next(); next(); next();
    screen.getByTestId("onb-watch");
    expect(screen.getByTestId("onb-count").textContent).toContain("0/3");
    expect(screen.getByText("다음 →")).toBeDisabled();
    fireEvent.click(screen.getByText("AI·빅테크"));                    // 프리셋 한 번에 담기
    expect(screen.getByTestId("onb-count").textContent).toContain("선택 완료 ✓");
    expect(screen.getByText("다음 →")).not.toBeDisabled();
  });

  it("⑥ 완료: 워치리스트 생성 + 종목 등록 + onboarded 마킹 후 onDone", async () => {
    stubApis();
    const onDone = vi.fn();
    render(<Onboarding onDone={onDone} />);
    next(); next(); next(); next();
    fireEvent.click(screen.getByText("AI·빅테크"));
    next();
    screen.getByTestId("onb-land");
    fireEvent.click(screen.getByText("시작하기 →"));
    await vi.waitFor(() => expect(onDone).toHaveBeenCalled());
    const calls = (globalThis.fetch as any).mock.calls.map((c: any[]) => String(c[0]));
    expect(calls).toContain("/api/watchlists");
    expect(calls.some((u: string) => u.includes("/api/watchlists/wl_1/items"))).toBe(true);
    expect(calls).toContain("/api/onboarded");
  });
});
