import { proxyStudio } from "@/lib/studio";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

// ASK-6: the 물어보기 entry feed — watchlist tickers (names+groups) + the background
// news_feed question cards. One studio-api DB read; no LLM at request time.
export async function GET() {
  return proxyStudio("/ask-feed");
}
