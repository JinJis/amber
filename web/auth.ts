import NextAuth from "next-auth";
import Credentials from "next-auth/providers/credentials";
import Google from "next-auth/providers/google";
import Kakao from "next-auth/providers/kakao";
import Naver from "next-auth/providers/naver";

// Social sign-in — each provider turns on when its credentials are present (Auth.js reads
// AUTH_{PROVIDER}_ID / AUTH_{PROVIDER}_SECRET from env). A dev-login (any email) is available
// locally without any OAuth app (AUTH_DEV_LOGIN=true).
const providers: any[] = [];
if (process.env.AUTH_GOOGLE_ID) providers.push(Google);
if (process.env.AUTH_KAKAO_ID) providers.push(Kakao);
if (process.env.AUTH_NAVER_ID) providers.push(Naver);
if (process.env.AUTH_DEV_LOGIN === "true") {
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
});
