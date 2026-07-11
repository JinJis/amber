// SH-2b — share-card pure helpers: preset table, kind-aware line extraction, the non-removable
// provenance strip (history kinds always carry the label), and word-wrap.
import { describe, expect, it } from "vitest";
import { OG, PRESETS, answerCardLines, isHistoryKind, ogCardForAnswer, ogCardForArtifact, plainText, provenanceStrip, shareCardLines, wrap } from "../lib/shareCard";
import type { Artifact } from "../lib/types";

describe("shareCard helpers", () => {
  it("exposes the three aspect presets with sane pixel sizes", () => {
    expect(Object.keys(PRESETS)).toEqual(["1:1", "4:5", "16:9"]);
    expect(PRESETS["16:9"].w).toBeGreaterThan(PRESETS["16:9"].h);
    expect(PRESETS["4:5"].h).toBeGreaterThan(PRESETS["4:5"].w);
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

  // SH-ANSWER — a whole-answer card strips markdown/markers and keeps only prose sentences.
  it("plainText strips figure markers, [n] refs and markdown syntax", () => {
    const md = "## 실적 요약\n삼성전자 영업이익은 **6.5조**였어요 [1]. {{figure:1}}\n- 전년比 개선";
    const flat = plainText(md);
    expect(flat).not.toContain("{{figure");
    expect(flat).not.toContain("[1]");
    expect(flat).not.toContain("**");
    expect(flat).not.toContain("##");
    expect(flat).toContain("삼성전자 영업이익은");
    expect(flat).toContain("6.5조");
  });

  it("answerCardLines yields bounded lead sentences", () => {
    const md = "첫 문장이에요. 둘째 문장이에요. 셋째 문장이에요. 넷째 문장이에요.";
    const lines = answerCardLines(md, 2);
    expect(lines).toHaveLength(2);
    expect(lines[0]).toContain("첫 문장");
    expect(answerCardLines("", 5)).toEqual([]);
  });

  // SH-OG — the link-unfurl card is the standard 1.91:1 ratio (nothing crops/breaks).
  it("OG dimensions are 1200×630 (1.91:1)", () => {
    expect(OG.W).toBe(1200);
    expect(OG.H).toBe(630);
    expect(OG.W / OG.H).toBeCloseTo(1.9, 1);
  });

  it("ogCardForAnswer dedupes cited sources and strips the body to prose", () => {
    const card = ogCardForAnswer({
      title: "삼성전자 이번 분기 실적",
      content: "## 요약\n영업이익 **6.5조** [1]. {{figure:1}}",
      citations: [
        { source: "DART", as_of: "2026-05-15", used: true, index: 1 },
        { source: "DART", as_of: "2026-05-10", index: 2 },     // dup source → one chip
        { source: "SEC EDGAR", as_of: "2026-04-01", index: 3 },
      ],
      artifacts: [{ kind: "base_rates", title: "x", series: [] }],
    });
    expect(card.sources).toEqual(["DART", "SEC EDGAR"]);
    expect(card.sourceCount).toBe(2);
    expect(card.as_of).toBe("2026-05-15");        // latest as_of
    expect(card.lead).not.toContain("##");
    expect(card.lead).not.toContain("{{figure");
    expect(card.history).toBe(true);              // a base_rates artifact ⇒ history label
  });

  it("ogCardForArtifact carries the artifact source + history flag", () => {
    const table: Artifact = { kind: "table", title: "지표", series: [], source: "SEC EDGAR",
      as_of: "2026-07-01", table: [["지표", "값"], ["PER", "32.8x"]] };
    const card = ogCardForArtifact(table);
    expect(card.sources).toEqual(["SEC EDGAR"]);
    expect(card.history).toBe(false);
    expect(card.lead).toContain("PER");
  });
});
