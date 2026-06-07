"""Asyncio-safety regression tests for pinning bootstrap.

These tests pin the contract that the pinning liveness loop never calls
blocking probe code (urllib HTTP requests to the FRR vty bridge) from
inside the asyncio event-loop thread.  Doing so would freeze every
other coroutine — including the WebSocket handler that receives DNS
postresolve callbacks from the PowerDNS recursor — for the duration of
each probe, breaking DNS hijack while pinning is enabled.

Code: pinning/bootstrap.py::_liveness_loop
"""
from __future__ import annotations

import asyncio
import inspect
import textwrap
from unittest.mock import MagicMock, patch

import pytest

from pinning import bootstrap


def test_liveness_loop_dispatches_probe_via_to_thread():
    """The liveness loop must hand probe_egress to asyncio.to_thread.

    probe_egress reaches into nexthop_monitor._vtysh, which performs a
    blocking urllib.request.urlopen call (timeout up to 5s).  Calling it
    directly from the coroutine freezes the event loop and stalls the
    websocket handler that pdns talks to over postresolve, causing DNS
    hijack to time out as soon as pinning is enabled.
    """
    src = inspect.getsource(bootstrap._liveness_loop)
    src = textwrap.dedent(src)

    assert "probe_egress(" not in src.replace(
        "asyncio.to_thread(probe_egress", ""
    ), (
        "_liveness_loop must call probe_egress via asyncio.to_thread, "
        "never directly — direct calls block the event loop on the "
        "synchronous urllib HTTP probe in tasks.nexthop_monitor._vtysh "
        "and stall the WebSocket DNS-postresolve handler"
    )


@pytest.mark.asyncio
async def test_liveness_loop_does_not_block_event_loop():
    """A slow probe must not delay other coroutines beyond the LIVENESS tick.

    Simulate a probe that blocks for 200ms (mimicking a slow vty bridge
    response) and assert that a separate coroutine scheduled at t=0 wakes
    up at ~50ms despite the probe running.  If the probe were called
    synchronously the wake-up would be delayed by ≥200ms.
    """
    catalog = {"outer-pt": MagicMock(gw="10.0.0.1", dev=None)}
    reconciler = MagicMock()

    async def fake_update(*args, **kwargs):
        return None

    reconciler.update_egress_liveness = fake_update

    def slow_probe(target, interfaces):
        # Synchronous sleep to mimic urllib timeout.
        import time as _t

        _t.sleep(0.2)
        return (True, "10.0.0.1", "wg-edge")

    stop = asyncio.Event()
    other_woke_at = []

    async def other():
        loop = asyncio.get_running_loop()
        await asyncio.sleep(0.05)
        other_woke_at.append(loop.time())
        stop.set()

    with patch.object(bootstrap, "probe_egress", side_effect=slow_probe):
        loop = asyncio.get_running_loop()
        t0 = loop.time()
        await asyncio.gather(
            bootstrap._liveness_loop(reconciler, catalog, lambda: [], stop),
            other(),
        )

    elapsed = other_woke_at[0] - t0
    # 50ms scheduled, must wake up <150ms (well under the 200ms blocking probe).
    assert elapsed < 0.15, (
        f"event loop was blocked: other coroutine woke at {elapsed*1000:.0f}ms "
        "(expected <150ms; probe takes 200ms synchronously)"
    )
