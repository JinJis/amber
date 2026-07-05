import { proxyStudio } from "@/lib/studio";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

// ENT-2: the market opening strip (indices · FX · VIX), 60s-cached in studio-api.
export async function GET() {
  return proxyStudio("/market/pulse");
}
