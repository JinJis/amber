import { proxyStudio } from "@/lib/studio";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

// ENT-4: the user's watchlist tickers with day moves, 120s-cached in studio-api.
export async function GET() {
  return proxyStudio("/market/watch");
}
