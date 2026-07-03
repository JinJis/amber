import { studioFetch } from "@/lib/studio";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

// Stream the ORIGINAL filing as sanitized HTML (→ studio-api → gateway → datasets) with the
// session's tenant key, for the in-app filing viewer. The HTML is already sanitized server-side
// (scripts stripped + strict CSP → no egress); the viewer drops it into a sandboxed iframe.
// 204 → the UI offers the external "원문 보기" link instead.
export async function GET(req: Request) {
  const { searchParams } = new URL(req.url);
  const r = await studioFetch(`/evidence/html?${searchParams.toString()}`);
  if (!r || r.status !== 200) return new Response(null, { status: 204 });
  const buf = await r.arrayBuffer();
  return new Response(buf, {
    status: 200,
    headers: {
      "content-type": "text/html; charset=utf-8",
      "cache-control": "private, max-age=86400",
    },
  });
}
