import { proxyStudio } from "@/lib/studio";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

export async function GET(req: Request, { params }: { params: { id: string } }) {
  const qs = new URL(req.url).search;
  return proxyStudio(`/alerts/${params.id}/deliveries${qs}`);
}
