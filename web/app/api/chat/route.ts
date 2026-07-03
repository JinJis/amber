import { streamStudioEvents } from "@/lib/studio";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

// BFF: call studio-api /chat/stream with the trusted service token + the user's email (via
// streamStudioEvents) and pipe the SSE back. The platform key never reaches the browser.
export async function POST(req: Request) {
  return streamStudioEvents("/chat/stream", { method: "POST", body: await req.text() });
}
