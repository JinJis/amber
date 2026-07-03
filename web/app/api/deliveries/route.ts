import { proxyStudio } from "@/lib/studio";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

// GET /api/deliveries?alert_id=&limit= — forwards the query through to studio-api.
export async function GET(req: Request) {
  const qs = new URL(req.url).search;
  return proxyStudio(`/deliveries${qs}`);
}
