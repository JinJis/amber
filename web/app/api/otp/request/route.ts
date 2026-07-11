import { headers } from "next/headers";

// AUTH-2: 로그인 코드 발송 — 아직 세션이 없는 단계라 studioFetch(액터 필요)를 안 쓰고
// 서비스 토큰만으로 프록시한다. 스로틀·형식 검증은 studio가 담당; IP는 스로틀용으로 전달.
export const runtime = "nodejs";
export const dynamic = "force-dynamic";

export async function POST(req: Request) {
  const base = process.env.STUDIO_API_URL ?? "http://127.0.0.1:8004";
  const token = process.env.SERVICE_TOKEN ??
    (process.env.NODE_ENV === "production" ? "" : "dev-service-token");
  const fwd = headers().get("x-forwarded-for");
  const ip = (fwd ? fwd.split(",")[0] : headers().get("x-real-ip")) ?? "";
  const r = await fetch(`${base}/auth/otp/request`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      "X-Service-Token": token,
      ...(ip ? { "X-Guest-Ip": ip.trim() } : {}),
    },
    body: await req.text(),
  });
  return new Response(await r.text(), { status: r.status, headers: { "Content-Type": "application/json" } });
}
