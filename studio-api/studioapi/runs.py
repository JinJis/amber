"""Background chat runs — generation that survives the client leaving.

A chat turn runs as a server-side background task (a ``Run``) that keeps generating even
after the browser disconnects, buffering every SSE event. The HTTP response just *tails* the
buffer; multiple tails (the original request, or a later re-entry into the conversation) read
the same buffer and replay from any index. When the run finishes, the assistant message is
persisted — so a completed turn shows on reload, and an in-flight one resumes live.

In-memory (per studio-api process): durable across client disconnects within a session, not
across a server restart — the pragmatic 80% of "like a normal LLM service".
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass, field

_MAX_RUNS = 200  # cap retained runs; prune oldest finished ones beyond this


@dataclass
class Run:
    id: str
    conversation_id: str
    status: str = "running"  # running | done | error
    events: list[dict] = field(default_factory=list)  # SSE buffer (for replay; head-trimmed, see base)
    # IMP-1: absolute index of events[0] — the buffer head is trimmed on long runs/finish so one
    # chatty run can't grow megabytes in-process; tails translate absolute → local via this.
    base: int = 0
    cond: asyncio.Condition = field(default_factory=asyncio.Condition)
    task: asyncio.Task | None = None


class RunManager:
    def __init__(self) -> None:
        self._runs: dict[str, Run] = {}
        self._active: dict[str, str] = {}  # conversation_id → run_id while running

    def get(self, run_id: str) -> Run | None:
        return self._runs.get(run_id)

    def active_run_id(self, conversation_id: str) -> str | None:
        """The run still generating for a conversation, if any (else None)."""
        rid = self._active.get(conversation_id)
        run = self._runs.get(rid) if rid else None
        return rid if (run and run.status == "running") else None

    def _prune(self) -> None:
        if len(self._runs) <= _MAX_RUNS:
            return
        finished = [r for r in self._runs.values() if r.status != "running"]
        for r in finished[: len(self._runs) - _MAX_RUNS]:
            self._runs.pop(r.id, None)

    def start(self, conversation_id: str, driver: Callable[[Run], Awaitable[None]]) -> Run:
        """Create a run (seeded with a ``run`` event so a tail learns its id + conv id first)
        and launch its driver as a detached background task."""
        run = Run(id=uuid.uuid4().hex, conversation_id=conversation_id)
        run.events.append({"type": "run", "run_id": run.id, "conversation_id": conversation_id})
        self._runs[run.id] = run
        self._active[conversation_id] = run.id
        self._prune()

        async def _wrap() -> None:
            try:
                await driver(run)
                await self.finish(run, "done")
            except Exception:  # noqa: BLE001 — never leave a run stuck "running"
                await self.append(run, {"type": "token", "text": "답변 생성 중 문제가 발생했어요."})
                await self.finish(run, "error")

        run.task = asyncio.create_task(_wrap())
        return run

    _MAX_LIVE_EVENTS = 4000   # in-flight cap (a run streaming beyond this trims its oldest chunk)
    _KEEP_FINISHED = 300      # after finish, keep only the tail (persisted messages cover the rest)

    async def append(self, run: Run, event: dict) -> None:
        async with run.cond:
            run.events.append(event)
            if len(run.events) > self._MAX_LIVE_EVENTS:   # IMP-1: bound live-buffer growth
                drop = len(run.events) - self._MAX_LIVE_EVENTS
                del run.events[:drop]
                run.base += drop
            run.cond.notify_all()

    async def finish(self, run: Run, status: str) -> None:
        async with run.cond:
            run.status = status
            if len(run.events) > self._KEEP_FINISHED:      # IMP-1: finished runs keep only a tail
                drop = len(run.events) - self._KEEP_FINISHED
                del run.events[:drop]
                run.base += drop
            if self._active.get(run.conversation_id) == run.id:
                self._active.pop(run.conversation_id, None)
            run.cond.notify_all()

    async def tail(self, run: Run, from_index: int = 0) -> AsyncIterator[dict]:
        """Yield buffered events from ``from_index``, then live ones until the run ends.
        Cancelling this (client disconnect) does NOT stop the driver — it keeps generating."""
        i = max(0, from_index)                # ABSOLUTE index (client counts every event it got)
        while True:
            async with run.cond:
                while i >= run.base + len(run.events) and run.status == "running":
                    await run.cond.wait()
                local = max(0, i - run.base)  # trimmed head → resume from the oldest retained
                pending = run.events[local:]
                i = run.base + len(run.events)
                terminal = run.status != "running"
            for ev in pending:
                yield ev
            if terminal and i >= run.base + len(run.events):
                return


# one registry per process
manager = RunManager()
