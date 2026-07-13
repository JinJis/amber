import { proxyStudio } from "@/lib/studio";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

// The user's chat history (titles + ids), newest first. HI-10: forward pagination params
// (limit/offset) so the sidebar can page rather than pull the entire history.
export async function GET(req: Request) {
  const qs = new URL(req.url).search;
  return proxyStudio(`/conversations${qs}`);
}
