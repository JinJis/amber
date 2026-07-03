"""PH-VIZ-3: agent-driven chart annotations.

When the user's question implies marking up a price chart ("draw a line from the 2024
low to the 2025 high", "highlight 2024-09-28", "mark the drawdown"), **Gemini** decides
WHAT to annotate from the question + the actual price data (no hardcoded keyword rules —
invariant #9). It returns a structured spec of trend lines / level lines / vertical
markers / zones that we validate against the real candles.

Guardrail (PH-VIZ): descriptive over HISTORICAL data only. Every annotation point must
fall within the chart's date range — anything in the future is dropped, so there are no
projections or price targets.
"""

from __future__ import annotations

import json
import logging

from agentengine.models import (
    Artifact,
    ChartAnnotations,
    ChartHLine,
    ChartLine,
    ChartVLine,
    ChartZone,
)

log = logging.getLogger(__name__)

_SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "lines": {"type": "ARRAY", "items": {"type": "OBJECT", "properties": {
            "x1": {"type": "STRING"}, "y1": {"type": "NUMBER"}, "x2": {"type": "STRING"}, "y2": {"type": "NUMBER"},
            "label": {"type": "STRING"}}, "required": ["x1", "y1", "x2", "y2"]}},
        "hlines": {"type": "ARRAY", "items": {"type": "OBJECT", "properties": {
            "price": {"type": "NUMBER"}, "label": {"type": "STRING"}}, "required": ["price"]}},
        "vlines": {"type": "ARRAY", "items": {"type": "OBJECT", "properties": {
            "time": {"type": "STRING"}, "label": {"type": "STRING"}}, "required": ["time"]}},
        "zones": {"type": "ARRAY", "items": {"type": "OBJECT", "properties": {
            "t0": {"type": "STRING"}, "t1": {"type": "STRING"}, "label": {"type": "STRING"}}, "required": ["t0", "t1"]}},
        "rebase": {"type": "BOOLEAN"},
        "note": {"type": "STRING"},
    },
}

_SYSTEM = (
    "You annotate a stock PRICE chart to answer the user's request. You are given the real "
    "daily price points (date, low, high, close). Choose annotations that map to ACTUAL points "
    "in that data:\n"
    "- lines: a straight segment between two historical points (e.g. a low to a later high). "
    "Use real dates from the data and the matching low/high price.\n"
    "- hlines: a horizontal level (e.g. a support/resistance the user names, or a notable close).\n"
    "- vlines: mark a specific date.\n"
    "- zones: shade a date range (t0..t1).\n"
    "Rules: ONLY use dates that appear in the provided range — never invent a future date, a "
    "projection, a price target, or a trend extrapolation. If the user didn't ask for any "
    "markup, return empty arrays. Keep it minimal (at most a few annotations)."
)


def _candle_digest(a: Artifact, max_points: int = 80) -> tuple[str, str, str, float, float]:
    """A compact (date,low,high,close) digest for the prompt + the (first,last,minLow,maxHigh)
    bounds used to validate the model's output. Downsampled to keep the prompt small."""
    cs = [c for c in a.candles if c.time]
    step = max(1, len(cs) // max_points)
    sample = cs[::step]
    if cs and sample and sample[-1].time != cs[-1].time:
        sample.append(cs[-1])
    lines = "\n".join(
        f"{c.time}\tlow={c.low}\thigh={c.high}\tclose={c.close}" for c in sample
    )
    lows = [c.low for c in cs if c.low is not None]
    highs = [c.high for c in cs if c.high is not None]
    return (lines, cs[0].time, cs[-1].time, (min(lows) if lows else 0.0), (max(highs) if highs else 0.0))


async def _gemini_annotate(model: str, question: str, digest: str, ticker: str) -> dict:
    """One Gemini structured call → raw annotation dict. [] / {} on any error."""
    import asyncio

    from google import genai
    from google.genai import types

    user = (f"Ticker: {ticker}\nUser request: {question}\n\nPrice points (date / low / high / close):\n{digest}\n\n"
            "Return the annotation spec.")
    cfg = types.GenerateContentConfig(system_instruction=_SYSTEM, temperature=0.1,
                                      response_mime_type="application/json", response_schema=_SCHEMA)
    try:
        from agentengine.gemini_io import genai_client
        client = genai_client()  # bounded request timeout (no infinite SSE hang)
        resp = await asyncio.to_thread(
            client.models.generate_content, model=model,
            contents=[types.Content(role="user", parts=[types.Part.from_text(text=user)])], config=cfg)
        return json.loads(resp.text or "{}")
    except Exception as exc:  # noqa: BLE001 — degrade to no annotations, never crash the turn
        log.warning("annotate: gemini failed for %s: %s", ticker, exc)
        return {}


def _validate(raw: dict, lo_t: str, hi_t: str, lo_p: float, hi_p: float) -> ChartAnnotations | None:
    """Keep only annotations whose dates fall inside the chart range (no future = no
    projection) and whose prices are within a sane band. Drop everything else."""
    def in_t(t) -> bool:
        return isinstance(t, str) and len(t) == 10 and lo_t <= t <= hi_t

    def in_p(p) -> bool:
        try:
            return (lo_p * 0.5) <= float(p) <= (hi_p * 1.5 if hi_p else float(p))
        except (TypeError, ValueError):
            return False

    lines = [ChartLine(x1=l["x1"], y1=float(l["y1"]), x2=l["x2"], y2=float(l["y2"]), label=l.get("label"), color="#4f8cff")
             for l in (raw.get("lines") or []) if in_t(l.get("x1")) and in_t(l.get("x2")) and in_p(l.get("y1")) and in_p(l.get("y2"))]
    hlines = [ChartHLine(price=float(h["price"]), label=h.get("label"), color="#9aa7bd")
              for h in (raw.get("hlines") or []) if in_p(h.get("price"))]
    vlines = [ChartVLine(time=v["time"], label=v.get("label"), color="#D9A300")
              for v in (raw.get("vlines") or []) if in_t(v.get("time"))]
    zones = [ChartZone(t0=z["t0"], t1=z["t1"], label=z.get("label"), color="rgba(79,140,255,0.10)")
             for z in (raw.get("zones") or []) if in_t(z.get("t0")) and in_t(z.get("t1"))]
    if not (lines or hlines or vlines or zones or raw.get("rebase")):
        return None
    return ChartAnnotations(lines=lines[:6], hlines=hlines[:6], vlines=vlines[:8], zones=zones[:4],
                            rebase=bool(raw.get("rebase")), note=raw.get("note"))


async def annotate_charts(artifacts: list[Artifact], question: str, model: str, backend: str) -> None:
    """Attach agent-authored annotations to the price chart(s). Gemini-only (the stub
    planner has no judgment); mutates artifacts in place. Best-effort — never raises."""
    if backend != "gemini" or not question:
        return
    for a in artifacts:
        if a.kind != "candlestick" or not a.candles or a.annotations is not None:
            continue
        digest, lo_t, hi_t, lo_p, hi_p = _candle_digest(a)
        if not digest:
            continue
        raw = await _gemini_annotate(model, question, digest, a.ticker or "")
        ann = _validate(raw, lo_t, hi_t, lo_p, hi_p) if isinstance(raw, dict) else None
        if ann:
            a.annotations = ann
