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
// Gate on ENV (the platform's deploy-environment signal, same as web/instrumentation.ts and
// studio-api's ENV=production), NOT NODE_ENV: the web is shipped as a Next standalone build, so
// NODE_ENV is "production" even in local docker — gating on it hard-disabled dev login everywhere,
// not just in real prod. A live deploy sets ENV=production (services refuse dev-default secrets
// under it), so dev login stays impossible in production; locally ENV is unset → it works.
if (process.env.AUTH_DEV_LOGIN === "true" && process.env.ENV !== "production") {
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
    // AUTH-4: 소셜 로그인마다 studio의 identity 매핑으로 캐노니컬 이메일을 해석한다 —
    // 카카오가 이메일을 안 줘도(비즈앱 심사 전) 센티널로 로그인이 되고, 이메일 연결 승격
    // 후에는 같은 카카오 계정이 새 이메일로 착지한다. studio 불통 시 폴백: 프로바이더
    // 이메일 또는 로컬 센티널 (로그인이 절대 죽지 않게).
    async jwt({ token, account, profile }) {
      if (account && (account.provider === "google" || account.provider === "kakao")) {
        const fallback = token.email ||
          (account.providerAccountId ? `${account.provider}_${account.providerAccountId}@noemail.local` : null);
        try {
          const base = process.env.STUDIO_API_URL ?? "http://127.0.0.1:8004";
          const r = await fetch(`${base}/auth/identity`, {
            method: "POST",
            headers: {
              "Content-Type": "application/json",
              "X-Service-Token": process.env.SERVICE_TOKEN ??
                (process.env.NODE_ENV === "production" ? "" : "dev-service-token"),
            },
            body: JSON.stringify({
              provider: account.provider,
              provider_account_id: String(account.providerAccountId ?? ""),
              email: token.email ?? null,
              name: (profile as { name?: string } | null)?.name ?? token.name ?? null,
              image: token.picture ?? null,
            }),
          });
          token.email = r.ok ? (await r.json()).email ?? fallback : fallback;
        } catch {
          token.email = fallback;
        }
      }
      return token;
    },
    session({ session, token }) {
      if (token.email && session.user) session.user.email = token.email;
      return session;
    },
  },
});
