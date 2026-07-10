import { proxyStudio } from "@/lib/studio";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

// RC-1: 질문 카드 탭 신호 — 유저별 kind 반응 분포로 피드가 재정렬된다(개인화).
export async function POST(req: Request) {
  return proxyStudio("/ask-feed/tap", { method: "POST", body: await req.text() });
}
