// SH-3 — the PUBLIC share page (no login): renders an immutable, provenance-carrying snapshot
// with OG meta for SNS previews, and the growth-loop CTA. Revoked/expired → an honest tombstone.
// Fetches studio-api server-side with the service token only (no user session involved).

import type { Metadata } from "next";
import { ShareView } from "@/components/ShareView";

export const dynamic = "force-dynamic";

type Share = {
  token: string; kind: string; title: string; payload: Record<string, unknown>;
  created_at?: string | null; share_urls?: Record<string, string>; has_image?: boolean;
};

async function fetchShare(token: string): Promise<{ status: number; share: Share | null }> {
  const base = process.env.STUDIO_API_URL ?? "http://127.0.0.1:8004";
  try {
    const r = await fetch(`${base}/shares/${encodeURIComponent(token)}`, {
      headers: { "X-Service-Token": process.env.SERVICE_TOKEN ?? "dev-service-token" },
      cache: "no-store",
    });
    return { status: r.status, share: r.status === 200 ? await r.json() : null };
  } catch {
    return { status: 502, share: null };
  }
}

export async function generateMetadata({ params }: { params: { token: string } }): Promise<Metadata> {
  const { share } = await fetchShare(params.token);
  const title = share ? `${share.title} · ValueGraph` : "공유된 자료 · ValueGraph";
  const description = "출처·기준일이 함께 담긴 검증 가능한 리서치 자료입니다. 원본 데이터로 직접 확인해보세요.";
  // SH-2b: when the share carries a baked card image, use it as the OG/Twitter preview (large card).
  const img = share?.has_image ? `/api/shares/${encodeURIComponent(params.token)}/image` : undefined;
  return {
    title, description,
    openGraph: { title, description, type: "article", ...(img ? { images: [{ url: img }] } : {}) },
    twitter: { card: img ? "summary_large_image" : "summary", title, description,
               ...(img ? { images: [img] } : {}) },
  };
}

export default async function SharePage({ params }: { params: { token: string } }) {
  const { status, share } = await fetchShare(params.token);
  return <ShareView status={status} share={share} />;
}
