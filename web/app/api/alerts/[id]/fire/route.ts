import { proxyStudio } from "@/lib/studio";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

// Fire an alert immediately — the "테스트 발송" / first-delivery action.
export async function POST(_req: Request, { params }: { params: { id: string } }) {
  return proxyStudio(`/alerts/${params.id}/fire`, { method: "POST" });
}
