// ASK-6 탐구 엔트리 v5: 섹션 세로 스택 — ①관심 @그룹 아코디언(그룹 → 종목 칩 → 온디맨드
// 분석 카드) + ＋ 새 그룹(생성 → 관심 페이지 이동) ②Macro Trends 마키(우→좌, 카드 복제 루프).
// 전역 규칙 = 카드 탭은 컴포저 채움, 출처 탭은 근거 뷰어(자동 전송 없음). 미생성/실패 =
// 정직한 공백 (콘텐츠 날조 없음).
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

const NEWS = [
  { kind: "macro", question: "물가 흐름을 최근 추이로 같이 볼까요?",
    query: "미국 CPI 3.1%가 최근 물가 추이에서 어디쯤인지 살펴봐", hook: "미 CPI 3.1%",
    citations: [{ source: "FRED", url: "http://z", tool: "fred__macro_panel" }] },
  { kind: "market", question: "코스피 급등, 수급으로 같이 볼까요?", query: "코스피 급등 수급 살펴봐",
    hook: "코스피 +5.8%", citations: [{ source: "Yahoo Finance", url: "http://y", tool: "yahoo__asset_classes" }] },
  { kind: "micro", question: "반도체 뉴스 파볼까요?", query: "반도체 업황 뉴스 정리해줘",
    hook: "HBM 수주 보도", citations: [{ source: "Google News", url: "http://n", tool: "google_news__news" }] },
  { kind: "macro", question: "환율 흐름 같이 볼까요?", query: "원달러 환율 최근 추이 살펴봐",
    hook: "원달러 1,390원", citations: [{ source: "Yahoo Finance", url: "http://f", tool: "yahoo__asset_classes" }] },
];

function stubApis({ empty = false, tickerFail = false, fewNews = false } = {}) {
  vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
    const u = String(url);
    if (u.includes("/api/watchlists") && init?.method === "POST") {
      return { ok: true, json: async () => ({ id: "wl-new", name: JSON.parse(String(init.body)).name }) };
    }
    if (u.includes("/api/ask-feed/ticker")) {
      if (tickerFail) return { ok: true, json: async () => ({ cards: [], generated_at: null, cached: false }) };
      return { ok: true, json: async () => ({ cards: TICKER_CARDS, generated_at: "2026-07-06T00:00:00+00:00", cached: false }) };
    }
    if (u.includes("/api/ask-feed")) {
      if (empty) return { ok: true, json: async () => ({ groups: [], tickers: [], news_feed: [] }) };
      return { ok: true, json: async () => ({
        groups: [
          { id: "g-semi", name: "반도체" },
          { id: "g-big", name: "빅테크" },
          { id: "g-empty", name: "빈그룹" },       // 빈 그룹도 내려온다 (방금 만든 그룹)
        ],
        tickers: [
          { market: "KR", ticker: "005930", name: "삼성전자", groups: ["반도체"] },
          { market: "US", ticker: "NVDA", name: "NVIDIA", groups: ["빅테크"] },
        ],
        news_feed: fewNews ? NEWS.slice(0, 1) : NEWS,
        news_generated_at: "2026-07-06T00:00:00+00:00",
      }) };
    }
    return { ok: false, json: async () => ({}) };
  }));
}

const openSemi = async () => {
  fireEvent.click(await screen.findByTestId("grp-반도체"));
  await screen.findByTestId("tk-005930");
};

describe("CockpitEntry (ASK-6 v5)", () => {
  it("세로 스택: 관심종목 섹션이 Macro Trends보다 먼저 온다", async () => {
    stubApis();
    render(<CockpitEntry onPick={vi.fn()} />);
    const mine = await screen.findByTestId("ck-mine");
    const news = await screen.findByTestId("ck-news");
    // DOM 순서: ck-mine이 ck-news보다 앞 (2단 grid가 아니라 세로 스택)
    expect(mine.compareDocumentPosition(news) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  });

  it("1차 depth = @그룹 아코디언: 펼치기 전에는 종목 칩이 없다; 그룹 탭 → 그 그룹 종목만", async () => {
    stubApis();
    render(<CockpitEntry onPick={vi.fn()} />);
    await screen.findByTestId("grp-반도체");
    expect(screen.queryByTestId("tk-005930")).toBeNull();     // 접힌 상태 — 칩 없음
    expect(screen.queryByTestId("tk-NVDA")).toBeNull();

    fireEvent.click(screen.getByTestId("grp-반도체"));
    expect(screen.getByTestId("tk-005930")).toBeTruthy();     // 반도체 그룹 종목만
    expect(screen.queryByTestId("tk-NVDA")).toBeNull();

    fireEvent.click(screen.getByTestId("grp-빅테크"));        // 다른 그룹 → 아코디언 전환
    expect(screen.getByTestId("tk-NVDA")).toBeTruthy();
    expect(screen.queryByTestId("tk-005930")).toBeNull();

    fireEvent.click(screen.getByTestId("grp-빅테크"));        // 다시 탭 → 접기
    expect(screen.queryByTestId("tk-NVDA")).toBeNull();
  });

  it("종목 칩 탭 → 온디맨드로 그 종목 분석 카드 (탭 전에는 카드 요청 없음)", async () => {
    stubApis();
    const onPick = vi.fn();
    render(<CockpitEntry onPick={onPick} />);
    await openSemi();
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
    await openSemi();
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
    fireEvent.click(await screen.findByTestId("grp-빅테크"));
    fireEvent.click(await screen.findByTestId("tk-NVDA"));
    const gap = await screen.findByTestId("tk-gap");
    expect(gap.textContent).toContain("준비하지 못했어요");
    fireEvent.click(screen.getAllByText("실적·재무")[0]);
    expect(onPick).toHaveBeenCalledWith("NVIDIA 최근 실적·재무 알려줘");
  });

  it("＋ 새 그룹: 이름 입력 → 생성(POST) → 관심 페이지로 이동(onManageWatch, 새 그룹 id)", async () => {
    stubApis();
    const onManageWatch = vi.fn();
    render(<CockpitEntry onPick={vi.fn()} onManageWatch={onManageWatch} />);
    fireEvent.click(await screen.findByTestId("eg-add"));
    fireEvent.change(screen.getByPlaceholderText(/그룹 이름/), { target: { value: "2차전지" } });
    fireEvent.keyDown(screen.getByPlaceholderText(/그룹 이름/), { key: "Enter" });
    await waitFor(() => expect(onManageWatch).toHaveBeenCalledWith("wl-new"));
    const post = (globalThis.fetch as any).mock.calls
      .find((c: any[]) => String(c[0]).includes("/api/watchlists") && c[1]?.method === "POST");
    expect(JSON.parse(String(post[1].body)).name).toBe("2차전지");
  });

  it("빈 그룹(방금 만든 그룹)도 보인다 — 펼치면 종목 담으러 가기", async () => {
    stubApis();
    const onManageWatch = vi.fn();
    render(<CockpitEntry onPick={vi.fn()} onManageWatch={onManageWatch} />);
    fireEvent.click(await screen.findByTestId("grp-빈그룹"));
    fireEvent.click(screen.getByText("＋ 종목 담으러 가기"));
    expect(onManageWatch).toHaveBeenCalledWith("g-empty");
  });

  it("Macro Trends 마키: 카드가 복제되어 흐르고, 탭 → 실행용 query가 컴포저로", async () => {
    stubApis();
    const onPick = vi.fn(); const onQuestions = vi.fn();
    render(<CockpitEntry onPick={onPick} onQuestions={onQuestions} />);
    const newsSec = await screen.findByTestId("ck-news");
    expect(newsSec.textContent).toContain("Macro Trends");
    expect(newsSec.textContent).toContain("🌍");
    expect(newsSec.textContent).toContain("미 CPI 3.1%");
    expect(newsSec.textContent).toContain("5분마다 갱신");
    // 끊김 없는 루프: 4장 이상이면 카드가 두 벌 (복제 절반은 aria-hidden)
    expect(screen.getByTestId("ck-news-mq").className).not.toContain("mq-static");
    expect(screen.getAllByText(/물가 흐름을 최근 추이로/).length).toBe(2);
    fireEvent.click(screen.getAllByText(/물가 흐름을 최근 추이로/)[0]);
    // F3: 컴포저에는 주체가 포함된 실행용 query가 들어간다 (표시용 question이 아니라)
    expect(onPick).toHaveBeenCalledWith("미국 CPI 3.1%가 최근 물가 추이에서 어디쯤인지 살펴봐");
    await waitFor(() => expect(onQuestions).toHaveBeenCalledWith(NEWS.map((c) => c.question)));
  });

  it("마키 복제 절반은 aria-hidden + tabIndex=-1 (키보드/스크린리더 탭 순서 제외)", async () => {
    stubApis();
    render(<CockpitEntry onPick={vi.fn()} />);
    const dup = (await screen.findByTestId("ck-news-mq")).querySelector(".mq-dup")!;
    expect(dup.getAttribute("aria-hidden")).toBe("true");
    const dupButtons = dup.querySelectorAll("button");
    expect(dupButtons.length).toBeGreaterThan(0);
    dupButtons.forEach((b) => expect(b.getAttribute("tabindex")).toBe("-1"));
    // 진짜(보이는) 절반의 버튼은 탭 가능해야 한다 (tabIndex 없음)
    const real = screen.getByTestId("ck-news-mq").querySelector(".mq-half:not(.mq-dup)")!;
    real.querySelectorAll("button").forEach((b) => expect(b.getAttribute("tabindex")).toBeNull());
  });

  it("＋ 새 그룹: 중복 이름(409) → 영어 서버 메시지 대신 한국어 해요체 안내", async () => {
    vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
      const u = String(url);
      if (u.includes("/api/watchlists") && init?.method === "POST") {
        return { ok: false, status: 409, json: async () => ({ detail: "You already have a group named '반도체'." }) };
      }
      if (u.includes("/api/ask-feed")) {
        return { ok: true, json: async () => ({ groups: [{ id: "g1", name: "반도체" }], tickers: [], news_feed: [] }) };
      }
      return { ok: false, json: async () => ({}) };
    }));
    render(<CockpitEntry onPick={vi.fn()} onManageWatch={vi.fn()} />);
    fireEvent.click(await screen.findByTestId("eg-add"));
    fireEvent.change(screen.getByPlaceholderText(/그룹 이름/), { target: { value: "반도체" } });
    fireEvent.keyDown(screen.getByPlaceholderText(/그룹 이름/), { key: "Enter" });
    const err = await screen.findByText("이미 같은 이름의 그룹이 있어요.");
    expect(err).toBeTruthy();
    expect(screen.queryByText(/You already have/)).toBeNull();     // 영어 격식체 노출 금지
  });

  it("카드가 4장 미만이면 마키 대신 정적 행 (복제 없음)", async () => {
    stubApis({ fewNews: true });
    render(<CockpitEntry onPick={vi.fn()} />);
    const mq = await screen.findByTestId("ck-news-mq");
    expect(mq.className).toContain("mq-static");
    expect(screen.getAllByText(/물가 흐름을 최근 추이로/).length).toBe(1);
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

  it("그룹 없음 → 등록 넛지 + 관심 그룹 만들기 버튼", async () => {
    stubApis({ empty: true });
    const onManageWatch = vi.fn();
    render(<CockpitEntry onPick={vi.fn()} onManageWatch={onManageWatch} />);
    const nudge = await screen.findByTestId("ck-nudge");
    expect(nudge.textContent).toContain("관심 그룹을 만들면");
    fireEvent.click(screen.getByText("＋ 관심 그룹 만들기"));
    expect(onManageWatch).toHaveBeenCalledWith();
  });

  it("구버전 응답(그룹이 문자열 배열)도 그린다", async () => {
    vi.stubGlobal("fetch", vi.fn(async (url: string) => {
      const u = String(url);
      if (u.includes("/api/ask-feed")) {
        return { ok: true, json: async () => ({
          groups: ["반도체"],
          tickers: [{ market: "KR", ticker: "005930", name: "삼성전자", groups: ["반도체"] }],
          news_feed: [],
        }) };
      }
      return { ok: false, json: async () => ({}) };
    }));
    render(<CockpitEntry onPick={vi.fn()} />);
    fireEvent.click(await screen.findByTestId("grp-반도체"));
    expect(screen.getByTestId("tk-005930")).toBeTruthy();
  });

  it("capabilityChips: US는 13F, KR은 수급", () => {
    expect(capabilityChips("Apple", "US").some((c) => c.label.includes("13F"))).toBe(true);
    expect(capabilityChips("삼성전자", "KR").some((c) => c.label.includes("수급"))).toBe(true);
  });
});
