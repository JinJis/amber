// SH-2b — share-card pure helpers: preset table, kind-aware line extraction, the non-removable
// provenance strip (history kinds always carry the label), and word-wrap.
import { describe, expect, it } from "vitest";
import { PRESETS, isHistoryKind, provenanceStrip, shareCardLines, wrap } from "../lib/shareCard";
import type { Artifact } from "../lib/types";

describe("shareCard helpers", () => {
  it("exposes the three aspect presets with sane pixel sizes", () => {
    expect(Object.keys(PRESETS)).toEqual(["1:1", "4:5", "16:9"]);
    expect(PRESETS["16:9"].w).toBeGreaterThan(PRESETS["16:9"].h);
    expect(PRESETS["4:5"].h).toBeGreaterThan(PRESETS["4:5"].w);
  });

  it("verdict lines carry the verdict, quoted claim and cited findings", () => {
    const a: Artifact = {
      kind: "verdict", title: "팩트체크", series: [], source: "공시 대조",
      verdict: {
        claim: "매출 5천억 넘었대", verdict: "사실과 다름",
        findings: [{ point: "실제 4,161억", supports: false, citation_idx: 1 }],
        corrected: "기록상 4,161억",
      },
    };
    const lines = shareCardLines(a);
    expect(lines[0]).toContain("사실과 다름");
    expect(lines.some((l) => l.includes("“매출 5천억 넘었대”"))).toBe(true);
    expect(lines.some((l) => l.includes("반박") && l.includes("[1]"))).toBe(true);
  });

  it("base_rates lines summarize the horizon table", () => {
    const a: Artifact = {
      kind: "base_rates", title: "베이스레이트", series: [], source: "derived",
      base_rates: { event: { text: "일간 -5%", spec: {} }, n: 60,
        horizons: [{ h: 20, n: 60, median: 2.8, p25: -3, p75: 9, min: -22, max: 24, pos_share: 63 }],
        event_dates: [], histogram: { h_ref: 20, bins: [] } },
    };
    const lines = shareCardLines(a);
    expect(lines[0]).toContain("n=60");
    expect(lines.some((l) => l.includes("20거래일 후") && l.includes("+2.8%") && l.includes("63%"))).toBe(true);
  });

  it("table artifacts flatten their matrix rows", () => {
    const a: Artifact = { kind: "table", title: "지표", series: [],
      table: [["지표", "값"], ["PER", "32.8x"]], source: "SEC EDGAR" };
    expect(shareCardLines(a)).toContain("PER  ·  32.8x");
  });

  it("the provenance strip is present, with the history label ONLY on history kinds", () => {
    const hist: Artifact = { kind: "base_rates", title: "t", series: [], source: "derived", as_of: "2026-07-02",
      base_rates: { event: { text: "x", spec: {} }, n: 1, horizons: [], event_dates: [], histogram: { h_ref: 0, bins: [] } } };
    const p = provenanceStrip(hist, "vg/s/aB3");
    expect(p.label).toBe("과거 기록 · 전망 아님");
    expect(p.source).toContain("SEC".length ? "출처" : "");
    expect(p.brand).toContain("ValueGraph · vg/s/aB3");
    expect(isHistoryKind(hist)).toBe(true);

    const table: Artifact = { kind: "table", title: "t", series: [], source: "SEC EDGAR", as_of: "2026-07-01", table: [["a", "b"]] };
    expect(provenanceStrip(table, "x").label).toBeNull();
    expect(isHistoryKind(table)).toBe(false);

    // a FUTURE verdict is descriptive-about-the-record → carries the label
    const fut: Artifact = { kind: "verdict", title: "t", series: [],
      verdict: { claim: "c", verdict: "미래 주장(검증 불가)", findings: [] } };
    expect(isHistoryKind(fut)).toBe(true);
  });

  it("quote lines carry the verbatim passage and doc title (SH-4)", () => {
    const a: Artifact = { kind: "quote", title: "SEC 인용", series: [],
      passage: "We face intense competition in all markets.", doc_title: "AAPL 10-K · 위험요소",
      source: "SEC EDGAR", as_of: "2024-11-01" };
    const lines = shareCardLines(a);
    expect(lines[0]).toContain("We face intense competition");
    expect(lines.some((l) => l.includes("AAPL 10-K"))).toBe(true);
    expect(isHistoryKind(a)).toBe(false);  // a quote is not a history kind
  });

  it("wrap breaks on words within the char budget", () => {
    expect(wrap("the quick brown fox", 9)).toEqual(["the quick", "brown fox"]);
    expect(wrap("single", 20)).toEqual(["single"]);
  });
});
