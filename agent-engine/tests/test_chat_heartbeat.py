"""SSE keepalive (sse_heartbeat): a long silent phase in a chat turn — a slow/retrying Gemini
call or the post-answer enrichment tail — must not let the downstream consumer's between-chunks
read timeout fire. sse_heartbeat interleaves keepalive COMMENTS so the socket never idles past the
interval, while passing real events through unchanged."""

from __future__ import annotations

import asyncio

from agentengine.chat import sse_heartbeat


async def test_keepalive_bridges_a_silent_gap():
    async def events():
        yield {"type": "token", "text": "a"}
        await asyncio.sleep(0.15)              # silent phase longer than the heartbeat interval
        yield {"type": "done", "refused": False}

    chunks = [c async for c in sse_heartbeat(events(), interval=0.03)]

    # real events pass through, serialized as SSE data lines (verbatim, ordered)
    data = [c for c in chunks if c.startswith("data:")]
    assert any('"text": "a"' in c for c in data)
    assert '"type": "done"' in data[-1]
    # at least one keepalive comment bridged the silent gap — and it carries NO data: line, so a
    # downstream SSE parser (studio-api / the browser) skips it entirely.
    hbs = [c for c in chunks if c.startswith(":")]
    assert hbs, "expected a keepalive comment during the silent phase"
    assert all("data:" not in c for c in hbs)


async def test_disabled_interval_is_plain_passthrough():
    async def events():
        yield {"type": "token", "text": "x"}
        yield {"type": "done"}

    chunks = [c async for c in sse_heartbeat(events(), interval=0)]
    assert [c.startswith("data:") for c in chunks] == [True, True]
    assert len(chunks) == 2


async def test_early_close_propagates_into_the_wrapped_stream():
    """A client disconnect closes the wrapper — which must aclose the inner stream (so stream_chat's
    own cleanup runs) and not leak the pending __anext__ task."""
    closed = {"v": False}

    async def events():
        try:
            while True:
                yield {"type": "token", "text": "."}
                await asyncio.sleep(0.01)
        finally:
            closed["v"] = True

    agen = sse_heartbeat(events(), interval=0.05)
    it = agen.__aiter__()
    await it.__anext__()          # pull one real chunk, then leave (disconnect)
    await agen.aclose()
    assert closed["v"], "sse_heartbeat must propagate close into the wrapped event stream"


async def test_early_close_during_silence_cancels_the_in_flight_fetch():
    """The disconnect-during-silence path: sse_heartbeat is parked in asyncio.wait with a next-event
    fetch in flight (stream_chat blocked in a long await). Closing must cancel AND await that fetch so
    stream_chat's own finally unwinds (its gateway stream closes) — deterministically, before return."""
    closed = {"v": False}
    started = asyncio.Event()

    async def events():
        try:
            started.set()
            await asyncio.sleep(10)          # long silent phase → the wrapper emits heartbeats
            yield {"type": "token", "text": "never reached"}
        finally:
            closed["v"] = True

    agen = sse_heartbeat(events(), interval=0.02)
    it = agen.__aiter__()
    first = await it.__anext__()             # a keepalive comment (silence exceeded the interval)
    assert first.startswith(":")
    await started.wait()
    await agen.aclose()                      # close while the __anext__ fetch is still in flight
    assert closed["v"], "closing during a silent gap must unwind the wrapped stream, not leak it"
