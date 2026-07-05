import { proxyStudio } from "@/lib/studio";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

// ASK-5: the 물어보기 entry feed — pre-generated per-ticker question pools + Hot Trend.
// One studio-api DB read; no LLM at request time.
export async function GET() {
  return proxyStudio("/ask-feed");
}
