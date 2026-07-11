import { proxyStudio } from "@/lib/studio";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

// The user's metered tool-call usage + cost (settings → 사용량).
export async function GET() {
  return proxyStudio("/users/me/usage");
}
