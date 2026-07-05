import { proxyStudio } from "@/lib/studio";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

export async function PATCH(req: Request, { params }: { params: { id: string; bid: string } }) {
  return proxyStudio(`/notebooks/${encodeURIComponent(params.id)}/blocks/${encodeURIComponent(params.bid)}`,
    { method: "PATCH", body: await req.text() });
}
export async function DELETE(_req: Request, { params }: { params: { id: string; bid: string } }) {
  return proxyStudio(`/notebooks/${encodeURIComponent(params.id)}/blocks/${encodeURIComponent(params.bid)}`,
    { method: "DELETE" });
}
