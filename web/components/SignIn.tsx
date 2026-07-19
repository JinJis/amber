import { signIn } from "@/auth";
import { Logo } from "./Logo";
import EmailOtp from "./EmailOtp";

// Sign-in / sign-up — one screen, social-first (Google · Kakao); each button appears only
// when its OAuth app is configured. A dev-login stays for local use (never in production —
// see web/auth.ts). First sign-in provisions the tenant + seeds the profile from the provider.
// AUTH-3: callbackUrl은 로그인 왕복에서 /?q= 딥링크를 보존한다 (전환 루프의 마지막 1인치).
export default function SignIn({ callbackUrl = "/" }: { callbackUrl?: string } = {}) {
  const has = {
    google: Boolean(process.env.AUTH_GOOGLE_ID),
    kakao: Boolean(process.env.AUTH_KAKAO_ID),
    // ENV (deploy env), not NODE_ENV — the standalone build is NODE_ENV=production even locally.
    // Must match the provider guard in web/auth.ts or the button shows but the login 401s.
    dev: process.env.AUTH_DEV_LOGIN === "true" && process.env.ENV !== "production",
  };
  const anySocial = has.google || has.kakao;
  return (
    <main className="signin">
      <div className="signin-card">
        <div className="signin-brand"><Logo size={26} /></div>
        <h1 className="signin-h">출처와 함께 답하는<br />투자 리서치 데스크</h1>
        <p className="signin-sub">시장·종목·뉴스·경제, 무엇이든 물어보세요 — 모든 답에 근거가 함께 가요.</p>

        <div className="signin-providers">
          {has.google && (
            <form action={async () => { "use server"; await signIn("google", { redirectTo: callbackUrl }); }}>
              <button className="sso sso-google" type="submit"><span className="sso-ic">G</span>Google로 계속하기</button>
            </form>
          )}
          {has.kakao && (
            <form action={async () => { "use server"; await signIn("kakao", { redirectTo: callbackUrl }); }}>
              <button className="sso sso-kakao" type="submit"><span className="sso-ic" aria-hidden>💬</span>카카오로 계속하기</button>
            </form>
          )}
        </div>

        {anySocial && <div className="signin-or"><span>또는</span></div>}
        <EmailOtp callbackUrl={callbackUrl} />{/* AUTH-2: 이메일 6자리 코드 로그인 */}
        {has.dev && (
          <>
            <div className="signin-or"><span>개발용</span></div>
            {/* 아무 이메일로 로그인 — 처음 보는 이메일이면 새 계정 생성(회원가입)까지 겸한다.
                로컬 전용: web/auth.ts의 dev provider가 ENV≠production일 때만 켜진다. */}
            <form className="signin-dev" action={async (fd: FormData) => { "use server"; await signIn("credentials", { email: String(fd.get("email") || ""), redirectTo: callbackUrl }); }}>
              <input className="input" name="email" type="email" placeholder="아무 이메일 (예: me@test.com)" required />
              <button className="btn ghost" type="submit">로그인 · 회원가입</button>
            </form>
            <p className="signin-legal mono">처음 보는 이메일이면 새 계정이 만들어지고 온보딩이 시작돼요.</p>
          </>
        )}
        <p className="signin-legal mono">계속하면 서비스 약관과 개인정보 처리방침에 동의하게 돼요.</p>
      </div>
    </main>
  );
}
