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


log = logging.getLogger(__name__)
LIVENESS_INTERVAL_SECONDS = 5


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

    asyncio.create_task(
        _liveness_loop(reconciler, cfg.pinning_egress, interfaces_view, stop_event)
    )

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
    last_alive: dict[str, bool] = {}
    while not stop_event.is_set():
        for egress, target in catalog.items():
            try:
                alive, nh_ip, nh_dev = await asyncio.to_thread(
                    probe_egress, target, set(interfaces_view()),
                )
                if last_alive.get(egress) != alive:
                    await reconciler.update_egress_liveness(
                        egress=egress, alive=alive, nh_ip=nh_ip, nh_dev=nh_dev,
                    )
                    last_alive[egress] = alive
            except Exception:
                log.exception("pinning liveness probe failed for %s", egress)
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=LIVENESS_INTERVAL_SECONDS)
        except asyncio.TimeoutError:
            pass
