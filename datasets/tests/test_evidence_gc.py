"""ME-10 / SC-3.4: the evidence-cache sweeper deletes aged files (version-key orphans) then LRU-evicts
by access time down to a size budget — hot evidence survives, cold/stale evidence is reclaimed."""

from __future__ import annotations

import os
import time

from app.store.evidence_gc import sweep_evidence


def _write(path, size: int, atime: float):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"x" * size)
    os.utime(path, (atime, atime))   # set access + mod time


def test_deletes_files_past_max_age(tmp_path):
    now = time.time()
    old = tmp_path / "source" / "old.html"
    fresh = tmp_path / "source" / "fresh.html"
    _write(old, 100, now - 40 * 86400)      # 40 days old
    _write(fresh, 100, now - 1 * 86400)     # 1 day old
    out = sweep_evidence(str(tmp_path), budget_bytes=10_000_000, max_age_days=30, now=now)
    assert out["deleted_age"] == 1
    assert not old.exists() and fresh.exists()


def test_lru_evicts_down_to_budget(tmp_path):
    now = time.time()
    # three fresh files (none age-eligible), 100 bytes each = 300; budget 250 → evict 1 (the coldest)
    cold = tmp_path / "a.html"
    warm = tmp_path / "b.html"
    hot = tmp_path / "c.html"
    _write(cold, 100, now - 3600)    # least-recently accessed → evicted first
    _write(warm, 100, now - 60)
    _write(hot, 100, now - 1)        # most-recently accessed → kept
    out = sweep_evidence(str(tmp_path), budget_bytes=250, max_age_days=30, now=now)
    assert out["deleted_age"] == 0 and out["deleted_lru"] == 1
    assert not cold.exists() and warm.exists() and hot.exists()
    assert out["bytes_after"] == 200


def test_under_budget_and_fresh_is_noop(tmp_path):
    now = time.time()
    _write(tmp_path / "x.html", 50, now - 100)
    out = sweep_evidence(str(tmp_path), budget_bytes=10_000_000, max_age_days=30, now=now)
    assert out == {"deleted_age": 0, "deleted_lru": 0, "bytes_after": 50}


def test_missing_dir_is_safe():
    out = sweep_evidence("/nonexistent/evidence/dir", budget_bytes=1, max_age_days=1)
    assert out == {"deleted_age": 0, "deleted_lru": 0, "bytes_after": 0}
