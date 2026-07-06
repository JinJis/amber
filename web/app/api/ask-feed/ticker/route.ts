import { NextRequest } from "next/server";
import { proxyStudio } from "@/lib/studio";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

// ASK-6: on-demand deep-dive questions for ONE watchlist ticker (tapped on the entry screen).
// studio-api serves its per-ticker cache when fresh, else generates ~3 cards right now.
export async function GET(req: NextRequest) {
  const qs = req.nextUrl.searchParams;
  const market = qs.get("market") ?? "US";
  const ticker = qs.get("ticker") ?? "";
  const name = qs.get("name") ?? "";
  const p = new URLSearchParams({ market, ticker });
  if (name) p.set("name", name);
  return proxyStudio(`/ask-feed/ticker?${p.toString()}`);
}
