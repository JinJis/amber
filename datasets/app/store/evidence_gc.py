"""ME-10 / SC-3.4 — bound the on-disk evidence cache.

``evidence_docs_dir`` accumulates one sanitized HTML file per cited URL plus parsed 8-K decks — and
worse, a change to the cache **version key** (``source_html._cache_path`` bumps ``v2:``→``v3:``…)
abandons every prior file under a new name, so dead files leak forever. Left alone the directory grows
without bound. This sweeper, run daily by the worker, deletes files past a max age (the version-key
orphans + genuinely stale evidence) then, if still over a size budget, evicts least-recently-ACCESSED
files (atime) until under budget — so hot evidence a viewer keeps opening survives, cold evidence goes.
"""

from __future__ import annotations

import logging
import pathlib

log = logging.getLogger(__name__)


def sweep_evidence(root: str, budget_bytes: int, max_age_days: int, now: float | None = None) -> dict:
    """Delete aged files, then LRU-evict by access time down to ``budget_bytes``. Pure filesystem work
    (no DB) — safe to call from a worker thread. Returns counts + the resulting total size."""
    import time

    now = float(now) if now is not None else time.time()
    base = pathlib.Path(root)
    if not base.exists():
        return {"deleted_age": 0, "deleted_lru": 0, "bytes_after": 0}

    files: list[tuple[pathlib.Path, int, float]] = []
    for p in base.rglob("*"):
        if p.is_file():
            try:
                st = p.stat()
                files.append((p, st.st_size, st.st_atime))
            except OSError:
                pass  # vanished mid-scan / permission → skip

    cutoff = now - max_age_days * 86400.0
    deleted_age = 0
    kept: list[tuple[pathlib.Path, int, float]] = []
    for p, size, atime in files:
        if atime < cutoff:
            try:
                p.unlink()
                deleted_age += 1
                continue
            except OSError:
                pass
        kept.append((p, size, atime))

    total = sum(s for _, s, _ in kept)
    deleted_lru = 0
    if total > budget_bytes:
        for p, size, _ in sorted(kept, key=lambda t: t[2]):   # least-recently-accessed first
            if total <= budget_bytes:
                break
            try:
                p.unlink()
                total -= size
                deleted_lru += 1
            except OSError:
                pass

    # best-effort: drop now-empty subdirectories left behind
    for d in sorted((p for p in base.rglob("*") if p.is_dir()), reverse=True):
        try:
            next(d.iterdir())
        except StopIteration:
            try:
                d.rmdir()
            except OSError:
                pass
        except OSError:
            pass
    return {"deleted_age": deleted_age, "deleted_lru": deleted_lru, "bytes_after": total}
