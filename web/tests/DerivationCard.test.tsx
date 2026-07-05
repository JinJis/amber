// M-DERIV (DRV-3) — the Derivation Card: symbol↔row binding, evidence deep-links,
// numbered steps with the final value emphasized, mandatory note, back-compat.
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { DerivationCard, derivationText, evidenceUrlOf } from "../components/DerivationCard";
import { ComputationPanel } from "../components/ComputationPanel";
import type { Computation } from "../lib/types";

afterEach(cleanup);

const COMP: Computation = {
  method: "시장가 × 최신 XBRL 라인아이템 (직접 계산)",
  formula: "시가총액 = P × S · PER = P ÷ EPS",
  inputs: [
    { label: "주가 P", value: "210.50", source: "가격 체인 (지연 시세)", symbol: "P" },
    { label: "EPS (희석)", value: "6.42", source: "SEC EDGAR · 10-K 2025-09-27", symbol: "EPS",
      evidence: { market: "US", accession: "0000320193-25-000073", concept: "EarningsPerShareDiluted", value: 6.42, cik: "0000320193" } },
  ],
  assumptions: [{ label: "할인율", value: "10%", symbol: "r" }],
  steps: [
    { label: "시가총액", value: "4.53T" },
    { label: "PER", value: "32.79x" },
  ],
  note: "주가는 지연 시세, 재무 입력은 각 최신 보고 기간",
};

describe("DerivationCard", () => {
  it("renders formula symbols as chips bound to their input rows (hover two-way)", () => {
    render(<DerivationCard comp={COMP} />);
    // "EPS" must match as its own chip even though "P" is a prefix of it (longest-first)
    expect(screen.getByTestId("dc-chip-EPS")).toHaveTextContent("EPS");
    fireEvent.mouseEnter(screen.getByTestId("dc-chip-EPS"));
    expect(screen.getByTestId("dc-row-input-1").className).toContain("hot");
    expect(screen.getByTestId("dc-row-input-0").className).not.toContain("hot");
  });

  it("input evidence opens through the handler with a valid /evidence URL", () => {
    const onEvidence = vi.fn();
    render(<DerivationCard comp={COMP} onEvidence={onEvidence} />);
    fireEvent.click(screen.getByTestId("dc-ev-1"));
    const [url, row] = onEvidence.mock.calls[0];
    expect(url).toContain("/evidence?");
    expect(url).toContain("accession=0000320193-25-000073");
    expect(url).toContain("concept=EarningsPerShareDiluted");
    expect(row.label).toBe("EPS (희석)");
    // the sourced-but-pageless input (price) offers no evidence button
    expect(screen.queryByTestId("dc-ev-0")).toBeNull();
  });

  it("numbers the steps and emphasizes the FINAL value; note is rendered", () => {
    render(<DerivationCard comp={COMP} />);
    expect(screen.getByText("①")).toBeInTheDocument();
    expect(screen.getByTestId("dc-final")).toHaveTextContent("PER");
    expect(screen.getByText("◀ 최종값")).toBeInTheDocument();
    expect(screen.getByTestId("dc-note")).toHaveTextContent("지연 시세");
  });

  it("derivationText quotes the whole derivation; evidenceUrlOf needs an accession", () => {
    const t = derivationText(COMP);
    expect(t).toContain("시가총액 = P × S");
    expect(t).toContain("EPS = EPS (희석): 6.42 (SEC EDGAR · 10-K 2025-09-27)");
    expect(t).toContain("② PER: 32.79x");
    expect(evidenceUrlOf({ market: "US" })).toBeNull();
  });

  it("back-compat: ComputationPanel without computation renders nothing", () => {
    const { container } = render(<ComputationPanel comp={null} />);
    expect(container.firstChild).toBeNull();
  });

  it("ComputationPanel unfolds into the Derivation Card", () => {
    render(<ComputationPanel comp={COMP} />);
    fireEvent.click(screen.getByRole("button", { name: /계산 근거/ }));
    expect(screen.getByTestId("derivation-card")).toBeInTheDocument();
  });
});
