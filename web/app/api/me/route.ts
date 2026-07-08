import { proxyStudio } from "@/lib/studio";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

export async function GET() {
  return proxyStudio("/users/me");
}

// Update the display name / avatar (settings → profile).
export async function PATCH(req: Request) {
  return proxyStudio("/users/me", { method: "PATCH", body: await req.text() });
}
