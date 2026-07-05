import { proxyStudio } from "@/lib/studio";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

export async function POST(req: Request, { params }: { params: { id: string } }) {
  return proxyStudio(`/notebooks/${encodeURIComponent(params.id)}/blocks`, { method: "POST", body: await req.text() });
}
