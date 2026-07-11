import { NextResponse } from "next/server";
import type { NextRequest } from "next/server";

// GUEST-1/4: 진입 시 쿠키 심기 — 서버 컴포넌트는 쿠키를 못 쓰므로 미들웨어가 담당.
// - vg_guest: 익명 체험 세션 id (httpOnly, 1년). FEATURE_GUEST가 꺼져 있어도 심어두면
//   나중에 켰을 때 기존 방문자가 그대로 이어진다 — 서버가 캡을 판정하므로 무해.
// - vg_ref: 공유 페이지에서 온 추천 코드 (30일, last-touch) — 가입 시 REF-1 귀속에 쓰인다.
export function middleware(req: NextRequest) {
  const res = NextResponse.next();
  if (!req.cookies.get("vg_guest")) {
    const gid = crypto.randomUUID().replace(/-/g, "");
    res.cookies.set("vg_guest", gid, {
      httpOnly: true, sameSite: "lax", path: "/", maxAge: 60 * 60 * 24 * 365,
    });
  }
  const ref = req.nextUrl.searchParams.get("ref");
  if (ref && /^[a-z0-9-]{4,16}$/i.test(ref)) {
    res.cookies.set("vg_ref", ref.toUpperCase(), {
      httpOnly: true, sameSite: "lax", path: "/", maxAge: 60 * 60 * 24 * 30,
    });
  }
  return res;
}

// 루트(채팅 착지점)만 — API/정적 경로에는 쿠키 로직이 필요 없다.
export const config = { matcher: ["/"] };
