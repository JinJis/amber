"""Gemini request/response serialization for the planner.

Shapes our (conversation, history, task) into ``google.genai`` Contents, builds
the function-declaration schema from a tool manifest entry, and pulls text back
out of a response. Extracted from ``planner.py``; ``GeminiPlanner`` uses these and
``planner.py`` re-exports them (tests reference ``_to_gemini_contents``/``_schema``).
The genai import stays lazy so the stub backend needs no SDK.
"""

from __future__ import annotations

import asyncio
import functools
import logging
import random
from concurrent.futures import ThreadPoolExecutor
from functools import lru_cache

logger = logging.getLogger("agentengine")


@lru_cache(maxsize=1)
def genai_client():
    """A ``google.genai`` Client with a bounded per-request timeout (``gemini_timeout_seconds``).
    The single place clients are built, and MEMOIZED (CR-5): every call site shares ONE client +
    httpx pool instead of building a fresh client (new creds read + new pool) per call. Without the
    timeout a stalled Gemini call hangs the SSE stream forever; with it the call raises and degrades."""
    from google import genai
    from google.genai import types

    from agentengine.config import settings

    return genai.Client(
        http_options=types.HttpOptions(timeout=int(settings.gemini_timeout_seconds * 1000)),
    )


@lru_cache(maxsize=1)
def _executor() -> ThreadPoolExecutor:
    """CR-5: a DEDICATED thread pool for the blocking generate_content calls, so they neither
    contend with the default asyncio to_thread pool (~min(32, cpu+4)≈12) nor let its queue wait
    sit outside the per-call timeout. Sized above the sum of the per-model caps."""
    from agentengine.config import settings
    return ThreadPoolExecutor(max_workers=max(4, settings.gemini_executor_workers),
                              thread_name_prefix="gemini")


# Per-(event loop, model) concurrency gates. Keyed by the running loop id so the same object is
# never shared across loops (tests each run in a fresh loop); in prod there is one loop.
_sems: dict[tuple[int, str], asyncio.Semaphore] = {}


def _tier_sem(model: str) -> asyncio.Semaphore:
    from agentengine.config import settings
    key = (id(asyncio.get_running_loop()), model)
    sem = _sems.get(key)
    if sem is None:
        sem = asyncio.Semaphore(max(1, settings.gemini_max_concurrent_per_model))
        _sems[key] = sem
    return sem


def _is_rate_limit(exc: BaseException) -> bool:
    """True for retryable transient errors (rate limit / overloaded / transient 5xx), NOT for
    schema/validation/auth errors — those must fail fast, not burn the retry budget."""
    s = f"{type(exc).__name__} {exc}".lower()
    return any(t in s for t in (
        "429", "resource_exhausted", "rate limit", "rate-limit", "quota",
        "503", "unavailable", "overloaded", "500", "internal error", "deadline",
    ))


async def generate(model: str, *, stream: bool = False, retries: int | None = None, **kw):
    """THE single Gemini entry point (CR-5): shared client + dedicated executor + per-model
    concurrency cap + 429/503 exponential backoff. ``stream=True`` returns the streaming iterator
    (the OPEN is gated; per-chunk polling stays uncapped so streaming isn't serialized). Retries
    only true rate-limit / transient-5xx; anything else propagates immediately."""
    from agentengine.config import settings

    if retries is None:
        retries = settings.gemini_max_retries
    client = genai_client()
    fn = client.models.generate_content_stream if stream else client.models.generate_content
    loop = asyncio.get_running_loop()
    call = functools.partial(fn, model=model, **kw)
    last: BaseException | None = None
    for attempt in range(retries + 1):
        try:
            async with _tier_sem(model):
                return await loop.run_in_executor(_executor(), call)
        except Exception as exc:  # noqa: BLE001
            last = exc
            if attempt >= retries or not _is_rate_limit(exc):
                raise
            delay = 0.7 * (2 ** attempt) + random.uniform(0, 0.5)
            logger.warning("gemini %s transient error (attempt %d/%d), backoff %.1fs: %s: %s",
                           model, attempt + 1, retries, delay, type(exc).__name__, exc)
            await asyncio.sleep(delay)
    raise last  # pragma: no cover — loop always returns or raises


def _get_text_from_response(resp) -> str | None:
    if not resp.candidates or not resp.candidates[0].content or not resp.candidates[0].content.parts:
        return None
    texts = []
    for part in resp.candidates[0].content.parts:
        if part.text:
            texts.append(part.text)
    return "".join(texts) if texts else None


def _to_gemini_contents(conversation: list | None, history: list, task: str):
    from google.genai import types

    out = []
    if conversation:
        for m in conversation:
            c = m.get("content")
            if not c:
                continue
            role = "model" if m.get("role") == "assistant" else "user"
            out.append(types.Content(role=role, parts=[types.Part.from_text(text=c)]))

    if not out:
        out.append(types.Content(role="user", parts=[types.Part.from_text(text=task)]))

    seen_raw: set[int] = set()
    for dec, res in history:
        # Function Call(s). Prefer replaying the model's RAW content verbatim — it carries ALL
        # the turn's function_call parts WITH their thought_signatures, which parallel calls
        # require (reconstructing part-by-part drops a signature → Gemini 400). One raw content
        # can back several (dec, res) entries from the same batch, so emit it only once.
        raw = getattr(dec, "raw_content", None)
        if raw is not None:
            rid = id(raw)
            if rid not in seen_raw:
                seen_raw.add(rid)
                out.append(raw)
        else:  # fallback (stub / single reconstructed call)
            if dec.thought_signature:
                model_part = types.Part(
                    function_call=types.FunctionCall(name=dec.tool, args=dec.args or {}),
                    thought_signature=dec.thought_signature,
                )
            else:
                model_part = types.Part.from_function_call(name=dec.tool, args=dec.args or {})
            out.append(types.Content(role="model", parts=[model_part]))

        # Function Response (one per call — matches each function_call in the model turn).
        response_data = res.get("data")
        if not isinstance(response_data, dict):
            response_data = {"result": response_data}
        out.append(types.Content(
            role="tool",
            parts=[types.Part.from_function_response(name=dec.tool, response=_compact_response(response_data))],
        ))

    return out


def _compact_response(response_data: dict) -> dict:
    """ME-12: cap the serialized size of a tool result replayed into the planner context, so a
    large payload (full price series, filing text, macro panel) isn't re-sent in full on every
    subsequent replan step. The full data still flows to the answer via citations/artifacts."""
    import json

    from agentengine.config import settings

    cap = settings.gemini_plan_context_max_chars
    if cap <= 0:
        return response_data
    try:
        s = json.dumps(response_data, ensure_ascii=False, default=str)
    except Exception:  # noqa: BLE001 — non-serializable → leave as-is for the SDK to handle
        return response_data
    if len(s) <= cap:
        return response_data
    return {"_truncated": s[:cap],
            "_note": f"result truncated to {cap} chars for planning; full data is in the cited sources"}


def _schema(tool: dict) -> dict:
    props, required = {}, []
    for p in tool.get("params", []):
        prop = {"type": p.get("type", "string").upper() if p.get("type") in ("integer", "number", "boolean") else "STRING"}
        if p.get("enum"):
            prop["enum"] = p["enum"]
        if p.get("description"):
            prop["description"] = p["description"]
        props[p["name"]] = prop
        if p.get("required"):
            required.append(p["name"])
    return {"type": "OBJECT", "properties": props, "required": required}
