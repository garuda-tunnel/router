"""Monitor task for gated route-health transitions."""

import asyncio
import logging

from ipt_server import state


logger = logging.getLogger(__name__)


async def monitor_route_health(health_source):
    """Periodically poll route health and apply/remove routes on state transitions.

    Maintains INTERFACE_HEALTH so that monitor_interfaces can skip unhealthy interfaces
    when replaying routes. Runs as an asyncio task alongside monitor_interfaces.

    If health_source is None (no gated interfaces configured) returns immediately.
    """
    if health_source is None:
        return

    logger.info("Starting route health monitor")
    last_health: dict[str, bool] = {}

    while True:
        current_health = health_source.get_interface_health(
            state.CONFIG.route_health.interfaces
        )

        for iface, healthy in current_health.items():
            was_healthy = last_health.get(iface)
            if was_healthy is False and healthy:
                logger.info(
                    f"Route health: {iface} recovered (unhealthy->healthy), replaying routes"
                )
                state.INTERFACE_HEALTH[iface] = True
                state.ROUTER.replay_routes_for_interface(iface)
            elif was_healthy is True and not healthy:
                logger.info(
                    f"Route health: {iface} degraded (healthy->unhealthy), removing routes"
                )
                state.INTERFACE_HEALTH[iface] = False
                state.ROUTER.remove_routes_for_interface(iface)
            else:
                state.INTERFACE_HEALTH[iface] = healthy

        last_health = current_health
        await asyncio.sleep(5)
