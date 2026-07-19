import { proxyStudio } from "@/lib/studio";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

export async function POST() {
  return proxyStudio("/users/onboarded", { method: "POST" });
}

// DEV-ONLY: 온보딩 다시 보기 — studio가 ENV=production이면 404로 막는다(하드 게이트).
export async function DELETE() {
  return proxyStudio("/users/onboarded", { method: "DELETE" });
}
