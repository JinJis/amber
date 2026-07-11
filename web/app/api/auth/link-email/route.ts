import { proxyStudio } from "@/lib/studio";

// AUTH-4: 센티널(카카오 무이메일) 계정에 실제 이메일 연결 — OTP 검증은 studio가 담당.
export const runtime = "nodejs";
export const dynamic = "force-dynamic";

export async function POST(req: Request) {
  return proxyStudio("/auth/identity/link-email", { method: "POST", body: await req.text() });
}
