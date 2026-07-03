import { proxyStudio } from "@/lib/studio";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

export async function POST(_req: Request, { params }: { params: { id: string } }) {
  return proxyStudio(`/alerts/${params.id}/resume`, { method: "POST" });
}
