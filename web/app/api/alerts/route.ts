import { proxyStudio } from "@/lib/studio";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

export async function GET(req: Request) {
  const qs = new URL(req.url).search;
  return proxyStudio(`/alerts${qs}`);
}

export async function POST(req: Request) {
  return proxyStudio("/alerts", { method: "POST", body: await req.text() });
}
