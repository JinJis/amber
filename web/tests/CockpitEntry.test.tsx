// ASK-5 — 물어보기 엔트리: 스트립 · 내 종목 질문거리(사전 생성) · Hot Trend.
// 전역 규칙 = 모든 탭은 컴포저 채움(전송 아님); 미생성 티커는 "준비 중" 스켈레톤(날조 금지).
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import CockpitEntry, { capabilityChips } from "../components/CockpitEntry";

afterEach(() => { cleanup(); vi.restoreAllMocks(); });

function stubApis({ empty = false } = {}) {
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
      ] }) };
    }
    if (String(url).includes("/api/ask-feed")) {
      if (empty) return { ok: true, json: async () => ({ tickers: [], pending: [], hot_trend: [] }) };
      return { ok: true, json: async () => ({
        tickers: [
          { market: "KR", ticker: "005930", name: "삼성전자", cards: [
            { kind: "filing_deep", question: "삼성전자 최신 분기보고서에서 재고자산 변화 보여줘",
              hook: "새 분기보고서 접수 (7/3)", citations: [{ source: "DART" }] },
            { kind: "history_echo", question: "삼성전자 지금 낙폭이 과거 분포에서 몇 퍼센타일이야?",
              hook: "오늘 +1.2% · 고점 대비 -8%", citations: [{ source: "Yahoo Finance" }] },
          ] },
        ],
        pending: [{ market: "US", ticker: "NVDA", name: "NVIDIA" }],
        hot_trend: [
          { kind: "macro", question: "미국 CPI 최근 추이 보여줘", hook: "미 CPI 3.1% (6월)",
            citations: [{ source: "FRED" }] },
        ],
      }) };
    }
    return { ok: false, json: async () => ({}) };
  }));
}

describe("CockpitEntry (ASK-5)", () => {
  it("시장 스트립: 지수 탭 → 그 지수 질문으로 컴포저 채움 (전송 아님)", async () => {
    stubApis();
    const onPick = vi.fn();
    render(<CockpitEntry onPick={onPick} />);
    const kospi = await screen.findByText("KOSPI");
    fireEvent.click(kospi.closest("button")!);
    expect(onPick).toHaveBeenCalledWith(expect.stringContaining("KOSPI"));
    expect(onPick.mock.calls[0][0]).toContain("올랐");   // +1.12% → 올랐
  });

  it("내 종목 질문거리: 사전 생성 카드 렌더 + 탭 → 채움; 질문은 플레이스홀더 콜백으로", async () => {
    stubApis();
    const onPick = vi.fn(); const onQuestions = vi.fn();
    render(<CockpitEntry onPick={onPick} onQuestions={onQuestions} />);
    const mine = await screen.findByTestId("ck-mine");
    expect(mine.textContent).toContain("새 분기보고서 접수 (7/3)");        // hook = 왜 지금
    expect(mine.textContent).toContain("DART");                            // 출처 표시
    fireEvent.click(screen.getByText(/재고자산 변화/));
    expect(onPick).toHaveBeenCalledWith("삼성전자 최신 분기보고서에서 재고자산 변화 보여줘");
    await waitFor(() => expect(onQuestions).toHaveBeenCalled());
    expect(onQuestions.mock.calls[0][0][0]).toContain("재고자산");
  });

  it("미생성 티커 → '준비 중' 스켈레톤 (콘텐츠 날조 없음)", async () => {
    stubApis();
    render(<CockpitEntry onPick={vi.fn()} />);
    const pend = await screen.findByTestId("ck-pending");
    expect(pend.textContent).toContain("NVIDIA");
    expect(pend.textContent).toContain("준비 중");
  });

  it("Hot Trend: 전역 카드 + 태그, 탭 → 채움", async () => {
    stubApis();
    const onPick = vi.fn();
    render(<CockpitEntry onPick={onPick} />);
    const hot = await screen.findByTestId("ck-hot");
    expect(hot.textContent).toContain("거시");            // kind=macro → 태그
    expect(hot.textContent).toContain("미 CPI 3.1% (6월)");
    fireEvent.click(screen.getByText(/CPI 최근 추이/));
    expect(onPick).toHaveBeenCalledWith("미국 CPI 최근 추이 보여줘");
  });

  it("관심종목 없음 → 등록 넛지 (질문거리 섹션이 비지 않음)", async () => {
    stubApis({ empty: true });
    render(<CockpitEntry onPick={vi.fn()} />);
    const nudge = await screen.findByTestId("ck-nudge");
    expect(nudge.textContent).toContain("관심종목을 등록하면");
  });

  it("티커 헤더 탭 → 능력 칩 (시장별 분기) + 전체 카드 확장", async () => {
    stubApis();
    const onPick = vi.fn();
    render(<CockpitEntry onPick={onPick} />);
    const mine = await screen.findByTestId("ck-mine");
    fireEvent.click(screen.getByTestId("tkr-005930"));           // 티커 필터 칩
    const caps = await screen.findByTestId("ck-caps");
    expect(caps.textContent).toContain("수급(외인·기관)");   // KR 분기
    expect(caps.textContent).not.toContain("13F");
    expect(mine).toBeTruthy();
    fireEvent.click(screen.getByText("밸류에이션"));
    expect(onPick).toHaveBeenCalledWith("삼성전자 밸류에이션 지표(PER·PBR·시가총액) 알려줘");
  });

  it("capabilityChips: US는 13F, KR은 수급", () => {
    expect(capabilityChips("Apple", "US").some((c) => c.label.includes("13F"))).toBe(true);
    expect(capabilityChips("삼성전자", "KR").some((c) => c.label.includes("수급"))).toBe(true);
  });
});
