import { proxyStudio } from "@/lib/studio";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

export async function DELETE(_req: Request, { params }: { params: { token: string } }) {
  return proxyStudio(`/shares/${encodeURIComponent(params.token)}`, { method: "DELETE" });
}
