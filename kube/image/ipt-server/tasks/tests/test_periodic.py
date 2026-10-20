"""Tests for the resilient periodic-loop helper (tasks/periodic.py).

Regression coverage for a live blackhole incident where every FRR-bridge-probing
monitor loop died silently after its first tick. The helper must guarantee:

  1. A single tick raising Exception NEVER kills the loop (logs + continues).
  2. A single tick raising a *non-Exception* BaseException (e.g. a stray
     KeyboardInterrupt/SystemExit surfaced from a worker thread) ALSO never
     kills the loop -- but asyncio.CancelledError still cancels it (correct
     cooperative shutdown).
  3. A tick that HANGS past tick_timeout NEVER wedges the loop: it is bounded
     by asyncio.wait_for, logged, and the next tick still runs.
  4. stop_event set => loop exits promptly and logs the exit reason.
"""

import asyncio

import pytest

from tasks.periodic import run_periodic


@pytest.mark.asyncio
async def test_loop_survives_tick_raising_exception():
    """A tick raising Exception is logged and the loop keeps ticking."""
    calls = {"n": 0}
    stop = asyncio.Event()

    async def tick():
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("boom on tick 1")
        if calls["n"] >= 3:
            stop.set()

    await asyncio.wait_for(
        run_periodic("t", tick, interval=0, stop_event=stop, tick_timeout=1),
        timeout=2,
    )
    # The loop kept running past the exploding tick and called tick again.
    assert calls["n"] >= 3


@pytest.mark.asyncio
async def test_loop_survives_tick_raising_base_exception():
    """A stray non-Exception BaseException also fails to kill the loop."""
    calls = {"n": 0}
    stop = asyncio.Event()

    async def tick():
        calls["n"] += 1
        if calls["n"] == 1:
            raise SystemExit("stray BaseException from a tick")  # not an Exception
        if calls["n"] >= 3:
            stop.set()

    await asyncio.wait_for(
        run_periodic("t", tick, interval=0, stop_event=stop, tick_timeout=1),
        timeout=2,
    )
    assert calls["n"] >= 3


@pytest.mark.asyncio
async def test_loop_survives_hanging_tick_via_tick_timeout():
    """A hung tick is abandoned at tick_timeout and the next tick still runs."""
    calls = {"n": 0}
    stop = asyncio.Event()

    async def tick():
        calls["n"] += 1
        if calls["n"] == 1:
            # Hang far longer than tick_timeout; the helper must bound it.
            await asyncio.sleep(100)
        if calls["n"] >= 3:
            stop.set()

    await asyncio.wait_for(
        run_periodic("t", tick, interval=0, stop_event=stop, tick_timeout=0.05),
        timeout=2,
    )
    # tick 1 was abandoned after 0.05s; ticks 2 and 3 still ran.
    assert calls["n"] >= 3


@pytest.mark.asyncio
async def test_cancelled_error_propagates_and_stops_loop():
    """Cancellation still propagates (cooperative shutdown is preserved).

    The tick signals that it started, then never returns on its own; the
    cancellation is issued only after that signal, so the CancelledError
    is provably delivered to a running loop rather than raced against it.
    The wait_for timeout is a hang guard only.
    """
    started = asyncio.Event()

    async def tick():
        started.set()
        await asyncio.Event().wait()  # never set: only cancellation ends it

    stop = asyncio.Event()
    task = asyncio.create_task(
        run_periodic("t", tick, interval=0, stop_event=stop, tick_timeout=60)
    )
    await asyncio.wait_for(started.wait(), timeout=5.0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, timeout=5.0)


@pytest.mark.asyncio
async def test_stop_event_exits_promptly_and_logs_reason(caplog):
    """stop_set exits promptly and the exit reason is greppable in logs."""
    stop = asyncio.Event()

    async def tick():
        stop.set()

    import logging

    with caplog.at_level(logging.INFO):
        await asyncio.wait_for(
            run_periodic("myloop", tick, interval=5, stop_event=stop, tick_timeout=1),
            timeout=1,
        )
    text = caplog.text
    assert "myloop" in text
    # Explicit, greppable exit reason.
    assert "stop_event" in text
