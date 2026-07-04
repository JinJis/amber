// IMP-8 — History Lab renderers: the HistoricalLabel is UNCONDITIONAL (ROADMAP §2 invariant),
// base-rate stats use record phrasing, and the analogue never draws an averaged path.
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import { AnalogueArtifact, BaseRatesArtifact } from "../components/HistoryArtifacts";
import type { Artifact } from "../lib/types";

afterEach(cleanup);

const BR: Artifact = {
  kind: "base_rates", title: "베이스레이트", series: [],
  base_rates: { event: { text: "일간 수익률 ≤ -5%", spec: { daily_return_lte: -5 } }, n: 60, raw_n: 87,
    horizons: [{ h: 20, n: 60, median: 2.81, p25: -3.2, p75: 8.9, min: -22, max: 24, pos_share: 60 }],
    event_dates: ["1929-10-28"], histogram: { h_ref: 20, bins: [{ lo: -22, hi: 0, count: 40 }] } },
  source: "derived", as_of: "2026-07-02",
};

const ANA: Artifact = {
  kind: "analogue", title: "유사 구간", series: [],
  analogue: { window: 3, anchor: "now", current: { label: "현재", path: [100, 98, 95] },
    matches: [{ ticker: "^GSPC", start_date: "2008-09-01", end_date: "2009-02-20", score: 0.91,
                path: [100, 97, 94], aftermath: [94, 96] }] },
  source: "derived", as_of: "2026-07-02",
};

describe("History Lab artifacts", () => {
  it("base_rates: label + record phrasing + enumerable event dates", () => {
    render(<BaseRatesArtifact a={BR} />);
    expect(screen.getByText(/과거 기록 · 전망 아님/)).toBeInTheDocument();  // mandatory, non-dismissable
    expect(screen.getByText(/상승 마감 비율\(과거\)/)).toBeInTheDocument(); // never "확률"
    expect(screen.getByText("1929-10-28")).toBeInTheDocument();
    expect(screen.getByText(/원자료 87건 군집화/)).toBeInTheDocument();
  });

  it("analogue: label + one path per match (no averaged/consensus path)", () => {
    const { container } = render(<AnalogueArtifact a={ANA} />);
    expect(screen.getByText(/과거 기록 · 전망 아님/)).toBeInTheDocument();
    // paths = 1 current + 1 match + 1 dashed aftermath — nothing synthetic added
    expect(container.querySelectorAll("svg path").length).toBe(3);
    expect(screen.getByText(/점선 = 그 뒤 실제로 일어난 일/)).toBeInTheDocument();
  });
});
