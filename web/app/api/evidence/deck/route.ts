import { studioFetch } from "@/lib/studio";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

// Stream a cached 8-K presentation deck PDF (→ studio-api → gateway → datasets) with the session's
// tenant key, for the in-app pdf.js DeckViewer (renders the slides + highlights the cited chunk).
// 204 → the UI degrades to the external "원문 보기" link.
export async function GET(req: Request) {
  const accession = new URL(req.url).searchParams.get("accession");
  if (!accession) return new Response(null, { status: 204 });
  const r = await studioFetch(`/evidence/deck?accession=${encodeURIComponent(accession)}`);
  if (!r || r.status !== 200) return new Response(null, { status: 204 });
  const buf = await r.arrayBuffer();
  return new Response(buf, {
    status: 200,
    headers: { "content-type": "application/pdf", "cache-control": "private, max-age=86400" },
  });
}
