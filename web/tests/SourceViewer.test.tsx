// SourceViewer — DRV-3 stage precedence: a DERIVED figure's trust envelope is its math, so a
// data/metric citation carrying `computation` renders the DerivationCard as the stage EVEN WHEN
// an in-app source page exists (viewerSrc non-null); each input row's [원문↗] still opens that
// input's own filing cell with a "← 계산 과정으로" way back. Without computation the real source
// page (FilingViewer) stays the stage.
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { SourceViewer } from "../components/SourceViewer";
import type { Citation } from "../lib/types";

// FilingViewer fetches its src on mount — never let jsdom hit the network. Non-200 → the
// viewer's honest empty state (enough to prove WHICH stage rendered).
const fetchMock = vi.fn(async () => ({ status: 500, text: async () => "" }));
beforeEach(() => {
  fetchMock.mockClear();
  vi.stubGlobal("fetch", fetchMock);
});
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

const EVIDENCE_URL =
  "/evidence?market=US&accession=0000320193-25-000073&concept=EarningsPerShareDiluted&value=6.42&cik=0000320193";

// a derived figure: data kind + computation + a source page (viewerSrc would be non-null)
const DERIVED: Citation = {
  index: 2, kind: "data", source: "재무 스냅샷", ticker: "AAPL",
  snippet: "PER 32.79x", as_of: "2026-07-10", freshness: "fresh",
  evidence_image_url: EVIDENCE_URL,
  computation: {
    method: "PER (직접 계산)",
    formula: "PER = P ÷ EPS",
    inputs: [
      { label: "EPS (희석)", value: "6.42", source: "SEC EDGAR · 10-K", symbol: "EPS",
        evidence: { market: "US", accession: "0000320193-25-000073", concept: "EarningsPerShareDiluted", value: 6.42, cik: "0000320193" } },
    ],
    steps: [{ label: "PER", value: "32.79x" }],
  },
};

describe("SourceViewer — 계산 근거가 스테이지를 이긴다 (DRV-3)", () => {
  it("data + computation + evidence page → DerivationCard가 스테이지, FilingViewer 아님", () => {
    const { container } = render(<SourceViewer c={DERIVED} onClose={vi.fn()} />);
    expect(screen.getByTestId("derivation-card")).toBeInTheDocument();
    // the FilingViewer never mounts: no iframe, no fetch of the source page
    expect(container.querySelector("iframe")).toBeNull();
    expect(fetchMock).not.toHaveBeenCalled();
    expect(screen.queryByText(/원문 불러오는 중/)).toBeNull();
  });

  it('kind "metric"도 data 모양 → 동일하게 DerivationCard가 스테이지', () => {
    render(<SourceViewer c={{ ...DERIVED, kind: "metric" }} onClose={vi.fn()} />);
    expect(screen.getByTestId("derivation-card")).toBeInTheDocument();
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("computation 없는 data 인용은 여전히 원문 뷰어(FilingViewer) 경로", async () => {
    render(<SourceViewer c={{ ...DERIVED, computation: undefined }} onClose={vi.fn()} />);
    expect(screen.queryByTestId("derivation-card")).toBeNull();
    // FilingViewer mounted and fetched the in-app filing route (non-200 → honest empty state)
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(String(fetchMock.mock.calls[0][0])).toContain("/api/evidence/html?");
    expect(String(fetchMock.mock.calls[0][0])).toContain("accession=0000320193-25-000073");
    await screen.findByText(/앱 안에서 보여드릴 수 없어요/);
  });

  it("입력 행의 원문↗ → 스테이지가 그 입력의 뷰어로 스왑, ← 계산 과정으로 복귀", async () => {
    render(<SourceViewer c={DERIVED} onClose={vi.fn()} />);
    fireEvent.click(screen.getByTestId("dc-ev-0"));
    // the stage is now the INPUT's own filing viewer (fetches its /evidence cell page)…
    expect(screen.queryByTestId("derivation-card")).toBeNull();
    const back = screen.getByRole("button", { name: "← 계산 과정으로" });
    expect(back).toBeInTheDocument();
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(String(fetchMock.mock.calls[0][0])).toContain("/api/evidence/html?");
    await screen.findByText(/앱 안에서 보여드릴 수 없어요/);
    // …and the back button restores the derivation stage
    fireEvent.click(back);
    expect(screen.getByTestId("derivation-card")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "← 계산 과정으로" })).toBeNull();
  });
});
