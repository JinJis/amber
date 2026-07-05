import { proxyStudio } from "@/lib/studio";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

export async function DELETE(_req: Request, { params }: { params: { id: string } }) {
  return proxyStudio(`/standing/${encodeURIComponent(params.id)}`, { method: "DELETE" });
}
