"""Nexthop group monitor: OSPF-aware single-active-member selection.

Queries the FRR sidecar's vty HTTP bridge (127.0.0.1:7890) for OSPF state
and polls interface presence for dev= members. On liveness change, switches
the nhg group's single active member via ip nexthop replace.
"""

import asyncio
import ipaddress
import json
import logging
import urllib.error
import urllib.request
from typing import Optional

from ipt_server import state
import nexthop
from Config import NhgDescriptor
from tasks.periodic import run_periodic

logger = logging.getLogger(__name__)

_TICK_INTERVAL_SECONDS = 5
_GW_FAILURE_THRESHOLD = 3  # consecutive failures before treating gw as dead
_VTY_BRIDGE_URL = "http://127.0.0.1:7890/vtysh"
_VTY_BRIDGE_TIMEOUT = 5
# Hard wall-clock deadline for one _tick (a full probe sweep across all
# members, each doing up to 4 vty-bridge HTTP calls at _VTY_BRIDGE_TIMEOUT).
# Bounds a hung to_thread(_tick) so it can never wedge the loop (see
# tasks/periodic.py). Generous headroom over the worst-case probe latency.
_TICK_TIMEOUT_SECONDS = 60

# The OSPF backbone transit subnet. The directly-attached fallback in
# _resolve_direct_router_nexthop may only return an address on this subnet as a
# kernel gateway (never a tunnel-edge WireGuard P2P IP, which the kernel rejects
# with "Nexthop has invalid gateway"). See spec §6.1 (Fix 8).
_BACKBONE_SUBNET = ipaddress.ip_network("172.30.0.0/24")


def _is_on_backbone(address: str) -> bool:
    """Return True iff *address* is on the OSPF backbone subnet (172.30.0.0/24).

    Used to guard the directly-attached fallback so it can only ever return a
    valid on-backbone gateway, never a tunnel-edge P2P IP.
    """
    try:
        return ipaddress.ip_address(address) in _BACKBONE_SUBNET
    except ValueError:
        return False


def _vtysh(command: str) -> Optional[dict]:
    """POST a vtysh command to the FRR sidecar HTTP bridge.

    Returns parsed JSON on 200, or None on any failure (network, retcode != 0,
    non-JSON body, timeout).
    """
    req = urllib.request.Request(
        _VTY_BRIDGE_URL,
        data=command.encode("utf-8"),
        method="POST",
        headers={"Content-Type": "text/plain"},
    )
    try:
        with urllib.request.urlopen(req, timeout=_VTY_BRIDGE_TIMEOUT) as resp:
            body = resp.read()
    except (urllib.error.URLError, OSError) as exc:
        logger.debug("vty_bridge call failed: %s", exc)
        return None
    try:
        data = json.loads(body)
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict):
        return None
    return data


def _find_router_owning_address(gw: str, router_lsdb: Optional[dict]) -> Optional[str]:
    """Return the advertisingRouter of a Router LSA that declares *gw*
    as one of its routerInterfaceAddress values.

    Search is scoped to Router LSAs. Matches against routerInterfaceAddress
    in routerLinks values; does NOT match against neighborRouterId.
    Returns None on missing / malformed data or when no router owns gw.
    """
    if not isinstance(router_lsdb, dict):
        return None
    areas = (
        router_lsdb.get("routerLinkStates", {})
        .get("areas", {})
    )
    if not isinstance(areas, dict):
        return None
    try:
        gw_addr = ipaddress.ip_address(gw)
    except ValueError:
        return None

    stub_match = None

    for area_lsas in areas.values():
        if not isinstance(area_lsas, list):
            continue
        for lsa in area_lsas:
            if not isinstance(lsa, dict):
                continue
            advertising_router = lsa.get("advertisingRouter")
            router_links = lsa.get("routerLinks", {})
            if not isinstance(router_links, dict):
                continue
            for link in router_links.values():
                if not isinstance(link, dict):
                    continue
                if link.get("routerInterfaceAddress") == gw:
                    return advertising_router
                if link.get("linkType") != "Stub Network":
                    continue
                network_address = link.get("networkAddress")
                network_mask = link.get("networkMask")
                if network_address is None or network_mask is None:
                    continue
                try:
                    network = ipaddress.ip_network(
                        f"{network_address}/{network_mask}", strict=False
                    )
                except ValueError:
                    continue
                if gw_addr in network:
                    stub_match = advertising_router
    return stub_match


def _router_originates_default(
    router_id: str, external_lsdb: Optional[dict]
) -> bool:
    """Return True iff *router_id* has an AS-external LSA for 0.0.0.0/0."""
    if not isinstance(external_lsdb, dict):
        return False
    lsas = external_lsdb.get("asExternalLinkStates", [])
    if not isinstance(lsas, list):
        return False
    for lsa in lsas:
        if not isinstance(lsa, dict):
            continue
        if (
            lsa.get("linkStateId") == "0.0.0.0"
            and lsa.get("networkMask") == 0
            and lsa.get("advertisingRouter") == router_id
        ):
            return True
    return False


def _resolve_router_nexthop(
    router_id: str, rib: Optional[dict]
) -> Optional[tuple[str, str]]:
    """Return (ip, via_iface) for the first nexthop of *router_id* in
    the OSPF RIB, or None if not present / malformed."""
    if not isinstance(rib, dict):
        return None
    route = rib.get(router_id)
    if not isinstance(route, dict):
        return None
    nexthops = route.get("nexthops", [])
    if not isinstance(nexthops, list) or not nexthops:
        return None
    nh = nexthops[0]
    if not isinstance(nh, dict):
        return None
    ip = nh.get("ip")
    via = nh.get("via")
    if not ip or not via:
        return None
    return (ip, via)


def _resolve_direct_router_nexthop(
    router_id: str, router_lsdb: Optional[dict], rib: Optional[dict]
) -> Optional[tuple[str, str]]:
    if not isinstance(router_lsdb, dict) or not isinstance(rib, dict):
        return None
    areas = router_lsdb.get("routerLinkStates", {}).get("areas", {})
    if not isinstance(areas, dict):
        return None

    router_addresses = []
    for area_lsas in areas.values():
        if not isinstance(area_lsas, list):
            continue
        for lsa in area_lsas:
            if not isinstance(lsa, dict) or lsa.get("advertisingRouter") != router_id:
                continue
            router_links = lsa.get("routerLinks", {})
            if not isinstance(router_links, dict):
                continue
            for link in router_links.values():
                if not isinstance(link, dict):
                    continue
                address = link.get("routerInterfaceAddress")
                if address:
                    router_addresses.append(address)

    best_prefixlen = -1
    best: Optional[tuple[str, str]] = None
    for route_prefix, route in rib.items():
        if route_prefix == "0.0.0.0/0":
            continue  # invariant 1: a default route never represents a direct adjacency
        if not isinstance(route, dict):
            continue
        nexthops = route.get("nexthops", [])
        if not isinstance(nexthops, list) or not nexthops:
            continue
        try:
            network = ipaddress.ip_network(route_prefix, strict=False)
        except ValueError:
            continue
        covering_address = None
        for address in router_addresses:
            try:
                if ipaddress.ip_address(address) in network:
                    covering_address = address
                    break
            except ValueError:
                continue
        if covering_address is None:
            continue
        if network.prefixlen <= best_prefixlen:
            continue  # invariant 2: longest-prefix-match — keep the most specific
        resolved = None
        fallback = None
        for nh in nexthops:
            if not isinstance(nh, dict):
                continue
            via = nh.get("directlyAttachedTo") or nh.get("via")
            if not via:
                continue
            nh_ip = nh.get("ip")
            gw = nh_ip.strip() if isinstance(nh_ip, str) else None
            if gw:
                # invariant 3: first usable nexthop — the OSPF-RIB routable
                # nexthop is the real gateway (this is the field-selection fix).
                resolved = (gw, via)
                break
            if fallback is None and _is_on_backbone(covering_address):
                # directly-attached (blank .ip): on-link gateway route via the
                # backbone interface-address — only if it is a valid on-backbone
                # gateway, never a tunnel-edge P2P IP (Fix 8).
                fallback = (covering_address, via)
        if resolved is not None:
            best, best_prefixlen = resolved, network.prefixlen
        elif fallback is not None:
            best, best_prefixlen = fallback, network.prefixlen
    return best


def _probe_gw_alive(gw: str) -> tuple[bool, Optional[str], Optional[str]]:
    """Return (alive, kernel_nexthop_ip, kernel_nexthop_iface).

    A gw is alive iff:
      1. Some router R in OSPF Router-LSDB declares gw as one of its
         routerInterfaceAddress values (R is OSPF-known and owns gw).
      2. R also originates an AS-external LSA for 0.0.0.0/0.
    The returned nexthop is how OSPF reaches R (from the OSPF RIB,
    indexed by R's router-id).
    """
    router_lsdb = _vtysh("show ip ospf database router json")
    if router_lsdb is None:
        logger.debug("probe gw=%s: no router-LSDB from vty bridge", gw)
        return False, None, None
    router_id = _find_router_owning_address(gw, router_lsdb)
    if not router_id:
        logger.debug("probe gw=%s: no owning router in OSPF router-LSDB", gw)
        return False, None, None

    rib = _vtysh("show ip ospf route json")
    if rib is None:
        logger.debug("probe gw=%s: no OSPF RIB from vty bridge", gw)
        return False, None, None

    direct_nh = _resolve_direct_router_nexthop(router_id, router_lsdb, rib)
    if direct_nh is not None:
        return True, direct_nh[0], direct_nh[1]

    external_lsdb = _vtysh("show ip ospf database external json")
    if external_lsdb is None:
        logger.debug(
            "probe gw=%s: router %s has no direct nexthop and no external-LSDB",
            gw, router_id,
        )
        return False, None, None
    if not _router_originates_default(router_id, external_lsdb):
        logger.debug(
            "probe gw=%s: router %s has no direct nexthop and does not originate default",
            gw, router_id,
        )
        return False, None, None

    nh = _resolve_router_nexthop(router_id, rib)
    if nh is None:
        logger.debug(
            "probe gw=%s: router %s originates default but has no router nexthop in RIB",
            gw, router_id,
        )
        return False, None, None
    return True, nh[0], nh[1]


def _probe_dev_alive(dev: str) -> tuple[bool, Optional[str]]:
    """Check if dev= member's interface is present.

    Returns (alive, gateway_ip_or_none).
    """
    with state.INTERFACES_LOCK:
        alive = dev in state.INTERFACES
    if not alive:
        return False, None
    # No gateway needed for device-only nexthop
    return True, None


def _tick(
    nhg_registry: dict,
    member_nhids: dict,
    member_alive: dict,
    active_member: dict,
    consecutive_failures: dict,
    first_tick: bool,
) -> None:
    """Execute one monitor tick: probe liveness, reconcile nexthop state.

    On first_tick, seeds state dicts without issuing any nexthop.replace_*
    calls. On subsequent ticks, detects changes and replaces member or group
    nexthop objects as needed.

    Args:
        nhg_registry: NhgDescriptor -> group_nhid mapping from Router.
        member_nhids: (gw, dev) tuple -> member_nhid mapping from Router.
        member_alive: (gw, dev) -> current alive bool (mutated in-place).
        active_member: NhgDescriptor -> active (gw, dev) key (mutated in-place).
        consecutive_failures: gw str -> consecutive probe failure count (mutated).
        first_tick: if True, seed state without issuing any replace calls.
    """
    for desc, group_nhid in nhg_registry.items():
        # Probe each member and collect new liveness state
        new_alive: dict[tuple, bool] = {}
        probe_results: dict[tuple, tuple] = {}  # key -> (via, dev) for gw members

        for member in desc.members:
            key = (member.gw, member.dev)

            if member.gw is not None:
                alive, resolved_gw, resolved_dev = _probe_gw_alive(member.gw)
                if not alive:
                    consecutive_failures[member.gw] = (
                        consecutive_failures.get(member.gw, 0) + 1
                    )
                    if consecutive_failures[member.gw] < _GW_FAILURE_THRESHOLD:
                        # Transient failure: preserve last known liveness
                        new_alive[key] = member_alive.get(key, False)
                    else:
                        new_alive[key] = False
                    probe_results[key] = (None, None)
                else:
                    consecutive_failures[member.gw] = 0
                    new_alive[key] = True
                    probe_results[key] = (resolved_gw, resolved_dev)

            elif member.dev is not None:
                alive, _ = _probe_dev_alive(member.dev)
                new_alive[key] = alive
                probe_results[key] = (None, None)

        if first_tick:
            # Seed state and reconcile: at startup the Router seeded nhids
            # using `ip route get`, which can be wrong when OSPF hasn't
            # converged yet. Force replace_* with probe-resolved values so
            # the kernel matches OSPF state from tick 1.
            member_alive.update(new_alive)
            for member in desc.members:
                key = (member.gw, member.dev)
                if not new_alive.get(key, False):
                    continue
                nhid = member_nhids[key]
                if member.gw is not None:
                    resolved_gw, resolved_dev = probe_results[key]
                    nexthop.replace_nexthop(nhid, via=resolved_gw, dev=resolved_dev)
                    logger.info(
                        "first_tick reconcile gw=%s nhid=%d via=%s dev=%s",
                        member.gw,
                        nhid,
                        resolved_gw,
                        resolved_dev,
                    )
                elif member.dev is not None:
                    nexthop.replace_device(nhid, dev=member.dev)
                    logger.info(
                        "first_tick reconcile dev=%s nhid=%d",
                        member.dev,
                        nhid,
                    )
            # Pick highest-priority alive member as active
            chosen_key = None
            for member in desc.members:
                key = (member.gw, member.dev)
                if new_alive.get(key, False):
                    chosen_key = key
                    break
            if chosen_key is None:
                chosen_key = (desc.members[0].gw, desc.members[0].dev)
            active_member[desc] = chosen_key
            chosen_nhid = member_nhids[chosen_key]
            nexthop.replace_group(group_nhid, chosen_nhid)
            logger.info(
                "first_tick group nhid=%d active=%s nhid=%d",
                group_nhid,
                chosen_key,
                chosen_nhid,
            )
            continue

        # Reconcile member nexthop objects where liveness changed
        for member in desc.members:
            key = (member.gw, member.dev)
            was_alive = member_alive.get(key, False)
            is_alive = new_alive.get(key, False)

            if was_alive == is_alive:
                continue  # no change

            if not is_alive:
                # alive -> dead
                nhid = member_nhids[key]
                nexthop.replace_nexthop_blackhole(nhid)
                logger.info(
                    "Member %s transitioned to dead, blackholed nhid=%d", key, nhid
                )
            else:
                # dead -> alive
                nhid = member_nhids[key]
                if member.gw is not None:
                    resolved_gw, resolved_dev = probe_results[key]
                    nexthop.replace_nexthop(nhid, via=resolved_gw, dev=resolved_dev)
                    logger.info(
                        "Member gw=%s recovered, replaced nhid=%d via=%s dev=%s",
                        member.gw,
                        nhid,
                        resolved_gw,
                        resolved_dev,
                    )
                elif member.dev is not None:
                    nexthop.replace_device(nhid, dev=member.dev)
                    logger.info(
                        "Member dev=%s recovered, replaced nhid=%d", member.dev, nhid
                    )

        # Update liveness state
        member_alive.update(new_alive)

        # Find highest-priority alive member
        new_active_key = None
        for member in desc.members:
            key = (member.gw, member.dev)
            if member_alive.get(key, False):
                new_active_key = key
                break

        if new_active_key is None:
            # All dead: keep current active (it's already blackholed)
            new_active_key = active_member.get(desc)

        old_active_key = active_member.get(desc)
        if new_active_key != old_active_key:
            new_active_nhid = member_nhids[new_active_key]
            nexthop.replace_group(group_nhid, new_active_nhid)
            logger.info(
                "NHG group nhid=%d switched active member from %s to %s (nhid=%d)",
                group_nhid,
                old_active_key,
                new_active_key,
                new_active_nhid,
            )
            active_member[desc] = new_active_key


async def monitor_nexthops(
    nhg_registry: dict,
    member_nhids: dict,
    stop_event: asyncio.Event,
) -> None:
    """Monitor nhg member liveness and switch active members on change.

    Args:
        nhg_registry: NhgDescriptor -> group_nhid mapping from Router.
        member_nhids: (gw, dev) tuple -> member_nhid mapping from Router.
        stop_event: signal to stop the monitor loop.
    """
    member_alive: dict[tuple, bool] = {}
    active_member: dict[NhgDescriptor, tuple] = {}
    consecutive_failures: dict[str, int] = {}
    # `first_tick` must survive across ticks; a list cell keeps it mutable
    # inside the tick closure without a `nonlocal` on a reassigned name.
    first_tick = [True]

    async def _do_tick() -> None:
        if not state.ROUTER or not state.ROUTER._routes_loaded.is_set():
            # Routes not yet loaded: skip this tick (loop keeps ticking).
            return
        try:
            await asyncio.to_thread(
                _tick,
                nhg_registry,
                member_nhids,
                member_alive,
                active_member,
                consecutive_failures,
                first_tick[0],
            )
        finally:
            # Advance out of first_tick only once we have actually run a tick
            # against loaded routes, so a real (non-seeding) reconcile fires
            # on the next iteration.
            first_tick[0] = False

    await run_periodic(
        "nexthop monitor",
        _do_tick,
        interval=_TICK_INTERVAL_SECONDS,
        stop_event=stop_event,
        tick_timeout=_TICK_TIMEOUT_SECONDS,
        logger=logger,
    )
