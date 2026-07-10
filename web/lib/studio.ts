import { auth } from "@/auth";

// AUTH-1: the BFF↔studio trust token. In production a missing SERVICE_TOKEN must FAIL LOUDLY —
// silently falling back to the well-known dev token would leave the trust boundary open.
function serviceToken(): string {
  const t = process.env.SERVICE_TOKEN;
  if (t) return t;
  if (process.env.NODE_ENV === "production") {
    throw new Error("SERVICE_TOKEN is required in production (dev fallback is disabled)");
  }
  return "dev-service-token";
}

// Server-only helper: call studio-api with the trusted service token + the
// authenticated user's email. The platform key stays in studio-api; the browser
// only ever holds an Auth.js session.
export async function studioFetch(path: string, init: RequestInit = {}): Promise<Response | null> {
  const session = await auth();
  const email = session?.user?.email;
  if (!email) return null;
  const base = process.env.STUDIO_API_URL ?? "http://127.0.0.1:8004";
  // name/image ride along so studio can seed the profile; URI-encoded because HTTP headers are
  // latin-1 and provider names/URLs can be Korean/unicode (studio unquotes them).
  const enc = (v?: string | null) => (v ? encodeURIComponent(v) : "");
  return fetch(`${base}${path}`, {
    ...init,
    headers: {
      "Content-Type": "application/json",
      "X-Service-Token": serviceToken(),
      "X-User-Email": email,
      "X-User-Name": enc(session?.user?.name),
      "X-User-Image": enc(session?.user?.image),
      ...(init.headers ?? {}),
    },
  });
}

// Proxy a JSON studio-api response straight back to the browser.
export async function proxyStudio(path: string, init: RequestInit = {}): Promise<Response> {
  const r = await studioFetch(path, init);
  if (!r) return new Response(JSON.stringify({ error: "unauthorized" }), { status: 401 });
  return new Response(await r.text(), {
    status: r.status,
    headers: { "Content-Type": "application/json" },
  });
}

const SSE_HEADERS = { "Content-Type": "text/event-stream", "Cache-Control": "no-cache" };

// Pipe an SSE stream from studio-api back to the browser (FE-05) — the streaming twin of
// proxyStudio, used by the chat + run-resume routes (the only two that bypassed studioFetch).
// Auth + the service-token/email headers come from studioFetch; we forward the body as
// text/event-stream (or an error if the caller is unauthorized / the upstream stream is missing).
export async function streamStudioEvents(path: string, init: RequestInit = {}): Promise<Response> {
  const r = await studioFetch(path, init);
  if (!r) return new Response("unauthorized", { status: 401 });
  if (!r.ok || !r.body) return new Response("upstream stream unavailable", { status: r.status || 502 });
  return new Response(r.body, { headers: SSE_HEADERS });
}
