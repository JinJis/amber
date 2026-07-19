// ASK-6 탐구 엔트리 v8: ①관심 @그룹 칩 한 줄(종목 수 순·가로 스크롤 + ＋새그룹 우측 sticky) →
// 종목 칩 → 온디맨드 분석 카드 ②트렌드 보드 — 탭 + 단일 랭킹 리스트(서버 정렬 + 실측 🔥,
// 출처 버튼 없음).
// 전역 규칙 = 카드 탭은 컴포저 채움, 출처 탭은 근거 뷰어(자동 전송 없음). 미생성/실패 =
// 정직한 공백 (콘텐츠 날조 없음).
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
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
  { kind: "macro", question: "물가 흐름을 최근 추이로 같이 볼까요?", taps: 12,
    query: "미국 CPI 3.1%가 최근 물가 추이에서 어디쯤인지 살펴봐", hook: "미 CPI 3.1%",
    citations: [{ source: "FRED", url: "http://z", tool: "fred__macro_panel" }] },
  { kind: "market", question: "코스피 급등, 수급으로 같이 볼까요?", query: "코스피 급등 수급 살펴봐",
    hook: "코스피 +5.8%", citations: [{ source: "Yahoo Finance", url: "http://y", tool: "yahoo__asset_classes" }] },
  { kind: "micro", question: "반도체 뉴스 파볼까요?", query: "반도체 업황 뉴스 정리해줘",
    hook: "HBM 수주 보도", citations: [{ source: "Google News", url: "http://n", tool: "google_news__news" }] },
  { kind: "macro", question: "환율 흐름 같이 볼까요?", query: "원달러 환율 최근 추이 살펴봐",
    hook: "원달러 1,390원", citations: [{ source: "Yahoo Finance", url: "http://f", tool: "yahoo__asset_classes" }] },
];

// v6: studio가 내려주는 마키 섹션(스코프별 카드). Macro Trends 다음 어닝·거장·히스토리.
const SECTIONS = [
  { scope: "news_feed", cards: NEWS },
  { scope: "earnings_radar", cards: [
    { kind: "earnings_upcoming", question: "엔비디아 실적 발표 전에 서프라이즈 흐름 같이 볼까요?",
      query: "엔비디아 최근 8개 분기 컨센서스 대비 실제 EPS 서프라이즈를 정리해줘",
      hook: "엔비디아 다음 실적 발표 8월 28일", ticker: "NVDA", market: "US",
      citations: [{ source: "API Ninjas / FMP", url: "http://e", tool: "fmp__earnings_calendar" }] },
    { kind: "earnings_surprise", question: "애플 최근 비트/미스 패턴 짚어볼까요?", query: "애플 최근 분기 어닝 서프라이즈 히스토리 보여줘",
      hook: "애플 최근 4개 분기 연속 비트", ticker: "AAPL", market: "US",
      citations: [{ source: "API Ninjas / FMP", url: "http://e2", tool: "fmp__earnings_calendar" }] },
  ] },
  { scope: "guru_flows", cards: [
    { kind: "guru_move", question: "버핏이 새로 담은 종목 들여다볼까요?", query: "버핏이 지난 분기 새로 담은 종목의 재무·주가를 살펴봐",
      hook: "버핏, 지난 분기 신규 편입 1건",
      citations: [{ source: "SEC EDGAR", url: "http://g", tool: "sec_edgar__guru_trades" }] },
  ] },
  { scope: "history_lab", cards: [
    { kind: "vol_now", question: "지금 변동성이 과거 어디쯤인지 같이 볼까요?", query: "VIX가 과거 대비 지금 몇 퍼센타일인지 보여줘",
      hook: "지금 VIX는 과거 상위 20% 수준",
      citations: [{ source: "Market History", url: "http://h", tool: "market_history__vol_context" }] },
  ] },
];

function stubApis({ empty = false, tickerFail = false, fewNews = false, sections = null as unknown } = {}) {
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
        ...(sections ? { sections } : {}),
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

  it("그룹 칩은 종목 수 큰 순으로 정렬되고, 빈 그룹은 홈에서 숨긴다", async () => {
    vi.stubGlobal("fetch", vi.fn(async (url: string) => {
      const u = String(url);
      if (u.includes("/api/ask-feed")) {
        return { ok: true, json: async () => ({
          groups: [
            { id: "g-semi", name: "반도체" },     // 1종목
            { id: "g-big", name: "빅테크" },      // 2종목 → 맨 앞
            { id: "g-empty", name: "빈그룹" },    // 0종목 → 숨김
          ],
          tickers: [
            { market: "KR", ticker: "005930", name: "삼성전자", groups: ["반도체"] },
            { market: "US", ticker: "NVDA", name: "NVIDIA", groups: ["빅테크"] },
            { market: "US", ticker: "MSFT", name: "Microsoft", groups: ["빅테크"] },
          ],
          news_feed: [],
        }) };
      }
      return { ok: false, json: async () => ({}) };
    }));
    render(<CockpitEntry onPick={vi.fn()} />);
    const big = await screen.findByTestId("grp-빅테크");
    const semi = screen.getByTestId("grp-반도체");
    // 종목 수 내림차순: 빅테크(2)가 반도체(1)보다 앞
    expect(big.compareDocumentPosition(semi) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(screen.queryByTestId("grp-빈그룹")).toBeNull();          // 빈 그룹 숨김
  });

  it("트렌드 보드: 탭 + 단일 리스트 — 정적(한 벌), 행 탭 → 실행용 query가 컴포저로", async () => {
    stubApis();
    const onPick = vi.fn(); const onQuestions = vi.fn();
    render(<CockpitEntry onPick={onPick} onQuestions={onQuestions} />);
    const board = await screen.findByTestId("ck-board");
    expect(board.textContent).toContain("지금 뜨는 질문");
    expect(screen.getByTestId("ck-news").textContent).toContain("Macro Trends");   // 탭
    const panel = screen.getByTestId("ck-board-panel");
    expect(panel.textContent).toContain("미 CPI 3.1%");
    expect(board.textContent).toContain("5분마다 갱신");
    expect(document.querySelector(".mq-track")).toBeNull();       // 마키 없음
    expect(panel.querySelector(".tb-src, .qc-src")).toBeNull();   // 출처 버튼 없음 (삭제됨)
    expect(screen.getAllByText(/물가 흐름을 최근 추이로/).length).toBe(1);   // 한 벌
    fireEvent.click(screen.getAllByText(/물가 흐름을 최근 추이로/)[0]);
    // F3: 컴포저에는 주체가 포함된 실행용 query가 들어간다 (표시용 question이 아니라)
    expect(onPick).toHaveBeenCalledWith("미국 CPI 3.1%가 최근 물가 추이에서 어디쯤인지 살펴봐");
    await waitFor(() => expect(onQuestions).toHaveBeenCalledWith(NEWS.map((c) => c.question)));
  });

  it("탭 전환: 다른 섹션 탭 → 그 섹션 리스트로 즉시 교체 (재요청 없음)", async () => {
    stubApis({ sections: SECTIONS });
    render(<CockpitEntry onPick={vi.fn()} />);
    const panel = await screen.findByTestId("ck-board-panel");
    expect(panel.textContent).toContain("물가 흐름");                  // 기본 = 첫 섹션(Macro)
    const before = (globalThis.fetch as any).mock.calls.length;
    fireEvent.click(screen.getByTestId("ck-sec-guru_flows"));          // 거장·수급 탭
    expect(screen.getByTestId("ck-board-panel").textContent).toContain("버핏이 새로 담은 종목");
    expect(screen.getByTestId("ck-board-panel").textContent).not.toContain("물가 흐름");
    expect((globalThis.fetch as any).mock.calls.length).toBe(before);  // 전환은 로컬 — 추가 fetch 0
  });

  it("랭킹: 순위 번호(상위 3 강조)와 실측 🔥 탭 수는 있을 때만 보인다", async () => {
    stubApis();
    render(<CockpitEntry onPick={vi.fn()} />);
    const panel = await screen.findByTestId("ck-board-panel");
    const ranks = [...panel.querySelectorAll(".tt-rank")].map((el) => el.textContent);
    expect(ranks.slice(0, 4)).toEqual(["1", "2", "3", "4"]);      // 서버 정렬 순서 그대로 순위
    expect(panel.querySelectorAll(".tt-rank.hot").length).toBe(3); // 상위 3 강조
    const taps = screen.getAllByTestId("tt-taps");                 // 🔥 수치 — taps>0인 카드만
    expect(taps.length).toBe(1);
    expect(taps[0].textContent).toContain("12");
    expect(taps[0].getAttribute("title")).toContain("최근 7일");
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

  it("카드가 적어도 보드는 그대로 정적 (복제·흐름·더보기 없음)", async () => {
    stubApis({ fewNews: true });
    render(<CockpitEntry onPick={vi.fn()} />);
    await screen.findByTestId("ck-board-panel");
    expect(screen.getAllByText(/물가 흐름을 최근 추이로/).length).toBe(1);
    expect(screen.queryByTestId("ck-board-more")).toBeNull();
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

  it("관심 그룹은 칩으로 나열된다 — 풀폭 행 아님(.eg-chips 안의 .eg-chip)", async () => {
    stubApis();
    render(<CockpitEntry onPick={vi.fn()} />);
    const chip = await screen.findByTestId("grp-반도체");
    const chips = chip.closest(".eg-chips");
    expect(chips).toBeTruthy();                                   // 칩 컨테이너(가로 wrap)
    expect(chip.className).toContain("eg-chip");
    // ＋ 새 그룹도 같은 칩 줄 안, 칩 스타일(풀폭 아님)
    const add = screen.getByTestId("eg-add");
    expect(add.closest(".eg-chips")).toBe(chips);
    expect(add.className).toContain("eg-add-chip");
    // 접힌 상태에서는 종목 칩이 없다(패널 미노출)
    expect(screen.queryByTestId("tk-005930")).toBeNull();
  });

  it("보드 탭: Macro Trends 뒤로 어닝·거장·히스토리가 순서대로", async () => {
    stubApis({ sections: SECTIONS });
    render(<CockpitEntry onPick={vi.fn()} />);
    const news = await screen.findByTestId("ck-news");
    const earn = await screen.findByTestId("ck-sec-earnings_radar");
    const guru = await screen.findByTestId("ck-sec-guru_flows");
    const hist = await screen.findByTestId("ck-sec-history_lab");
    expect(earn.textContent).toContain("어닝 레이더");
    expect(guru.textContent).toContain("투자거장·수급");
    expect(hist.textContent).toContain("히스토리 랩");
    fireEvent.click(hist);                                          // 히스토리 탭 활성화
    expect(screen.getByTestId("ck-board").textContent).toContain("과거 기록 · 전망 아님");  // 무전망 브랜드(캐던스 배지)
    // DOM 순서: news → earnings → guru → history
    const after = (a: Element, b: Element) => a.compareDocumentPosition(b) & Node.DOCUMENT_POSITION_FOLLOWING;
    expect(after(news, earn)).toBeTruthy();
    expect(after(earn, guru)).toBeTruthy();
    expect(after(guru, hist)).toBeTruthy();
  });

  it("섹션 카드 탭 → 실행용 query가 컴포저로 (표시용 question 아님)", async () => {
    stubApis({ sections: SECTIONS });
    const onPick = vi.fn();
    render(<CockpitEntry onPick={onPick} />);
    fireEvent.click(await screen.findByTestId("ck-sec-earnings_radar"));   // 어닝 탭으로
    fireEvent.click(screen.getAllByText(/서프라이즈 흐름 같이 볼까요/)[0]);
    expect(onPick).toHaveBeenCalledWith("엔비디아 최근 8개 분기 컨센서스 대비 실제 EPS 서프라이즈를 정리해줘");
  });

  it("리스트는 상위 8개만 접어서 보여주고, 더 보기로 전체(≤20)를 펼친다", async () => {
    const many = Array.from({ length: 11 }, (_, i) => ({
      kind: "macro", question: `심층 질문 ${i}번을 같이 볼까요?`, query: `질문 ${i} 살펴봐`,
      hook: `훅 ${i}`, citations: [{ source: "FRED", url: "http://z", tool: "fred__macro_panel" }],
    }));
    stubApis({ sections: [{ scope: "news_feed", cards: many }] });
    render(<CockpitEntry onPick={vi.fn()} />);
    const panel = await screen.findByTestId("ck-board-panel");
    expect(panel.querySelectorAll(".tt-row").length).toBe(8);        // 접힘: 상위 8
    const more = screen.getByTestId("ck-board-more");
    expect(more.textContent).toContain("3개 더 보기");
    fireEvent.click(more);
    expect(panel.querySelectorAll(".tt-row").length).toBe(11);       // 펼침: 전체
    const ranks = [...panel.querySelectorAll(".tt-rank")].map((el) => el.textContent);
    expect(ranks[10]).toBe("11");                                    // 순위 연속
    fireEvent.click(more);                                           // 접기
    expect(panel.querySelectorAll(".tt-row").length).toBe(8);
  });

  it("모르는 스코프의 섹션은 건너뛴다", async () => {
    stubApis({ sections: [{ scope: "news_feed", cards: NEWS }, { scope: "made_up_scope", cards: NEWS }] });
    render(<CockpitEntry onPick={vi.fn()} />);
    await screen.findByTestId("ck-news");
    expect(screen.queryByTestId("ck-sec-made_up_scope")).toBeNull();
  });
});
