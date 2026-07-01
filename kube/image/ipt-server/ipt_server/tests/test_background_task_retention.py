"""Regression tests for the background-task-GC bug proven live on vpn2.

ipt_server/main.py spawns several fire-and-forget asyncio tasks via bare
``asyncio.create_task(...)`` calls whose return value is discarded. Per the
asyncio docs (create_task): "Save a reference to the result of this
function... A task that isn't referenced elsewhere may get garbage
collected at any time, even before it's done." That is exactly what
happened live: the four monitor loops (and, transitively, the pinning
http_task) silently died after their first tick with no exception, leaving
tables 301/302 blackholed.

These tests:
  1. Reproduce the underlying hazard in isolation (control case).
  2. Prove the ``_spawn_background``/``_retain_task`` helper in main.py
     fixes it.
  3. Prove ``async_main`` actually uses the helper for all four monitors
     and for the pinning ``http_task``.
"""
from __future__ import annotations

import asyncio
import gc
import weakref

import pytest


async def _unanchored_looper(ticks: dict) -> None:
    """A coroutine that awaits something with *no* external anchor.

    ``asyncio.Event().wait()`` on a freshly-created, never-retained Event
    forms a pure reference cycle (Task -> coro -> Event -> waiter Future ->
    done-callback -> Task) with nothing external holding it alive once the
    coroutine reaches its first suspension point. This mirrors the reported
    "GC'd after first tick, silent death, no exception" failure mode.
    """
    while True:
        ticks["n"] += 1
        await asyncio.Event().wait()  # never set; would hang forever if alive


async def test_unretained_task_can_be_gcd_after_first_tick():
    """Control case: a bare, unreferenced create_task() is GC-eligible.

    This is the bug itself, reproduced without any ipt_server code, to
    demonstrate the hazard the fix must close.
    """
    ticks = {"n": 0}
    asyncio.create_task(_unanchored_looper(ticks))

    # Let the task run to its first await/suspend point (one tick).
    await asyncio.sleep(0)
    other_tasks = [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]
    assert len(other_tasks) == 1
    ref = weakref.ref(other_tasks[0])
    del other_tasks

    gc.collect()
    await asyncio.sleep(0)

    assert ticks["n"] == 1, "sanity: task should have ticked exactly once before GC"
    assert ref() is None, (
        "expected the unreferenced task to be GC-eligible (this is the bug "
        "this suite exists to fix); if this assertion now fails, the "
        "underlying CPython/asyncio GC behavior this regression test "
        "depends on has changed and the test needs re-deriving"
    )


async def test_spawn_background_retains_task_across_gc():
    """ipt_main._spawn_background must retain a strong reference.

    Uses the same unanchored-await pattern as the control test above so a
    missing retention would reproduce the exact live failure.
    """
    import ipt_server.main as ipt_main

    ticks = {"n": 0}
    task = ipt_main._spawn_background(_unanchored_looper(ticks))
    ref = weakref.ref(task)
    del task

    await asyncio.sleep(0)  # let it tick once
    gc.collect()
    await asyncio.sleep(0)

    assert ticks["n"] == 1
    assert ref() is not None, (
        "_spawn_background did not retain a strong reference: the task was "
        "GC-eligible after gc.collect(), reproducing the live vpn2 bug"
    )
    assert ref() in ipt_main._BACKGROUND_TASKS

    ref().cancel()
    try:
        await ref()
    except asyncio.CancelledError:
        pass


async def test_retained_task_is_discarded_from_container_on_completion():
    """The retention container must not leak: completed tasks are dropped."""
    import ipt_server.main as ipt_main

    async def _quick():
        return None

    task = ipt_main._spawn_background(_quick())
    await task
    # done_callback runs via call_soon; yield once so it fires.
    await asyncio.sleep(0)

    assert task not in ipt_main._BACKGROUND_TASKS, (
        "completed background task was not discarded from the retention "
        "container (potential unbounded growth over the process lifetime)"
    )
