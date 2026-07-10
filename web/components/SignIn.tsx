import { signIn } from "@/auth";

// Sign-in / sign-up — one screen, social-first (Google · Kakao); each button appears only
// when its OAuth app is configured. A dev-login stays for local use (never in production —
// see web/auth.ts). First sign-in provisions the tenant + seeds the profile from the provider.
export default function SignIn() {
  const has = {
    google: Boolean(process.env.AUTH_GOOGLE_ID),
    kakao: Boolean(process.env.AUTH_KAKAO_ID),
    dev: process.env.AUTH_DEV_LOGIN === "true" && process.env.NODE_ENV !== "production",
  };
  const anySocial = has.google || has.kakao;
  return (
    <main className="signin">
      <div className="signin-card">
        <div className="signin-brand"><span className="mascot" aria-hidden /><b>ValueGraph</b></div>
        <h1 className="signin-h">출처와 함께 답하는<br />투자 리서치 데스크</h1>
        <p className="signin-sub">시장·종목·뉴스·경제, 무엇이든 물어보세요 — 모든 답에 근거가 함께 가요.</p>

        <div className="signin-providers">
          {has.google && (
            <form action={async () => { "use server"; await signIn("google", { redirectTo: "/" }); }}>
              <button className="sso sso-google" type="submit"><span className="sso-ic">G</span>Google로 계속하기</button>
            </form>
          )}
          {has.kakao && (
            <form action={async () => { "use server"; await signIn("kakao", { redirectTo: "/" }); }}>
              <button className="sso sso-kakao" type="submit"><span className="sso-ic" aria-hidden>💬</span>카카오로 계속하기</button>
            </form>
          )}
        </div>

        {has.dev && (
          <>
            {anySocial && <div className="signin-or"><span>또는</span></div>}
            <form className="signin-dev" action={async (fd: FormData) => { "use server"; await signIn("credentials", { email: String(fd.get("email") || ""), redirectTo: "/" }); }}>
              <input className="input" name="email" type="email" placeholder="dev@example.com" required />
              <button className="btn ghost" type="submit">개발용 로그인</button>
            </form>
          </>
        )}
        {!anySocial && !has.dev && <p className="muted">로그인 제공자가 설정되지 않았어요 (.env 참고).</p>}
        <p className="signin-legal mono">계속하면 서비스 약관과 개인정보 처리방침에 동의하게 돼요.</p>
      </div>
    </main>
  );
}
