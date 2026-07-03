import { proxyStudio } from "@/lib/studio";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

export async function POST() {
  return proxyStudio("/users/onboarded", { method: "POST" });
}
