// 온보딩 v3 프리뷰 픽스처 — 진짜 컴포넌트에 물리는 "예시 화면"용 가짜 데이터.
// 실제 서비스가 그리는 근거 카드·분석거리 카드·후속 질문의 모양 그대로, 데이터만 샘플.
// (프리뷰에는 항상 "예시 화면" 배지가 붙는다 — 무날조 원칙.)

import type { Citation } from "./types";
import type { TrustSummary } from "./evidence";
import type { AskCard } from "@/components/QCard";

// 근거 패널에 뜨는 3가지 카드 모양: 공시 문서 · 뉴스 원문 · 추출 데이터 표
export const FIX_CITATIONS: Citation[] = [
  {
    index: 1, kind: "filing", source: "SEC EDGAR · 10-Q", doc_type: "10-Q · Item 2. MD&A",
    page: "p.34", snippet: "Total revenue increased 18% to $35.1 billion, driven by …",
    as_of: "2026-05-01", freshness: "fresh", cadence: "event",
  },
  {
    index: 2, kind: "news", source: "Reuters", url: "https://www.reuters.com/technology/",
    snippet: "chipmaker posted record quarterly revenue, beating estimates",
    as_of: "2026-07-04", freshness: "fresh", cadence: "intraday",
  },
  {
    index: 3, kind: "metric", source: "재무제표 추출", ticker: "005930",
    table: [["분기", "매출", "YoY"], ["2026 Q1", "79.1조", "+12%"], ["2025 Q4", "75.8조", "+9%"]],
    as_of: "2026-05-15", freshness: "fresh", cadence: "scheduled",
  },
];

// 판정 스트립: 답변 속 모든 수치가 원자료와 대조 확인된 상태
export const FIX_TRUST: TrustSummary = {
  checked: 3, unsupported: 0, sources: 3,
  freshness: { fresh: 3, aging: 0, stale: 0 },
  allClear: true, conceptual: false,
};

// 종목 탭 → 큐레이션된 분석거리 카드 (서로 다른 각도)
export const FIX_QCARDS: AskCard[] = [
  {
    kind: "filing_deep",
    question: "새로 올라온 공시에 바뀐 위험요소가 있는지 들여다볼까요?",
    query: "삼성전자(005930) 최근 공시에서 바뀐 위험요소를 원문과 함께 살펴봐",
    hook: "7/4 새 공시가 접수됐어요", citations: [FIX_CITATIONS[0]],
  },
  {
    kind: "valuation",
    question: "지금 밸류에이션이 과거 밴드에서 어디쯤인지 같이 볼까요?",
    query: "삼성전자(005930) 밸류에이션이 과거 밴드에서 어디쯤인지 살펴봐",
    hook: "PER이 최근 5년 중앙값 아래예요", citations: [FIX_CITATIONS[2]],
  },
  {
    kind: "ownership",
    question: "외국인이 5일째 순매수 중인데, 수급 흐름을 살펴볼까요?",
    query: "삼성전자(005930) 외국인·기관 수급 흐름을 최근 데이터로 살펴봐",
    hook: "외국인 5일 연속 순매수", citations: [FIX_CITATIONS[1]],
  },
];

// 답변 뒤에 이어지는 후속 질문 (꼬리물기 리서치)
export const FIX_FOLLOWUPS: string[] = [
  "이 매출 성장이 경쟁사와 비교하면 어느 정도인지 볼까요?",
  "재고자산이 전분기 대비 어떻게 움직였는지 파볼까요?",
  "과거 비슷한 실적 서프라이즈 뒤 주가는 어땠는지 기록을 볼까요?",
];
