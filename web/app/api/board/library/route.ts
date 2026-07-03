import { proxyStudio } from "@/lib/studio";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

// The 탐색 pin pool — every data asset the user pinned from chat, across all boards. The dashboard
// widget gallery draws widgets from here (a widget is always something pinned in 탐색).
export async function GET() {
  return proxyStudio("/board/library");
}
