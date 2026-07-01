"""Resilient periodic-loop helper for background monitor tasks.

Motivation (vpn2 blackhole, 2026-07-02): every FRR-bridge-probing monitor
loop (`nexthop_monitor`, pinning `_liveness_loop`) died *silently* after its
first tick, leaving pinning egress tables 301/302 blackhole even though the
OSPF resolver was proven correct. The loops each already had a
`try/except Exception` around the tick body, yet they still stopped and
logged nothing -- which means the terminating event was either:

  * a tick that HUNG forever inside a blocking worker-thread call (a
    `to_thread` FRR-bridge probe whose socket read never returned), so the
    coroutine was suspended on the `await` and never re-looped; or
  * a *non-Exception* ``BaseException`` (e.g. a stray ``SystemExit`` /
    ``KeyboardInterrupt`` surfaced from a worker thread) which
    ``except Exception`` does NOT catch, terminating the task -- and because
    the earlier retention fix keeps a strong reference, a retained task that
    finishes with an exception is *silent* (asyncio only warns when an
    unretrieved-exception task is garbage-collected).

`run_periodic` closes both holes for EVERY periodic loop at once:

  * each tick runs under ``asyncio.wait_for(tick(), tick_timeout)`` so a hung
    tick can never wedge the loop -- it is abandoned, logged, and the next
    tick runs;
  * the tick body is wrapped to swallow ``BaseException`` (except
    ``asyncio.CancelledError``, which is re-raised for correct cooperative
    shutdown) so no single bad tick can ever end the loop;
  * loop entry and exit are logged with an explicit, greppable reason
    (``stop_event`` vs ``cancelled``) so a future death/stall is loud.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Awaitable, Callable, Optional

_DEFAULT_LOGGER = logging.getLogger(__name__)


async def run_periodic(
    name: str,
    tick: Callable[[], Awaitable[None]],
    *,
    interval: float,
    stop_event: asyncio.Event,
    tick_timeout: float,
    logger: Optional[logging.Logger] = None,
) -> None:
    """Run ``tick`` every ``interval`` seconds until ``stop_event`` is set.

    Crash-proof by construction: no single tick -- whether it raises an
    ``Exception``, raises a non-``Exception`` ``BaseException``, or hangs past
    ``tick_timeout`` -- can terminate the loop. Only ``asyncio.CancelledError``
    (cooperative shutdown) stops it, and that is re-raised.

    Args:
        name: human-readable loop name for log lines (e.g. "nexthop monitor").
        tick: zero-arg coroutine function invoked once per iteration.
        interval: seconds to wait between ticks (interruptible by stop_event).
        stop_event: when set, the loop exits after the current wait.
        tick_timeout: hard per-tick wall-clock deadline; a tick exceeding it is
            abandoned and logged, and the loop continues.
        logger: logger to use; defaults to this module's logger.
    """
    log = logger or _DEFAULT_LOGGER
    log.info("Starting %s (interval=%ss, tick_timeout=%ss)", name, interval, tick_timeout)

    exit_reason = "stop_event"
    try:
        while not stop_event.is_set():
            try:
                await asyncio.wait_for(tick(), timeout=tick_timeout)
            except asyncio.CancelledError:
                # Cooperative shutdown: never swallow this.
                raise
            except asyncio.TimeoutError:
                log.error(
                    "%s: tick exceeded tick_timeout=%ss and was abandoned; "
                    "loop continues",
                    name,
                    tick_timeout,
                )
            except Exception:
                log.exception("%s: tick raised; loop continues", name)
            except BaseException:  # noqa: B036 - deliberate: a stray non-Exception
                # (SystemExit/KeyboardInterrupt surfaced from a worker thread)
                # must NOT be allowed to silently end the loop.
                log.exception(
                    "%s: tick raised a BaseException; loop continues", name
                )

            if stop_event.is_set():
                break

            try:
                await asyncio.wait_for(stop_event.wait(), timeout=interval)
            except asyncio.TimeoutError:
                pass  # normal: interval elapsed, run the next tick
    except asyncio.CancelledError:
        exit_reason = "cancelled"
        log.info("%s stopped (reason=cancelled)", name)
        raise
    log.info("%s stopped (reason=%s)", name, exit_reason)
