"""Bootstrap helpers for the pinning subsystem.

Called from ipt_server/main.py once the catalog is non-empty. Returns
the manager + reconciler; spawns liveness loop; starts aiohttp.

Lifecycle:
  1. Install static ip rules (fwmark→table, no DNS escape needed
     since pin marks 0xA00+i don't overlap DNS_MARK 0x201).
  2. Apply an empty pinning nft table so the static `ip rule
     fwmark X lookup` entries have a chain to dispatch into.
  3. Spawn liveness loop (probe each egress over the FRR vty bridge).
  4. Start the aiohttp pin API.

There is no sweep loop: nft set elements carry timeout=ttl, the
kernel expires them, and the next reconcile naturally re-emits the
surviving subset.  The Python map filters expired rows on snapshot()
reads.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Tuple

from aiohttp import web

from pinning.api import create_app
from pinning.kernel import KernelReconciler
from pinning.liveness import probe_egress
from pinning.manager import PinningManager
from tasks.periodic import run_periodic


log = logging.getLogger(__name__)
LIVENESS_INTERVAL_SECONDS = 5
# Bounds one full liveness sweep (each egress probe is a to_thread FRR-bridge
# call at up to _VTY_BRIDGE_TIMEOUT); a hung probe can never wedge the loop
# (see tasks/periodic.py).
LIVENESS_TICK_TIMEOUT_SECONDS = 60


async def setup_pinning(
    cfg, interfaces_view, stop_event: asyncio.Event,
) -> Tuple[PinningManager, KernelReconciler, asyncio.Task]:
    manager = PinningManager(ttl_seconds=cfg.pinning_ttl)
    reconciler = KernelReconciler(
        catalog=cfg.pinning_egress,
        portal_addr=cfg.pinning_portal_anchor_addr,
        portal_port=cfg.pinning_portal_anchor_port,
        api_port=cfg.pinning_api_port,
        ttl_seconds=cfg.pinning_ttl,
    )
    await reconciler.install_static_rules()
    # Empty initial nft table so the static ip-rule entries dispatch
    # into a chain that exists.
    await reconciler.reconcile({})

    liveness_task = asyncio.create_task(
        _liveness_loop(reconciler, cfg.pinning_egress, interfaces_view, stop_event)
    )
    # Retain a strong reference for the process lifetime: an unreferenced
    # asyncio.Task is weak-ref-GC-eligible and can be silently collected
    # mid-run, killing the liveness loop.
    reconciler._liveness_task = liveness_task

    app = create_app(
        manager=manager, reconciler=reconciler, catalog=cfg.pinning_egress,
    )
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", cfg.pinning_api_port)
    await site.start()
    log.info("pinning API listening on :%d", cfg.pinning_api_port)

    async def _shutdown_runner():
        await stop_event.wait()
        await runner.cleanup()

    http_task = asyncio.create_task(_shutdown_runner())
    return manager, reconciler, http_task


async def _liveness_loop(reconciler, catalog, interfaces_view, stop_event):
    """Probe each egress periodically and reconcile per-egress liveness.

    probe_egress reaches into tasks.nexthop_monitor._vtysh, which
    makes a blocking urllib.request.urlopen call against the FRR vty
    bridge with a 5s timeout.  Calling it directly from this
    coroutine would freeze the asyncio event loop for up to 5s x N
    egresses every tick — and in particular would stall the
    WebSocket handler that PowerDNS uses for postresolve callbacks,
    breaking DNS hijack as soon as pinning is enabled.  Hop into a
    worker thread instead.
    """
    # last_alive is keyed on the FULL outcome tuple (alive, nh_ip, nh_dev) so a
    # same-alive nexthop change (OSPF SPF recompute) re-fires the update (Fix C).
    last_alive: dict[str, tuple] = {}
    # Throttle key for failure logging: full traceback once per changed outcome,
    # then suppress repeats — the retry itself is never suppressed (Fix B).
    last_logged_failure: dict[str, object] = {}

    # Observability (vpn2 STILL-BLACKHOLE, 2026-07-02): name the FULL catalog
    # once at loop entry so the reached set is unambiguous in the pod logs.
    # Without this, the live forensics could only INFER (from the absence of a
    # probe DEBUG line) that de/pt were skipped — never confirm the iterated
    # set. This line is emitted exactly once, at loop start.
    log.info(
        "pinning liveness loop iterating catalog: %s",
        ", ".join(sorted(catalog.keys())) or "(empty)",
    )

    async def _tick() -> None:
        for egress, target in catalog.items():
            # Non-throttled per-egress iteration marker: proves the loop
            # actually reached this egress on this tick (the vpn2 blackhole
            # left de/pt with NO per-egress line at all). Kept at INFO because
            # a silent per-egress drop is precisely the failure we must be able
            # to see; it is one line per egress per tick.
            log.info(
                "pinning liveness tick reached egress=%s gw=%s dev=%s",
                egress,
                getattr(target, "gw", None),
                getattr(target, "dev", None),
            )
            outcome = None  # bound before to_thread so the throttle key is robust
            try:
                alive, nh_ip, nh_dev = await asyncio.to_thread(
                    probe_egress, target, set(interfaces_view()),
                )
                outcome = (alive, nh_ip, nh_dev)
                if last_alive.get(egress) != outcome:
                    await reconciler.update_egress_liveness(
                        egress=egress, alive=alive, nh_ip=nh_ip, nh_dev=nh_dev,
                    )
                    # SUCCESS-ONLY: reached only if update_egress_liveness did
                    # not raise, so a transient failure is never cached and is
                    # retried on the next tick (Fix B).
                    last_alive[egress] = outcome
                    last_logged_failure.pop(egress, None)  # clear throttle on recovery
            except Exception:
                # THROTTLE the log (not the retry): full traceback once per
                # changed (egress, outcome) key, then drop to debug.
                if last_logged_failure.get(egress) != outcome:
                    log.exception("pinning liveness probe failed for %s", egress)
                    last_logged_failure[egress] = outcome
                else:
                    log.debug("pinning liveness still failing for %s", egress)

    await run_periodic(
        "pinning liveness loop",
        _tick,
        interval=LIVENESS_INTERVAL_SECONDS,
        stop_event=stop_event,
        tick_timeout=LIVENESS_TICK_TIMEOUT_SECONDS,
        logger=log,
    )
