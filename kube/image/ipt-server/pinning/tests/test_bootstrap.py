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
    cfg.pinning_portal_anchor_addr = "1.1.1.1"
    cfg.pinning_portal_anchor_port = 1111
    cfg.pinning_api_port = 0  # ephemeral port; site.start binds it
    return cfg


@pytest.mark.asyncio
async def test_liveness_task_reference_is_retained():
    """The liveness task must be retained (not fire-and-forget) so it is not
    garbage-collected mid-run (weak-ref GC of an unreferenced asyncio.Task)."""
    import gc
    import weakref

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

    with patch.object(
        bootstrap.KernelReconciler, "install_static_rules", _noop
    ), patch.object(
        bootstrap.KernelReconciler, "reconcile", _noop
    ), patch("asyncio.create_task", side_effect=tracking_create_task):
        _, _, _ = await bootstrap.setup_pinning(cfg, lambda: [], stop)

    # Drop the only local strong ref we know of and force GC.
    task = created.pop("task")
    del task
    gc.collect()
    assert created["ref"]() is not None, (
        "liveness task was GC-eligible: setup_pinning must retain a strong "
        "reference for the process lifetime"
    )
    stop.set()


@pytest.mark.asyncio
async def test_one_egress_exception_does_not_stop_probing_others():
    """A raise inside update_egress_liveness for one egress must not stop the
    loop from probing the other egresses on the same tick."""
    probed = []

    def probe(target, interfaces):
        probed.append(target.egress_name)
        return (True, "172.30.0.35", "backbone")

    reconciler = MagicMock()

    async def update(*, egress, alive, nh_ip, nh_dev):
        if egress == "usa":
            raise RuntimeError("Nexthop has invalid gateway")
    reconciler.update_egress_liveness = update

    # NB: MagicMock consumes the `name=` kwarg, so tag identity via a plain attr.
    usa = MagicMock(gw="10.9.19.2", dev=None)
    usa.egress_name = "usa"
    border = MagicMock(gw="10.130.30.50", dev=None)
    border.egress_name = "border"
    catalog = {"usa": usa, "border": border}
    stop = asyncio.Event()

    async def stopper():
        await asyncio.sleep(0.05)
        stop.set()

    with patch.object(bootstrap, "probe_egress", side_effect=probe):
        await asyncio.gather(
            bootstrap._liveness_loop(reconciler, catalog, lambda: [], stop),
            stopper(),
        )
    assert "border" in probed, "loop stopped after usa raised; border never probed"


@pytest.mark.asyncio
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

    async def stopper():
        await asyncio.sleep(0.08)
        stop.set()

    with patch.object(bootstrap, "probe_egress", side_effect=probe), \
         patch.object(bootstrap, "LIVENESS_INTERVAL_SECONDS", 0.02):
        await asyncio.gather(
            bootstrap._liveness_loop(reconciler, catalog, lambda: [], stop),
            stopper(),
        )

    assert len(calls) >= 2, (
        "transient failure was cached and never retried: update_egress_liveness "
        "must re-fire on the next tick because last_alive is written on success only"
    )


@pytest.mark.asyncio
async def test_persistent_failure_does_not_hotloop_log_spam(caplog):
    """A persistent failure must keep retrying but log the full traceback only
    once per changed (egress, outcome) key (throttled), not every tick."""
    import logging as _l

    async def update(*, egress, alive, nh_ip, nh_dev):
        raise RuntimeError("persistent NetlinkError")

    reconciler = MagicMock()
    reconciler.update_egress_liveness = update

    def probe(target, interfaces):
        return (True, "172.30.0.35", "backbone")  # identical failing outcome

    catalog = {"usa": MagicMock(name="usa", gw="10.9.19.2", dev=None)}
    stop = asyncio.Event()

    async def stopper():
        await asyncio.sleep(0.1)
        stop.set()

    with patch.object(bootstrap, "probe_egress", side_effect=probe), \
         patch.object(bootstrap, "LIVENESS_INTERVAL_SECONDS", 0.02), \
         caplog.at_level(_l.ERROR, logger="pinning.bootstrap"):
        await asyncio.gather(
            bootstrap._liveness_loop(reconciler, catalog, lambda: [], stop),
            stopper(),
        )

    spam = [r for r in caplog.records
            if r.levelno >= _l.ERROR and "usa" in r.getMessage()]
    assert len(spam) == 1, (
        f"expected the failure traceback logged once (throttled), got {len(spam)}; "
        "the retry must continue but the log must not spam every tick"
    )


@pytest.mark.asyncio
async def test_same_alive_different_nexthop_refires_update():
    """OSPF SPF recompute can change nh_ip while alive stays True; the loop
    must re-fire update_egress_liveness on that transition."""
    calls = []
    reconciler = MagicMock()

    async def update(*, egress, alive, nh_ip, nh_dev):
        calls.append((egress, alive, nh_ip, nh_dev))
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
    stop = asyncio.Event()

    async def stopper():
        await asyncio.sleep(0.06)
        stop.set()

    with patch.object(bootstrap, "probe_egress", side_effect=probe), \
         patch.object(bootstrap, "LIVENESS_INTERVAL_SECONDS", 0.02):
        await asyncio.gather(
            bootstrap._liveness_loop(reconciler, catalog, lambda: [], stop),
            stopper(),
        )

    nexthops = [c[2] for c in calls if c[0] == "usa"]
    assert "172.30.0.35" in nexthops and "172.30.0.99" in nexthops, (
        "loop keyed last_alive on alive alone and missed the nexthop change"
    )


# ---------------------------------------------------------------------------
# vpn2 STILL-BLACKHOLE (2026-07-02): the liveness loop MUST iterate EVERY
# configured egress on EVERY tick, independent of startup reachability. de/pt
# gws are tunnel IPs that are not OSPF-resolvable at process start (convergence
# race); border's gw is directly-attached and resolves immediately. A loop that
# only ever processes the reachable-at-startup subset (border) leaves 301/302
# blackhole forever. These tests lock in: (a) every egress is probed every tick,
# and (b) an egress dead at startup is recovered once its gw becomes resolvable.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_liveness_loop_probes_every_egress_every_tick():
    """The loop must probe border AND de AND pt on the very first tick.

    Regression for the vpn2 blackhole where only `border` (the first,
    alphabetically-sorted, startup-reachable egress) was ever processed and
    de/pt (tunnel-IP gws) were never probed/installed.
    """
    catalog = {
        "border": MagicMock(gw="10.130.30.50", dev=None),
        "de": MagicMock(gw="10.9.21.2", dev=None),
        "pt": MagicMock(gw="10.9.19.2", dev=None),
    }
    probed: list[str] = []

    def probe(target, interfaces):
        probed.append(target.gw)
        return (True, "172.30.0.116", "backbone")

    reconciler = MagicMock()

    async def update(*, egress, alive, nh_ip, nh_dev):
        return None

    reconciler.update_egress_liveness = update
    stop = asyncio.Event()

    async def stopper():
        await asyncio.sleep(0.03)
        stop.set()

    with patch.object(bootstrap, "probe_egress", side_effect=probe), \
         patch.object(bootstrap, "LIVENESS_INTERVAL_SECONDS", 0.01):
        await asyncio.gather(
            bootstrap._liveness_loop(reconciler, catalog, lambda: [], stop),
            stopper(),
        )

    assert {"10.130.30.50", "10.9.21.2", "10.9.19.2"} <= set(probed), (
        "liveness loop did not probe every configured egress; probed only "
        f"{sorted(set(probed))} — de/pt were dropped from the per-egress "
        "iteration (the vpn2 blackhole)"
    )


@pytest.mark.asyncio
async def test_liveness_loop_installs_egress_dead_at_startup_once_alive():
    """An egress whose gw is unresolvable at startup must still be installed
    once its gw becomes resolvable (OSPF convergence), because the loop
    re-probes the FULL catalog every tick, not just the startup-reachable set.
    """
    catalog = {
        "border": MagicMock(gw="10.130.30.50", dev=None),
        "de": MagicMock(gw="10.9.21.2", dev=None),
        "pt": MagicMock(gw="10.9.19.2", dev=None),
    }
    # border alive from tick 1; de/pt dead until the 3rd sweep (OSPF converges).
    sweeps = {"n": 0}

    def probe(target, interfaces):
        if target.gw == "10.130.30.50":
            return (True, "172.30.0.116", "backbone")
        # de/pt: dead for the first couple of sweeps, then alive.
        if sweeps["n"] < 2:
            return (False, None, None)
        via = "172.30.0.112" if target.gw == "10.9.21.2" else "172.30.0.110"
        return (True, via, "backbone")

    installed_alive: dict[str, bool] = {}

    async def update(*, egress, alive, nh_ip, nh_dev):
        installed_alive[egress] = alive

    reconciler = MagicMock()
    reconciler.update_egress_liveness = update
    stop = asyncio.Event()

    async def sweeper():
        # Let a few ticks pass with de/pt dead, then converge, then a couple
        # more ticks so the recovery tick fires, then stop.
        await asyncio.sleep(0.03)
        sweeps["n"] = 2
        await asyncio.sleep(0.03)
        stop.set()

    with patch.object(bootstrap, "probe_egress", side_effect=probe), \
         patch.object(bootstrap, "LIVENESS_INTERVAL_SECONDS", 0.01):
        await asyncio.gather(
            bootstrap._liveness_loop(reconciler, catalog, lambda: [], stop),
            sweeper(),
        )

    assert installed_alive.get("de") is True, (
        "de was dead at startup and never recovered — the loop must re-probe "
        "and install it once OSPF converges"
    )
    assert installed_alive.get("pt") is True, (
        "pt was dead at startup and never recovered — the loop must re-probe "
        "and install it once OSPF converges"
    )


@pytest.mark.asyncio
async def test_liveness_loop_logs_full_catalog_at_entry_and_each_egress(caplog):
    """Observability: the loop must emit a non-throttled, greppable line naming
    the FULL catalog at loop entry and each egress as iteration reaches it.

    This is the log evidence the vpn2 forensics needed: on the live run there
    was no per-egress line for de/pt, so the operator could not tell whether
    the iteration reached them. Loop-entry + per-egress lines make the reached
    set unambiguous on the next run.
    """
    import logging as _l

    catalog = {
        "border": MagicMock(gw="10.130.30.50", dev=None),
        "de": MagicMock(gw="10.9.21.2", dev=None),
        "pt": MagicMock(gw="10.9.19.2", dev=None),
    }

    def probe(target, interfaces):
        return (True, "172.30.0.116", "backbone")

    reconciler = MagicMock()

    async def update(*, egress, alive, nh_ip, nh_dev):
        return None

    reconciler.update_egress_liveness = update
    stop = asyncio.Event()

    async def stopper():
        await asyncio.sleep(0.03)
        stop.set()

    with patch.object(bootstrap, "probe_egress", side_effect=probe), \
         patch.object(bootstrap, "LIVENESS_INTERVAL_SECONDS", 0.01), \
         caplog.at_level(_l.INFO, logger="pinning.bootstrap"):
        await asyncio.gather(
            bootstrap._liveness_loop(reconciler, catalog, lambda: [], stop),
            stopper(),
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
