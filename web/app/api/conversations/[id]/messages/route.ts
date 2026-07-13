import { proxyStudio } from "@/lib/studio";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

// Persisted messages of one conversation (role · content · citations) — to resume a chat. HI-10:
// forward pagination params (limit/before) so a very long thread loads its recent tail + "load earlier".
export async function GET(req: Request, { params }: { params: { id: string } }) {
  const qs = new URL(req.url).search;
  return proxyStudio(`/conversations/${params.id}/messages${qs}`);
}
