import { streamStudioEvents } from "@/lib/studio";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

// BFF: tail (resume) a background chat run's SSE — replays buffered events from `from`, then
// continues live. Used when the user re-enters a conversation whose answer is still generating.
export async function GET(req: Request, { params }: { params: { id: string } }) {
  const from = new URL(req.url).searchParams.get("from") ?? "0";
  return streamStudioEvents(`/runs/${params.id}/stream?from_index=${from}`);
}
