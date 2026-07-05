import { proxyStudio } from "@/lib/studio";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

export async function GET() { return proxyStudio("/notebooks"); }
export async function POST(req: Request) {
  return proxyStudio("/notebooks", { method: "POST", body: await req.text() });
}
