"""Monitor task for DNS backend DNAT reconciliation."""

import asyncio


async def monitor_dns_backend(stop_event: asyncio.Event) -> None:
    """Periodically reconcile DNS backend NAT state.

    garuda_pdns starts after garuda_ipt and may be either unresolved or not yet
    accepting queries. This loop keeps retrying until the backend is reachable
    and keeps the NAT target current if Docker recreates garuda_pdns with a new IP.
    """
    from ipt_server.main import reconcile_dns_backend

    while not stop_event.is_set():
        reconcile_dns_backend()
        await asyncio.sleep(10)
