import { cookies } from "next/headers";

import { auth } from "@/auth";
import { proxyStudio, studioFetch } from "@/lib/studio";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

export async function GET() {
  // GUEST-3: 로그인 직후 첫 /api/me에서 게스트 대화를 새 계정으로 1회 이어붙이고 쿠키를 지운다
  // — "다시 원래 하던거부터". 실패해도(이미 claim된 세션 등) 프로필 응답은 정상 진행.
  const session = await auth();
  const jar = cookies();
  const gid = jar.get("vg_guest")?.value;
  if (session?.user?.email && gid) {
    // Drop the cookie only once the claim actually succeeded. Clearing it on a failure (studio
    // briefly unavailable, or the account still being provisioned) would strand the guest's
    // conversations for good — nothing else remembers the id. A kept cookie just retries next load;
    // an already-claimed session is rejected by studio, so retrying is safe.
    let claimed = false;
    try {
      const r = await studioFetch("/users/claim-guest", { method: "POST", headers: { "X-Guest-Id": gid } });
      claimed = !!r && (r.ok || r.status === 401);   // 401 = already claimed — nothing left to keep
    } catch {}
    if (claimed) jar.delete("vg_guest");
  }
  return proxyStudio("/users/me");
}

// Update the display name / avatar (settings → profile).
export async function PATCH(req: Request) {
  return proxyStudio("/users/me", { method: "PATCH", body: await req.text() });
}
