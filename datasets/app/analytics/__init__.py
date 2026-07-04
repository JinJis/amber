"""History Lab analytics (HL-3) — pure, deterministic functions over price bars.

No I/O, no LLM, no randomness. Every function operates on bars passed in and returns plain
dicts carrying `method` / `params` / `n` / `label` so the caller can attach provenance. These
are DESCRIPTIVE statistics of the historical record — never forecasts (ROADMAP §2). See
docs/HISTORY_LAB_SPEC.md §4 for the algorithms and edge cases.
"""

from app.analytics._common import LABEL

__all__ = ["LABEL"]
