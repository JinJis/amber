// SH-3 — the PUBLIC share page (no login): renders an immutable, provenance-carrying snapshot
// with OG meta for SNS previews, and the growth-loop CTA. Revoked/expired → an honest tombstone.
// Fetches studio-api server-side with the service token only (no user session involved).

import type { Metadata } from "next";
import { ShareView } from "@/components/ShareView";
import { plainText } from "@/lib/shareCard";

// V-3: 스냅샷 불변 — Next data cache로 바이럴 트래픽 흡수 (revoke 반영 ≤1h 지연 허용)
export const revalidate = 3600;

type Share = {
  token: string; kind: string; title: string; payload: Record<string, unknown>;
  created_at?: string | null; share_urls?: Record<string, string>; has_image?: boolean;
};

async function fetchShare(token: string): Promise<{ status: number; share: Share | null }> {
  const base = process.env.STUDIO_API_URL ?? "http://127.0.0.1:8004";
  try {
    const r = await fetch(`${base}/shares/${encodeURIComponent(token)}`, {
      headers: { "X-Service-Token": process.env.SERVICE_TOKEN ?? "dev-service-token" },
      next: { revalidate: 3600 },
    });
    return { status: r.status, share: r.status === 200 ? await r.json() : null };
  } catch {
    return { status: 502, share: null };
  }
}

export async function generateMetadata({ params }: { params: { token: string } }): Promise<Metadata> {
  const { share } = await fetchShare(params.token);
  const title = share ? `${share.title} · Amber` : "공유된 자료 · Amber";
  // For an answer share, the preview text IS the real answer lead (so text-only unfurls — e.g.
  // KakaoTalk/Telegram — show the content, not a generic blurb).
  const lead = share?.kind === "answer"
    ? plainText(String((share.payload as { content?: string })?.content ?? "")).slice(0, 160)
    : "";
  const description = lead || "출처·기준일이 함께 담긴 검증 가능한 리서치 자료입니다. 원본 데이터로 직접 확인해보세요.";
  // V-1: 서버사이드 OG — 링크가 생기는 순간 이미지도 존재(클라이언트 업로드 레이스 없음).
  // 실데이터(차트 실루엣 포함)로 /og/s/{token}이 그린다; 구 링크의 저장 이미지는 그 라우트가 대체.
  const img = share ? `/og/s/${encodeURIComponent(params.token)}` : undefined;
  return {
    title, description,
    openGraph: { title, description, type: "article", ...(img ? { images: [{ url: img, width: 1200, height: 630 }] } : {}) },
    twitter: { card: img ? "summary_large_image" : "summary", title, description,
               ...(img ? { images: [img] } : {}) },
  };
}

export default async function SharePage({ params }: { params: { token: string } }) {
  const { status, share } = await fetchShare(params.token);
  return <ShareView status={status} share={share} />;
}
