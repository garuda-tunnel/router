"""Tests for the resilient periodic-loop helper (tasks/periodic.py).

Regression coverage for the vpn2 blackhole where every FRR-bridge-probing
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
    calls = {"n": 0}
    stop = asyncio.Event()

    async def tick():
        calls["n"] += 1
        await asyncio.sleep(0.5)

    task = asyncio.create_task(
        run_periodic("t", tick, interval=0, stop_event=stop, tick_timeout=1)
    )
    await asyncio.sleep(0.05)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


@pytest.mark.asyncio
async def test_stop_event_exits_promptly_and_logs_reason(caplog):
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
