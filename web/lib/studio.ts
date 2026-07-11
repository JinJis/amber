import { auth } from "@/auth";
import { cookies, headers } from "next/headers";

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

// GUEST-2: 비로그인이면 게스트 쿠키로 폴백 — studio가 actor(유저 또는 게스트)를 판정한다.
// 게스트에게 열리지 않은 studio 엔드포인트는 그쪽에서 401이 나므로 BFF는 헤더만 바꿔 낀다.
function guestHeaders(): Record<string, string> | null {
  if (process.env.FEATURE_GUEST !== "true") return null;
  const gid = cookies().get("vg_guest")?.value;
  if (!gid) return null;
  const fwd = headers().get("x-forwarded-for");
  const ip = (fwd ? fwd.split(",")[0] : headers().get("x-real-ip")) ?? "";
  return { "X-Guest-Id": gid, ...(ip ? { "X-Guest-Ip": ip.trim() } : {}) };
}

// Server-only helper: call studio-api with the trusted service token + the
// authenticated user's email (or the guest session id). The platform key stays in
// studio-api; the browser only ever holds an Auth.js session / an opaque guest cookie.
export async function studioFetch(path: string, init: RequestInit = {}): Promise<Response | null> {
  const session = await auth();
  const email = session?.user?.email;
  const base = process.env.STUDIO_API_URL ?? "http://127.0.0.1:8004";
  // name/image ride along so studio can seed the profile; URI-encoded because HTTP headers are
  // latin-1 and provider names/URLs can be Korean/unicode (studio unquotes them).
  const enc = (v?: string | null) => (v ? encodeURIComponent(v) : "");
  // REF-1: 공유 페이지 ?ref=에서 심긴 30일 쿠키 — 가입(첫 프로비저닝) 시 studio가 귀속한다.
  const refCode = cookies().get("vg_ref")?.value;
  const actor = email
    ? { "X-User-Email": email, "X-User-Name": enc(session?.user?.name), "X-User-Image": enc(session?.user?.image),
        ...(refCode ? { "X-Referral-Code": refCode } : {}) }
    : guestHeaders();
  if (!actor) return null;
  return fetch(`${base}${path}`, {
    ...init,
    headers: {
      "Content-Type": "application/json",
      "X-Service-Token": serviceToken(),
      ...actor,
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
