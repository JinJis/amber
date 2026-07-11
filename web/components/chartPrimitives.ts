// HL-8 — regime-zone background shading as a Lightweight-Charts series primitive (v4.2 API).
// Native charts have no background rectangles; we draw semi-transparent ink fills + tiny mono
// labels behind the series (zOrder 'bottom' · drawBackground). Descriptive era markers only —
// the shaded spans are the dot-com/GFC/COVID regimes fetched with the artifact, never signals.

import type {
  IChartApi, ISeriesApi, ISeriesPrimitive, ISeriesPrimitivePaneRenderer,
  ISeriesPrimitivePaneView, SeriesAttachedParameter, SeriesType, Time,
} from "lightweight-charts";

export type RegimeZone = { t0: string; t1: string; label?: string | null; color?: string | null };

type CanvasTarget = {
  useBitmapCoordinateSpace: (cb: (scope: {
    context: CanvasRenderingContext2D; bitmapSize: { width: number; height: number };
    horizontalPixelRatio: number; verticalPixelRatio: number;
  }) => void) => void;
};

class RegimeRenderer implements ISeriesPrimitivePaneRenderer {
  constructor(private readonly p: RegimeZones) {}

  draw() { /* fills go in the background layer */ }

  drawBackground(target: CanvasTarget) {
    const chart = this.p.chart;
    if (!chart) return;
    const ts = chart.timeScale();
    target.useBitmapCoordinateSpace(({ context: ctx, bitmapSize, horizontalPixelRatio: hpr, verticalPixelRatio: vpr }) => {
      for (const z of this.p.zones) {
        const x0 = ts.timeToCoordinate(z.t0 as unknown as Time);
        const x1 = ts.timeToCoordinate(z.t1 as unknown as Time);
        if (x0 == null && x1 == null) continue;
        // clamp to the visible pane when one edge is off-screen
        const left = Math.max(0, (x0 ?? 0) * hpr);
        const right = Math.min(bitmapSize.width, (x1 ?? (bitmapSize.width / hpr)) * hpr);
        if (right <= left) continue;
        ctx.fillStyle = z.color ?? "rgba(26,27,30,0.055)";  // 4-5% ink
        ctx.fillRect(left, 0, right - left, bitmapSize.height);
        // left divider + tiny mono label at the top
        ctx.fillStyle = "rgba(26,27,30,0.18)";
        ctx.fillRect(left, 0, Math.max(1, hpr), bitmapSize.height);
        if (z.label) {
          ctx.save();
          ctx.font = `${10 * vpr}px "Space Mono", ui-monospace, monospace`;
          ctx.fillStyle = "rgba(26,27,30,0.55)";
          ctx.textBaseline = "top";
          ctx.fillText(z.label, left + 4 * hpr, 4 * vpr);
          ctx.restore();
        }
      }
    });
  }
}

class RegimeView implements ISeriesPrimitivePaneView {
  private readonly r: RegimeRenderer;
  constructor(p: RegimeZones) { this.r = new RegimeRenderer(p); }
  zOrder() { return "bottom" as const; }
  renderer() { return this.r; }
}

export class RegimeZones implements ISeriesPrimitive<Time> {
  chart: IChartApi | null = null;
  private readonly view: RegimeView;
  private req: (() => void) | null = null;

  constructor(public zones: RegimeZone[]) { this.view = new RegimeView(this); }

  attached(param: SeriesAttachedParameter<Time, SeriesType>) {
    this.chart = param.chart as unknown as IChartApi;
    this.req = param.requestUpdate;
    this.req?.();
  }
  detached() { this.chart = null; this.req = null; }
  updateAllViews() { /* zones are static per attach */ }
  paneViews() { return [this.view]; }
}

/** Attach regime shading to a series; returns a detach fn (or null if nothing to draw). */
export function attachRegimeZones(
  series: ISeriesApi<SeriesType>, zones: RegimeZone[] | undefined,
): (() => void) | null {
  if (!zones || zones.length === 0) return null;
  const prim = new RegimeZones(zones);
  series.attachPrimitive(prim as unknown as ISeriesPrimitive<Time>);
  return () => { try { series.detachPrimitive(prim as unknown as ISeriesPrimitive<Time>); } catch { /* chart gone */ } };
}

// HL-8(a) — underwater (drawdown %) computed from a close series, peak-to-date. Pure → tested.
export function underwaterSeries(
  closes: { time: string; value: number }[],
): { time: string; value: number }[] {
  let peak = -Infinity;
  const out: { time: string; value: number }[] = [];
  for (const p of closes) {
    if (!(p.value > 0)) continue;
    if (p.value > peak) peak = p.value;
    out.push({ time: p.time, value: peak > 0 ? (p.value / peak - 1) * 100 : 0 });
  }
  return out;
}
