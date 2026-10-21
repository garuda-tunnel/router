"""Nexthop group monitor: OSPF-aware single-active-member selection.

Queries the FRR sidecar's vty HTTP bridge (127.0.0.1:7890) for OSPF state
(LSDB, RIB, and neighbor adjacency state) and polls interface presence for
dev= members. On liveness change, switches the nhg group's single active
member via ip nexthop replace.
"""

import asyncio
import ipaddress
import json
import logging
import socket
import urllib.error
import urllib.request
from typing import Optional

from pyroute2 import IPRoute

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

# The OSPF backbone transit subnets, derived at runtime from the IPv4
# addresses on the pod's PBR interfaces (state.CONFIG.interfaces, i.e. the
# IPT_INTERFACES_JSON entry; default ["backbone"]). Consumers configure this
# subnet freely (mini-site backbone_subnet; stands may run 192.0.2.0/26 or
# 172.30.0.0/24), so it must not be hardcoded. Refreshed once per monitor
# tick by _refresh_backbone_subnets(); empty (interface missing or without an
# address) makes _is_on_backbone fail closed, same as for off-subnet
# addresses. The directly-attached fallback in _resolve_direct_router_nexthop
# may only return an address on these subnets as a kernel gateway (never a
# tunnel-edge WireGuard P2P IP, which the kernel rejects with "Nexthop has
# invalid gateway"). See spec §6.1 (Fix 8) and #65.
_BACKBONE_SUBNETS: tuple[ipaddress.IPv4Network, ...] = ()
# True while the current empty stretch of _BACKBONE_SUBNETS has already been
# WARNING-logged (the warning fires once per transition, not every tick).
_LOGGED_EMPTY_BACKBONE = False


def _pbr_interface_names() -> tuple[str, ...]:
    """Return the configured PBR interface names (default: ("backbone",))."""
    interfaces = getattr(state.CONFIG, "interfaces", None)
    return tuple(interfaces) if interfaces else ("backbone",)


def _backbone_addr_prefixes() -> list[tuple[str, int]]:
    """Read (address, prefixlen) for every IPv4 address on the PBR interfaces."""
    prefixes: list[tuple[str, int]] = []
    with IPRoute() as ipr:
        for name in _pbr_interface_names():
            for index in ipr.link_lookup(ifname=name):
                for addr in ipr.get_addr(index=index, family=socket.AF_INET):
                    local = addr.get_attr("IFA_LOCAL") or addr.get_attr("IFA_ADDRESS")
                    if local:
                        prefixes.append((local, addr["prefixlen"]))
    return prefixes


def _refresh_backbone_subnets() -> None:
    """Recompute _BACKBONE_SUBNETS from the PBR interfaces' IPv4 addresses.

    Re-run at the start of every monitor tick (the interface may appear after
    process start; the lookup is cheap). Any failure or empty result leaves
    the subnets empty, so _is_on_backbone fails closed. Visibility (#65): a
    transition to empty logs one WARNING (a silent empty set is exactly how
    the blackhole went unnoticed), and a subnet change logs one INFO listing
    the new subnets; neither repeats while the state is unchanged.
    """
    global _BACKBONE_SUBNETS, _LOGGED_EMPTY_BACKBONE
    try:
        subnets = []
        for local, prefixlen in _backbone_addr_prefixes():
            try:
                subnets.append(ipaddress.ip_interface(f"{local}/{prefixlen}").network)
            except ValueError:
                logger.debug("ignoring malformed address %s/%s", local, prefixlen)
        new_subnets = tuple(subnets)
    except Exception as exc:
        logger.debug("backbone subnet derivation failed: %s", exc)
        new_subnets = ()

    if not new_subnets:
        if not _LOGGED_EMPTY_BACKBONE:
            logger.warning(
                "no backbone subnet found on PBR interfaces %s — on-backbone "
                "neighbours will not resolve until an IPv4 address appears "
                "(#65)",
                ", ".join(_pbr_interface_names()),
            )
            _LOGGED_EMPTY_BACKBONE = True
    else:
        _LOGGED_EMPTY_BACKBONE = False
        if new_subnets != _BACKBONE_SUBNETS:
            logger.info(
                "backbone subnets now: %s",
                ", ".join(str(net) for net in new_subnets),
            )
    _BACKBONE_SUBNETS = new_subnets


def _is_on_backbone(address: str) -> bool:
    """Return True iff *address* is on a backbone interface subnet.

    Used to guard the directly-attached fallback so it can only ever return a
    valid on-backbone gateway, never a tunnel-edge P2P IP.
    """
    try:
        addr = ipaddress.ip_address(address)
    except ValueError:
        return False
    return any(addr in subnet for subnet in _BACKBONE_SUBNETS)


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


def _router_neighbor_state_is(
    router_id: str, neighbor_data: Optional[dict], state_prefix: str
) -> bool:
    """Return True iff *router_id* is a DIRECT OSPF neighbor whose adjacency
    state starts with *state_prefix* (e.g. ``"Full"``, ``"2-Way"``; FRR
    reports role-suffixed states like ``"Full/DR"``, ``"2-Way/DROther"``).

    Reads the parsed JSON of ``show ip ospf neighbor json`` (same shape used
    by ``route_health.FrrVtyshOspfHealthSource``): a top-level ``neighbors``
    dict keyed by neighbor router-id, whose values are lists of entries each
    carrying an OSPF adjacency ``state`` / ``nbrState``.
    """
    if not isinstance(neighbor_data, dict):
        return False
    neighbors = neighbor_data.get("neighbors")
    if not isinstance(neighbors, dict):
        return False
    entries = neighbors.get(router_id)
    if not isinstance(entries, list):
        return False
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        state = entry.get("nbrState") or entry.get("state")
        if isinstance(state, str) and state.startswith(state_prefix):
            return True
    return False


def _router_neighbor_is_full(
    router_id: str, neighbor_data: Optional[dict]
) -> bool:
    """Return True iff *router_id* is a DIRECT OSPF neighbor in Full state.

    This is only meaningful for a router that is ONE OSPF hop away (a direct
    neighbor on the backbone) — e.g. border or a hub-side wg pod — and is the
    gate for DIRECT gateway liveness, which keeps requiring Full. It is NOT a
    valid liveness signal for a hub↔edge P2P WireGuard tunnel: the gw there is
    owned by the FAR/edge router-id, which is TWO OSPF hops from the hub
    ipt-server and is never a direct neighbor, so this returns always-False for
    it (issue #37 regression). Edge liveness uses
    ``_edge_p2p_adjacency_healthy`` instead.
    """
    return _router_neighbor_state_is(router_id, neighbor_data, "Full")


def _find_edge_owning_p2p_address(
    gw: str, router_lsdb: Optional[dict]
) -> Optional[tuple[str, str]]:
    """If *gw* is a hub↔edge P2P tunnel address, return (edge_router_id,
    hub_side_router_id); otherwise None.

    A gw qualifies iff some Router-LSA advertises it as the
    ``routerInterfaceAddress`` of a point-to-point ("another Router") link.
    That advertising router is the EDGE that owns the gw (the far side of the
    hub↔edge WireGuard tunnel); the link's ``neighborRouterId`` is the hub-side
    router (the wg pod) on the near side of the same P2P link.

    Only P2P links are considered — a Stub-Network / Transit ownership (e.g.
    border's own backbone interface address) is NOT an edge tunnel and returns
    None so the caller falls back to the directly-attached / direct-neighbor
    path.
    """
    if not isinstance(router_lsdb, dict):
        return None
    areas = router_lsdb.get("routerLinkStates", {}).get("areas", {})
    if not isinstance(areas, dict):
        return None
    for area_lsas in areas.values():
        if not isinstance(area_lsas, list):
            continue
        for lsa in area_lsas:
            if not isinstance(lsa, dict):
                continue
            edge_router_id = lsa.get("advertisingRouter")
            router_links = lsa.get("routerLinks", {})
            if not isinstance(router_links, dict):
                continue
            for link in router_links.values():
                if not isinstance(link, dict):
                    continue
                if not str(link.get("linkType", "")).startswith("another Router"):
                    continue
                if link.get("routerInterfaceAddress") != gw:
                    continue
                hub_side_router_id = link.get("neighborRouterId")
                if edge_router_id and hub_side_router_id:
                    return (edge_router_id, hub_side_router_id)
    return None


def _lsa_has_p2p_link_to(
    advertising_router: str, neighbor_router_id: str, router_lsdb: Optional[dict]
) -> bool:
    """Return True iff *advertising_router*'s Router-LSA declares a
    point-to-point link whose ``neighborRouterId`` is *neighbor_router_id*.

    Used to verify the reciprocal (hub-side → edge) leg of a hub↔edge P2P
    adjacency. When the edge dies, the hub-side pod reoriginates its LSA
    withdrawing this link (verified live: ~15s P2P dead interval on vpn2), so
    this flips to False even though the edge's on-link /24 stub route lingers.
    """
    if not isinstance(router_lsdb, dict):
        return False
    areas = router_lsdb.get("routerLinkStates", {}).get("areas", {})
    if not isinstance(areas, dict):
        return False
    for area_lsas in areas.values():
        if not isinstance(area_lsas, list):
            continue
        for lsa in area_lsas:
            if not isinstance(lsa, dict):
                continue
            if lsa.get("advertisingRouter") != advertising_router:
                continue
            router_links = lsa.get("routerLinks", {})
            if not isinstance(router_links, dict):
                continue
            for link in router_links.values():
                if not isinstance(link, dict):
                    continue
                if not str(link.get("linkType", "")).startswith("another Router"):
                    continue
                if link.get("neighborRouterId") == neighbor_router_id:
                    return True
    return False


def _edge_p2p_adjacency_healthy(
    gw: str, router_lsdb: Optional[dict], neighbor_data: Optional[dict]
) -> Optional[bool]:
    """Topology-correct edge-liveness signal for hub-and-spoke (issue #37).

    Returns:
      * ``None``  — *gw* is on the OSPF backbone (border / directly-attached
        transit gw, one hop away); the caller should fall back to the
        directly-attached + direct-neighbor path.
      * ``True``  — *gw* is an off-backbone (edge tunnel) address whose owning
        EDGE router-id holds a BIDIRECTIONAL P2P adjacency with a hub-side
        router that is itself an ADJACENT direct OSPF neighbor of the hub
        ipt-server (Full, or the stable DROther↔DROther 2-Way state on a
        broadcast backbone segment — accepted only together with the
        reciprocal P2P LSAs below). The edge is alive.
      * ``False`` — *gw* is an off-backbone (edge tunnel) address but it is
        NOT a live edge's P2P interface-address (the edge's LSA aged out and
        the hub-side pod withdrew its reciprocal P2P link), or the reciprocal
        adjacency / hub-side-Full condition fails. The edge is dead — even if
        its /24 is still on-link via the still-up hub-side wg interface.

    Discriminator: an edge tunnel gw is a CGNAT 10.x P2P WireGuard address,
    never on the backbone interface subnets. Backbone gws (border/transit)
    return None and take the directly-attached path; off-backbone gws are
    always edge-gated (True/False), so a dead edge whose gw resolves ONLY via
    the hub-side pod's lingering /24 stub is correctly judged DEAD (issue #37),
    never leaking into the directly-attached path.

    Why this is correct where the rolled-back direct-neighbor-Full gate failed:
    the edge owning the gw is TWO OSPF hops from the hub and is never a direct
    neighbor, so ``_router_neighbor_is_full(edge)`` is structurally always
    False. Here we instead check the hub-side leg (a real adjacent direct
    neighbor, one hop: Full, or stable 2-Way between DROthers on a broadcast
    segment) AND the reciprocal LSDB P2P link that only survives while the edge
    is actually up. See
    docs/artifacts/2026-07-04-nexthop-monitor-edge-liveness-signal-analysis.md.
    """
    if _is_on_backbone(gw):
        # Backbone gw (border / transit): not an edge tunnel — directly-attached.
        return None
    owner = _find_edge_owning_p2p_address(gw, router_lsdb)
    if owner is None:
        # Off-backbone gw that is NOT any router's live P2P interface-address:
        # the edge's LSA has aged out and the hub-side pod withdrew its P2P
        # link. Only the lingering hub-side /24 stub remains (issue #37 trap).
        # This is a DEAD edge, not a directly-attached gw.
        return False
    edge_router_id, hub_side_router_id = owner
    # (1) the hub-side leg must be an ADJACENT direct OSPF neighbor of this
    #     ipt-server: Full, OR the stable 2-Way state two DROther routers
    #     legitimately hold forever on a broadcast backbone segment when the
    #     DR/BDR election leaves both this hub and the hub-side pod DROther.
    #     2-Way alone proves only hellos, so it is accepted solely because
    #     (2) below still requires BOTH reciprocal P2P LSAs, which prove
    #     bidirectional reachability. Init/ExStart and friends never qualify.
    hub_side_adjacent = _router_neighbor_state_is(
        hub_side_router_id, neighbor_data, "Full"
    ) or _router_neighbor_state_is(hub_side_router_id, neighbor_data, "2-Way")
    if not hub_side_adjacent:
        return False
    # (2) the hub-side pod must still advertise the reciprocal P2P link to the
    #     edge (withdrawn on edge death), AND the edge must still advertise the
    #     P2P link back (its own LSA present) — i.e. the OSPF two-way condition.
    hub_to_edge = _lsa_has_p2p_link_to(
        hub_side_router_id, edge_router_id, router_lsdb
    )
    edge_to_hub = _lsa_has_p2p_link_to(
        edge_router_id, hub_side_router_id, router_lsdb
    )
    return bool(hub_to_edge and edge_to_hub)


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
      1. Some router R in OSPF Router-LSDB declares gw as an interface address
         or in a stub network (R is OSPF-known and owns gw).
      2. The topology-correct liveness gate holds (issue #37):
         * If owning router R is a Full DIRECT OSPF neighbor of this ipt-server
           and gw is a direct-neighbor class (on-backbone address, or a direct
           router-id /32 stub such as border gw=10.130.30.50 owned by
           10.130.30.50), the gw is alive at the adjacency layer. Do this check
           FIRST: border is off-backbone and has no hub↔edge P2P owner, so
           forcing it through the P2P path would prune it at steady state (1.2.7
           regression). Do NOT let a lingering edge /24 stub qualify here.
         * Otherwise, R is not directly adjacent. The only supported non-direct
           geo gw class is a hub↔edge P2P tunnel address: the owning EDGE
           router-id must hold a BIDIRECTIONAL P2P adjacency with a hub-side
           router that is itself an ADJACENT direct OSPF neighbor of this
           ipt-server — Full, or the stable DROther↔DROther 2-Way state on a
           broadcast backbone segment
           (``_edge_p2p_adjacency_healthy``). A dead edge keeps its /24 on-link
           via the still-up hub-side WireGuard interface, so RIB resolution
           alone false-alives; but the hub-side pod withdraws its reciprocal
           P2P link (and the edge's LSA ages out) when the edge dies. The edge
           is TWO OSPF hops away and is never a direct neighbor, so a
           direct-neighbor-Full check on the edge owner would be always-False
           and permanently prune it (the rolled-back 1.2.6 regression).
      3. Either R has a directly-resolvable OSPF-RIB nexthop, or R
         originates an AS-external LSA for 0.0.0.0/0 and is RIB-resolvable.
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

    # Topology-correct liveness gate (issue #37): classify by the gw owner.
    # Direct-neighbor classes (backbone, or direct stub-router/border where the
    # gw equals the owning router-id) are alive iff the owner is a Full direct
    # neighbor. Other non-direct owners are treated as 2-hop P2P edges and must
    # pass the reciprocal P2P-adjacency check. The gw==router_id guard is what
    # lets border gw=10.130.30.50 through without letting a dead edge's lingering
    # 10.9.x/24 stub be mistaken for a direct owner via the hub-side pod.
    neighbor_data = _vtysh("show ip ospf neighbor json")
    if neighbor_data is None:
        logger.debug("probe gw=%s: no OSPF neighbor state from vty bridge", gw)
        return False, None, None
    direct_owner_alive = _router_neighbor_is_full(router_id, neighbor_data) and (
        _is_on_backbone(gw) or gw == router_id
    )
    if not direct_owner_alive:
        edge_healthy = _edge_p2p_adjacency_healthy(gw, router_lsdb, neighbor_data)
        if edge_healthy is not True:
            logger.debug(
                "probe gw=%s: owner %s is not an adjacent direct OSPF neighbor "
                "and has no healthy bidirectional P2P adjacency to a "
                "hub-side neighbor", gw, router_id,
            )
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
    # The backbone interface (and its address) may appear after process
    # start, so re-derive the backbone subnets on every tick (#65).
    _refresh_backbone_subnets()

    for desc, group_nhid in nhg_registry.items():
        # Probe each member and collect new liveness state
        new_alive: dict[tuple, bool] = {}
        probe_results: dict[tuple, tuple] = {}  # key -> (via, dev) for gw members

        # Observability (vpn2 STILL-BLACKHOLE, 2026-07-02): name the full member
        # set of this group once per tick so the reached set is unambiguous in
        # the pod logs. On the live run there was no per-member line for de/pt,
        # so the operator could not confirm whether the iteration reached them.
        logger.info(
            "nexthop tick group nhid=%d members=%s",
            group_nhid,
            [(m.gw, m.dev) for m in desc.members],
        )

        for member in desc.members:
            key = (member.gw, member.dev)

            # Non-throttled per-member iteration marker: proves the loop reached
            # this member on this tick (the vpn2 blackhole left de/pt with NO
            # per-member line at all). One line per member per tick.
            logger.info(
                "nexthop tick reached member gw=%s dev=%s (group nhid=%d)",
                member.gw,
                member.dev,
                group_nhid,
            )

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
