import { proxyStudio } from "@/lib/studio";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

export async function POST(req: Request) {
  return proxyStudio("/referrals/enter", { method: "POST", body: await req.text() });
}
