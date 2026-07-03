"""Agent Engine settings (AGENT_* in the shared platform env)."""

from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="AGENT_", env_file=("../.env", ".env"), env_file_encoding="utf-8", extra="ignore"
    )

    # App log verbosity (DEBUG|INFO|WARNING|…). A bare `LOG_LEVEL` env var (prefix-independent,
    # shared across all services) overrides this in logging_config — see setup_logging().
    log_level: str = "INFO"

    # Model tiering — quality where it's READ, economy where it's MECHANICAL. Override via env.
    # The platform is Gemini-only (invariant #7). The legacy `stub` planner has been removed;
    # this remains for env compatibility but only "gemini" is supported.
    llm_backend: str = "gemini"
    model: str = "gemini-flash-latest"  # tool-routing / planning model (frequent → flash)
    # A light/cheap model for the first-pass intake: guardrail + budget + plan (+ needs_data).
    budget_model: str = "gemini-flash-lite-latest"
    # The RESPONSE/synthesis model — composes the user-facing final answer (and the A2A combiner
    # + conceptual answers), MIXING sourced facts (cited [n]) with analyst context. This is what
    # the user actually reads, so it runs on the DEEP tier (pro) for quality. One call per turn.
    synthesis_model: str = "gemini-pro-latest"
    # The verify/refine + per-source confidence pass that GROUNDS the synthesis. Defaults to the
    # flash tier (cheap); bump to a pro/reasoning model when you want stricter grounding.
    reasoning_model: str = "gemini-flash-latest"
    # The control-plane gateway the agent's tools are called through.
    gateway_url: str = "http://127.0.0.1:8010"
    # Per-request timeout (seconds) on EVERY Gemini call. Without it a stalled call hangs the SSE
    # stream forever (no `done` → the UI stays "답변 작성 중" and input stays disabled). With it a
    # stall raises → callers degrade gracefully → the turn always finishes.
    gemini_timeout_seconds: float = 90.0
    # Tighter overall cap for BEST-EFFORT post-synthesis enrichment (follow-ups, evidence verify,
    # chart annotation). The answer has already streamed, so these must never delay `done` long —
    # if they exceed this (e.g. retry/backoff under rate-limit), we degrade and finish the turn.
    gemini_enrich_timeout_seconds: float = 25.0
    max_steps: int = 8         # base tool-step budget (raised for multi-source tasks, up to the cap)
    max_steps_cap: int = 14    # hard ceiling for the dynamic budget
    http_timeout_seconds: float = 60.0
    # Guardrail: the refuse/allow decision is a JUDGMENT made by the LLM intake
    # (`agent.analyze_task`), not keyword matching (invariant #9). The model scores how
    # confidently the request *asks for* a forecast/advice/target; refuse only at/above this.
    guardrail_threshold: float = 0.6


settings = Settings()
