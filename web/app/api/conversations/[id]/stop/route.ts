import { proxyStudio } from "@/lib/studio";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

// UXQ-2: 진행 중 답변 중지 (서버측 런 취소 — 부분 답변은 보존·영속)
export async function POST(_req: Request, { params }: { params: { id: string } }) {
  return proxyStudio(`/conversations/${encodeURIComponent(params.id)}/stop`, { method: "POST" });
}
