export const runtime = "nodejs";
export const dynamic = "force-dynamic";

// V-4 — 공개 공유 페이지의 뷰 비콘. 뷰어는 비로그인이므로 세션 없이 서비스 토큰으로
// studio에 전달한다 (쓰기는 +1 카운터뿐 — 개인정보·컨텐츠 접근 없음).
export async function POST(_req: Request, { params }: { params: { token: string } }) {
  const base = process.env.STUDIO_API_URL ?? "http://127.0.0.1:8004";
  try {
    await fetch(`${base}/shares/${encodeURIComponent(params.token)}/view`, {
      method: "POST",
      headers: { "X-Service-Token": process.env.SERVICE_TOKEN ?? "dev-service-token" },
    });
  } catch { /* best-effort — 측정 실패가 페이지를 방해하지 않음 */ }
  return new Response(null, { status: 204 });
}
