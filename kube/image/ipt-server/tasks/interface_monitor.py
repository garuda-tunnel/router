"""Monitor task for interface-state driven route/PBR replay.

This module owns ``state.INTERFACES`` (the ``ifname -> ifindex`` snapshot).
Writers: only this module. Readers: ``RouteObject.interfaces`` (via
``state.INTERFACES_LOCK``).
"""

import asyncio
import logging
from typing import Dict, Set, Tuple

from pyroute2 import IPRoute

from ipt_server import state


logger = logging.getLogger(__name__)


def _collect_link_state() -> Tuple[Dict[str, int], Dict[str, str]]:
    """Read kernel links and return (ifindex_map, operstate_map).

    ``ifindex_map``: ``{ifname: ifindex}``
    ``operstate_map``: ``{ifname: IFLA_OPERSTATE}``
    """
    ifindex_map: Dict[str, int] = {}
    operstate_map: Dict[str, str] = {}
    with IPRoute() as ipr:
        for link in ipr.get_links():
            name = link.get_attr("IFLA_IFNAME")
            if name is None:
                continue
            ifindex_map[name] = link["index"]
            operstate_map[name] = link.get_attr("IFLA_OPERSTATE")
    return ifindex_map, operstate_map


def _publish_snapshot(ifindex_map: Dict[str, int]) -> None:
    """Replace ``state.INTERFACES`` atomically under ``INTERFACES_LOCK``."""
    with state.INTERFACES_LOCK:
        state.INTERFACES.clear()
        state.INTERFACES.update(ifindex_map)


def _poll_links_once() -> Dict[str, str]:
    """Poll kernel links once, publish the snapshot, and return operstate map.

    Returned ``operstate_map`` is used by the monitor loop to diff
    against the previous tick and decide whether to reapply PBR or
    replay routes for a given interface.
    """
    ifindex_map, operstate_map = _collect_link_state()
    _publish_snapshot(ifindex_map)
    return operstate_map


async def refresh_interfaces_snapshot() -> None:
    """Run one synchronous poll from async context.

    Called by ``async_main`` once before the WebSocket server starts so
    that ``state.INTERFACES`` is guaranteed non-empty before the first
    DNS A-record is processed.
    """
    await asyncio.to_thread(_poll_links_once)


def _dev_interfaces_from_routes(routes) -> Set[str]:
    """Collect dev= interface names from all route config entries."""
    devs: Set[str] = set()
    for r in routes:
        route = getattr(r, "route", None)
        if isinstance(route, list):
            # RouteActionGroup: route is List[RouteMember]
            for member in route:
                if member.dev:
                    devs.add(member.dev)
        elif route is not None and getattr(route, "dev", None):
            # Legacy BaseRoute: route is _LegacyRouteAction
            devs.add(route.dev)
    return devs


async def monitor_interfaces(stop_event):
    """Monitor interface state changes and reconcile PBR/routes."""
    from ipt_server.main import apply_pbr

    logger.info("Starting interface monitor")
    last_state: Dict[str, str] = {}

    route_interfaces: Set[str] = _dev_interfaces_from_routes(state.CONFIG.routes)
    all_monitored: Set[str] = set(state.CONFIG.interfaces) | route_interfaces
    logger.info(
        f"Monitoring PBR interfaces: {state.CONFIG.interfaces}, "
        f"route interfaces: {sorted(route_interfaces)}"
    )

    while not stop_event.is_set():
        try:
            # pyroute2 0.9.x sync API internally calls
            # ``event_loop.run_until_complete`` and raises
            # ``RuntimeError: This event loop is already running`` if
            # invoked from the main asyncio loop.  Run the netlink poll
            # in a worker thread so it executes on a fresh event loop.
            current_state = await asyncio.to_thread(_poll_links_once)

            need_pbr_reapply = False
            need_route_replay = False

            for iface in all_monitored:
                if iface in current_state and iface not in last_state:
                    logger.info(f"Interface {iface} appeared")
                    if iface in state.CONFIG.interfaces:
                        need_pbr_reapply = True
                    else:
                        need_route_replay = True
                elif iface not in current_state and iface in last_state:
                    logger.info(f"Interface {iface} disappeared")

                if iface in current_state and iface in last_state:
                    if current_state[iface] != last_state[iface]:
                        logger.info(
                            f"Interface {iface} state changed: "
                            f"{last_state[iface]} -> {current_state[iface]}"
                        )
                        if iface in state.CONFIG.interfaces:
                            need_pbr_reapply = True
                        else:
                            need_route_replay = True

            async def replay_healthy_routes():
                unhealthy = {
                    iface for iface, ok in state.INTERFACE_HEALTH.items() if not ok
                }
                if unhealthy:
                    logger.info(
                        f"Skipping route replay for unhealthy gated "
                        f"interfaces: {unhealthy}"
                    )
                    for iface in route_interfaces - unhealthy:
                        await asyncio.to_thread(
                            state.ROUTER.replay_routes_for_interface, iface
                        )
                else:
                    await asyncio.to_thread(state.ROUTER.replay_routes)

            if need_pbr_reapply:
                logger.info("PBR interface changed, reapplying PBR rules...")
                try:
                    await asyncio.to_thread(apply_pbr)
                    if state.ROUTER:
                        await replay_healthy_routes()
                except Exception as e:
                    logger.error(f"Failed to re-apply PBR: {e}")
            elif need_route_replay:
                logger.info("Route interface changed, replaying routes...")
                try:
                    if state.ROUTER:
                        await replay_healthy_routes()
                except Exception as e:
                    logger.error(f"Failed to replay routes: {e}")

            last_state = {k: v for k, v in current_state.items() if k in all_monitored}

        except Exception as e:
            logger.error(f"Error in interface monitor: {e}")

        await asyncio.sleep(5)
