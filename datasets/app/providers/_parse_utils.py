"""Shared upstream number parsing.

Upstream APIs return numbers as strings, sometimes comma-grouped ("1,234,567"), sometimes empty,
sometimes already numeric (JSON numbers). These two helpers parse them leniently — returning ``None``
on anything unparseable instead of raising — so a single bad field never sinks a whole row.

One implementation, reused by every provider (SEC, FMP, KIS, …) so comma/None/empty handling stays
identical across markets. (RF-02 — replaced the per-provider ``_num``/``_i``/``_f`` twins.)
"""

from __future__ import annotations


def parse_float(value: object) -> float | None:
    """Best-effort float; strips thousands separators. ``None`` for None/empty/unparseable."""
    if value in (None, ""):
        return None
    try:
        return float(str(value).replace(",", ""))
    except (TypeError, ValueError):
        return None


def parse_int(value: object) -> int | None:
    """Best-effort int; strips thousands separators. ``None`` for None/empty/unparseable."""
    if value in (None, ""):
        return None
    try:
        return int(str(value).replace(",", ""))
    except (TypeError, ValueError):
        return None
