"""Realistic OSPF LSDB / RIB JSON fixtures for nexthop_monitor tests.

These mirror the reference lab hub-and-spoke topology: two egress edges (pt-edge
10.130.30.23 owning gw 10.9.19.2, de-edge 10.130.30.33 owning gw 10.9.21.2),
each reached over a hub↔edge P2P WireGuard tunnel via its HUB-SIDE wg pod
(pt-hub 10.130.30.20, de-hub 10.130.30.30). The hub ipt_server (10.130.30.99)
is the viewer; its DIRECT OSPF neighbors are the hub-side pods, NOT the edges
(the edges are two OSPF hops away).

Topology correctness note (issue #37): earlier revisions of this fixture omitted
the de-hub pod LSA (10.130.30.30) and keyed the EDGE router-ids as direct
neighbors. That mis-modelled hub-and-spoke and is exactly why the rolled-back
1.2.6 direct-neighbor-Full gate passed review while regressing live. The fixture
now carries BOTH the edge LSA and its hub-side pod LSA with a reciprocal P2P
link, matching the capture in ospf_hubspoke.py.
"""
from __future__ import annotations


# router LSDB fragment — only the sections probe code actually reads
ROUTER_LSDB_TWO_OUTERS = {
    "routerId": "10.130.30.99",
    "routerLinkStates": {
        "areas": {
            "0.0.0.0": [
                # pt-edge Router LSA — declares 10.9.19.2 as its p2p link back
                # to the pt-hub pod (10.130.30.20).
                {
                    "linkStateId": "10.130.30.23",
                    "advertisingRouter": "10.130.30.23",
                    "asbr": True,
                    "routerLinks": {
                        "link0": {"linkType": "Stub Network",
                                   "networkAddress": "172.30.0.0",
                                   "networkMask": "255.255.255.0"},
                        "link1": {"linkType": "another Router (point-to-point)",
                                   "neighborRouterId": "10.130.30.20",
                                   "routerInterfaceAddress": "10.9.19.2"},
                        "link2": {"linkType": "Stub Network",
                                   "networkAddress": "10.9.19.0",
                                   "networkMask": "255.255.255.0"},
                    },
                },
                # de-edge Router LSA — declares 10.9.21.2 as its p2p link back
                # to the de-hub pod (10.130.30.30).
                {
                    "linkStateId": "10.130.30.33",
                    "advertisingRouter": "10.130.30.33",
                    "asbr": True,
                    "routerLinks": {
                        "link0": {"linkType": "Stub Network",
                                   "networkAddress": "172.30.0.0",
                                   "networkMask": "255.255.255.0"},
                        "link1": {"linkType": "another Router (point-to-point)",
                                   "neighborRouterId": "10.130.30.30",
                                   "routerInterfaceAddress": "10.9.21.2"},
                        "link2": {"linkType": "Stub Network",
                                   "networkAddress": "10.9.21.0",
                                   "networkMask": "255.255.255.0"},
                    },
                },
                # pt-HUB pod (10.130.30.20) — reciprocal p2p link to pt-edge,
                # hub-side interface 10.9.19.1. A DIRECT neighbor of the hub.
                {
                    "linkStateId": "10.130.30.20",
                    "advertisingRouter": "10.130.30.20",
                    "asbr": False,
                    "routerLinks": {
                        "link0": {"linkType": "another Router (point-to-point)",
                                   "neighborRouterId": "10.130.30.23",
                                   "routerInterfaceAddress": "10.9.19.1"},
                        "link1": {"linkType": "Stub Network",
                                   "networkAddress": "10.9.19.0",
                                   "networkMask": "255.255.255.0"},
                    },
                },
                # de-HUB pod (10.130.30.30) — reciprocal p2p link to de-edge,
                # hub-side interface 10.9.21.1. A DIRECT neighbor of the hub.
                {
                    "linkStateId": "10.130.30.30",
                    "advertisingRouter": "10.130.30.30",
                    "asbr": False,
                    "routerLinks": {
                        "link0": {"linkType": "another Router (point-to-point)",
                                   "neighborRouterId": "10.130.30.33",
                                   "routerInterfaceAddress": "10.9.21.1"},
                        "link1": {"linkType": "Stub Network",
                                   "networkAddress": "10.9.21.0",
                                   "networkMask": "255.255.255.0"},
                    },
                },
            ],
        },
    },
}


# external LSDB — both outers originate default
EXTERNAL_LSDB_BOTH_DEFAULTS = {
    "routerId": "10.130.30.99",
    "asExternalLinkStates": [
        {"linkStateId": "0.0.0.0", "networkMask": 0,
         "advertisingRouter": "10.130.30.23"},
        {"linkStateId": "0.0.0.0", "networkMask": 0,
         "advertisingRouter": "10.130.30.33"},
        # Unrelated external — firezone advertising its subnet
        {"linkStateId": "10.9.20.0", "networkMask": 24,
         "advertisingRouter": "10.9.20.2"},
    ],
}


# external LSDB — only outer_pt originates default (outer_de down or
# default_originate disabled)
EXTERNAL_LSDB_ONLY_PT = {
    "routerId": "10.130.30.99",
    "asExternalLinkStates": [
        {"linkStateId": "0.0.0.0", "networkMask": 0,
         "advertisingRouter": "10.130.30.23"},
    ],
}


EXTERNAL_LSDB_NONE = {
    "routerId": "10.130.30.99",
    "asExternalLinkStates": [],
}


# OSPF neighbor state (show ip ospf neighbor json). Keyed by DIRECT-neighbor
# router-id. In hub-and-spoke the hub ipt_server's direct neighbors are the
# HUB-SIDE wg pods (pt-hub 10.130.30.20, de-hub 10.130.30.30), never the
# far-side edges — those are two OSPF hops away. Both hub-side pods Full here =
# the healthy steady state.
NEIGHBORS_BOTH_FULL = {
    "neighbors": {
        "10.130.30.20": [  # pt-hub pod (direct neighbor)
            {"state": "Full/DROther", "address": "172.30.0.3", "ifaceName": "backbone"},
        ],
        "10.130.30.30": [  # de-hub pod (direct neighbor)
            {"state": "Full/-", "address": "172.30.0.4", "ifaceName": "backbone"},
        ],
    },
}


# de-edge DEAD: the hub-side de-hub POD (10.130.30.30) remains a Full direct
# neighbor (its backbone adjacency is unaffected — it is the EDGE behind it, on
# the P2P tunnel, that died two hops away). Edge death is modelled in the LSDB
# (ROUTER_LSDB_DE_DEAD withdraws the de-hub↔de-edge reciprocal P2P link), NOT in
# this direct-neighbor table. pt-hub stays Full so pt is unaffected.
NEIGHBORS_DE_DOWN = {
    "neighbors": {
        "10.130.30.20": [
            {"state": "Full/DROther", "address": "172.30.0.3", "ifaceName": "backbone"},
        ],
        "10.130.30.30": [
            {"state": "Full/-", "address": "172.30.0.4", "ifaceName": "backbone"},
        ],
    },
}


# Same as NEIGHBORS_DE_DOWN — kept as a distinct name for the "Dead Interval
# expired, edge LSA fully gone" scenario (which is an LSDB condition, see
# ROUTER_LSDB_DE_DEAD). The hub-side direct neighbors are identical.
NEIGHBORS_DE_ABSENT = NEIGHBORS_DE_DOWN


# de-edge DEAD LSDB: the de-hub pod (10.130.30.30) reoriginated its Router-LSA
# DROPPING its P2P link to the de-edge (10.130.30.33), and the de-edge's own
# Router-LSA aged out of the LSDB entirely. The 10.9.21.0/24 stub REMAINS
# (advertised by the still-up hub-side wg interface) — the issue #37 trap. pt is
# untouched.
ROUTER_LSDB_DE_DEAD = {
    "routerId": "10.130.30.99",
    "routerLinkStates": {
        "areas": {
            "0.0.0.0": [
                # pt-edge — unchanged
                {
                    "linkStateId": "10.130.30.23",
                    "advertisingRouter": "10.130.30.23",
                    "asbr": True,
                    "routerLinks": {
                        "link0": {"linkType": "Stub Network",
                                   "networkAddress": "172.30.0.0",
                                   "networkMask": "255.255.255.0"},
                        "link1": {"linkType": "another Router (point-to-point)",
                                   "neighborRouterId": "10.130.30.20",
                                   "routerInterfaceAddress": "10.9.19.2"},
                        "link2": {"linkType": "Stub Network",
                                   "networkAddress": "10.9.19.0",
                                   "networkMask": "255.255.255.0"},
                    },
                },
                # pt-hub — unchanged
                {
                    "linkStateId": "10.130.30.20",
                    "advertisingRouter": "10.130.30.20",
                    "asbr": False,
                    "routerLinks": {
                        "link0": {"linkType": "another Router (point-to-point)",
                                   "neighborRouterId": "10.130.30.23",
                                   "routerInterfaceAddress": "10.9.19.1"},
                        "link1": {"linkType": "Stub Network",
                                   "networkAddress": "10.9.19.0",
                                   "networkMask": "255.255.255.0"},
                    },
                },
                # de-hub pod — REORIGINATED: P2P link to de-edge WITHDRAWN; the
                # 10.9.21.0/24 stub REMAINS (wg interface still up).
                {
                    "linkStateId": "10.130.30.30",
                    "advertisingRouter": "10.130.30.30",
                    "asbr": False,
                    "routerLinks": {
                        "link0": {"linkType": "Stub Network",
                                   "networkAddress": "10.9.21.0",
                                   "networkMask": "255.255.255.0"},
                    },
                },
                # de-edge (10.130.30.33) LSA aged out — absent.
            ],
        },
    },
}


# OSPF RIB (show ip ospf route)
RIB_TWO_OUTERS = {
    "10.9.19.0/24": {"routeType": "N", "cost": 20, "area": "0.0.0.0",
                     "nexthops": [{"ip": "172.30.0.3", "via": "backbone",
                                    "advertisedRouter": "10.130.30.20"}]},
    "10.9.21.0/24": {"routeType": "N", "cost": 20, "area": "0.0.0.0",
                     "nexthops": [{"ip": "172.30.0.4", "via": "backbone",
                                    "advertisedRouter": "10.130.30.30"}]},
    "10.130.30.23": {"routeType": "R ", "cost": 20, "area": "0.0.0.0",
                     "routerType": "asbr",
                     "nexthops": [{"ip": "172.30.0.3", "via": "backbone"}]},
    "10.130.30.33": {"routeType": "R ", "cost": 20, "area": "0.0.0.0",
                     "routerType": "asbr",
                     "nexthops": [{"ip": "172.30.0.4", "via": "backbone"}]},
    "0.0.0.0/0": {"routeType": "N E2", "cost": 20,
                  "nexthops": [
                      {"ip": "172.30.0.3", "via": "backbone",
                       "advertisedRouter": "10.130.30.23"},
                      {"ip": "172.30.0.4", "via": "backbone",
                       "advertisedRouter": "10.130.30.33"},
                  ]},
}


# RIB with outer_pt gone (Dead Interval expired)
RIB_PT_GONE = {
    "10.9.21.0/24": {"routeType": "N", "cost": 20, "area": "0.0.0.0",
                     "nexthops": [{"ip": "172.30.0.4", "via": "backbone",
                                    "advertisedRouter": "10.130.30.30"}]},
    "10.130.30.33": {"routeType": "R ", "cost": 20, "area": "0.0.0.0",
                     "routerType": "asbr",
                     "nexthops": [{"ip": "172.30.0.4", "via": "backbone"}]},
    "0.0.0.0/0": {"routeType": "N E2", "cost": 20,
                  "nexthops": [
                      {"ip": "172.30.0.4", "via": "backbone",
                       "advertisedRouter": "10.130.30.33"},
                  ]},
}
