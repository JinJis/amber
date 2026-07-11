import { studioFetch } from "@/lib/studio";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

// A ticker's company logo (→ studio-api → gateway → datasets, cached best-effort). 204 when there
// is no logo — the <TickerLogo> component then draws a monogram (never a broken image). Logos are
// decorative public brand assets, so this is safe to cache long in the browser.
export async function GET(req: Request) {
  const { searchParams } = new URL(req.url);
  const market = searchParams.get("market") || "US";
  const ticker = searchParams.get("ticker") || "";
  if (!ticker) return new Response(null, { status: 204 });
  const r = await studioFetch(`/logos?market=${encodeURIComponent(market)}&ticker=${encodeURIComponent(ticker)}`);
  const ct = r?.headers.get("content-type") || "";
  if (!r || r.status !== 200 || !ct.startsWith("image/")) return new Response(null, { status: 204 });
  const buf = await r.arrayBuffer();
  return new Response(buf, {
    status: 200,
    headers: { "content-type": ct, "cache-control": "public, max-age=604800" },
  });
}
