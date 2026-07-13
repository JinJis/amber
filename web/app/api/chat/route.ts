import { auth } from "@/auth";
import { streamStudioEvents } from "@/lib/studio";
import { cookies } from "next/headers";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

// BFF: call studio-api /chat/stream with the trusted service token + the user's email (via
// streamStudioEvents) and pipe the SSE back. The platform key never reaches the browser.
export async function POST(req: Request) {
  const body = await req.text();
  // ME-4: mint the guest session cookie on the FIRST chat — NOT on every page visit. Minting in the
  // page-visit middleware spawned a guest User row server-side for every visitor (crawlers included),
  // growing the users/guest_sessions tables without bound. Here the cookie (and thus the row) is
  // created only when an anonymous visitor actually commits to chatting.
  let gid: string | undefined;
  let setCookie: string | undefined;
  if (process.env.FEATURE_GUEST === "true") {
    const session = await auth();
    if (!session?.user?.email) {
      gid = cookies().get("vg_guest")?.value;
      if (!gid) {
        gid = crypto.randomUUID().replace(/-/g, "");
        setCookie = `vg_guest=${gid}; HttpOnly; SameSite=Lax; Path=/; Max-Age=${60 * 60 * 24 * 365}`;
      }
    }
  }
  const res = await streamStudioEvents("/chat/stream", { method: "POST", body }, gid);
  if (setCookie) res.headers.append("Set-Cookie", setCookie);
  return res;
}
