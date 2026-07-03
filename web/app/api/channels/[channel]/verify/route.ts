import { proxyStudio } from "@/lib/studio";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

export async function POST(_req: Request, { params }: { params: { channel: string } }) {
  return proxyStudio(`/channels/${params.channel}/verify`, { method: "POST" });
}
