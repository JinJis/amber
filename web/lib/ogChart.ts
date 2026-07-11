// V-1 — OG 카드용 차트 실루엣: 아티팩트의 실데이터(캔들 종가/라인 시리즈)를 SVG path로 변환.
// TradeChart는 canvas(클라이언트 전용)라 서버 OG에서는 순수 SVG로 다시 그린다 — 데이터는
// 스냅샷의 실측 그대로, 장식만 단순화(실루엣). 날조 없음: 그릴 데이터가 없으면 null.
// 순수 함수(vitest 대상) — 렌더 JSX는 라우트 쪽.

import type { Artifact } from "./types";

export type OgSeries = { label: string; d: string; area: string; last: number | null };
export type OgChartData = {
  series: OgSeries[];          // ≤2 series (더 많으면 실루엣이 죽처럼 보임)
  min: number; max: number;
  x0: string; x1: string;      // 기간 라벨 (첫/끝 x)
};

/** 아티팩트 목록에서 OG에 그릴 첫 차트 데이터를 고른다 — 캔들(종가) 우선, 라인 시리즈 차선. */
export function pickChartData(artifacts: Artifact[] | undefined | null): { a: Artifact; pts: { x: string; y: number }[][]; labels: string[] } | null {
  for (const a of artifacts ?? []) {
    if (a?.candles?.length) {
      const pts = a.candles
        .filter((c) => c.close != null && c.time)
        .map((c) => ({ x: String(c.time), y: Number(c.close) }));
      if (pts.length >= 2) return { a, pts: [pts], labels: [a.ticker || a.title || "close"] };
    }
    const usable = (a?.series ?? [])
      .map((s) => ({
        label: s.label,
        pts: (s.points ?? []).filter((p) => p.y != null && p.x).map((p) => ({ x: String(p.x), y: Number(p.y) })),
      }))
      .filter((s) => s.pts.length >= 2)
      .slice(0, 2);
    if (usable.length) return { a, pts: usable.map((u) => u.pts), labels: usable.map((u) => u.label) };
  }
  return null;
}

/** 포인트들을 w×h viewBox의 SVG path로 정규화. 모든 시리즈가 같은 y-스케일을 공유. */
export function toOgChart(artifacts: Artifact[] | undefined | null, w = 520, h = 300): OgChartData | null {
  const picked = pickChartData(artifacts);
  if (!picked) return null;
  const all = picked.pts.flat();
  let min = Math.min(...all.map((p) => p.y));
  let max = Math.max(...all.map((p) => p.y));
  if (!isFinite(min) || !isFinite(max)) return null;
  if (min === max) { min -= 1; max += 1; }        // 평평한 시리즈도 선은 그린다
  const pad = (max - min) * 0.06;
  min -= pad; max += pad;

  const series: OgSeries[] = picked.pts.map((pts, si) => {
    const n = pts.length;
    const step = n > 240 ? Math.ceil(n / 240) : 1;   // 실루엣엔 240점이면 충분 (payload 절약)
    const used = pts.filter((_, i) => i % step === 0 || i === n - 1);
    const coords = used.map((p, i) => {
      const x = (i / (used.length - 1)) * w;
      const y = h - ((p.y - min) / (max - min)) * h;
      return `${x.toFixed(1)} ${y.toFixed(1)}`;
    });
    const d = `M ${coords.join(" L ")}`;
    const area = `${d} L ${w} ${h} L 0 ${h} Z`;
    return { label: picked.labels[si] ?? `s${si + 1}`, d, area, last: used[used.length - 1]?.y ?? null };
  });

  return {
    series, min, max,
    x0: picked.pts[0][0]?.x?.slice(0, 10) ?? "",
    x1: picked.pts[0][picked.pts[0].length - 1]?.x?.slice(0, 10) ?? "",
  };
}
