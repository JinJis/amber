// ASK-5 탐구 엔트리 v3: 티커 테이프 · 2단(내 관심종목 파고들기 · Hot Trend) · 관심그룹 필터 ·
// 눌러보는 출처. 전역 규칙 = 카드 탭은 컴포저 채움, 출처 탭은 근거 뷰어(자동 전송 없음).
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
    if (String(url).includes("/api/ask-feed")) {
      if (empty) return { ok: true, json: async () => ({ groups: [], tickers: [], pending: [], hot_trend: [] }) };
      return { ok: true, json: async () => ({
        groups: ["반도체", "빅테크"],
        tickers: [
          { market: "KR", ticker: "005930", name: "삼성전자", groups: ["반도체"], cards: [
            { kind: "price_context", question: "오늘 8% 넘게 뛰었는데 왜 그런지 같이 알아볼까요?",
              hook: "오늘 삼성전자 +8.2%", citations: [{ source: "Yahoo Finance", url: "http://x", tool: "yahoo__price_snapshot" }] },
          ] },
          { market: "US", ticker: "NVDA", name: "NVIDIA", groups: ["빅테크"], cards: [
            { kind: "filing_deep", question: "새 8-K에 임원 변동이 있는데 내용 들여다볼까요?",
              hook: "6/28 8-K 접수", citations: [{ source: "SEC EDGAR", url: "http://y", tool: "sec_edgar__filings" }] },
          ] },
        ],
        pending: [{ market: "US", ticker: "AAPL", name: "Apple", groups: ["빅테크"] }],
        hot_trend: [
          { kind: "macro", question: "물가 흐름을 최근 추이로 같이 볼까요?", hook: "미 CPI 3.1%",
            citations: [{ source: "FRED", url: "http://z", tool: "fred__macro_panel" }] },
        ],
      }) };
    }
    return { ok: false, json: async () => ({}) };
  }));
}

describe("CockpitEntry (ASK-5 v3)", () => {
  it("티커 테이프: 지수 아이템 탭 → 친근한 질문으로 컴포저 채움 (전송 아님)", async () => {
    stubApis();
    const onPick = vi.fn();
    render(<CockpitEntry onPick={onPick} />);
    const tape = await screen.findByTestId("ck-tape");
    const kospi = tape.querySelector("button")!; // first visible item
    // click the KOSPI item specifically
    const kospiBtn = [...tape.querySelectorAll("button")].find((b) => b.textContent?.includes("KOSPI"))!;
    fireEvent.click(kospiBtn);
    expect(onPick).toHaveBeenCalledWith(expect.stringContaining("KOSPI"));
    expect(onPick.mock.calls[0][0]).toMatch(/올랐|볼까요/);
    expect(kospi).toBeTruthy();
  });

  it("내 관심종목 파고들기: 친근한 분석 카드 + 탭 → 채움; 질문은 플레이스홀더 콜백으로", async () => {
    stubApis();
    const onPick = vi.fn(); const onQuestions = vi.fn();
    render(<CockpitEntry onPick={onPick} onQuestions={onQuestions} />);
    const mine = await screen.findByTestId("ck-mine");
    expect(mine.textContent).toContain("오늘 삼성전자 +8.2%");        // hook
    expect(mine.textContent).toContain("Yahoo Finance");            // 출처 이름
    fireEvent.click(screen.getByText(/왜 그런지 같이 알아볼까요/));
    expect(onPick).toHaveBeenCalledWith("오늘 8% 넘게 뛰었는데 왜 그런지 같이 알아볼까요?");
    await waitFor(() => expect(onQuestions).toHaveBeenCalled());
  });

  it("출처 탭 → 근거 뷰어 콜백 (컴포저는 채우지 않음)", async () => {
    stubApis();
    const onPick = vi.fn(); const onEvidence = vi.fn();
    render(<CockpitEntry onPick={onPick} onEvidence={onEvidence} />);
    await screen.findByTestId("ck-mine");
    fireEvent.click(screen.getAllByText("근거 보기 →")[0].closest("button")!);
    expect(onEvidence).toHaveBeenCalledWith(expect.objectContaining({ source: "Yahoo Finance" }));
    expect(onPick).not.toHaveBeenCalled();                          // 출처는 컴포저를 채우지 않음
  });

  it("관심그룹 필터: 그룹 선택 → 그 그룹 종목 카드만", async () => {
    stubApis();
    render(<CockpitEntry onPick={vi.fn()} />);
    await screen.findByTestId("ck-mine");
    expect(screen.getByText(/왜 그런지 같이 알아볼까요/)).toBeTruthy();   // 삼성(반도체)
    expect(screen.getByText(/임원 변동/)).toBeTruthy();                  // NVDA(빅테크)
    fireEvent.click(screen.getByTestId("grp-반도체"));
    expect(screen.getByText(/왜 그런지 같이 알아볼까요/)).toBeTruthy();
    expect(screen.queryByText(/임원 변동/)).toBeNull();                  // 빅테크는 필터로 숨김
  });

  it("미생성 티커 → '준비 중' (콘텐츠 날조 없음)", async () => {
    stubApis();
    render(<CockpitEntry onPick={vi.fn()} />);
    const pend = await screen.findByTestId("ck-pending");
    expect(pend.textContent).toContain("Apple");
    expect(pend.textContent).toContain("준비 중");
  });

  it("Hot Trend: 이모지 아이콘 + 카드, 탭 → 채움", async () => {
    stubApis();
    const onPick = vi.fn();
    render(<CockpitEntry onPick={onPick} />);
    const hot = await screen.findByTestId("ck-hot");
    expect(hot.textContent).toContain("🌍");                         // macro 이모지
    expect(hot.textContent).toContain("미 CPI 3.1%");
    fireEvent.click(screen.getByText(/물가 흐름을 최근 추이로/));
    expect(onPick).toHaveBeenCalledWith("물가 흐름을 최근 추이로 같이 볼까요?");
  });

  it("관심종목 없음 → 등록 넛지 (섹션이 비지 않음)", async () => {
    stubApis({ empty: true });
    render(<CockpitEntry onPick={vi.fn()} />);
    const nudge = await screen.findByTestId("ck-nudge");
    expect(nudge.textContent).toContain("관심종목을 등록하면");
  });

  it("capabilityChips: US는 13F, KR은 수급", () => {
    expect(capabilityChips("Apple", "US").some((c) => c.label.includes("13F"))).toBe(true);
    expect(capabilityChips("삼성전자", "KR").some((c) => c.label.includes("수급"))).toBe(true);
  });
});
