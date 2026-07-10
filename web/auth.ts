import NextAuth from "next-auth";
import Credentials from "next-auth/providers/credentials";
import Google from "next-auth/providers/google";
import Kakao from "next-auth/providers/kakao";

// Social sign-in — each provider turns on when its credentials are present (Auth.js reads
// AUTH_{PROVIDER}_ID / AUTH_{PROVIDER}_SECRET from env). Google + Kakao only (AUTH-1: Naver
// dropped by product decision). A dev-login (any email) is available locally without any OAuth
// app (AUTH_DEV_LOGIN=true) — HARD-DISABLED in production builds: it accepts any email with no
// password and would provision a real tenant for it, so no env slip may ever enable it live.
const providers: any[] = [];
if (process.env.AUTH_GOOGLE_ID) providers.push(Google);
if (process.env.AUTH_KAKAO_ID) providers.push(Kakao);
// AUTH-2: 이메일 6자리 OTP — 코드 발급·검증은 studio-api가 담당(sha256 저장·만료·스로틀),
// 여기서는 verify를 프록시만 한다. 매직링크가 아닌 이유: 카톡/인스타 인앱 브라우저에서
// 링크는 다른 브라우저로 열려 세션·/?q= 딥링크가 유실된다.
providers.push(
  Credentials({
    id: "email-otp",
    name: "Email",
    credentials: { email: { label: "Email", type: "email" }, code: { label: "Code", type: "text" } },
    authorize: async (creds) => {
      const email = String(creds?.email || "").trim().toLowerCase();
      const code = String(creds?.code || "").trim();
      if (!email.includes("@") || !/^\d{6}$/.test(code)) return null;
      try {
        const base = process.env.STUDIO_API_URL ?? "http://127.0.0.1:8004";
        const r = await fetch(`${base}/auth/otp/verify`, {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
            "X-Service-Token": process.env.SERVICE_TOKEN ??
              (process.env.NODE_ENV === "production" ? "" : "dev-service-token"),
          },
          body: JSON.stringify({ email, code }),
        });
        if (!r.ok) return null;
        return { id: email, email, name: email.split("@")[0] };
      } catch {
        return null;
      }
    },
  }),
);
if (process.env.AUTH_DEV_LOGIN === "true" && process.env.NODE_ENV !== "production") {
  providers.push(
    Credentials({
      name: "Dev",
      credentials: { email: { label: "Email", type: "email" } },
      authorize: (creds) => {
        const email = String(creds?.email || "");
        return email.includes("@") ? { id: email, email, name: email.split("@")[0] } : null;
      },
    }),
  );
}

export const { handlers, auth, signIn, signOut } = NextAuth({
  providers,
  trustHost: true,
  callbacks: {
    // AUTH-4: 카카오는 비즈앱 심사 전까지 이메일을 안 줄 수 있다 — 로그인 자체가 깨지지 않게
    // 결정적 센티널(kakao_{id}@noemail.local)로 계정을 만든다. studio가 이 도메인을
    // email_verified=false로 마킹해 메일 발송을 차단하고, 이후 이메일 연결(OTP)로 승격한다.
    jwt({ token, account }) {
      if (account?.provider === "kakao" && !token.email && account.providerAccountId) {
        token.email = `kakao_${account.providerAccountId}@noemail.local`;
      }
      return token;
    },
    session({ session, token }) {
      if (token.email && session.user) session.user.email = token.email;
      return session;
    },
  },
});
