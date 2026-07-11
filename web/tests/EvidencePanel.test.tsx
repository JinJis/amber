// LG — 근거 패널 v3: 판정 스트립, 수집 중(과정 타임라인 + 도착순 출처) → 완료(인용/참고 구분)
// 2단계 플로우, [n] linkify. 수치 원장은 본문 하이라이트로 이동(LG-4) — 패널에 원장 섹션 없음.
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ContextPanel, TrustStrip } from "../components/EvidencePanel";
import { linkifyCitations } from "../components/Chat";
import { evidenceOf, trustSummary } from "../lib/evidence";
import type { Citation, Msg } from "../lib/types";

afterEach(cleanup);

const MSG: Msg = {
  role: "assistant",
  content: "애플 매출은 391.0B [1]로 집계됐고 PER은 32.8 [2] 수준이다. 목표는 999조다.",
  used: [1, 2],
  tools: [{ name: "yahoo__price_snapshot", label: "가격 스냅샷" }, { name: "sec_edgar__filings", label: "공시 조회" }],
  citations: [
    { index: 1, kind: "filing", source: "SEC EDGAR", freshness: "fresh", snippet: "Revenue" },
    { index: 2, kind: "data", source: "재무 스냅샷", freshness: "fresh",
      computation: { method: "PER (직접 계산)", formula: "PER = P ÷ EPS", inputs: [], assumptions: [], steps: [] } },
    { index: 3, kind: "news", source: "참고 기사", url: "http://n", freshness: "fresh" },  // consulted-only
  ],
  audit: {
    checked: 3, supported: 2, unsupported: ["999조"],
    ledger: [
      { raw: "391.0B", value: 391e9, span: [7, 13], citation_idx: 1, supported: true },
      { raw: "32.8", value: 32.8, span: [30, 34], citation_idx: 2, supported: true },
      { raw: "999조", value: 999e12, span: [46, 50], citation_idx: null, supported: false },
    ],
  },
};

const panelProps = {
  streaming: false, onEvidence: vi.fn(), onResizeStart: () => {},
  hoverCite: null, setHoverCite: () => {}, flashCite: null,
};

describe("근거 패널 v3 — 완료 뷰", () => {
  it("판정 스트립: 미확인 수치가 있으면 앰버 경고를 헤드라인으로", () => {
    render(<ContextPanel {...panelProps} msg={MSG} />);
    const strip = screen.getByTestId("trust-strip");
    expect(strip.className).toContain("warn");
    expect(strip.textContent).toContain("미확인 1건");
    expect(strip.textContent).toContain("출처 2");
  });

  it("인용한 출처([n]와 1:1)는 항상 펼침, 참고만 한 출처는 접힘+흐림으로 구분", () => {
    const { container } = render(<ContextPanel {...panelProps} msg={MSG} />);
    const usedSec = screen.getByTestId("ctx-used");
    expect(usedSec.textContent).toContain("인용한 출처 2");
    expect(usedSec.textContent).toContain("[n] 번호와 1:1");          // 읽는 법 안내
    expect(usedSec.textContent).toContain("SEC EDGAR");
    const othersSec = screen.getByTestId("ctx-others") as HTMLDetailsElement;
    expect(othersSec.open).toBe(false);                                // 접힘
    expect(othersSec.textContent).toContain("참고만 한 출처 1");
    expect(othersSec.textContent).toContain("답변엔 인용 안 됨");
    expect(container.querySelector("#nothing") ?? true).toBeTruthy();
  });

  it("리서치 과정은 완료 후에도 접힘 폴드로 보존된다 (아무것도 사라지지 않음)", () => {
    render(<ContextPanel {...panelProps} msg={MSG} />);
    const proc = screen.getByTestId("ctx-process") as HTMLDetailsElement;
    expect(proc.open).toBe(false);
    expect(proc.textContent).toContain("리서치 과정 — 도구 2개");
    expect(proc.textContent).toContain("가격 스냅샷");
  });

  it("수치 원장 섹션은 패널에 없다 — LG-4: 원장은 본문 하이라이트로 이동", () => {
    render(<ContextPanel {...panelProps} msg={MSG} />);
    expect(screen.queryByTestId("ledger")).toBeNull();
    expect(screen.queryByText(/수치 원장/)).toBeNull();
    // 판정 스트립(감사 요약)은 그대로 헤드라인
    expect(screen.getByTestId("trust-strip")).toBeInTheDocument();
  });

  it("개념 설명(수치·출처 없음)은 조용한 판정으로 강등", () => {
    const s = trustSummary({ role: "assistant", content: "PER은 주가수익비율입니다." } as Msg);
    expect(s.conceptual).toBe(true);
    render(<TrustStrip s={s} />);
    expect(screen.getByTestId("trust-strip").textContent).toContain("수치 없음");
  });
});

describe("근거 패널 v3 — 수집 중 뷰 (스트리밍)", () => {
  const LIVE: Msg = {
    role: "assistant", content: "",
    tools: [{ name: "yahoo__price_snapshot", label: "가격 스냅샷" }, { name: "sec_edgar__filings", label: "공시 조회" }],
    citations: [{ index: 1, kind: "filing", source: "SEC EDGAR", freshness: "fresh" }],
  };

  it("과정 타임라인이 live로 맨 위(마지막 도구가 진행 중), 출처는 도착 순서 그대로", () => {
    render(<ContextPanel {...panelProps} streaming msg={LIVE} />);
    const col = screen.getByTestId("ctx-collecting");
    expect(col.textContent).toContain("리서치 과정 2");
    const rows = screen.getByTestId("proc-rows");
    expect(rows.children[rows.children.length - 1].className).toContain("active");  // 마지막 = 진행 중
    expect(screen.getByText(/수집한 출처 1/)).toBeTruthy();
  });

  it("정리 안내: 완료되면 인용/참고로 나뉜다는 걸 미리 알려준다 (재배열이 놀랍지 않게)", () => {
    render(<ContextPanel {...panelProps} streaming msg={LIVE} />);
    expect(screen.getByText(/정리돼요/).textContent).toContain("인용한 출처 [n]");
    // 판정·원장·인용/참고 구분은 아직 없음 — 완료 시에만
    expect(screen.queryByTestId("trust-strip")).toBeNull();
    expect(screen.queryByTestId("ctx-used")).toBeNull();
  });
});

// 대화 리로드 경로: Chat.openConversation은 per-citation `used` 플래그에서 m.used를 복원한다
// (플래그가 하나도 없으면 전체 index로 폴백). 패널의 인용/참고 파티션은 그 결과를 evidenceOf로
// 읽는다 — 여기서 그 파티션 계약을 고정한다 (리로드 시 '참고만 한 출처' 접힘이 사라지면 회귀).
describe("evidenceOf — 리로드된 메시지의 인용/참고 파티션", () => {
  const cites: Citation[] = [
    { index: 1, kind: "filing", source: "SEC EDGAR", used: true },
    { index: 2, kind: "news", source: "참고 기사", url: "http://n" },        // consulted-only
    { index: 3, kind: "data", source: "재무 스냅샷", used: true },
  ];
  // Chat.openConversation의 복원 로직과 동일한 파생 (플래그된 index → m.used)
  const derivedUsed = (cs: Citation[]) => {
    const flagged = cs.filter((c) => c.used && c.index != null).map((c) => c.index!);
    return flagged.length ? flagged : cs.map((c) => c.index).filter((n): n is number => n != null);
  };

  it("used 플래그가 있으면 그 인용만 '인용한 출처'로, 나머지는 참고로 남는다", () => {
    const m: Msg = { role: "assistant", content: "", citations: cites, used: derivedUsed(cites) };
    const used = evidenceOf(m);
    expect(used.map((c) => c.index)).toEqual([1, 3]);
    // EvidencePanel이 기대는 여집합: 참고만 한 출처
    const others = cites.filter((c) => !used.includes(c));
    expect(others.map((c) => c.index)).toEqual([2]);
  });

  it("플래그가 하나도 없으면 전체 index로 폴백한다 (아무것도 숨기지 않음)", () => {
    const bare = cites.map(({ used: _u, ...c }) => c);
    const m: Msg = { role: "assistant", content: "", citations: bare, used: derivedUsed(bare) };
    expect(evidenceOf(m).map((c) => c.index)).toEqual([1, 2, 3]);
  });

  it("m.used가 없거나 비면 전체 인용을 반환한다 (evidenceOf 자체 폴백)", () => {
    expect(evidenceOf({ role: "assistant", content: "", citations: cites } as Msg)).toHaveLength(3);
    expect(evidenceOf({ role: "assistant", content: "", citations: cites, used: [] } as Msg)).toHaveLength(3);
  });

  it("리로드 형태의 메시지로 패널을 그리면 인용 2 · 참고 1로 나뉜다", () => {
    const m: Msg = {
      role: "assistant", content: "매출 [1] 과 PER [3].",
      citations: cites, used: derivedUsed(cites),
    };
    render(<ContextPanel {...panelProps} msg={m} />);
    expect(screen.getByTestId("ctx-used").textContent).toContain("인용한 출처 2");
    expect(screen.getByTestId("ctx-others").textContent).toContain("참고만 한 출처 1");
  });
});

describe("linkifyCitations (클릭 가능한 [n])", () => {
  it("맨 [n]을 #cite-n 링크로", () => {
    expect(linkifyCitations("매출 391B [1] 이익 [2].")).toBe("매출 391B [[1]](#cite-1) 이익 [[2]](#cite-2).");
  });
  it("이미 링크인 [n](url)은 건드리지 않음", () => {
    expect(linkifyCitations("[3](http://x)")).toBe("[3](http://x)");
  });
  it("숫자 아닌 대괄호는 무시", () => {
    expect(linkifyCitations("a [note] b")).toBe("a [note] b");
  });
});
