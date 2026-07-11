import { auth } from "@/auth";
import Chat from "@/components/Chat";
import SignIn from "@/components/SignIn";
import { getFeatures } from "@/lib/features";

export const dynamic = "force-dynamic";

// AUTH-3: 공유 딥링크(/?q=질문)의 로그인 왕복 보존 — SignIn의 redirectTo가 현재 쿼리를
// 그대로 들고 돌아오게 callbackUrl을 내려준다 (기존엔 "/" 하드코딩이라 질문이 유실됐다).
// GUEST-2: FEATURE_GUEST면 비로그인 방문자도 SignIn 대신 게스트 챗에 착지 — 공유 페이지
// suggestion 클릭이 곧바로 살아있는 채팅 + 컴포저 프리필로 이어진다.
export default async function Home({ searchParams }: { searchParams?: Record<string, string | string[] | undefined> }) {
  const session = await auth();
  const features = getFeatures();
  if (!session?.user?.email) {
    if (process.env.FEATURE_GUEST === "true") {
      const providers = {
        google: Boolean(process.env.AUTH_GOOGLE_ID),
        kakao: Boolean(process.env.AUTH_KAKAO_ID),
        dev: process.env.AUTH_DEV_LOGIN === "true" && process.env.NODE_ENV !== "production",
      };
      return <Chat name="게스트" image={null} guest providers={providers} features={features} />;
    }
    const q = typeof searchParams?.q === "string" ? searchParams.q : "";
    const callbackUrl = q ? `/?q=${encodeURIComponent(q)}` : "/";
    return <SignIn callbackUrl={callbackUrl} />;
  }
  return <Chat name={session.user.name ?? session.user.email} email={session.user.email}
    image={session.user.image ?? null} features={getFeatures()} />;
}
