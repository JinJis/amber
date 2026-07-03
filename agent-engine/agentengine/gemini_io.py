"""Gemini request/response serialization for the planner.

Shapes our (conversation, history, task) into ``google.genai`` Contents, builds
the function-declaration schema from a tool manifest entry, and pulls text back
out of a response. Extracted from ``planner.py``; ``GeminiPlanner`` uses these and
``planner.py`` re-exports them (tests reference ``_to_gemini_contents``/``_schema``).
The genai import stays lazy so the stub backend needs no SDK.
"""

from __future__ import annotations


def genai_client():
    """A ``google.genai`` Client with a bounded per-request timeout (``gemini_timeout_seconds``).
    The single place clients are built — without the timeout a stalled Gemini call hangs the SSE
    stream forever; with it the call raises and the caller degrades gracefully."""
    from google import genai
    from google.genai import types

    from agentengine.config import settings

    return genai.Client(
        http_options=types.HttpOptions(timeout=int(settings.gemini_timeout_seconds * 1000)),
    )


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
            parts=[types.Part.from_function_response(name=dec.tool, response=response_data)],
        ))

    return out


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
