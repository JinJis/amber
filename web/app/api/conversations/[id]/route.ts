import { proxyStudio } from "@/lib/studio";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

// UXQ-4: 대화 제목 변경 · 삭제
export async function PATCH(req: Request, { params }: { params: { id: string } }) {
  return proxyStudio(`/conversations/${encodeURIComponent(params.id)}`, { method: "PATCH", body: await req.text() });
}
export async function DELETE(_req: Request, { params }: { params: { id: string } }) {
  return proxyStudio(`/conversations/${encodeURIComponent(params.id)}`, { method: "DELETE" });
}
