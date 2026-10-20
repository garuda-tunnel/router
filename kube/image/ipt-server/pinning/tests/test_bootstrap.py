"""Asyncio-safety regression tests for pinning bootstrap.

These tests pin the contract that the pinning liveness loop never calls
blocking probe code (urllib HTTP requests to the FRR vty bridge) from
inside the asyncio event-loop thread.  Doing so would freeze every
other coroutine — including the WebSocket handler that receives DNS
postresolve callbacks from the PowerDNS recursor — for the duration of
each probe, breaking DNS hijack while pinning is enabled.

Code: pinning/bootstrap.py::_liveness_loop

Conventions: the HTTP server is faked (no real listener), probe/update
fakes signal completion through asyncio events so scenario ends are
deterministic — never wall-clock sleeps.
"""

from __future__ import annotations

import asyncio
import gc
import logging
import threading
import weakref
from unittest.mock import MagicMock, patch

import pytest

from pinning import bootstrap


def _threadsafe_setter(stop: asyncio.Event):
    """Return a callable that sets *stop* safely from any thread."""
    loop = asyncio.get_event_loop()

    def _set():
        loop.call_soon_threadsafe(stop.set)

    return _set


async def test_liveness_loop_does_not_block_event_loop():
    """A never-returning probe must not block the event loop.

    The probe blocks on a gate that only the test driver opens. A second
    coroutine proves it can still run while the probe is pending: it
    yields to the loop and records its ticks. The gate is bounded: if
    probe_egress regressed to running inline on the loop thread, the
    driver could never resume to release it, the probe would time out on
    its own (recorded as such) and the assertions would FAIL in bounded
    time instead of hanging. This is the behavioral replacement for the
    old source scan asserting ``asyncio.to_thread(probe_egress`` in
    _liveness_loop source.
    """
    catalog = {"outer-pt": MagicMock(gw="10.0.0.1", dev=None)}
    reconciler = MagicMock()

    async def fake_update(*args, **kwargs):
        return None

    reconciler.update_egress_liveness = fake_update

    probe_entered = threading.Event()
    gate = threading.Event()
    order = []

    def gated_probe(target, interfaces):
        probe_entered.set()
        order.append("probe_entered")
        if gate.wait(timeout=5.0):
            order.append("probe_released_by_driver")
        else:
            order.append("probe_timed_out")
        order.append("probe_returned")
        return (True, "10.0.0.1", "wg-edge")

    stop = asyncio.Event()

    async def driver():
        # Deterministic: wait until the probe has actually started in its
        # worker thread, then prove the loop still runs by yielding to it.
        await asyncio.to_thread(probe_entered.wait, 10.0)
        order.append("driver_resumed")
        await asyncio.sleep(0)
        order.append("driver_yielded")
        gate.set()
        stop.set()

    async def hang_guard():
        # Bounded safety only: if the loop were blocked, the driver could
        # never open the gate, the probe would time out and the
        # assertions below would fail (in bounded time).
        await asyncio.wait_for(
            asyncio.gather(
                bootstrap._liveness_loop(reconciler, catalog, lambda: [], stop),
                driver(),
            ),
            timeout=10.0,
        )

    try:
        with patch.object(bootstrap, "probe_egress", side_effect=gated_probe):
            await hang_guard()
    finally:
        gate.set()  # idempotent: release a lingering probe before teardown

    assert "probe_released_by_driver" in order, (
        "the gate must have been released by the driver, not by the "
        "probe's own timeout — an inline probe would time out here "
        f"(order was {order})"
    )
    assert "probe_timed_out" not in order
    assert order.index("driver_resumed") < order.index("probe_returned"), (
        "the driver coroutine must run while the probe is pending — a "
        "synchronous probe would block the loop and trip the hang guard"
    )



# ---------------------------------------------------------------------------
# Fix B/C: liveness task retention, one-egress isolation, success-only
# last_alive (no failure caching), and throttled failure logging.
# ---------------------------------------------------------------------------


def _make_cfg():
    """Minimal cfg stand-in for setup_pinning with an empty egress catalog so
    no real netlink work runs when the reconciler methods are patched."""
    cfg = MagicMock()
    cfg.pinning_ttl = 86400
    cfg.pinning_egress = {}
    cfg.pinning_portal_anchor_addr = "192.0.2.1"
    cfg.pinning_portal_anchor_port = 1111
    cfg.pinning_api_port = 0  # ephemeral port; site.start binds it
    return cfg


async def test_liveness_task_reference_is_retained():
    """The liveness task must be retained (not fire-and-forget) so it is not
    garbage-collected mid-run (weak-ref GC of an unreferenced asyncio.Task).

    The HTTP server is faked: this is a retention unit test, not an HTTP
    integration test (the real server is exercised in pinning/tests/test_api.py
    over loopback).
    """
    created = {}
    real_create_task = asyncio.create_task

    def tracking_create_task(coro, *a, **k):
        task = real_create_task(coro, *a, **k)
        if "_liveness_loop" in getattr(coro, "__qualname__", ""):
            created["task"] = task
            created["ref"] = weakref.ref(task)
        return task

    cfg = _make_cfg()
    stop = asyncio.Event()

    async def _noop(*a, **k):
        return None

    async def _async_noop(*a, **k):
        return None

    fake_runner = MagicMock()
    fake_runner.setup = _async_noop
    fake_runner.cleanup = _async_noop
    fake_site = MagicMock()
    fake_site.start = _async_noop

    def fake_create_app(**kwargs):
        return MagicMock()

    with (
        patch.object(bootstrap.KernelReconciler, "install_static_rules", _noop),
        patch.object(bootstrap.KernelReconciler, "reconcile", _noop),
        patch.object(bootstrap, "create_app", side_effect=fake_create_app),
        patch.object(bootstrap.web, "AppRunner", return_value=fake_runner),
        patch.object(bootstrap.web, "TCPSite", return_value=fake_site),
        patch("asyncio.create_task", side_effect=tracking_create_task),
    ):
        _, _, http_task = await bootstrap.setup_pinning(cfg, lambda: [], stop)

    try:
        # Drop the only local strong ref we know of and force GC.
        task = created.pop("task")
        del task
        gc.collect()
        assert created["ref"]() is not None, (
            "liveness task was GC-eligible: setup_pinning must retain a strong "
            "reference for the process lifetime"
        )
    finally:
        # Await/cancel both background tasks so nothing leaks out of the test.
        stop.set()
        tasks = [t for t in (created.get("ref")(), http_task) if t is not None]
        for t in tasks:
            if not t.done():
                t.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


async def test_one_egress_exception_does_not_stop_probing_others():
    """A raise inside update_egress_liveness for one egress must not stop the
    loop from probing the other egresses on the same tick."""
    probed = []

    def probe(target, interfaces):
        probed.append(target.egress_name)
        return (True, "172.30.0.35", "backbone")

    reconciler = MagicMock()
    stop = asyncio.Event()
    set_stop = _threadsafe_setter(stop)

    async def update(*, egress, alive, nh_ip, nh_dev):
        if egress == "usa":
            raise RuntimeError("Nexthop has invalid gateway")
        if "border" in probed:
            set_stop()  # both egresses probed: deterministic end

    reconciler.update_egress_liveness = update

    # NB: MagicMock consumes the `name=` kwarg, so tag identity via a plain attr.
    usa = MagicMock(gw="10.9.19.2", dev=None)
    usa.egress_name = "usa"
    border = MagicMock(gw="10.130.30.50", dev=None)
    border.egress_name = "border"
    catalog = {"usa": usa, "border": border}

    async def run_loop():
        await asyncio.wait_for(
            bootstrap._liveness_loop(reconciler, catalog, lambda: [], stop),
            timeout=5.0,
        )

    with patch.object(bootstrap, "probe_egress", side_effect=probe):
        await asyncio.gather(run_loop(), _wait_stop(stop))

    assert "border" in probed, "loop stopped after usa raised; border never probed"


async def _wait_stop(stop: asyncio.Event, timeout: float = 5.0):
    """Block the gather until stop is set (bounded, deterministic)."""
    await asyncio.wait_for(stop.wait(), timeout=timeout)


async def test_transient_install_failure_is_retried_not_cached():
    """A transient failure in update_egress_liveness must NOT be cached: the
    next tick with the same outcome must re-fire update (success-only last_alive).
    Otherwise a transient NetlinkError permanently blackholes the egress."""
    calls = []
    fail_once = {"done": False}

    async def update(*, egress, alive, nh_ip, nh_dev):
        calls.append((egress, alive, nh_ip, nh_dev))
        if not fail_once["done"]:
            fail_once["done"] = True
            raise RuntimeError("transient NetlinkError")
        # second call succeeds

    reconciler = MagicMock()
    reconciler.update_egress_liveness = update

    def probe(target, interfaces):
        return (True, "172.30.0.35", "backbone")  # identical outcome every tick

    catalog = {"usa": MagicMock(name="usa", gw="10.9.19.2", dev=None)}
    stop = asyncio.Event()

    async def update_and_stop(*args, **kwargs):
        await update(*args, **kwargs)
        if len(calls) >= 2:
            stop.set()

    reconciler.update_egress_liveness = update_and_stop

    with (
        patch.object(bootstrap, "probe_egress", side_effect=probe),
        patch.object(bootstrap, "LIVENESS_INTERVAL_SECONDS", 0.001),
    ):
        await asyncio.wait_for(
            bootstrap._liveness_loop(reconciler, catalog, lambda: [], stop),
            timeout=5.0,
        )

    assert len(calls) >= 2, (
        "transient failure was cached and never retried: update_egress_liveness "
        "must re-fire on the next tick because last_alive is written on success only"
    )


async def test_persistent_failure_does_not_hotloop_log_spam(caplog):
    """A persistent failure must keep retrying but log the full traceback only
    once per changed (egress, outcome) key (throttled), not every tick."""
    n_updates = {"n": 0}
    stop = asyncio.Event()

    async def update_count_and_raise(*, egress, alive, nh_ip, nh_dev):
        n_updates["n"] += 1
        if n_updates["n"] >= 3:
            stop.set()  # arm the stop BEFORE raising: the raise is swallowed
        raise RuntimeError("persistent NetlinkError")

    reconciler = MagicMock()
    reconciler.update_egress_liveness = update_count_and_raise

    def probe(target, interfaces):
        return (True, "172.30.0.35", "backbone")  # identical failing outcome

    catalog = {"usa": MagicMock(name="usa", gw="10.9.19.2", dev=None)}

    with (
        patch.object(bootstrap, "probe_egress", side_effect=probe),
        patch.object(bootstrap, "LIVENESS_INTERVAL_SECONDS", 0.001),
        caplog.at_level(logging.ERROR, logger="pinning.bootstrap"),
    ):
        await asyncio.wait_for(
            bootstrap._liveness_loop(reconciler, catalog, lambda: [], stop),
            timeout=5.0,
        )

    spam = [r for r in caplog.records if r.levelno >= logging.ERROR and "usa" in r.getMessage()]
    assert n_updates["n"] >= 3, "retry must continue while failing"
    assert len(spam) == 1, (
        f"expected the failure traceback logged once (throttled), got {len(spam)}; "
        "the retry must continue but the log must not spam every tick"
    )


async def test_same_alive_different_nexthop_refires_update():
    """OSPF SPF recompute can change nh_ip while alive stays True; the loop
    must re-fire update_egress_liveness on that transition."""
    calls = []
    reconciler = MagicMock()

    stop = asyncio.Event()

    async def update(*, egress, alive, nh_ip, nh_dev):
        calls.append((egress, alive, nh_ip, nh_dev))
        if len(calls) >= 2:
            stop.set()

    reconciler.update_egress_liveness = update

    seq = iter([
        (True, "172.30.0.35", "backbone"),   # tick 1
        (True, "172.30.0.99", "backbone"),   # tick 2: same alive, new nexthop
    ])

    def probe(target, interfaces):
        try:
            return next(seq)
        except StopIteration:
            return (True, "172.30.0.99", "backbone")

    catalog = {"usa": MagicMock(name="usa", gw="10.9.19.2", dev=None)}

    with (
        patch.object(bootstrap, "probe_egress", side_effect=probe),
        patch.object(bootstrap, "LIVENESS_INTERVAL_SECONDS", 0.001),
    ):
        await asyncio.wait_for(
            bootstrap._liveness_loop(reconciler, catalog, lambda: [], stop),
            timeout=5.0,
        )

    nexthops = [c[2] for c in calls if c[0] == "usa"]
    assert "172.30.0.35" in nexthops and "172.30.0.99" in nexthops, (
        "loop keyed last_alive on alive alone and missed the nexthop change"
    )


# ---------------------------------------------------------------------------
# Live-incident regression (2026-07-02 still-blackhole): the liveness loop
# MUST iterate EVERY configured egress on EVERY tick, independent of startup
# reachability. Tunnel-IP gws are not OSPF-resolvable at process start
# (convergence race); a directly-attached gw resolves immediately. A loop
# that only ever processes the reachable-at-startup subset leaves the pinning
# tables blackhole forever. These tests lock in: (a) every egress is probed
# every tick, and (b) an egress dead at startup is recovered once its gw
# becomes resolvable.
# ---------------------------------------------------------------------------


async def test_liveness_loop_probes_every_egress_every_tick():
    """The loop must probe border AND de AND pt on the very first tick.

    Regression for a live incident where only `border` (the first,
    alphabetically-sorted, startup-reachable egress) was ever processed and
    de/pt (tunnel-IP gws) were never probed/installed.
    """
    catalog = {
        "border": MagicMock(gw="10.130.30.50", dev=None),
        "de": MagicMock(gw="10.9.21.2", dev=None),
        "pt": MagicMock(gw="10.9.19.2", dev=None),
    }
    probed: list[str] = []
    stop = asyncio.Event()
    set_stop = _threadsafe_setter(stop)

    def probe(target, interfaces):
        probed.append(target.gw)
        if {"10.130.30.50", "10.9.21.2", "10.9.19.2"} <= set(probed):
            set_stop()
        return (True, "172.30.0.116", "backbone")

    reconciler = MagicMock()

    async def update(*, egress, alive, nh_ip, nh_dev):
        return None

    reconciler.update_egress_liveness = update

    with (
        patch.object(bootstrap, "probe_egress", side_effect=probe),
        patch.object(bootstrap, "LIVENESS_INTERVAL_SECONDS", 0.001),
    ):
        await asyncio.wait_for(
            bootstrap._liveness_loop(reconciler, catalog, lambda: [], stop),
            timeout=5.0,
        )

    assert {"10.130.30.50", "10.9.21.2", "10.9.19.2"} <= set(probed), (
        "liveness loop did not probe every configured egress; probed only "
        f"{sorted(set(probed))} — de/pt were dropped from the per-egress "
        "iteration (the live blackhole)"
    )


async def test_liveness_loop_installs_egress_dead_at_startup_once_alive():
    """An egress whose gw is unresolvable at startup must still be installed
    once its gw becomes resolvable (OSPF convergence), because the loop
    re-probes the FULL catalog every tick, not just the startup-reachable set.

    Deterministic on observed outcomes, not on time: convergence is armed
    only after BOTH tunnel egresses have been observed dead at least once
    (the startup-reachability state the regression guards)."""
    catalog = {
        "border": MagicMock(gw="10.130.30.50", dev=None),
        "de": MagicMock(gw="10.9.21.2", dev=None),
        "pt": MagicMock(gw="10.9.19.2", dev=None),
    }
    converged = asyncio.Event()
    installed_alive: dict[str, bool] = {}
    dead_seen = {"de": False, "pt": False}
    stop = asyncio.Event()

    async def update(*, egress, alive, nh_ip, nh_dev):
        installed_alive[egress] = alive
        if egress in dead_seen and not alive:
            dead_seen[egress] = True
        if installed_alive.get("de") and installed_alive.get("pt"):
            stop.set()

    reconciler = MagicMock()
    reconciler.update_egress_liveness = update

    def probe(target, interfaces):
        if target.gw == "10.130.30.50":
            return (True, "172.30.0.116", "backbone")
        # de/pt: dead until convergence is armed, then alive.
        if not converged.is_set():
            return (False, None, None)
        via = "172.30.0.112" if target.gw == "10.9.21.2" else "172.30.0.110"
        return (True, via, "backbone")

    async def converge_after_both_dead():
        # Gate strictly on observed dead updates for BOTH tunnel egresses.
        while not (dead_seen["de"] and dead_seen["pt"]):
            await asyncio.sleep(0)
        converged.set()

    with (
        patch.object(bootstrap, "probe_egress", side_effect=probe),
        patch.object(bootstrap, "LIVENESS_INTERVAL_SECONDS", 0.001),
    ):
        await asyncio.wait_for(
            asyncio.gather(
                bootstrap._liveness_loop(
                    reconciler, catalog, lambda: [], stop
                ),
                converge_after_both_dead(),
                _wait_stop(stop),
            ),
            timeout=5.0,
        )

    assert installed_alive.get("de") is True, (
        "de was dead at startup and never recovered — the loop must re-probe "
        "and install it once OSPF converges"
    )
    assert installed_alive.get("pt") is True, (
        "pt was dead at startup and never recovered — the loop must re-probe "
        "and install it once OSPF converges"
    )


async def test_liveness_loop_logs_full_catalog_at_entry_and_each_egress(caplog):
    """Observability: the loop must emit a non-throttled, greppable line naming
    the FULL catalog at loop entry and each egress as iteration reaches it.

    This is the log evidence a live forensics needed: on the incident run
    there was no per-egress line for de/pt, so the operator could not tell
    whether the iteration reached them. Loop-entry + per-egress lines make
    the reached set unambiguous on the next run.
    """
    catalog = {
        "border": MagicMock(gw="10.130.30.50", dev=None),
        "de": MagicMock(gw="10.9.21.2", dev=None),
        "pt": MagicMock(gw="10.9.19.2", dev=None),
    }
    stop = asyncio.Event()
    set_stop = _threadsafe_setter(stop)

    def probe(target, interfaces):
        set_stop()  # one full sweep is enough for the log assertions
        return (True, "172.30.0.116", "backbone")

    reconciler = MagicMock()

    async def update(*, egress, alive, nh_ip, nh_dev):
        return None

    reconciler.update_egress_liveness = update

    with (
        patch.object(bootstrap, "probe_egress", side_effect=probe),
        patch.object(bootstrap, "LIVENESS_INTERVAL_SECONDS", 0.001),
        caplog.at_level(logging.INFO, logger="pinning.bootstrap"),
    ):
        await asyncio.wait_for(
            bootstrap._liveness_loop(reconciler, catalog, lambda: [], stop),
            timeout=5.0,
        )

    messages = [r.getMessage() for r in caplog.records]
    joined = "\n".join(messages)
    assert any("border" in m and "de" in m and "pt" in m for m in messages), (
        "expected a loop-entry line naming the full catalog "
        "(border, de, pt); got:\n" + joined
    )
    for egress in ("border", "de", "pt"):
        assert any(
            f"egress={egress}" in m for m in messages
        ), (
            f"expected a per-egress iteration line for {egress!r}; got:\n"
            + joined
        )
