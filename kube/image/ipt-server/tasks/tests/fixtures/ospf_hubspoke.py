"""Hub-and-spoke OSPF fixtures modelling the reference lab topology (issue #37).

Originally captured read-only from the live hub ipt-server frr-sidecar
(ALIVE state) via the FRR vty bridge, then sanitized to generic labels:

    vtysh -c 'show ip ospf neighbor json'          -> HS_NEIGHBORS_ALIVE
    vtysh -c 'show ip ospf route json'             -> HS_RIB_ALIVE
    vtysh -c 'show ip ospf database router json'   -> HS_ROUTER_LSDB_ALIVE

Only RFC1918 172.30.0.0/24 backbone + CGNAT 10.x addresses, per
router-internal/AGENTS.md.  Full analysis:
The original capture analysis lives in the workspace docs artifacts.

WHY A NEW FIXTURE MODULE
------------------------
The pre-existing `ospf_lsdb_direct.py` fixtures modelled the gw-owning EDGE
router-id as a DIRECT OSPF neighbor of the hub (NEIGHBORS_BOTH_FULL keys the
edge router-ids 10.130.30.23 / 10.130.30.33 directly). That is FALSE for
hub-and-spoke: the hub ipt-server never neighbors the far-side edge; it
neighbors the HUB-SIDE wg pod, which in turn holds a P2P adjacency to the edge.
Modelling the edge as a direct neighbor is exactly why the rolled-back 1.2.6
gate (direct-neighbor-Full on the edge owner) passed review yet regressed live.

TOPOLOGY (hub ipt-server router-id 10.130.30.99)
------------------------------------------------
Geo NHG members: pt gw=10.9.19.2, de gw=10.9.21.2, border gw=10.130.30.50.

    hub ipt-server (.99)
      |  DIRECT Full OSPF neighbors on the backbone (all one hop):
      |    .20 pt-hub pod,  .30 de-hub pod,  .21 firezone,
      |    .22 routeros,    .50 border
      |
      +-- .20 pt-hub pod --P2P(10.9.19.1 <-> 10.9.19.2)--> .23 pt-EDGE  (gw 10.9.19.2)
      +-- .30 de-hub pod --P2P(10.9.21.1 <-> 10.9.21.2)--> .33 de-EDGE  (gw 10.9.21.2)
      +-- .50 border (direct Full neighbor advertising stub 10.130.30.50/32)

The EDGE router-ids .23/.33 are:
  * NOT direct neighbors of the hub (2 OSPF hops away);
  * NOT present as R-routes in `show ip ospf route` even when ALIVE (they are
    plain internal routers, not ASBRs, so SPF emits no R-route for them);
  * reachable only via the hub-side pod's P2P adjacency.

The correct liveness signal is the BIDIRECTIONAL P2P adjacency between the edge
and its hub-side pod (which is itself a Full direct neighbor). See the analysis
doc for the candidate evaluation.
"""
from __future__ import annotations


# ---------------------------------------------------------------------------
# ALIVE state — both edges up (verbatim live capture, trimmed to the fields the
# probe code reads; extra live fields like lsaAge/checksum omitted for brevity
# but their presence/absence does not affect parsing).
# ---------------------------------------------------------------------------

# `show ip ospf database router json`
HS_ROUTER_LSDB_ALIVE = {
    "routerId": "10.130.30.99",
    "routerLinkStates": {
        "areas": {
            "0.0.0.0": [
                # pt-hub pod .20 — P2P link1 to pt-edge .23 (hub side 10.9.19.1)
                {
                    "lsaType": "router-LSA",
                    "linkStateId": "10.130.30.20",
                    "advertisingRouter": "10.130.30.20",
                    "routerLinks": {
                        "link0": {
                            "linkType": "a Transit Network",
                            "designatedRouterAddress": "172.30.0.20",
                            "routerInterfaceAddress": "172.30.0.17",
                        },
                        "link1": {
                            "linkType": "another Router (point-to-point)",
                            "neighborRouterId": "10.130.30.23",
                            "routerInterfaceAddress": "10.9.19.1",
                        },
                        "link2": {
                            "linkType": "Stub Network",
                            "networkAddress": "10.9.19.0",
                            "networkMask": "255.255.255.0",
                        },
                    },
                },
                # firezone .21 (ASBR) — no edge relevance
                {
                    "asbr": True,
                    "lsaType": "router-LSA",
                    "linkStateId": "10.130.30.21",
                    "advertisingRouter": "10.130.30.21",
                    "routerLinks": {
                        "link0": {
                            "linkType": "a Transit Network",
                            "designatedRouterAddress": "172.30.0.20",
                            "routerInterfaceAddress": "172.30.0.16",
                        },
                        "link1": {
                            "linkType": "Stub Network",
                            "networkAddress": "10.9.20.0",
                            "networkMask": "255.255.255.0",
                        },
                    },
                },
                # routeros .22 (ASBR)
                {
                    "asbr": True,
                    "lsaType": "router-LSA",
                    "linkStateId": "10.130.30.22",
                    "advertisingRouter": "10.130.30.22",
                    "routerLinks": {
                        "link0": {
                            "linkType": "a Transit Network",
                            "designatedRouterAddress": "172.30.0.20",
                            "routerInterfaceAddress": "172.30.0.19",
                        },
                    },
                },
                # pt-EDGE .23 — its OWN P2P link0 back to pt-hub .20; owns gw 10.9.19.2
                {
                    "lsaType": "router-LSA",
                    "linkStateId": "10.130.30.23",
                    "advertisingRouter": "10.130.30.23",
                    "routerLinks": {
                        "link0": {
                            "linkType": "another Router (point-to-point)",
                            "neighborRouterId": "10.130.30.20",
                            "routerInterfaceAddress": "10.9.19.2",
                        },
                        "link1": {
                            "linkType": "Stub Network",
                            "networkAddress": "10.9.19.0",
                            "networkMask": "255.255.255.0",
                        },
                    },
                },
                # de-hub pod .30 — P2P link1 to de-edge .33 (hub side 10.9.21.1)
                {
                    "lsaType": "router-LSA",
                    "linkStateId": "10.130.30.30",
                    "advertisingRouter": "10.130.30.30",
                    "routerLinks": {
                        "link0": {
                            "linkType": "a Transit Network",
                            "designatedRouterAddress": "172.30.0.20",
                            "routerInterfaceAddress": "172.30.0.18",
                        },
                        "link1": {
                            "linkType": "another Router (point-to-point)",
                            "neighborRouterId": "10.130.30.33",
                            "routerInterfaceAddress": "10.9.21.1",
                        },
                        "link2": {
                            "linkType": "Stub Network",
                            "networkAddress": "10.9.21.0",
                            "networkMask": "255.255.255.0",
                        },
                    },
                },
                # de-EDGE .33 — its OWN P2P link0 back to de-hub .30; owns gw 10.9.21.2
                {
                    "lsaType": "router-LSA",
                    "linkStateId": "10.130.30.33",
                    "advertisingRouter": "10.130.30.33",
                    "routerLinks": {
                        "link0": {
                            "linkType": "another Router (point-to-point)",
                            "neighborRouterId": "10.130.30.30",
                            "routerInterfaceAddress": "10.9.21.2",
                        },
                        "link1": {
                            "linkType": "Stub Network",
                            "networkAddress": "10.9.21.0",
                            "networkMask": "255.255.255.0",
                        },
                    },
                },
                # border .50 — direct neighbor advertising stub 10.130.30.50/32
                {
                    "lsaType": "router-LSA",
                    "linkStateId": "10.130.30.50",
                    "advertisingRouter": "10.130.30.50",
                    "routerLinks": {
                        "link0": {
                            "linkType": "a Transit Network",
                            "designatedRouterAddress": "172.30.0.20",
                            "routerInterfaceAddress": "172.30.0.15",
                        },
                        "link1": {
                            "linkType": "Stub Network",
                            "networkAddress": "10.130.30.50",
                            "networkMask": "255.255.255.255",
                        },
                    },
                },
                # hub ipt-server .99 itself (ASBR/viewer)
                {
                    "asbr": True,
                    "lsaType": "router-LSA",
                    "linkStateId": "10.130.30.99",
                    "advertisingRouter": "10.130.30.99",
                    "routerLinks": {
                        "link0": {
                            "linkType": "a Transit Network",
                            "designatedRouterAddress": "172.30.0.20",
                            "routerInterfaceAddress": "172.30.0.20",
                        },
                    },
                },
            ],
        },
    },
}


# `show ip ospf neighbor json` — the hub's DIRECT neighbors, all Full. NOTE the
# edge router-ids 10.130.30.23 / 10.130.30.33 are ABSENT: the hub is not a
# direct neighbor of the far-side edges (this is the whole point).
HS_NEIGHBORS_ALIVE = {
    "neighbors": {
        "10.130.30.50": [  # border
            {"nbrState": "Full/Backup", "ifaceName": "backbone", "address": "172.30.0.15"},
        ],
        "10.130.30.21": [  # firezone
            {"nbrState": "Full/DROther", "ifaceName": "backbone", "address": "172.30.0.16"},
        ],
        "10.130.30.20": [  # pt-hub pod
            {"nbrState": "Full/DROther", "ifaceName": "backbone", "address": "172.30.0.17"},
        ],
        "10.130.30.30": [  # de-hub pod
            {"nbrState": "Full/DROther", "ifaceName": "backbone", "address": "172.30.0.18"},
        ],
        "10.130.30.22": [  # routeros
            {"nbrState": "Full/DROther", "ifaceName": "backbone", "address": "172.30.0.19"},
        ],
    },
}


# `show ip ospf route json` — ALIVE. The gw /24s are advertised by the HUB-SIDE
# pods (.20/.30), NOT by the edges; and the edge router-ids have NO R-route.
HS_RIB_ALIVE = {
    "10.9.19.0/24": {
        "routeType": "N", "transit": False, "cost": 20, "area": "0.0.0.0",
        "nexthops": [{"ip": "172.30.0.17", "via": "backbone",
                      "advertisedRouter": "10.130.30.20"}],
    },
    "10.9.20.0/24": {
        "routeType": "N", "transit": False, "cost": 20, "area": "0.0.0.0",
        "nexthops": [{"ip": "172.30.0.16", "via": "backbone",
                      "advertisedRouter": "10.130.30.21"}],
    },
    "10.9.21.0/24": {
        "routeType": "N", "transit": False, "cost": 20, "area": "0.0.0.0",
        "nexthops": [{"ip": "172.30.0.18", "via": "backbone",
                      "advertisedRouter": "10.130.30.30"}],
    },
    "10.130.30.50/32": {
        "routeType": "N", "transit": False, "cost": 20, "area": "0.0.0.0",
        "nexthops": [{"ip": "172.30.0.15", "via": "backbone",
                      "advertisedRouter": "10.130.30.50"}],
    },
    "172.30.0.0/24": {
        "routeType": "N", "transit": True, "cost": 10, "area": "0.0.0.0",
        "nexthops": [{"ip": " ", "directlyAttachedTo": "backbone"}],
    },
    # R-routes exist ONLY for the ASBRs, never for the edges .23/.33.
    "10.130.30.21": {
        "routeType": "R ", "cost": 10, "area": "0.0.0.0", "routerType": "asbr",
        "nexthops": [{"ip": "172.30.0.16", "via": "backbone"}],
    },
    "10.130.30.22": {
        "routeType": "R ", "cost": 10, "area": "0.0.0.0", "routerType": "asbr",
        "nexthops": [{"ip": "172.30.0.19", "via": "backbone"}],
    },
}


# ---------------------------------------------------------------------------
# DEAD state — de-edge (.33) is down. Reasoned from OSPF semantics + the live
# 15s P2P dead interval (analysis doc §1d/§3):
#   * the de-hub pod .30 loses its P2P adjacency to .33 and reoriginates its
#     router-LSA DROPPING link1 (the P2P to .33). => no router advertises a
#     reciprocal P2P link to .33 anymore.
#   * .33's own router-LSA ages out of the LSDB.
#   * BUT the hub-side wg-de interface stays UP, so the 10.9.21.0/24 stub
#     (advertised by .30) and its RIB route REMAIN on-link — the exact original
#     #37 false-alive trap.
# pt (.23) is untouched and stays fully alive in the same view.
# ---------------------------------------------------------------------------

HS_ROUTER_LSDB_DE_DEAD = {
    "routerId": "10.130.30.99",
    "routerLinkStates": {
        "areas": {
            "0.0.0.0": [
                # pt-hub .20 — unchanged (pt still alive)
                {
                    "lsaType": "router-LSA",
                    "linkStateId": "10.130.30.20",
                    "advertisingRouter": "10.130.30.20",
                    "routerLinks": {
                        "link0": {"linkType": "a Transit Network",
                                  "designatedRouterAddress": "172.30.0.20",
                                  "routerInterfaceAddress": "172.30.0.17"},
                        "link1": {"linkType": "another Router (point-to-point)",
                                  "neighborRouterId": "10.130.30.23",
                                  "routerInterfaceAddress": "10.9.19.1"},
                        "link2": {"linkType": "Stub Network",
                                  "networkAddress": "10.9.19.0",
                                  "networkMask": "255.255.255.0"},
                    },
                },
                # pt-EDGE .23 — unchanged
                {
                    "lsaType": "router-LSA",
                    "linkStateId": "10.130.30.23",
                    "advertisingRouter": "10.130.30.23",
                    "routerLinks": {
                        "link0": {"linkType": "another Router (point-to-point)",
                                  "neighborRouterId": "10.130.30.20",
                                  "routerInterfaceAddress": "10.9.19.2"},
                        "link1": {"linkType": "Stub Network",
                                  "networkAddress": "10.9.19.0",
                                  "networkMask": "255.255.255.0"},
                    },
                },
                # de-hub .30 — REORIGINATED: link1 (P2P to .33) is GONE. The
                # transit link and the 10.9.21.0/24 stub REMAIN (wg iface up).
                {
                    "lsaType": "router-LSA",
                    "linkStateId": "10.130.30.30",
                    "advertisingRouter": "10.130.30.30",
                    "routerLinks": {
                        "link0": {"linkType": "a Transit Network",
                                  "designatedRouterAddress": "172.30.0.20",
                                  "routerInterfaceAddress": "172.30.0.18"},
                        # link1 (P2P -> 10.130.30.33) withdrawn — edge dead.
                        "link2": {"linkType": "Stub Network",
                                  "networkAddress": "10.9.21.0",
                                  "networkMask": "255.255.255.0"},
                    },
                },
                # de-EDGE .33 — LSA aged out of the LSDB entirely (absent).
                # border .50 — unchanged
                {
                    "lsaType": "router-LSA",
                    "linkStateId": "10.130.30.50",
                    "advertisingRouter": "10.130.30.50",
                    "routerLinks": {
                        "link0": {"linkType": "a Transit Network",
                                  "designatedRouterAddress": "172.30.0.20",
                                  "routerInterfaceAddress": "172.30.0.15"},
                        "link1": {"linkType": "Stub Network",
                                  "networkAddress": "10.130.30.50",
                                  "networkMask": "255.255.255.255"},
                    },
                },
            ],
        },
    },
}


# Neighbor view in the de-dead state — the hub's DIRECT neighbors are unchanged
# (the de-hub POD .30 is still Full; it is the EDGE that died, two hops away and
# never a direct neighbor to begin with).
HS_NEIGHBORS_DE_DEAD = {
    "neighbors": {
        "10.130.30.50": [
            {"nbrState": "Full/Backup", "ifaceName": "backbone", "address": "172.30.0.15"},
        ],
        "10.130.30.21": [
            {"nbrState": "Full/DROther", "ifaceName": "backbone", "address": "172.30.0.16"},
        ],
        "10.130.30.20": [
            {"nbrState": "Full/DROther", "ifaceName": "backbone", "address": "172.30.0.17"},
        ],
        "10.130.30.30": [  # de-hub pod STILL Full — the edge behind it is dead
            {"nbrState": "Full/DROther", "ifaceName": "backbone", "address": "172.30.0.18"},
        ],
        "10.130.30.22": [
            {"nbrState": "Full/DROther", "ifaceName": "backbone", "address": "172.30.0.19"},
        ],
    },
}


# RIB in the de-dead state — CRUCIALLY 10.9.21.0/24 is STILL PRESENT (advertised
# by the hub-side pod .30 from its still-up wg interface). This is the on-link
# route that made naive RIB-resolution false-alive (original #37).
HS_RIB_DE_DEAD = {
    "10.9.19.0/24": {
        "routeType": "N", "transit": False, "cost": 20, "area": "0.0.0.0",
        "nexthops": [{"ip": "172.30.0.17", "via": "backbone",
                      "advertisedRouter": "10.130.30.20"}],
    },
    "10.9.20.0/24": {
        "routeType": "N", "transit": False, "cost": 20, "area": "0.0.0.0",
        "nexthops": [{"ip": "172.30.0.16", "via": "backbone",
                      "advertisedRouter": "10.130.30.21"}],
    },
    # de gw /24 STILL resolves on-link via the hub-side pod — the #37 trap.
    "10.9.21.0/24": {
        "routeType": "N", "transit": False, "cost": 20, "area": "0.0.0.0",
        "nexthops": [{"ip": "172.30.0.18", "via": "backbone",
                      "advertisedRouter": "10.130.30.30"}],
    },
    "10.130.30.50/32": {
        "routeType": "N", "transit": False, "cost": 20, "area": "0.0.0.0",
        "nexthops": [{"ip": "172.30.0.15", "via": "backbone",
                      "advertisedRouter": "10.130.30.50"}],
    },
    "172.30.0.0/24": {
        "routeType": "N", "transit": True, "cost": 10, "area": "0.0.0.0",
        "nexthops": [{"ip": " ", "directlyAttachedTo": "backbone"}],
    },
}
