// V-1 — 서버사이드 OG 카드: /og/s/{token} → 1200×630 PNG. 링크가 생기는 순간 이미지도
// 존재한다(클라이언트 업로드 레이스 소멸). 실제 답변 데이터로 그린다 — 좌측 훅(제목)+리드+
// 출처 스트립, 우측 차트 실루엣(스냅샷의 실측 시리즈; SVG). 한글은 Pretendard 번들.
// 날조 없음: 차트 데이터가 없으면 수치 라인으로, 그것도 없으면 텍스트만.

import { readFile } from "node:fs/promises";
import path from "node:path";
import { ImageResponse } from "next/og";
import { plainText, isHistoryKind } from "@/lib/shareCard";
import { toOgChart } from "@/lib/ogChart";
import type { Artifact, Citation } from "@/lib/types";

export const runtime = "nodejs";

const W = 1200, H = 630;
// finnote brand (docs/branding/brand.css): 종이색 배경 + 심해/오션 + 하이라이터. 그라데이션·그림자 없음.
const INK = "#0E2A3F", SUB = "#33566B", MUTED = "#5C7688", LINE = "#CBDDE5", BG = "#FFFCF6";
const OCEAN = "#12708A", OCEAN_MID = "#1690AE";

// 지느러미 단독(docs/branding/finnote-mark-mono.svg) — satori-safe 단일 filled path. path 데이터 수정 금지.
function FinIcon({ size, color }: { size: number; color: string }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24">
      <path d="M2.6 21.4 Q7 11 15 3.4 Q19.4 0.2 18 7 Q16 14.4 21.4 21.4 Z" fill={color} />
    </svg>
  );
}

let _fonts: { name: string; data: Buffer; weight: 400 | 500 | 700 }[] | null = null;
async function fonts() {
  if (_fonts) return _fonts;
  const dir = path.join(process.cwd(), "assets", "fonts");
  _fonts = [
    { name: "Pretendard", data: await readFile(path.join(dir, "Pretendard-Regular.otf")), weight: 400 as const },
    { name: "Pretendard", data: await readFile(path.join(dir, "Pretendard-Bold.otf")), weight: 700 as const },
  ];
  return _fonts;
}

async function fetchShare(token: string) {
  const base = process.env.STUDIO_API_URL ?? "http://127.0.0.1:8004";
  try {
    const r = await fetch(`${base}/shares/${encodeURIComponent(token)}`, {
      headers: { "X-Service-Token": process.env.SERVICE_TOKEN ?? "dev-service-token" },
      next: { revalidate: 3600 },   // 스냅샷 불변 — OG는 바이럴 트래픽의 최전선이라 캐시 필수
    });
    return r.status === 200 ? await r.json() : null;
  } catch { return null; }
}

export async function GET(_req: Request, { params }: { params: { token: string } }) {
  const share = await fetchShare(params.token);
  const kind: string = share?.kind ?? "";
  const payload = (share?.payload ?? {}) as {
    content?: string; artifacts?: Artifact[]; citations?: Citation[]; passage?: string;
    source?: string; as_of?: string;
  };

  const title: string = share?.title || "finnote 리서치";
  const lead = kind === "answer" ? plainText(payload.content ?? "")
    : kind === "quote" ? `“${payload.passage ?? ""}”`
    : plainText(String((payload as { title?: string }).title ?? ""));
  const cits = (payload.citations ?? []).filter((c) => c.used || c.index != null);
  const sources = [...new Set(cits.map((c) => c.source).filter(Boolean))] as string[];
  if (!sources.length && payload.source) sources.push(String(payload.source));
  const asOf = cits.map((c) => c.as_of).filter(Boolean).sort().slice(-1)[0]
    ?? (payload.as_of ? String(payload.as_of) : null);
  const artifacts: Artifact[] = kind === "answer" ? (payload.artifacts ?? [])
    : kind === "artifact" ? [payload as unknown as Artifact] : [];
  const chart = toOgChart(artifacts, 520, 300);
  const history = artifacts.some((a) => a && isHistoryKind(a));
  const srcLine = sources.length
    ? `출처 ${sources.slice(0, 3).join(" · ")}${sources.length > 3 ? ` 외 ${sources.length - 3}곳` : ""}${asOf ? ` · ${asOf}` : ""}`
    : "출처·기준일이 함께 담긴 리서치";

  return new ImageResponse(
    (
      <div style={{ width: W, height: H, display: "flex", background: BG, fontFamily: "Pretendard" }}>
        <div style={{ width: 12, height: H, background: OCEAN, display: "flex" }} />
        <div style={{ display: "flex", flexDirection: "column", flex: 1, padding: "52px 56px 44px" }}>
          {/* header: brand(지느러미 + 워드마크 fin=오션 / note=심해) + trust chip */}
          <div style={{ display: "flex", alignItems: "center" }}>
            <FinIcon size={26} color={OCEAN} />
            <div style={{ display: "flex", marginLeft: 9, fontSize: 27, fontWeight: 700, letterSpacing: "-0.03em" }}>
              <div style={{ display: "flex", color: OCEAN_MID }}>fin</div>
              <div style={{ display: "flex", color: INK }}>note</div>
            </div>
            <div style={{ marginLeft: "auto", fontSize: 21, color: SUB, border: `1.5px solid ${LINE}`,
              borderRadius: 19, padding: "6px 16px", display: "flex" }}>✓ 출처와 함께</div>
          </div>
          {/* body: text left, chart right */}
          <div style={{ display: "flex", flex: 1, marginTop: 34 }}>
            <div style={{ display: "flex", flexDirection: "column", flex: 1, paddingRight: chart ? 36 : 0 }}>
              <div style={{ fontSize: 52, fontWeight: 700, color: INK, lineHeight: 1.22,
                display: "block", lineClamp: 3, maxHeight: 190, overflow: "hidden" } as React.CSSProperties}>
                {title}
              </div>
              {lead ? (
                <div style={{ fontSize: 27, color: SUB, lineHeight: 1.45, marginTop: 20,
                  display: "block", lineClamp: chart ? 4 : 5, overflow: "hidden" } as React.CSSProperties}>
                  {lead}
                </div>
              ) : null}
            </div>
            {chart ? (
              <div style={{ display: "flex", flexDirection: "column", width: 520 }}>
                <svg width={520} height={300} viewBox="0 0 520 300">
                  <path d={chart.series[0].area} fill={INK} fillOpacity={0.07} />
                  <path d={chart.series[0].d} stroke={INK} strokeWidth={3.5} fill="none" />
                  {chart.series[1] ? (
                    <path d={chart.series[1].d} stroke={MUTED} strokeWidth={2.5} fill="none" strokeDasharray="7 6" />
                  ) : null}
                </svg>
                <div style={{ display: "flex", fontSize: 18, color: MUTED, marginTop: 8 }}>
                  <div style={{ display: "flex" }}>{chart.x0}</div>
                  <div style={{ display: "flex", marginLeft: "auto" }}>{chart.x1}</div>
                </div>
              </div>
            ) : null}
          </div>
          {/* footer strip */}
          <div style={{ display: "flex", flexDirection: "column", borderTop: `1.5px solid ${LINE}`, paddingTop: 18 }}>
            {history ? (
              <div style={{ display: "flex", fontSize: 21, fontWeight: 700, color: INK, marginBottom: 8 }}>
                ⏳ 과거 기록 · 전망 아님
              </div>
            ) : null}
            <div style={{ display: "flex", alignItems: "center" }}>
              <div style={{ display: "flex", fontSize: 22, color: MUTED, maxWidth: 860, overflow: "hidden" }}>{srcLine}</div>
              <div style={{ display: "flex", marginLeft: "auto", fontSize: 22, fontWeight: 700, letterSpacing: "-0.03em" }}>
                <div style={{ display: "flex", color: OCEAN_MID }}>fin</div>
                <div style={{ display: "flex", color: INK }}>note</div>
              </div>
            </div>
          </div>
        </div>
      </div>
    ),
    {
      width: W, height: H, fonts: await fonts(),
      headers: { "Cache-Control": "public, s-maxage=86400, max-age=3600" },
    },
  );
}
