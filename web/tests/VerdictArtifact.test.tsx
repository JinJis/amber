// M-FACT (FC-2) — the verdict card: cited findings gate the render (an uncited receipt is
// worse than none), the future verdict renders without findings, corrected record shown.
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import { VerdictArtifact } from "../components/VerdictArtifact";
import type { Artifact } from "../lib/types";

afterEach(cleanup);

const base: Artifact = {
  kind: "verdict", title: "팩트체크 — 사실과 다름", series: [],
  source: "공시·재무제표 대조", as_of: "2026-07-05",
  verdict: {
    claim: "삼성전자 이번 분기 영업이익 15조 넘었대",
    verdict: "사실과 다름", confidence: "high",
    findings: [
      { point: "2026 Q1 영업이익은 12.1조원으로 공시됨", supports: false, citation_idx: 1 },
      { point: "잠정실적 공정공시도 동일 수치", supports: false, citation_idx: 2 },
    ],
    corrected: "기록상 2026 Q1 영업이익은 12.1조원 (공시 기준)",
    method: "공시·재무제표 대조",
  },
};

describe("VerdictArtifact", () => {
  it("renders the verdict chip, quoted claim, cited findings and corrected record", () => {
    render(<VerdictArtifact a={base} />);
    expect(screen.getByTestId("verdict-chip")).toHaveTextContent("✕ 사실과 다름");
    expect(screen.getByTestId("verdict-chip")).toHaveTextContent("근거 강함");
    expect(screen.getByText(/삼성전자 이번 분기 영업이익 15조/)).toBeInTheDocument();
    expect(screen.getAllByText("반박")).toHaveLength(2);      // both findings contradict
    expect(screen.getByText("[1]")).toBeInTheDocument();       // anchors visible
    expect(screen.getByText(/기록상 사실/)).toBeInTheDocument();
    expect(screen.getByText(/12.1조원 \(공시 기준\)/)).toBeInTheDocument();
  });

  it("REFUSES to render a scored verdict with zero cited findings", () => {
    const a = { ...base, verdict: { ...base.verdict!, findings: [] } } as Artifact;
    const { container } = render(<VerdictArtifact a={a} />);
    expect(container.firstChild).toBeNull();
  });

  it("renders the future verdict without findings — and never scores it", () => {
    const a = {
      ...base,
      verdict: { claim: "다음 달 급등한대", verdict: "미래 주장(검증 불가)", findings: [] },
    } as Artifact;
    render(<VerdictArtifact a={a} />);
    expect(screen.getByTestId("verdict-chip")).toHaveTextContent("⏳ 미래 주장(검증 불가)");
    expect(screen.getByText(/기록으로 검증할 수 없습니다/)).toBeInTheDocument();
    expect(screen.queryByText("지지")).toBeNull();
  });

  it("ignores an unknown verdict value entirely", () => {
    const a = { ...base, verdict: { ...base.verdict!, verdict: "아마도" } } as Artifact;
    const { container } = render(<VerdictArtifact a={a} />);
    expect(container.firstChild).toBeNull();
  });
});
