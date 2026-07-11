// FilingViewer — the honest empty state: when the real page can't render in-app (fetch non-200:
// 문서 미확보·스크립트 전용·유료 기사 …) we still show the cited passage, say plainly that the
// page can't be shown in-app (hostname named when parseable), and hand off to the 원문 link.
// The needle-normalization used for DOM highlighting is internal to the component (not exported)
// — per the harness rules we don't restructure it for testability, so highlighting itself is
// exercised only through the empty-state / stage-selection behavior here.
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { FilingViewer, evidenceHtmlSrc, viewerSrc } from "../components/FilingViewer";
import type { Citation } from "../lib/types";

const fetchMock = vi.fn(async () => ({ status: 402, text: async () => "" }));
beforeEach(() => {
  fetchMock.mockClear();
  vi.stubGlobal("fetch", fetchMock);
});
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("FilingViewer — 정직한 빈 상태 (non-200)", () => {
  it("외부 기사: 인용 구절 + '앱 안에서 보여드릴 수 없어요'(호스트명 포함) + 원문 보기 ↗", async () => {
    const c: Citation = {
      kind: "news", source: "뉴스", url: "https://www.newsroom.example.com/apple-earnings",
      snippet: "애플 4분기 매출은 서비스 부문이 견인했다",
    };
    render(<FilingViewer c={c} />);
    await screen.findByText(/앱 안에서 보여드릴 수 없어요/);
    // the honest copy names the host (www. stripped) and points at the original
    expect(screen.getByText(/newsroom\.example\.com 페이지는 앱 안에서 보여드릴 수 없어요/)).toBeInTheDocument();
    expect(screen.getByText(/“애플 4분기 매출은 서비스 부문이 견인했다”/)).toBeInTheDocument();
    const link = screen.getByRole("link", { name: /원문 보기 ↗/ });
    expect(link).toHaveAttribute("href", "https://www.newsroom.example.com/apple-earnings");
    // the fetch went through the SSRF-safe sanitizing route, never the raw url
    expect(String(fetchMock.mock.calls[0][0])).toBe(
      `/api/evidence/url?u=${encodeURIComponent(c.url!)}`);
  });

  it("공시(원본 url 없음): 일반 문구로 폴백, 원문 링크 없음", async () => {
    const c: Citation = {
      kind: "filing", source: "SEC EDGAR",
      evidence_image_url: "/evidence?market=US&accession=0000320193-25-000073&cik=0000320193",
      snippet: "Total net sales 391,035",
    };
    render(<FilingViewer c={c} />);
    await screen.findByText(/이 문서는 지금 앱 안에서 보여드릴 수 없어요/);
    expect(screen.getByText(/“Total net sales 391,035”/)).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: /원문 보기 ↗/ })).toBeNull();
    expect(String(fetchMock.mock.calls[0][0])).toContain("/api/evidence/html?");
  });

  it("보여줄 src가 아예 없으면 fetch 없이 곧장 빈 상태", () => {
    render(<FilingViewer c={{ kind: "data", source: "표", snippet: "값 42" }} />);
    expect(fetchMock).not.toHaveBeenCalled();
    expect(screen.getByText(/이 문서는 지금 앱 안에서 보여드릴 수 없어요/)).toBeInTheDocument();
  });
});

describe("viewerSrc / evidenceHtmlSrc (스테이지 선택이 기대는 순수 매핑)", () => {
  it("filing 파라미터가 있으면 /api/evidence/html, 아니면 외부 페이지는 /api/evidence/url", () => {
    expect(evidenceHtmlSrc({ evidence_image_url: "/evidence?market=US&accession=A1&cik=99" }))
      .toBe("/api/evidence/html?market=US&accession=A1&cik=99");
    expect(viewerSrc({ url: "https://x.test/a?b=1" }))
      .toBe(`/api/evidence/url?u=${encodeURIComponent("https://x.test/a?b=1")}`);
    expect(viewerSrc({ snippet: "숫자만" })).toBeNull();
  });

  it("US filing인데 cik이 빠지면 SEC 원문 url에서 복원한다", () => {
    const src = evidenceHtmlSrc({
      evidence_image_url: "/evidence?market=US&accession=0000320193-25-000073",
      url: "https://www.sec.gov/Archives/edgar/data/320193/000032019325000073/aapl-20250927.htm",
    });
    expect(src).toContain("cik=320193");
  });
});
