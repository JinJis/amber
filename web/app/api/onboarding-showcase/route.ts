import { proxyStudio } from "@/lib/studio";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

// ONB-LIVE: 온보딩 라이브 쇼케이스(핫 KR 종목 실데이터, 일 1회 캐시) — 없으면 {} → 픽스처 폴백.
export async function GET() {
  return proxyStudio("/ask-feed/onboarding");
}
