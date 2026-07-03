#!/usr/bin/env python3
"""Quality-evaluation harness for the investment-agent platform.

Drives each scenario in `scenarios.py` through the REAL product path:
  provision a user → create an agent with chosen data sources (studio-api /agents)
  → ask the question (studio-api /chat/stream) → parse the SSE → grade.

Graders check that the agent fetched from the right source (MCP/data-plane/RAG),
grounded the answer in real data, cited the source, and respected its data-source
restrictions / guardrails. An optional Gemini LLM-judge scores answer quality.

Needs the stack up (default ports). Stdlib only — run with system python3:
    python3 eval/run_eval.py
Env: STUDIO_URL, RAG_URL, SERVICE_TOKEN, EVAL_USER, GOOGLE_API_KEY, AGENT_MODEL.
"""

from __future__ import annotations

import datetime
import http.client
import json
import os
import re
import sys
import urllib.error
import urllib.request

sys.path.insert(0, os.path.dirname(__file__))
from scenarios import SCENARIOS  # noqa: E402

STUDIO = os.environ.get("STUDIO_URL", "http://127.0.0.1:8004")
RAG = os.environ.get("RAG_URL", "http://127.0.0.1:8002")
SVC = os.environ.get("SERVICE_TOKEN", "dev-service-token")
USER = os.environ.get("EVAL_USER", "eval@valuegraph.local")


def _envval(*keys: str) -> str:
    path = os.path.join(os.path.dirname(__file__), "..", ".env")
    if not os.path.exists(path):
        return ""
    rows = {}
    for line in open(path, encoding="utf-8"):
        line = line.strip()
        if "=" in line and not line.startswith("#"):
            k, v = line.split("=", 1)
            v = v.strip()
            # strip an unquoted inline comment (dotenv: `KEY=value  # note` → value)
            if v[:1] not in ("'", '"'):
                v = re.split(r"\s+#", v, 1)[0].rstrip()
            rows[k.strip()] = v.strip("\"'")
    for k in keys:
        if rows.get(k):
            return rows[k]
    return ""


# Accept GOOGLE_API_KEY or GEMINI_API_KEY (the genai SDK reads either).
GKEY = (os.environ.get("GOOGLE_API_KEY") or os.environ.get("GEMINI_API_KEY")
        or _envval("GOOGLE_API_KEY", "GEMINI_API_KEY"))
# The judge is decoupled from the agent's model. Default is a FAST model (flash) so the eval runs
# quickly; override with EVAL_JUDGE_MODEL=gemini-pro-latest for the strongest (slower) grading.
JUDGE_MODEL = (os.environ.get("EVAL_JUDGE_MODEL") or _envval("EVAL_JUDGE_MODEL")
               or "gemini-flash-latest")
# Pass bar: rubric overall average must clear this (per-dimension shown in the summary).
JUDGE_BAR = float(os.environ.get("EVAL_JUDGE_BAR") or _envval("EVAL_JUDGE_BAR") or "3.8")


# --- HTTP (stdlib) --------------------------------------------------------
def _request(method: str, url: str, body=None, headers=None, timeout=300) -> tuple[int, bytes]:
    """Robust request: recovers a truncated streaming body and never raises (a
    dropped SSE connection should fail the scenario, not abort the whole run)."""
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method, headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            try:
                return resp.status, resp.read()
            except http.client.IncompleteRead as e:
                return resp.status, e.partial  # keep whatever SSE events arrived
    except urllib.error.HTTPError as e:
        try:
            return e.code, e.read()
        except Exception:
            return e.code, b""
    except Exception as e:  # connection reset / timeout / DNS — report, don't crash
        return 0, f"__ERROR__ {type(e).__name__}: {e}".encode()


def studio(method: str, path: str, body=None) -> tuple[int, dict]:
    headers = {"X-Service-Token": SVC, "X-User-Email": USER, "Content-Type": "application/json"}
    code, raw = _request(method, f"{STUDIO}{path}", body, headers)
    try:
        return code, json.loads(raw or b"{}")
    except ValueError:
        return code, {"_raw": raw.decode("utf-8", "replace")}


def chat_messages(messages: list[dict], agent_id: str) -> dict:
    """POST /chat/stream with a full conversation and fold the SSE into a result."""
    headers = {"X-Service-Token": SVC, "X-User-Email": USER, "Content-Type": "application/json"}
    code, raw = _request("POST", f"{STUDIO}/chat/stream",
                         {"messages": messages, "agent_id": agent_id}, headers)
    tools, statuses, cites, ans, arts, cads = [], [], [], [], [], []
    confs, suggestions, subagents, cite_urls = [], [], {}, []
    clarify = None
    refused = None
    for line in raw.decode("utf-8", "replace").splitlines():
        line = line.strip()
        if not line.startswith("data:"):
            continue
        try:
            ev = json.loads(line[5:])
        except ValueError:
            continue
        t = ev.get("type")
        if t == "tool":
            tools.append(ev.get("name"))
        elif t == "tool_result":
            statuses.append(ev.get("status"))
        elif t == "citation":
            if ev.get("source"):
                cites.append(ev["source"])
            if ev.get("url"):                # the external source page the in-app viewer renders
                cite_urls.append(ev["url"])
            if ev.get("cadence"):
                cads.append(ev["cadence"])
            if ev.get("confidence"):
                confs.append(ev["confidence"])
        elif t == "artifact":
            if ev.get("artifact"):
                arts.append(ev["artifact"])
                if ev["artifact"].get("cadence"):
                    cads.append(ev["artifact"]["cadence"])
        # LLM-driven orchestration events (each is produced only by a Gemini decision):
        elif t == "clarify":                       # intake offered scoping options
            clarify = ev
        elif t == "subagent":                      # A2A: one facet researched in parallel
            subagents[ev.get("id")] = ev
        elif t == "suggestions":                   # smart follow-ups
            suggestions = ev.get("items", []) or []
        elif t == "token":
            ans.append(ev.get("text", ""))
        elif t == "done":
            refused = ev.get("refused")
            # the done list is authoritative — the verify pass enriches its citations with
            # per-source confidence (not present on the earlier streamed `citation` events).
            for c in ev.get("citations") or []:
                if isinstance(c, dict) and c.get("confidence"):
                    confs.append(c["confidence"])
                if isinstance(c, dict) and c.get("url"):
                    cite_urls.append(c["url"])
    return {"http": code, "tools": tools, "statuses": statuses, "citations": cites,
            "artifacts": arts, "cadences": cads, "confidences": confs, "cite_urls": cite_urls,
            "clarify": clarify, "subagents": list(subagents.values()), "suggestions": suggestions,
            "answer": "".join(ans).strip(), "refused": bool(refused)}


def chat(question: str, agent_id: str) -> dict:
    return chat_messages([{"role": "user", "content": question}], agent_id)


def chat_turns(turns: list[str], agent_id: str) -> dict:
    """Run a multi-turn conversation, feeding each real assistant answer back in.
    Returns the LAST turn's result (what the checks grade)."""
    messages: list[dict] = []
    result: dict = {}
    for q in turns:
        messages.append({"role": "user", "content": q})
        result = chat_messages(messages, agent_id)
        messages.append({"role": "assistant", "content": result["answer"]})
    return result


def rag_ingest(docs: list[dict]) -> None:
    _request("POST", f"{RAG}/rag/ingest", {"documents": docs}, {"Content-Type": "application/json"})


# --- LLM judge (deep Gemini, rubric-based; optional) ----------------------
# The rubric: each dimension scored 1-5 by the deep judge. Keep this in sync with
# eval/RUBRIC.md (the human-facing spec). `overall` is the headline score.
RUBRIC = [
    ("sourcing",  "Every figure/claim ties to a NAMED institutional source (cited / [n]); no unsourced numbers."),
    ("relevance", "Directly and completely answers the question that was asked — nothing missing, nothing off-topic."),
    ("grounding", "Uses the retrieved data; invents NO figures or sources (retrieved figures are GROUND TRUTH)."),
    ("guardrail", "States facts only — no price predictions, price targets, or buy/sell advice; frames news as context, not a call."),
    ("clarity",   "Clear, well-structured (markdown); figures carry units/period and an as-of/freshness where relevant."),
]
RUBRIC_KEYS = [k for k, _ in RUBRIC]


def judge(question: str, answer: str, citations: list[str], criteria: str | None = None) -> dict | None:
    """Deep-model rubric grade. Returns {overall, dims:{...}, reason} or None."""
    if not GKEY or not answer:
        return None
    today = os.environ.get("EVAL_TODAY") or datetime.date.today().isoformat()
    dims = "\n".join(f"  - {k}: {d}" for k, d in RUBRIC)
    crit = f"\nFor THIS question specifically, a correct answer MUST: {criteria}\n" if criteria else ""
    keys_json = ", ".join(f'"{k}": <1-5>' for k in RUBRIC_KEYS)
    prompt = (
        f"You are a rigorous evaluator of a financial-research assistant. Today's date is {today}. The "
        "figures in the ANSWER were retrieved LIVE from authoritative primary sources (SEC EDGAR, OpenDART, "
        "Bank of Korea, Yahoo Finance) — treat them as GROUND TRUTH: do NOT penalise 2025/2026 dates as "
        "'future' or 'hallucinated', and do not re-fact-check the numbers yourself (you can't see live data).\n\n"
        "Score EACH rubric dimension 1-5 (5 = excellent, 3 = acceptable, 1 = poor):\n"
        f"{dims}\n{crit}\n"
        f"QUESTION: {question}\nCITED SOURCES: {', '.join(citations) or 'none'}\nANSWER:\n{answer}\n\n"
        "Be strict but fair. Then give an `overall` (holistic 1-5, not a mere average) and a one-sentence "
        f'reason. Return JSON: {{{keys_json}, "overall": <1-5>, "reason": "<one sentence>"}}'
    )
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{JUDGE_MODEL}:generateContent?key={GKEY}"
    payload = {"contents": [{"parts": [{"text": prompt}]}],
               "generationConfig": {"responseMimeType": "application/json", "temperature": 0}}
    code, raw = _request("POST", url, payload, {"Content-Type": "application/json"}, timeout=180)
    if code != 200:
        return None
    try:
        data = json.loads(raw)
        text = data["candidates"][0]["content"]["parts"][0]["text"]
        out = json.loads(text)
        dim_scores = {k: int(out[k]) for k in RUBRIC_KEYS if k in out}
        overall = int(out.get("overall") or (round(sum(dim_scores.values()) / len(dim_scores)) if dim_scores else 0))
        return {"overall": overall, "dims": dim_scores, "reason": str(out.get("reason", ""))[:160]}
    except Exception:
        return None


# --- graders --------------------------------------------------------------
def grade(checks: dict, r: dict) -> list[tuple[str, bool, str]]:
    out: list[tuple[str, bool, str]] = []
    tools_joined = " ".join(t for t in r["tools"] if t)

    if "expect_connector" in checks:
        c = checks["expect_connector"]
        out.append((f"calls {c}", c in tools_joined, f"tools={r['tools']}"))
    if "forbid_connectors" in checks:
        bad = [c for c in checks["forbid_connectors"] if c in tools_joined]
        out.append(("avoids " + "/".join(checks["forbid_connectors"]), not bad, f"tools={r['tools']}"))
    if "expect_status" in checks:
        s = checks["expect_status"]
        out.append((f"tool status {s}", s in r["statuses"], f"statuses={r['statuses']}"))
    if "expect_cite" in checks:
        c = checks["expect_cite"]
        out.append((f"cites {c}", any(c in s for s in r["citations"]), f"cites={r['citations']}"))
    if "expect_artifact" in checks:
        kind = checks["expect_artifact"]
        arts = r.get("artifacts") or []
        kinds = [a.get("kind") for a in arts]
        ok = bool(arts) if kind is True else (kind in kinds)
        out.append((f"emits artifact {kind if kind is not True else ''}".strip(), ok, f"artifacts={kinds}"))
    if "expect_computation" in checks:
        # PH-DATA-6: a self-computed figure (valuation/backtest/screener) must carry the auditable
        # derivation — method + at least one input/assumption/step row — so the math isn't a black box.
        arts = r.get("artifacts") or []
        comps = [a.get("computation") for a in arts if isinstance(a, dict) and a.get("computation")]
        ok = any(isinstance(c, dict) and c.get("method")
                 and (c.get("inputs") or c.get("assumptions") or c.get("steps")) for c in comps)
        out.append(("emits computation trace", ok, f"computations={[(c or {}).get('method') for c in comps]}"))
    if "expect_cite_url" in checks:
        # the source-page viewer: a citation must carry an external source URL (so the in-app viewer
        # can render + highlight it). A substring asserts it points at the expected host.
        want = checks["expect_cite_url"]
        urls = r.get("cite_urls") or []
        ok = bool(urls) if want is True else any(want in (u or "") for u in urls)
        out.append((f"carries source url {want if want is not True else ''}".strip(), ok, f"urls={urls[:4]}"))
    if "expect_cadence" in checks:
        # periodicity rides on provenance (citations/artifacts) → the pin→alert gate. `True` =
        # any periodic (alertable) source present; a string = that exact cadence present.
        want = checks["expect_cadence"]
        cads = r.get("cadences") or []
        ok = any(c and c != "one_shot" for c in cads) if want is True else (want in cads)
        out.append((f"carries cadence {want if want is not True else 'periodic'}", ok, f"cadences={cads}"))
    if "answer_regex" in checks:
        rx = checks["answer_regex"]
        out.append((f"answer matches /{rx}/", bool(re.search(rx, r["answer"])), r["answer"][:80]))
    if "answer_contains" in checks:
        for sub in checks["answer_contains"]:
            out.append((f"answer contains '{sub}'", sub.lower() in r["answer"].lower(), r["answer"][:80]))
    if "expect_refused" in checks:
        want = checks["expect_refused"]
        out.append((f"refused={want}", r["refused"] == want, f"refused={r['refused']}"))
    # --- LLM-orchestration checks (each gates a Gemini-driven behavior) -----------------
    if "expect_connectors_all" in checks:
        # PARALLEL multi-source gather: every named connector was reached in the turn.
        joined = " ".join(t or "" for t in r["tools"])
        for c in checks["expect_connectors_all"]:
            out.append((f"reaches {c}", c in joined, f"tools={r['tools']}"))
    if "expect_clarify" in checks:
        # intake offered scoping options instead of guessing (clarify-with-options).
        want = checks["expect_clarify"]
        cl = r.get("clarify")
        n = len((cl or {}).get("options") or [])
        ok = (bool(cl) and n >= 2) if want is True else (bool(cl) == want)
        out.append((f"clarify={want}", ok, f"clarify_options={n}"))
    if "expect_subagents" in checks:
        # A2A decomposition: at least N facets researched in parallel.
        want = checks["expect_subagents"]
        n = len(r.get("subagents") or [])
        out.append((f"≥{want} sub-agents", n >= want, f"subagents={n}"))
    if "expect_suggestions" in checks:
        # smart follow-ups: at least N capability-aware next questions.
        want = checks["expect_suggestions"]
        n = len(r.get("suggestions") or [])
        out.append((f"≥{want} follow-ups", n >= want, f"suggestions={n}"))
    if "expect_confidence" in checks:
        # verify pass scored per-source evidentiary confidence (high|medium|low).
        confs = r.get("confidences") or []
        out.append(("per-source confidence scored", bool(confs), f"confidences={confs}"))
    if "forbid_artifact" in checks:
        # e.g. a conceptual answer must NOT fabricate a chart/table.
        arts = r.get("artifacts") or []
        out.append(("no artifact (conceptual)", not arts, f"artifacts={[a.get('kind') for a in arts]}"))
    return out


# --- terminal viz --------------------------------------------------------
_TTY = sys.stdout.isatty() and not os.environ.get("NO_COLOR")


def _c(s, code):
    return f"\033[{code}m{s}\033[0m" if _TTY else s


def green(s): return _c(s, "32")
def red(s): return _c(s, "31")
def yellow(s): return _c(s, "33")
def cyan(s): return _c(s, "36")
def dim(s): return _c(s, "2")
def bold(s): return _c(s, "1")


def _bar(p, t, width=12):
    if not t:
        return ""
    fill = round(p / t * width)
    return (green if p == t else (yellow if p else red))("█" * fill) + dim("░" * (width - fill))


def _looks_transient(r: dict) -> bool:
    a = r.get("answer", "")
    return (not r.get("tools") and not r.get("refused")
            and (not a or "unavailable" in a or "문제가 발생" in a or a.startswith("__ERROR__")))


def _run_scenario_chat(sc: dict, agent_id: str) -> dict:
    """Run the chat with one retry on a transient blip (a dropped gateway/SSE
    connection or 'platform unavailable'), so one hiccup doesn't fail a scenario."""
    import time
    r = chat_turns(sc["turns"], agent_id) if sc.get("turns") else chat(sc["question"], agent_id)
    if _looks_transient(r):
        time.sleep(2)
        r = chat_turns(sc["turns"], agent_id) if sc.get("turns") else chat(sc["question"], agent_id)
    return r


# --- run ------------------------------------------------------------------
def main() -> int:
    if not GKEY:
        print("⏭️  EVAL SKIPPED — no GOOGLE_API_KEY / GEMINI_API_KEY (env or .env).")
        print("   The eval builds Gemini-backed agents and grades real natural-language answers, so it")
        print("   needs a key. Set GOOGLE_API_KEY (or GEMINI_API_KEY) in .env, then re-run.")
        return 2

    # optional `--only=<substr>` to run a subset by name (cheap iteration; default = all)
    only = next((a.split("=", 1)[1] for a in sys.argv[1:] if a.startswith("--only=")), None)
    scenarios = [s for s in SCENARIOS if not only or only.lower() in s["name"].lower()]
    n = len(scenarios)
    print(bold("Quality eval") + dim(f"  · studio={STUDIO} · judge={JUDGE_MODEL} · {n} scenarios"
                                      + (f" · only={only!r}" if only else "")))
    print(dim("─" * 70))
    code, _ = studio("POST", "/users/ensure")
    if code != 200:
        print(red(f"FATAL: could not provision the eval user (studio /users/ensure → {code}). Is the stack up?"))
        return 2

    total = passed = 0
    judged: list[int] = []
    dim_sums = {k: 0 for k in RUBRIC_KEYS}
    dim_n = {k: 0 for k in RUBRIC_KEYS}
    rows: list[tuple[str, int, int, str]] = []

    for i, sc in enumerate(scenarios, 1):
        name = sc["name"]
        tag = cyan(f"[{i:>2}/{n}]")
        try:
            if sc.get("rag_docs"):
                rag_ingest(sc["rag_docs"])
            ac, agent = studio("POST", "/agents", {
                "name": sc["agent"]["name"], "model": sc["agent"].get("model", "gemini"),
                "data_sources": sc["agent"]["data_sources"], "system_prompt": sc["agent"].get("system_prompt"),
            })
            if ac != 200 or "id" not in agent:
                print(f"{tag} {red('✗')} {bold(name)}  {red(f'agent-create-failed ({ac})')}")
                rows.append((name, 0, 1, "")); total += 1; print(); continue
            r = _run_scenario_chat(sc, agent["id"])
            question = sc["turns"][-1] if sc.get("turns") else sc["question"]
        except Exception as e:  # never let one scenario abort the run
            print(f"{tag} {red('✗')} {bold(name)}  {red(f'ERROR {type(e).__name__}: {e}')}")
            rows.append((name, 0, 1, "")); total += 1; print(); continue

        results = grade(sc["checks"], r)
        sp = sum(1 for _, ok, _ in results if ok)
        total += len(results); passed += sp
        mark = green("✓") if sp == len(results) else red("✗")
        print(f"{tag} {mark} {bold(name)}  {dim(f'{sp}/{len(results)} checks')}")

        qshow = " | ".join(sc["turns"]) if sc.get("turns") else question
        print(dim(f"        Q  {qshow[:100]}"))
        tools = ", ".join(r["tools"]) or "—"
        print(dim(f"        ↳  tools=[{tools}] status={r['statuses'] or '—'} refused={r['refused']}"))
        print(dim(f"        A  {r['answer'][:130].replace(chr(10), ' ')}"))
        for label, ok, detail in results:
            if ok:
                print("        " + green("✓") + " " + dim(label))
            else:
                print("        " + red("✗") + " " + label + "  " + red(f"({detail[:90]})"))

        jtxt = ""
        if sc["checks"].get("judge"):
            j = judge(question, r["answer"], r["citations"], sc.get("criteria"))
            if j:
                judged.append(j["overall"])
                for k, v in j["dims"].items():
                    dim_sums[k] += v
                    dim_n[k] += 1
                jtxt = f"{j['overall']}/5"
                dimstr = " ".join(f"{k[:4]}{v}" for k, v in j["dims"].items())
                col = green if j["overall"] >= 4 else (yellow if j["overall"] >= 3 else red)
                print("        " + dim("◆ judge ") + col(jtxt) + dim(f"  [{dimstr}]  {j['reason'][:64]}"))
        rows.append((name, sp, len(results), jtxt))
        print()

    # --- summary ----------------------------------------------------------
    print(dim("─" * 70))
    print(bold("Summary"))
    w = max((len(name) for name, *_ in rows), default=10)
    for name, sp, tot, jtxt in rows:
        mark = green("✓") if sp == tot else red("✗")
        line = f"  {mark} {name.ljust(w)}  {_bar(sp, tot)} {sp}/{tot}"
        print(line + (dim(f"   judge {jtxt}") if jtxt else ""))

    pct = passed / total * 100 if total else 0
    avg = sum(judged) / len(judged) if judged else None
    print()
    print(f"  {bold('Deterministic')}  {_bar(passed, total)} {passed}/{total} {dim(f'({pct:.0f}%)')}")
    if avg is not None:
        col = green if avg >= JUDGE_BAR else red
        print(f"  {bold('LLM-judge')}      {col(f'{avg:.2f}/5')} {dim(f'overall · over {len(judged)} answers · judge={JUDGE_MODEL}')}")
        # per-rubric-dimension averages — where quality is strong vs weak
        parts = []
        for k in RUBRIC_KEYS:
            if dim_n[k]:
                d = dim_sums[k] / dim_n[k]
                parts.append((green if d >= JUDGE_BAR else (yellow if d >= 3 else red))(f"{k} {d:.1f}"))
        if parts:
            print(f"  {bold('Rubric')}         " + dim(" · ").join(parts))

    ok = (passed == total) and (avg is None or avg >= JUDGE_BAR)
    print()
    print((green("✅ EVAL PASSED") if ok else red("❌ EVAL BELOW BAR"))
          + dim(f"   (all checks pass + judge overall ≥ {JUDGE_BAR})"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
