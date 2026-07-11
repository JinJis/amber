// V-1 — OG 차트 실루엣 순수 헬퍼: 실데이터→SVG path 정규화, 캔들 우선, 무데이터 null.
import { describe, expect, it } from "vitest";
import { pickChartData, toOgChart } from "../lib/ogChart";
import type { Artifact } from "../lib/types";

const CANDLES: Artifact = { kind: "candlestick", title: "AAPL", series: [], ticker: "AAPL",
  candles: Array.from({ length: 10 }, (_, i) => ({ time: `2026-06-${String(i + 1).padStart(2, "0")}`,
    open: 100 + i, high: 102 + i, low: 99 + i, close: 100 + i, volume: 1 })) };
const LINES: Artifact = { kind: "compare", title: "매출", series: [
  { label: "AAPL", points: [{ x: "2024", y: 10 }, { x: "2025", y: 12 }, { x: "2026", y: 15 }] },
  { label: "MSFT", points: [{ x: "2024", y: 9 }, { x: "2025", y: 11 }, { x: "2026", y: 14 }] },
  { label: "GOOG", points: [{ x: "2024", y: 8 }, { x: "2025", y: 10 }, { x: "2026", y: 13 }] },
] };

describe("ogChart", () => {
  it("candles win over line series and use closes", () => {
    const got = pickChartData([LINES, CANDLES]);
    expect(got).not.toBeNull();
    // 첫 아티팩트(LINES)가 시리즈를 가지므로 그게 선택됨 — 순서 존중
    expect(got!.labels[0]).toBe("AAPL");
    const c = pickChartData([CANDLES]);
    expect(c!.pts[0][0].y).toBe(100);
  });

  it("normalizes into the viewBox and caps at 2 series", () => {
    const chart = toOgChart([LINES], 520, 300)!;
    expect(chart.series).toHaveLength(2);              // 3개 중 2개만
    expect(chart.series[0].d.startsWith("M 0")).toBe(true);
    expect(chart.series[0].d).toContain("520");        // 마지막 점이 우측 끝
    expect(chart.series[0].area.endsWith("Z")).toBe(true);
    expect(chart.x0).toBe("2024");
    expect(chart.x1).toBe("2026");
  });

  it("flat series still draws; empty artifacts → null (no fabrication)", () => {
    const flat: Artifact = { kind: "timeseries", title: "f", series: [
      { label: "x", points: [{ x: "a", y: 5 }, { x: "b", y: 5 }] }] };
    expect(toOgChart([flat])).not.toBeNull();
    expect(toOgChart([])).toBeNull();
    expect(toOgChart(undefined)).toBeNull();
    expect(toOgChart([{ kind: "table", title: "t", series: [] }])).toBeNull();
  });

  it("downsamples long series to ~240 points", () => {
    const long: Artifact = { kind: "timeseries", title: "l", series: [
      { label: "x", points: Array.from({ length: 2000 }, (_, i) => ({ x: `d${i}`, y: i })) }] };
    const chart = toOgChart([long])!;
    expect(chart.series[0].d.split(" L ").length).toBeLessThan(260);
  });
});
