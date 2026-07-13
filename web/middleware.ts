import { NextResponse } from "next/server";
import type { NextRequest } from "next/server";

// GUEST-1/4: 진입 시 추천 코드 쿠키만 심는다. (서버 컴포넌트는 쿠키를 못 쓰므로 미들웨어가 담당.)
// - vg_ref: 공유 페이지에서 온 추천 코드 (30일, last-touch) — 가입 시 REF-1 귀속에 쓰인다.
// ME-4: vg_guest(게스트 세션 id)는 여기서 심지 않는다 — 방문마다 심으면 크롤러 포함 모든 방문자가
//   서버에 게스트 User 행을 만들어 무한 증가한다. 이제 /api/chat이 "첫 채팅"에서만 발급한다.
export function middleware(req: NextRequest) {
  const res = NextResponse.next();
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
