"""HI-9 / SC-1.11: a hung background run can't stay 'running' forever (deadline watchdog), and a
flood of turns can't spawn unbounded detached tasks (global concurrency cap). Both reuse the
existing cancel → driver-cleanup → finish flow."""

from __future__ import annotations

import asyncio
import time

from studioapi.config import settings as cfg
from studioapi.runs import RunManager


async def _hung(run):
    await asyncio.sleep(3600)  # simulate an upstream call that never returns


async def _drain(mgr: RunManager) -> None:
    for r in list(mgr._runs.values()):
        if r.task and not r.task.done():
            r.task.cancel()
    await asyncio.sleep(0.05)


async def test_watchdog_finishes_a_hung_run():
    mgr = RunManager()
    run = mgr.start("convH", _hung)
    await asyncio.sleep(0.02)
    assert run.status == "running"

    run.deadline = time.monotonic() - 1   # force past its deadline
    assert mgr.sweep_expired() == 1
    for _ in range(50):
        if run.status != "running":
            break
        await asyncio.sleep(0.02)
    assert run.status == "error"   # force-finished, not stuck 'running'
    await _drain(mgr)


async def test_cap_evicts_oldest_running_run(monkeypatch):
    monkeypatch.setattr(cfg, "run_max_concurrent", 2)
    mgr = RunManager()
    r1 = mgr.start("c1", _hung)
    r2 = mgr.start("c2", _hung)
    await asyncio.sleep(0.02)
    assert len(mgr._running()) == 2

    r3 = mgr.start("c3", _hung)          # at cap → evict the oldest (r1)
    for _ in range(50):
        if r1.status != "running":
            break
        await asyncio.sleep(0.02)
    assert r1.status == "error"          # oldest evicted + finished
    assert r3.status == "running"        # the incoming run got its slot
    assert r2.status == "running"
    await _drain(mgr)
