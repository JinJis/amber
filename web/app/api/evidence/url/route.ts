import { studioFetch } from "@/lib/studio";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

// Stream ANY public source page as sanitized HTML (→ studio-api → gateway → datasets) with the
// session's tenant key, for the in-app source viewer. The data plane fetches the URL SSRF-safe and
// sanitizes it (scripts stripped + strict CSP → no egress); the viewer drops it into a sandboxed
// iframe and highlights the cited value. Used for non-filing sources (BLS/DBnomics/FRED/news/…).
// 204 → the UI offers the external "원문 보기" link instead.
export async function GET(req: Request) {
  const { searchParams } = new URL(req.url);
  const u = searchParams.get("u");
  if (!u) return new Response(null, { status: 204 });
  const r = await studioFetch(`/evidence/url?u=${encodeURIComponent(u)}`);
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
