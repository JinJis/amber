// BILL-4: 토스 웹훅의 공개 착지점 — 세션 없이 서비스 토큰만 끼워 studio로 전달한다.
// 진위 검증은 studio가 paymentKey 재조회로 수행(바디 불신); URL 시크릿이 2차 인증.
export const runtime = "nodejs";
export const dynamic = "force-dynamic";

export async function POST(req: Request, { params }: { params: { secret: string } }) {
  const base = process.env.STUDIO_API_URL ?? "http://127.0.0.1:8004";
  const token = process.env.SERVICE_TOKEN ??
    (process.env.NODE_ENV === "production" ? "" : "dev-service-token");
  const r = await fetch(`${base}/billing/webhook/${encodeURIComponent(params.secret)}`, {
    method: "POST",
    headers: { "Content-Type": "application/json", "X-Service-Token": token },
    body: await req.text(),
  });
  return new Response(await r.text(), { status: r.status, headers: { "Content-Type": "application/json" } });
}
