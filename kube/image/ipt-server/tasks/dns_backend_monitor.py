"""Monitor task for DNS backend DNAT reconciliation."""

import asyncio
import logging

from tasks.periodic import run_periodic

logger = logging.getLogger(__name__)

_INTERVAL_SECONDS = 10
# reconcile_dns_backend does blocking socket work (gethostbyname +
# create_connection, ~1s timeout each); bound it so a hung resolver/connect
# can never wedge the loop (see tasks/periodic.py).
_TICK_TIMEOUT_SECONDS = 30


async def monitor_dns_backend(stop_event: asyncio.Event) -> None:
    """Periodically reconcile DNS backend NAT state.

    garuda_pdns starts after garuda_ipt and may be either unresolved or not yet
    accepting queries. This loop keeps retrying until the backend is reachable
    and keeps the NAT target current if Docker recreates garuda_pdns with a new IP.
    """
    from ipt_server.main import reconcile_dns_backend

    async def _tick() -> None:
        # reconcile_dns_backend is synchronous and does blocking socket I/O;
        # run it off the event loop so it cannot stall the loop thread.
        await asyncio.to_thread(reconcile_dns_backend)

    await run_periodic(
        "dns backend monitor",
        _tick,
        interval=_INTERVAL_SECONDS,
        stop_event=stop_event,
        tick_timeout=_TICK_TIMEOUT_SECONDS,
        logger=logger,
    )
