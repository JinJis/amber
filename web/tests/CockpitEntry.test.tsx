// ASK-6 탐구 엔트리 v4: 종목 칩 탭 → 온디맨드 분석 카드 3개 · 관심그룹 필터 · 뉴스 질문 피드
// (10분 백그라운드 캐시) · 눌러보는 출처. 전역 규칙 = 카드 탭은 컴포저 채움, 출처 탭은 근거
// 뷰어(자동 전송 없음). 미생성/실패 = 정직한 공백 (콘텐츠 날조 없음).
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import CockpitEntry, { capabilityChips } from "../components/CockpitEntry";

afterEach(() => { cleanup(); vi.restoreAllMocks(); });

const TICKER_CARDS = [
  { kind: "price_context", question: "오늘 8% 넘게 뛰었는데 왜 그런지 같이 알아볼까요?",
    hook: "오늘 삼성전자 +8.2%", citations: [{ source: "Yahoo Finance", url: "http://x", tool: "yahoo__price_snapshot" }] },
  { kind: "filing_deep", question: "새로 올라온 공시에 바뀐 위험요소가 있는지 들여다볼까요?",
    hook: "6/28 새 공시 접수", citations: [{ source: "DART", url: "http://d", tool: "opendart__filings" }] },
];

function stubApis({ empty = false, tickerFail = false } = {}) {
  vi.stubGlobal("fetch", vi.fn(async (url: string) => {
    const u = String(url);
    if (u.includes("/api/ask-feed/ticker")) {
      if (tickerFail) return { ok: true, json: async () => ({ cards: [], generated_at: null, cached: false }) };
      return { ok: true, json: async () => ({ cards: TICKER_CARDS, generated_at: "2026-07-06T00:00:00+00:00", cached: false }) };
    }
    if (u.includes("/api/ask-feed")) {
      if (empty) return { ok: true, json: async () => ({ groups: [], tickers: [], news_feed: [] }) };
      return { ok: true, json: async () => ({
        groups: ["반도체", "빅테크"],
        tickers: [
          { market: "KR", ticker: "005930", name: "삼성전자", groups: ["반도체"] },
          { market: "US", ticker: "NVDA", name: "NVIDIA", groups: ["빅테크"] },
        ],
        news_feed: [
          { kind: "macro", question: "물가 흐름을 최근 추이로 같이 볼까요?", hook: "미 CPI 3.1%",
            citations: [{ source: "FRED", url: "http://z", tool: "fred__macro_panel" }] },
        ],
        news_generated_at: "2026-07-06T00:00:00+00:00",
      }) };
    }
    return { ok: false, json: async () => ({}) };
  }));
}

describe("CockpitEntry (ASK-6 v4)", () => {
  it("티커 테이프는 사라졌다 — 엔트리에 marquee 없음", async () => {
    stubApis();
    render(<CockpitEntry onPick={vi.fn()} />);
    await screen.findByTestId("ck-mine");
    expect(screen.queryByTestId("ck-tape")).toBeNull();
  });

  it("종목 칩 탭 → 온디맨드로 그 종목 분석 카드 3개 (탭 전에는 카드 요청 없음)", async () => {
    stubApis();
    const onPick = vi.fn();
    render(<CockpitEntry onPick={onPick} />);
    await screen.findByTestId("tk-005930");
    // no on-demand call yet
    const calls = (globalThis.fetch as any).mock.calls.map((c: any[]) => String(c[0]));
    expect(calls.some((u: string) => u.includes("/api/ask-feed/ticker"))).toBe(false);

    fireEvent.click(screen.getByTestId("tk-005930"));
    const cards = await screen.findByTestId("tk-cards");
    await waitFor(() => expect(cards.textContent).toContain("오늘 삼성전자 +8.2%"));
    expect(cards.textContent).toContain("Yahoo Finance");
    const sent = (globalThis.fetch as any).mock.calls.map((c: any[]) => String(c[0]))
      .find((u: string) => u.includes("/api/ask-feed/ticker"));
    expect(sent).toContain("ticker=005930");

    fireEvent.click(screen.getByText(/왜 그런지 같이 알아볼까요/));
    expect(onPick).toHaveBeenCalledWith("오늘 8% 넘게 뛰었는데 왜 그런지 같이 알아볼까요?");
  });

  it("같은 종목 다시 탭 → 접기; 재펼침은 세션 캐시(추가 fetch 없음)", async () => {
    stubApis();
    render(<CockpitEntry onPick={vi.fn()} />);
    await screen.findByTestId("tk-005930");
    fireEvent.click(screen.getByTestId("tk-005930"));
    await waitFor(() => expect(screen.getByTestId("tk-cards").textContent).toContain("+8.2%"));
    fireEvent.click(screen.getByTestId("tk-005930"));               // fold
    expect(screen.queryByTestId("tk-cards")).toBeNull();
    fireEvent.click(screen.getByTestId("tk-005930"));               // re-open from cache
    expect(screen.getByTestId("tk-cards").textContent).toContain("+8.2%");
    const n = (globalThis.fetch as any).mock.calls.map((c: any[]) => String(c[0]))
      .filter((u: string) => u.includes("/api/ask-feed/ticker")).length;
    expect(n).toBe(1);
  });

  it("생성 실패/빈 결과 → 정직한 공백 + 직접 물어보기 칩 (날조 없음)", async () => {
    stubApis({ tickerFail: true });
    const onPick = vi.fn();
    render(<CockpitEntry onPick={onPick} />);
    await screen.findByTestId("tk-NVDA");
    fireEvent.click(screen.getByTestId("tk-NVDA"));
    const gap = await screen.findByTestId("tk-gap");
    expect(gap.textContent).toContain("준비하지 못했어요");
    fireEvent.click(screen.getAllByText("실적·재무")[0]);
    expect(onPick).toHaveBeenCalledWith("NVIDIA 최근 실적·재무 알려줘");
  });

  it("관심그룹 필터: 그룹 선택 → 그 그룹 종목 칩만", async () => {
    stubApis();
    render(<CockpitEntry onPick={vi.fn()} />);
    await screen.findByTestId("tk-005930");
    expect(screen.getByTestId("tk-NVDA")).toBeTruthy();
    fireEvent.click(screen.getByTestId("grp-반도체"));
    expect(screen.getByTestId("tk-005930")).toBeTruthy();
    expect(screen.queryByTestId("tk-NVDA")).toBeNull();
  });

  it("지금 뉴스에서: 백그라운드 캐시 카드 + 탭 → 채움; 질문은 플레이스홀더 콜백으로", async () => {
    stubApis();
    const onPick = vi.fn(); const onQuestions = vi.fn();
    render(<CockpitEntry onPick={onPick} onQuestions={onQuestions} />);
    const newsSec = await screen.findByTestId("ck-news");
    expect(newsSec.textContent).toContain("🌍");                    // macro 이모지
    expect(newsSec.textContent).toContain("미 CPI 3.1%");
    expect(newsSec.textContent).toContain("10분마다 갱신");
    fireEvent.click(screen.getByText(/물가 흐름을 최근 추이로/));
    expect(onPick).toHaveBeenCalledWith("물가 흐름을 최근 추이로 같이 볼까요?");
    await waitFor(() => expect(onQuestions).toHaveBeenCalledWith(["물가 흐름을 최근 추이로 같이 볼까요?"]));
  });

  it("출처 탭 → 근거 뷰어 콜백 (컴포저는 채우지 않음)", async () => {
    stubApis();
    const onPick = vi.fn(); const onEvidence = vi.fn();
    render(<CockpitEntry onPick={onPick} onEvidence={onEvidence} />);
    await screen.findByTestId("ck-news");
    fireEvent.click(screen.getAllByText("근거 보기 →")[0].closest("button")!);
    expect(onEvidence).toHaveBeenCalledWith(expect.objectContaining({ source: "FRED" }));
    expect(onPick).not.toHaveBeenCalled();                          // 출처는 컴포저를 채우지 않음
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
