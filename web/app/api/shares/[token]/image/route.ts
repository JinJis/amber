// SH-2b — the PUBLIC OG card image. Two paths:
//  GET: no user session (SNS crawlers fetch this) — proxy the public studio-api image with the
//       service token only, streaming the PNG through. Used by the /s/[token] page's og:image.
//  PUT: owner-only — attach the client-rendered card PNG to the share (goes through studioFetch).
import { proxyStudio, studioFetch } from "@/lib/studio";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

export async function GET(_req: Request, { params }: { params: { token: string } }) {
  const base = process.env.STUDIO_API_URL ?? "http://127.0.0.1:8004";
  try {
    const r = await fetch(`${base}/shares/${encodeURIComponent(params.token)}/image`, {
      headers: { "X-Service-Token": process.env.SERVICE_TOKEN ?? "dev-service-token" },
      cache: "no-store",
    });
    if (!r.ok) return new Response("not found", { status: r.status });
    return new Response(r.body, {
      status: 200,
      headers: { "Content-Type": "image/png", "Cache-Control": "public, max-age=86400" },
    });
  } catch {
    return new Response("upstream error", { status: 502 });
  }
}

export async function PUT(req: Request, { params }: { params: { token: string } }) {
  return proxyStudio(`/shares/${encodeURIComponent(params.token)}/image`,
    { method: "PUT", body: await req.text() });
}
