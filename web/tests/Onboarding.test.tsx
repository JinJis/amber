// ASK-6 — 온보딩 v2: 소개(제품 약속) → 관심종목 필수(최소 3) → 착륙. 알림/보드 스텝 없음,
// 건너뛰기 없음 — 물어보기 첫 화면이 관심종목에서 나오므로 등록이 곧 온보딩이다.
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import Onboarding from "../components/Onboarding";

afterEach(() => { cleanup(); vi.restoreAllMocks(); });

function renderOnb() {
  vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
    if (String(url) === "/api/watchlists" && init?.method === "POST") {
      return { ok: true, json: async () => ({ id: "wl_1", name: "내 관심" }) };
    }
    return { ok: true, json: async () => ({}) };
  }));
  const onDone = vi.fn();
  render(<Onboarding onDone={onDone} />);
  return onDone;
}

describe("Onboarding v2 (ASK-6)", () => {
  it("STEP1 소개: 제품 약속 3가지 (출처·질문거리·전망 없음)", () => {
    renderOnb();
    const intro = screen.getByTestId("onb-intro");
    expect(intro.textContent).toContain("출처와 함께 답합니다");
    expect(intro.textContent).toContain("물어볼 거리를 먼저 준비");
    expect(intro.textContent).toContain("전망은 하지 않습니다");
    // 건너뛰기 버튼이 없다 — 관심종목 등록은 필수
    expect(screen.queryByText("건너뛰기")).toBeNull();
  });

  it("STEP2 관심종목: 3종목 미만이면 다음 버튼 비활성 + 카운터 안내", () => {
    renderOnb();
    fireEvent.click(screen.getByText("다음 →"));                    // intro → watch
    expect(screen.getByTestId("onb-count").textContent).toContain("0/3");
    expect((screen.getByText("다음 →").closest("button"))!.hasAttribute("disabled")).toBe(true);
    // 프리셋 하나(3종목 이상) 탭 → 진행 가능
    fireEvent.click(screen.getByText("AI·빅테크"));
    expect(screen.getByTestId("onb-count").textContent).toContain("종목 선택됨 ✓");
    expect((screen.getByText("다음 →").closest("button"))!.hasAttribute("disabled")).toBe(false);
  });

  it("알림·대시보드 스텝은 존재하지 않는다 (3스텝 고정)", () => {
    renderOnb();
    fireEvent.click(screen.getByText("다음 →"));
    fireEvent.click(screen.getByText("AI·빅테크"));
    fireEvent.click(screen.getByText("다음 →"));                    // watch → land (알림 스텝 없음)
    expect(screen.getByTestId("onb-land")).toBeTruthy();
    expect(screen.queryByText(/알림 받을 곳/)).toBeNull();
    expect(screen.getByText("물어보기 시작 →")).toBeTruthy();
  });

  it("완료: 관심 그룹 생성 + 종목 추가 + onboarded 마킹", async () => {
    const onDone = renderOnb();
    fireEvent.click(screen.getByText("다음 →"));
    fireEvent.click(screen.getByText("AI·빅테크"));
    fireEvent.click(screen.getByText("다음 →"));
    fireEvent.click(screen.getByText("물어보기 시작 →"));
    await vi.waitFor(() => expect(onDone).toHaveBeenCalled());
    const calls = (global.fetch as ReturnType<typeof vi.fn>).mock.calls.map((c) => String(c[0]));
    expect(calls).toContain("/api/watchlists");
    expect(calls.some((u) => u.includes("/api/watchlists/wl_1/items"))).toBe(true);
    expect(calls).toContain("/api/onboarded");
  });
});
