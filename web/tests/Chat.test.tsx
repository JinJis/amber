// Chat — toCitation: the SSE citation event → Citation mapper. A whitelist mapper silently
// drops what it doesn't name; that's exactly how computation/confidence(+why) got lost and
// the 계산 근거 DerivationCard + 신뢰 badge never rendered on LIVE turns (only on reload,
// where the persisted JSON is used verbatim). These tests pin every field the UI depends on.
import { describe, expect, it } from "vitest";
import { toCitation } from "../components/Chat";
import type { Computation } from "../lib/types";

const COMP: Computation = {
  method: "PER (직접 계산)",
  formula: "PER = P ÷ EPS",
  inputs: [
    { label: "주가 P", value: "210.50", source: "가격 체인 (지연 시세)", symbol: "P" },
    { label: "EPS (희석)", value: "6.42", source: "SEC EDGAR · 10-K", symbol: "EPS",
      evidence: { market: "US", accession: "0000320193-25-000073", concept: "EarningsPerShareDiluted", value: 6.42, cik: "0000320193" } },
  ],
};

// a full streamed `citation` SSE event (the `done` list rides the same shape)
const EVENT = {
  type: "citation",                       // transport field — NOT part of a Citation
  tool: "sec_edgar__financials",
  source: "SEC EDGAR",
  url: "https://www.sec.gov/Archives/edgar/data/320193/10-K.htm",
  index: 2,
  kind: "data",
  doc_type: "10-K",
  as_of: "2025-09-27",
  freshness: "fresh",
  cadence: "event",
  category: "fundamentals",
  snippet: "PER 32.79x",
  ticker: "AAPL",
  page: "F-3",
  table: [["항목", "값"], ["EPS", "6.42"]],
  evidence_image_url: "/evidence?market=US&accession=0000320193-25-000073&concept=EarningsPerShareDiluted&value=6.42&cik=0000320193",
  used: true,
  computation: COMP,
  confidence: "high",
  confidence_why: "두 출처의 수치가 일치해요",
};

describe("toCitation (SSE event → Citation)", () => {
  it("preserves computation + confidence + confidence_why (계산 근거 카드·신뢰 배지의 생명줄)", () => {
    const c = toCitation(EVENT);
    expect(c.computation).toEqual(COMP);
    expect(c.computation?.inputs?.[1].evidence?.accession).toBe("0000320193-25-000073");
    expect(c.confidence).toBe("high");
    expect(c.confidence_why).toBe("두 출처의 수치가 일치해요");
  });

  it("still maps all 16 pre-existing fields 1:1", () => {
    const c = toCitation(EVENT);
    expect(c).toMatchObject({
      tool: "sec_edgar__financials",
      source: "SEC EDGAR",
      url: "https://www.sec.gov/Archives/edgar/data/320193/10-K.htm",
      index: 2,
      kind: "data",
      doc_type: "10-K",
      as_of: "2025-09-27",
      freshness: "fresh",
      cadence: "event",
      category: "fundamentals",
      snippet: "PER 32.79x",
      ticker: "AAPL",
      page: "F-3",
      table: [["항목", "값"], ["EPS", "6.42"]],
      evidence_image_url: "/evidence?market=US&accession=0000320193-25-000073&concept=EarningsPerShareDiluted&value=6.42&cik=0000320193",
      used: true,
    });
  });

  it("is a whitelist — transport-only fields don't leak into the Citation", () => {
    const c = toCitation(EVENT) as Record<string, unknown>;
    expect("type" in c && c.type !== undefined).toBe(false);
  });

  it("an event without the derivation trio maps them as undefined (back-compat)", () => {
    const c = toCitation({ tool: "t", source: "s", index: 1, kind: "news" });
    expect(c.computation).toBeUndefined();
    expect(c.confidence).toBeUndefined();
    expect(c.confidence_why).toBeUndefined();
  });
});
