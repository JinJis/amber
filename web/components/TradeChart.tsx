"use client";

import { useEffect, useRef, useState } from "react";
import {
  createChart,
  ColorType,
  CrosshairMode,
  LineStyle,
  type IChartApi,
  type ISeriesApi,
  type SeriesMarker,
  type Time,
} from "lightweight-charts";
import type { Artifact, ArtifactMarker, ChartAnnotations, Citation } from "../lib/types";
import { attachRegimeZones, underwaterSeries } from "./chartPrimitives";
import { fmtBig } from "../lib/format";

const MARKER_SHAPE: Record<string, "circle" | "arrowUp" | "arrowDown" | "square"> = {
  dividend: "circle", split: "square", earnings: "arrowUp", filing: "arrowDown",
};

// PH-VIZ-1: a professional trader chart (TradingView Lightweight Charts, Apache-2.0,
// client-side canvas — no data egress, no paid API). Renders an artifact as real
// candlesticks + a volume pane when it carries OHLCV, else as line series. Crosshair,
// time/price scales, a range selector and log / %-rebase toggles. Descriptive only —
// no forecast/projection lines (PH-VIZ guardrail).

const LINE_COLORS = ["#4f8cff", "#9aa7bd", "#1FA463", "#D9A300"];
const RANGES: [string, number][] = [["1M", 30], ["3M", 90], ["6M", 180], ["1Y", 365], ["5Y", 1825], ["MAX", 0]];
// The default "auto" window: fit the agent's own narrow candles (the exact as-of window the evidence
// is about). Not one of RANGES, so no range button is highlighted and clicking MAX stays a real
// "zoom to full history" action.
const AUTO = "auto";

// Normalize a period label to a 'YYYY-MM-DD' the chart accepts; null = unplottable.
function toTime(x: string): string | null {
  if (/^\d{4}-\d{2}-\d{2}$/.test(x)) return x;
  if (/^\d{4}-\d{2}$/.test(x)) return `${x}-01`;
  if (/^\d{4}$/.test(x)) return `${x}-12-31`;
  return null;
}

function daysBefore(last: string, days: number): string {
  const d = new Date(last + "T00:00:00Z");
  d.setUTCDate(d.getUTCDate() - days);
  return d.toISOString().slice(0, 10);
}

// PH-VIZ-4: normalize an overlay point time to 'YYYY-MM-DD' (overlays are daily).
function overlayPoints(pts: { time: string; value: number }[]) {
  return pts
    .map((p) => ({ t: toTime(p.time), value: p.value }))
    .filter((p): p is { t: string; value: number } => p.t != null && p.value != null);
}

export function TradeChart(
  { a, onEvidence, userAnn, onDraw, bars, series, currency = "USD", compact = false, fillHeight = false }:
  { a: Artifact; onEvidence?: (c: Citation) => void;
    userAnn?: ChartAnnotations | null; onDraw?: (next: ChartAnnotations | null) => void;
    bars?: NonNullable<Artifact["candles"]> | null;
    series?: NonNullable<Artifact["series"]> | null; currency?: "KRW" | "USD";
    // compact: hide the toolbar (dashboard widget). fillHeight: the chart fills its container
    // height (responsive widget) instead of a fixed 260px.
    compact?: boolean; fillHeight?: boolean },
) {
  const box = useRef<HTMLDivElement>(null);
  const chartRef = useRef<IChartApi | null>(null);   // PH-VIZ-6: for the PNG snapshot export
  // prefer the generous fetched history (bars) over the agent's narrow candles
  const candleData = (bars && bars.length) ? bars : (a.candles ?? []);
  const lineData = (series && series.length) ? series : (a.series ?? []);  // financials override
  const isCandle = candleData.length > 0;
  const lineCount = lineData.length;
  const overlays = a.overlays ?? [];
  // default to the AUTO point-in-time window (fit the agent's own candles); no range button is
  // highlighted until the user picks one, and MAX stays a real "zoom to full history" action.
  const [range, setRange] = useState(AUTO);
  const [logScale, setLogScale] = useState(false);
  const [underwater, setUnderwater] = useState(false);   // HL-8(a): drawdown % sub-pane toggle
  const [rebase, setRebase] = useState(false);   // line mode only: index each series to 100
  // PH-VIZ-5: drawing mode (only when onDraw is provided). The pending point of a 2-click
  // trend line lives in a ref so it survives a re-render between clicks.
  const [drawMode, setDrawMode] = useState<null | "trend" | "hline">(null);
  const pending = useRef<{ time: string; price: number } | null>(null);

  // Rebuild the chart only when the DATA actually changes — not when a parent re-render hands
  // down new object identities. Streaming recreates the message (and its artifacts) on every SSE
  // event, so identity-based deps tore the chart down at stream end and re-fit it (the "suddenly
  // zooms out / goes blank" bug). The signature captures content: lengths + last timestamps.
  const dataSig = [
    a.kind, a.title, a.chart_style ?? "",
    candleData.length, candleData[candleData.length - 1]?.time ?? "",
    lineData.map((s) => `${s.label}:${s.points?.length ?? 0}:${s.points?.[s.points.length - 1]?.x ?? ""}`).join("|"),
    overlays.map((o) => `${o.key}:${o.lines?.length ?? 0}`).join("|"),
    a.markers?.length ?? 0, a.pricelines?.length ?? 0,
    (a.annotations?.lines?.length ?? 0) + (a.annotations?.hlines?.length ?? 0) + (a.annotations?.zones?.length ?? 0),
    (userAnn?.lines?.length ?? 0) + (userAnn?.hlines?.length ?? 0),
  ].join("§");

  useEffect(() => {
    const el = box.current;
    if (!el) return;
    const chart: IChartApi = createChart(el, {
      width: el.clientWidth,
      height: fillHeight ? (el.clientHeight || 220) : 260,
      layout: { background: { type: ColorType.Solid, color: "transparent" }, textColor: "#86868C", fontSize: 11 },
      // readable axis/crosshair: abbreviate big figures (revenue → 조/억 or $B/M); prices stay plain.
      localization: {
        priceFormatter: (v: number) =>
          Math.abs(v) >= 1e6 ? fmtBig(v, currency) : v.toLocaleString(undefined, { maximumFractionDigits: 2 }),
      },
      grid: { vertLines: { color: "rgba(150,150,160,0.08)" }, horzLines: { color: "rgba(150,150,160,0.08)" } },
      crosshair: { mode: CrosshairMode.Normal },
      rightPriceScale: { borderColor: "rgba(150,150,160,0.2)", mode: logScale ? 1 : 0 },
      timeScale: { borderColor: "rgba(150,150,160,0.2)", timeVisible: false },
      handleScale: true,
      handleScroll: true,
    });

    let lastTime: string | null = null;
    let hasVol = false;
    // PH-VIZ-5: the reference series for pixel→price conversion + drawing user annotations.
    let mainSeries: ISeriesApi<"Candlestick"> | ISeriesApi<"Line"> | ISeriesApi<"Histogram"> | null = null;

    if (isCandle) {
      const rows = candleData
        .map((c) => ({ t: toTime(c.time), c }))
        .filter((r): r is { t: string; c: NonNullable<Artifact["candles"]>[number] } =>
          r.t != null && r.c.open != null && r.c.high != null && r.c.low != null && r.c.close != null);
      const candle = chart.addCandlestickSeries({
        upColor: "#1FA463", downColor: "#D1483A", borderVisible: false,
        wickUpColor: "#1FA463", wickDownColor: "#D1483A",
      });
      candle.setData(rows.map((r) => ({
        time: r.t as Time, open: r.c.open!, high: r.c.high!, low: r.c.low!, close: r.c.close!,
      })));
      mainSeries = candle;
      hasVol = rows.some((r) => r.c.volume != null);
      if (hasVol) {
        const vol = chart.addHistogramSeries({ priceFormat: { type: "volume" }, priceScaleId: "" });
        vol.priceScale().applyOptions({ scaleMargins: { top: 0.82, bottom: 0 } });
        vol.setData(rows.map((r) => ({
          time: r.t as Time, value: r.c.volume ?? 0,
          color: (r.c.close ?? 0) >= (r.c.open ?? 0) ? "rgba(31,164,99,0.4)" : "rgba(209,72,58,0.4)",
        })));
      }
      lastTime = rows.length ? rows[rows.length - 1].t : null;

      // PH-VIZ-2: descriptive price lines. When we loaded full history (bars), recompute the
      // 52-week high/low from the last ~252 bars so the lines match the data shown; otherwise
      // use the server's (narrow-window) lines.
      let priceLines = a.pricelines ?? [];
      if (bars && bars.length) {
        const last = rows.slice(-252);
        const hs = last.map((r) => r.c.high!).filter((v) => v != null);
        const ls = last.map((r) => r.c.low!).filter((v) => v != null);
        priceLines = hs.length && ls.length
          ? [{ price: Math.max(...hs), label: "52주 고가", color: "#1FA463" },
             { price: Math.min(...ls), label: "52주 저가", color: "#D1483A" }]
          : [];
      }
      priceLines.forEach((pl) =>
        candle.createPriceLine({
          price: pl.price, color: pl.color ?? "#86868C", lineStyle: LineStyle.Dashed,
          lineWidth: 1, axisLabelVisible: true, title: pl.label,
        }));
      const times = rows.map((r) => r.t);
      const snap = (d: string): string | null => {
        let r: string | null = null;
        for (const t of times) { if (t <= d) r = t; else break; }
        return r;
      };
      const byTime = new Map<string, ArtifactMarker>();
      const marks: SeriesMarker<Time>[] = [];
      (a.markers ?? []).forEach((m) => {
        const t = snap(m.time);
        if (!t) return;
        byTime.set(t, m);
        marks.push({
          time: t as Time, position: m.position === "aboveBar" ? "aboveBar" : "belowBar",
          color: m.color ?? "#4f8cff", shape: MARKER_SHAPE[m.kind ?? ""] ?? "circle", text: m.label,
        });
      });

      // PH-VIZ-3: agent-authored annotations — trend lines (2-pt line series), level lines
      // (price lines), date marks + zone edges (markers). Historical only (validated server-side).
      const ann = a.annotations;
      if (ann) {
        (ann.lines ?? []).forEach((l) => {
          const pts = [{ time: l.x1, value: l.y1 }, { time: l.x2, value: l.y2 }]
            .sort((p, q) => (p.time < q.time ? -1 : 1));
          const ls = chart.addLineSeries({
            color: l.color ?? "#4f8cff", lineWidth: 2, lastValueVisible: false,
            priceLineVisible: false, crosshairMarkerVisible: false, title: l.label ?? "",
          });
          ls.setData(pts.map((p) => ({ time: p.time as Time, value: p.value })));
        });
        (ann.hlines ?? []).forEach((h) =>
          candle.createPriceLine({
            price: h.price, color: h.color ?? "#9aa7bd", lineStyle: LineStyle.Dotted,
            lineWidth: 1, axisLabelVisible: true, title: h.label ?? "",
          }));
        (ann.vlines ?? []).forEach((v) => {
          const t = snap(v.time);
          if (t) marks.push({ time: t as Time, position: "aboveBar", color: v.color ?? "#D9A300", shape: "arrowDown", text: v.label ?? "" });
        });
        (ann.zones ?? []).forEach((z) => {
          const a0 = snap(z.t0), a1 = snap(z.t1);
          if (a0) marks.push({ time: a0 as Time, position: "belowBar", color: "#4f8cff", shape: "square", text: z.label ? `▸ ${z.label}` : "▸" });
          if (a1) marks.push({ time: a1 as Time, position: "belowBar", color: "#4f8cff", shape: "square", text: "◂" });
        });
      }

      if (marks.length) {
        marks.sort((x, y) => ((x.time as string) < (y.time as string) ? -1 : 1));
        candle.setMarkers(marks);
      }
      if (onEvidence) {
        chart.subscribeClick((param) => {
          if (drawMode) return;   // a click in draw mode is a drawing, not an evidence open
          const t = param.time as string | undefined;
          const m = t ? byTime.get(t) : undefined;
          if (m) onEvidence({
            tool: "chart", source: m.source ?? undefined, url: m.url ?? undefined,
            snippet: m.snippet ?? m.label, kind: "data", page: m.label, ticker: a.ticker ?? undefined,
          });
        });
      }
    } else {
      // money amounts (revenue/income) render as BARS; ratios/other series stay lines. The
      // builder flags this via chart_style="bar" (rebase/index-to-100 only applies to lines).
      const asBars = a.chart_style === "bar" && !rebase;
      lineData.forEach((s, i) => {
        const pts = s.points
          .map((p) => ({ t: toTime(p.x), y: p.y }))
          .filter((p): p is { t: string; y: number } => p.t != null && p.y != null);
        if (!pts.length) return;
        const base = pts[0].y;
        if (asBars) {
          const bar = chart.addHistogramSeries({ color: LINE_COLORS[i % LINE_COLORS.length], title: s.label, priceScaleId: "right" });
          if (!mainSeries) mainSeries = bar;
          bar.setData(pts.map((p) => ({ time: p.t as Time, value: p.y })));
        } else {
          const line = chart.addLineSeries({ color: LINE_COLORS[i % LINE_COLORS.length], lineWidth: 2, title: s.label });
          if (!mainSeries) mainSeries = line;
          line.setData(pts.map((p) => ({
            time: p.t as Time,
            value: rebase && base ? ((p.y / base) - 1) * 100 : (s.unit === "ratio" ? p.y * 100 : p.y),
          })));
        }
        const lt = pts[pts.length - 1].t;
        if (!lastTime || lt > lastTime) lastTime = lt;
      });
    }

    // PH-VIZ-4: render technical indicators. Price-pane lines (SMA/EMA/Bollinger) draw on
    // the right price scale over the price; sub-pane indicators (RSI/MACD/volatility) stack
    // in bands at the bottom, each on its own overlay scale. Descriptive — never a signal.
    const priceOv = overlays.filter((o) => (o.pane ?? "price") !== "sub");
    const subOv = overlays.filter((o) => (o.pane ?? "price") === "sub");
    const S = subOv.length;
    const SUB_H = 0.16;
    const subTotal = Math.min(S * SUB_H, 0.48);   // fraction of height reserved for sub-panes
    const bh = S ? subTotal / S : 0;

    if (subTotal > 0) {
      // free room below the price for the sub-pane stack (+ keep volume above it)
      chart.priceScale("right").applyOptions({ scaleMargins: { top: 0.05, bottom: subTotal + (hasVol ? 0.14 : 0.04) } });
      if (hasVol) chart.priceScale("").applyOptions({ scaleMargins: { top: 1 - subTotal - 0.12, bottom: subTotal } });
    }

    priceOv.forEach((o) =>
      o.lines.forEach((l) => {
        const pts = overlayPoints(l.points);
        if (!pts.length) return;
        const s = chart.addLineSeries({
          color: l.color ?? "#86868C", lineWidth: 1, priceScaleId: "right",
          lastValueVisible: false, priceLineVisible: false, crosshairMarkerVisible: false, title: l.label,
        });
        if (!mainSeries) mainSeries = s;
        s.setData(pts.map((p) => ({ time: p.t as Time, value: p.value })));
        const lt = pts[pts.length - 1].t;
        if (!lastTime || lt > lastTime) lastTime = lt;
      }));

    subOv.forEach((o, j) => {
      const scaleId = `sub_${j}`;
      const top = (1 - subTotal) + j * bh + 0.01;
      const bottom = (S - 1 - j) * bh + 0.01;
      let first: ISeriesApi<"Line"> | null = null;
      o.lines.forEach((l, li) => {
        const pts = overlayPoints(l.points);
        if (!pts.length) return;
        const s = chart.addLineSeries({
          color: l.color ?? LINE_COLORS[li % LINE_COLORS.length], lineWidth: 1, priceScaleId: scaleId,
          lastValueVisible: false, priceLineVisible: false, crosshairMarkerVisible: false, title: l.label,
        });
        s.priceScale().applyOptions({ scaleMargins: { top, bottom } });
        s.setData(pts.map((p) => ({ time: p.t as Time, value: p.value })));
        if (!first) first = s;
        const lt = pts[pts.length - 1].t;
        if (!lastTime || lt > lastTime) lastTime = lt;
      });
      // RSI context bounds (30/70) — descriptive reference levels, not buy/sell signals.
      if (o.unit === "ratio_0_100" && first) {
        [30, 70].forEach((lvl) => (first as ISeriesApi<"Line">).createPriceLine({
          price: lvl, color: "rgba(150,150,160,0.35)", lineStyle: LineStyle.Dotted,
          lineWidth: 1, axisLabelVisible: false, title: String(lvl),
        }));
      }
    });

    // PH-VIZ-5: render the user's own drawings (distinct accent style) — trend lines as a
    // 2-point series, horizontal lines on the main price scale. Works in every chart mode.
    if (userAnn && mainSeries) {
      const ms = mainSeries;
      (userAnn.lines ?? []).forEach((l) => {
        const t1 = toTime(l.x1), t2 = toTime(l.x2);
        if (!t1 || !t2) return;
        const pts = [{ time: t1, value: l.y1 }, { time: t2, value: l.y2 }]
          .sort((p, q) => (p.time < q.time ? -1 : 1));
        const ls = chart.addLineSeries({
          color: l.color ?? "#E8E8EA", lineWidth: 2, lastValueVisible: false,
          priceLineVisible: false, crosshairMarkerVisible: false, title: l.label ?? "",
        });
        ls.setData(pts.map((p) => ({ time: p.time as Time, value: p.value })));
      });
      (userAnn.hlines ?? []).forEach((h) =>
        ms.createPriceLine({
          price: h.price, color: h.color ?? "#E8E8EA", lineStyle: LineStyle.Solid,
          lineWidth: 1, axisLabelVisible: true, title: h.label ?? "",
        }));
    }

    // PH-VIZ-5: capture drawing clicks → (time, price) → append to the user annotations.
    if (onDraw && drawMode) {
      const ms = mainSeries;
      chart.subscribeClick((param) => {
        if (!ms || !param.point || param.time == null) return;
        const price = ms.coordinateToPrice(param.point.y);
        const time = param.time as string;
        if (price == null) return;
        const px = Math.round(price * 10000) / 10000;
        if (drawMode === "hline") {
          const next: ChartAnnotations = {
            ...(userAnn ?? {}),
            hlines: [...(userAnn?.hlines ?? []), { price: px, label: String(px) }],
          };
          onDraw(next);
          setDrawMode(null);
        } else {
          if (!pending.current) {
            pending.current = { time, price: px };
          } else {
            const p0 = pending.current;
            pending.current = null;
            const next: ChartAnnotations = {
              ...(userAnn ?? {}),
              lines: [...(userAnn?.lines ?? []), { x1: p0.time, y1: p0.price, x2: time, y2: px }],
            };
            onDraw(next);
            setDrawMode(null);
          }
        }
      });
    }

    // HL-8(b): regime shading — semi-transparent background spans for the fetched regimes
    // (annotations.zones), behind the series. Only meaningful on long ranges; the primitive
    // clips to the visible window itself.
    let detachZones: (() => void) | null = null;
    if (mainSeries && a.annotations?.zones?.length) {
      detachZones = attachRegimeZones(mainSeries as any, a.annotations.zones.map((z) => ({
        t0: toTime(z.t0) ?? z.t0, t1: toTime(z.t1) ?? z.t1, label: z.label, color: z.color,
      })));
    }

    // HL-8(a): underwater (drawdown %) sub-pane — computed peer-to-date from the chart's OWN
    // close series (no extra fetch). Its own bottom scale; area under zero.
    if (underwater) {
      const closes = isCandle
        ? candleData.map((c) => ({ time: toTime(c.time) ?? "", value: c.close ?? 0 }))
        : (lineData[0]?.points ?? []).map((p) => ({ time: toTime(p.x) ?? "", value: p.y ?? 0 }));
      const uw = underwaterSeries(closes.filter((c) => c.time && c.value > 0));
      if (uw.length) {
        const uwSeries = chart.addAreaSeries({
          priceScaleId: "uw", lineColor: "rgba(209,72,58,0.7)", lineWidth: 1,
          topColor: "rgba(209,72,58,0.04)", bottomColor: "rgba(209,72,58,0.22)",
          priceFormat: { type: "custom", formatter: (v: number) => `${v.toFixed(0)}%` },
          lastValueVisible: false, priceLineVisible: false,
        });
        uwSeries.setData(uw.map((u) => ({ time: u.time as Time, value: u.value })));
        uwSeries.priceScale().applyOptions({ scaleMargins: { top: 0.78, bottom: 0 } });
        chart.priceScale("right").applyOptions({ scaleMargins: { top: 0.05, bottom: 0.26 } });
      }
    }

    // Apply the visible window. Deterministic — no view state to lose when the effect re-runs after
    // the async `bars` history lands (that data-only rebuild was the "resets to MAX a few seconds
    // later" bug: with range="MAX"→fitContent, the chart re-fit to the full ~8y history the moment
    // `bars` replaced the agent's narrow candles).
    const days = RANGES.find(([k]) => k === range)?.[1] ?? 0;
    // AUTO (default): the point-in-time window = the agent's OWN candle span (a.candles, NOT the
    // generous fetched `bars`), so it's stable across the bars-load rebuild. The right edge extends
    // to the latest loaded bar so freshly-fetched newer bars aren't scrolled off-screen on a
    // reopened/pinned card. Only for candlestick charts; financials/overlay charts (no a.candles)
    // fall through to fitContent so a wider fetched series (finSeries) still shows its full range.
    const anchorFrom = range === AUTO && (a.candles?.length ?? 0) > 0 ? toTime(a.candles![0].time) : null;
    if (days && lastTime) {
      // a fixed lookback preset (1M/3M/6M/1Y/5Y) ending at the latest bar
      try {
        chart.timeScale().setVisibleRange({ from: daysBefore(lastTime, days) as Time, to: lastTime as Time });
      } catch { chart.timeScale().fitContent(); }
    } else if (anchorFrom && lastTime) {
      try {
        chart.timeScale().setVisibleRange({ from: anchorFrom as Time, to: lastTime as Time });
      } catch { chart.timeScale().fitContent(); }
    } else {
      // MAX (explicit), or AUTO with no candle span → fit everything available
      chart.timeScale().fitContent();
    }

    chartRef.current = chart;
    // never apply a 0 width — a temporarily hidden container (view/tab switch, sheet transition)
    // reports clientWidth 0 and a 0-width chart renders blank ("차트가 꺼짐").
    const ro = new ResizeObserver(() => {
      const w = el.clientWidth;
      if (w > 0) chart.applyOptions({ width: w, ...(fillHeight ? { height: el.clientHeight || 220 } : {}) });
    });
    ro.observe(el);
    return () => { ro.disconnect(); detachZones?.(); chart.remove(); chartRef.current = null; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [dataSig, currency, range, logScale, rebase, isCandle, drawMode, onDraw, fillHeight, underwater]);

  const hasDrawings = (userAnn?.lines?.length || 0) + (userAnn?.hlines?.length || 0) > 0;

  // PH-VIZ-6: export the chart as a self-describing PNG — title header + a sourced footer
  // (source · as_of · finnote) so the snapshot can be cited/shared like any source card.
  function exportPng() {
    const chart = chartRef.current;
    if (!chart) return;
    const shot = chart.takeScreenshot();   // canvas at the chart's pixel resolution
    const dpr = Math.max(1, Math.round(shot.width / (box.current?.clientWidth || shot.width)));
    const headH = 30 * dpr, footH = 26 * dpr, pad = 14 * dpr;
    const out = document.createElement("canvas");
    out.width = shot.width;
    out.height = shot.height + headH + footH;
    const ctx = out.getContext("2d");
    if (!ctx) return;
    ctx.fillStyle = "#0E0E10";
    ctx.fillRect(0, 0, out.width, out.height);
    ctx.textBaseline = "middle";
    ctx.fillStyle = "#E8E8EA";
    ctx.font = `600 ${14 * dpr}px ui-sans-serif, system-ui, sans-serif`;
    ctx.fillText(a.title || "차트", pad, headH / 2 + 2 * dpr);
    ctx.drawImage(shot, 0, headH);
    ctx.fillStyle = "#86868C";
    ctx.font = `${11 * dpr}px ui-sans-serif, system-ui, sans-serif`;
    const foot = `${a.source || "출처"}${a.as_of ? ` · as of ${a.as_of}` : ""} · finnote`;
    ctx.fillText(foot, pad, headH + shot.height + footH / 2);
    const link = document.createElement("a");
    link.href = out.toDataURL("image/png");
    link.download = `${(a.ticker || a.title || "chart").toString().replace(/\s+/g, "_")}.png`;
    link.click();
  }

  return (
    <div className={`tradechart${fillHeight ? " tc-fill" : ""}`}>
      {!compact && (
      <div className="tc-toolbar">
        <div className="tc-ranges">
          {RANGES.map(([k]) => (
            <button key={k} type="button" className={range === k ? "on" : ""} onClick={() => setRange(k)}>{k}</button>
          ))}
        </div>
        <div className="tc-toggles">
          <button type="button" className={logScale ? "on" : ""} onClick={() => setLogScale((v) => !v)} title="로그 스케일">log</button>
          {!isCandle && lineCount >= 1 && (
            <button type="button" className={rebase ? "on" : ""} onClick={() => setRebase((v) => !v)} title="시작점=100 기준 % 변화">% 기준</button>
          )}
          {/* HL-8(a): drawdown (underwater) sub-pane */}
          <button type="button" className={underwater ? "on" : ""} onClick={() => setUnderwater((v) => !v)}
            title="고점 대비 낙폭(%) 하단 패널">낙폭</button>
          {/* PH-VIZ-5: drawing tools (only when the parent persists them via onDraw) */}
          {onDraw && (
            <>
              <span className="tc-sep" />
              <button type="button" className={drawMode === "trend" ? "on" : ""}
                onClick={() => { pending.current = null; setDrawMode((m) => (m === "trend" ? null : "trend")); }}
                title="추세선: 두 점을 클릭">✏ 추세선</button>
              <button type="button" className={drawMode === "hline" ? "on" : ""}
                onClick={() => { pending.current = null; setDrawMode((m) => (m === "hline" ? null : "hline")); }}
                title="수평선: 한 점을 클릭">─ 수평선</button>
              {hasDrawings && (
                <button type="button" onClick={() => { pending.current = null; setDrawMode(null); onDraw(null); }}
                  title="내 드로잉 지우기">🗑 지우기</button>
              )}
            </>
          )}
          {/* PH-VIZ-6: export the (annotated) chart as a sourced PNG */}
          <span className="tc-sep" />
          <button type="button" onClick={exportPng} title="차트를 출처 포함 PNG로 내보내기">📸 PNG</button>
        </div>
      </div>
      )}
      <div ref={box} className={`tc-canvas${fillHeight ? " tc-canvas-fill" : ""}${drawMode ? " drawing" : ""}`} />
      {/* HL-8c: vol-context ribbon — realized vol + self-history percentile (+ VIX level) */}
      {a.vol_context?.windows && Object.keys(a.vol_context.windows).length > 0 && (() => {
        const vc = a.vol_context!;
        const ws = Object.entries(vc.windows!).sort((x, y) => Number(x[0]) - Number(y[0]));
        const pctLabel = (p?: number | null) => p == null ? "—"
          : `${Math.round(p)}p${p >= 80 ? " · 상위권" : p <= 20 ? " · 하위권" : ""}`;
        return (
          <div className="tc-vol mono" title="실현변동성(연율)과 자체 히스토리 퍼센타일 · 과거 기록">
            <span className="tc-vol-h">변동성</span>
            {ws.map(([w, v]) => (
              <span key={w} className="tc-vol-item">
                {w}일 <b>{v.realized_vol_pct != null ? `${v.realized_vol_pct.toFixed(1)}%` : "—"}</b>
                <span className="tc-vol-pct">{pctLabel(v.percentile)}</span>
              </span>
            ))}
            {vc.level && (
              <span className="tc-vol-item">VIX <b>{vc.level.current ?? "—"}</b>
                <span className="tc-vol-pct">{pctLabel(vc.level.percentile)}</span></span>
            )}
            <span className="tc-vol-src">{vc.source || "출처"}{vc.as_of ? ` · ${vc.as_of}` : ""}</span>
          </div>
        );
      })()}
      {drawMode && (
        <div className="tc-note">
          {drawMode === "trend" ? "추세선: 시작점과 끝점을 차례로 클릭하세요." : "수평선: 차트에서 원하는 가격대를 클릭하세요."}
        </div>
      )}
      {a.annotations?.note && <div className="tc-note">✎ {a.annotations.note}</div>}
    </div>
  );
}
