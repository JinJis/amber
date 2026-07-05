// ENT — 관제탑 엔트리: 스트립·제안·티커→능력 칩. 전역 규칙 = 모든 탭은 컴포저 채움(전송 아님).
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import CockpitEntry, { capabilityChips } from "../components/CockpitEntry";

afterEach(() => { cleanup(); vi.restoreAllMocks(); });

function stubApis() {
  vi.stubGlobal("fetch", vi.fn(async (url: string) => {
    if (String(url).includes("/api/market/pulse")) {
      return { ok: true, json: async () => ({ as_of: "2026-07-05", items: [
        { label: "S&P 500", ticker: "^GSPC", price: 6213.1, change_percent: -0.42 },
        { label: "KOSPI", ticker: "^KS11", price: 3120.5, change_percent: 1.12 },
      ] }) };
    }
    if (String(url).includes("/api/market/watch")) {
      return { ok: true, json: async () => ({ tickers: [
        { ticker: "005930", market: "KR", name: "삼성전자", price: 74300, change_percent: 1.2 },
        { ticker: "AAPL", market: "US", name: "Apple", price: 210.5, change_percent: -0.8 },
      ] }) };
    }
    if (String(url).includes("/api/desk-feed")) {
      return { ok: true, json: async () => ({ cards: [
        { kind: "earnings_upcoming", question: "TSLA 실적 컨센서스 확인해줘", hook: "TSLA 실적 D-2",
          citations: [{ source: "FMP" }] },
        { kind: "watchlist_nudge", question: "", hook: "관심그룹을 만들어보세요" },
        { kind: "price_move", question: "코스피 오늘 왜 올랐어?", hook: "코스피 +1.1%", citations: [{ source: "Yahoo Finance" }] },
      ] }) };
    }
    return { ok: false, json: async () => ({}) };
  }));
}

describe("CockpitEntry", () => {
  it("시장 스트립: 지수 탭 → 그 지수 질문으로 컴포저 채움 (전송 아님)", async () => {
    stubApis();
    const onPick = vi.fn();
    render(<CockpitEntry onPick={onPick} />);
    const kospi = await screen.findByText("KOSPI");
    fireEvent.click(kospi.closest("button")!);
    expect(onPick).toHaveBeenCalledWith(expect.stringContaining("KOSPI"));
    expect(onPick.mock.calls[0][0]).toContain("올랐");   // +1.12% → 올랐
  });

  it("오늘의 제안: 데이터 카드 3개(넛지 제외), hook+question, 탭 → 채움; 질문은 플레이스홀더 콜백으로", async () => {
    stubApis();
    const onPick = vi.fn(); const onQuestions = vi.fn();
    render(<CockpitEntry onPick={onPick} onQuestions={onQuestions} />);
    const sg = await screen.findByTestId("ck-suggest");
    expect(sg.textContent).toContain("TSLA 실적 D-2");            // 왜(hook)
    expect(sg.textContent).not.toContain("관심그룹을 만들어보세요"); // 넛지는 제안에서 제외
    fireEvent.click(screen.getByText(/TSLA 실적 컨센서스/));
    expect(onPick).toHaveBeenCalledWith("TSLA 실적 컨센서스 확인해줘");
    await waitFor(() => expect(onQuestions).toHaveBeenCalled());
    expect(onQuestions.mock.calls[0][0]).toEqual(["TSLA 실적 컨센서스 확인해줘", "코스피 오늘 왜 올랐어?"]);
  });

  it("내 종목: 티커 탭 → 능력 칩 → 완성된 질문으로 채움 (시장별 칩 분기)", async () => {
    stubApis();
    const onPick = vi.fn();
    render(<CockpitEntry onPick={onPick} />);
    fireEvent.click(await screen.findByText(/삼성전자/));
    const caps = await screen.findByTestId("ck-caps");
    expect(caps.textContent).toContain("수급(외인·기관)");   // KR 분기
    expect(caps.textContent).not.toContain("13F");
    fireEvent.click(screen.getByText("밸류에이션"));
    expect(onPick).toHaveBeenCalledWith("삼성전자 밸류에이션 지표(PER·PBR·시가총액) 알려줘");
  });

  it("capabilityChips: US는 13F, KR은 수급", () => {
    expect(capabilityChips("Apple", "US").some((c) => c.label.includes("13F"))).toBe(true);
    expect(capabilityChips("삼성전자", "KR").some((c) => c.label.includes("수급"))).toBe(true);
  });
});
