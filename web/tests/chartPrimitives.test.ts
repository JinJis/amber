// HL-8 — chart primitives: underwater (peak-to-date drawdown) + regime attach guards.
import { describe, expect, it, vi } from "vitest";
import { attachRegimeZones, underwaterSeries } from "../components/chartPrimitives";

describe("underwaterSeries (HL-8a)", () => {
  it("computes peak-to-date drawdown %; new highs read 0", () => {
    const uw = underwaterSeries([
      { time: "2020-01-01", value: 100 },
      { time: "2020-02-01", value: 120 },   // new peak → 0
      { time: "2020-03-01", value: 60 },    // −50% from 120
      { time: "2020-04-01", value: 90 },    // −25% from 120
      { time: "2020-05-01", value: 150 },   // new peak → 0
    ]);
    expect(uw.map((u) => Math.round(u.value))).toEqual([0, 0, -50, -25, 0]);
  });
  it("skips non-positive values and never fabricates", () => {
    expect(underwaterSeries([{ time: "t", value: 0 }, { time: "u", value: -5 }])).toEqual([]);
  });
});

describe("attachRegimeZones (HL-8b)", () => {
  it("no-ops on empty zones (returns null, never touches the series)", () => {
    const series = { attachPrimitive: vi.fn(), detachPrimitive: vi.fn() } as any;
    expect(attachRegimeZones(series, [])).toBeNull();
    expect(attachRegimeZones(series, undefined)).toBeNull();
    expect(series.attachPrimitive).not.toHaveBeenCalled();
  });
  it("attaches a primitive and returns a working detach fn", () => {
    const series = { attachPrimitive: vi.fn(), detachPrimitive: vi.fn() } as any;
    const detach = attachRegimeZones(series, [{ t0: "2000-03-01", t1: "2002-10-01", label: "닷컴버블" }]);
    expect(series.attachPrimitive).toHaveBeenCalledOnce();
    detach?.();
    expect(series.detachPrimitive).toHaveBeenCalledOnce();
  });
});
