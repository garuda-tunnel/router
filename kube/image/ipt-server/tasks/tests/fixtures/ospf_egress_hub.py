"""Synthetic OSPF LSDB / RIB fixtures: hub router with tunnelled egress edges.

Modelled on a hub-and-spoke topology (originally captured read-only from a
lab stand via the FRR vty bridge; sanitized to generic labels).

    vtysh -c 'show ip ospf database router json'  -> HUB_EGRESS_ROUTER_LSDB
    vtysh -c 'show ip ospf route json'            -> HUB_EGRESS_RIB

Values use only RFC1918 172.30.0.0/24 backbone + CGNAT 10.x addresses per
router-internal/AGENTS.md.

Egress → owning router → advertised iface addr → OSPF-RIB prefix → nexthops[0].ip:
    de     10.130.30.33  10.9.21.2   10.9.21.0/24  172.30.0.112
    pt     10.130.30.23  10.9.19.2   10.9.19.0/24  172.30.0.110
    border 10.130.30.50  172.30.0.116 172.30.0.0/24 " " (directly-attached)

NOTE the router-id ↔ egress mapping differs from ospf_edge_standalone.py:
here 10.130.30.23
owns pt (not usa) and 10.130.30.33 owns de. The fix is independent of the
mapping (spec §4).
"""
from __future__ import annotations


# `show ip ospf database router json` payload.
HUB_EGRESS_ROUTER_LSDB = {
    "routerId": "10.130.30.99",
    "routerLinkStates": {
        "areas": {
            "0.0.0.0": [
                {
                    "lsaType": "router-LSA",
                    "linkStateId": "10.130.30.20",
                    "advertisingRouter": "10.130.30.20",
                    "numOfLinks": 3,
                    "routerLinks": {
                        "link0": {
                            "linkType": "a Transit Network",
                            "designatedRouterAddress": "172.30.0.117",
                            "routerInterfaceAddress": "172.30.0.110",
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
                {
                    "asbr": True,
                    "lsaType": "router-LSA",
                    "linkStateId": "10.130.30.21",
                    "advertisingRouter": "10.130.30.21",
                    "numOfLinks": 2,
                    "routerLinks": {
                        "link0": {
                            "linkType": "a Transit Network",
                            "designatedRouterAddress": "172.30.0.117",
                            "routerInterfaceAddress": "172.30.0.100",
                        },
                        "link1": {
                            "linkType": "Stub Network",
                            "networkAddress": "10.9.20.0",
                            "networkMask": "255.255.255.0",
                        },
                    },
                },
                {
                    "asbr": True,
                    "lsaType": "router-LSA",
                    "linkStateId": "10.130.30.22",
                    "advertisingRouter": "10.130.30.22",
                    "numOfLinks": 1,
                    "routerLinks": {
                        "link0": {
                            "linkType": "a Transit Network",
                            "designatedRouterAddress": "172.30.0.117",
                            "routerInterfaceAddress": "172.30.0.113",
                        },
                    },
                },
                {
                    "lsaType": "router-LSA",
                    "linkStateId": "10.130.30.23",
                    "advertisingRouter": "10.130.30.23",
                    "numOfLinks": 2,
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
                {
                    "lsaType": "router-LSA",
                    "linkStateId": "10.130.30.30",
                    "advertisingRouter": "10.130.30.30",
                    "numOfLinks": 3,
                    "routerLinks": {
                        "link0": {
                            "linkType": "a Transit Network",
                            "designatedRouterAddress": "172.30.0.117",
                            "routerInterfaceAddress": "172.30.0.112",
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
                {
                    "lsaType": "router-LSA",
                    "linkStateId": "10.130.30.33",
                    "advertisingRouter": "10.130.30.33",
                    "numOfLinks": 2,
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
                {
                    "lsaType": "router-LSA",
                    "linkStateId": "10.130.30.50",
                    "advertisingRouter": "10.130.30.50",
                    "numOfLinks": 2,
                    "routerLinks": {
                        "link0": {
                            "linkType": "a Transit Network",
                            "designatedRouterAddress": "172.30.0.117",
                            "routerInterfaceAddress": "172.30.0.116",
                        },
                        "link1": {
                            "linkType": "Stub Network",
                            "networkAddress": "10.130.30.50",
                            "networkMask": "255.255.255.255",
                        },
                    },
                },
                {
                    "asbr": True,
                    "lsaType": "router-LSA",
                    "linkStateId": "10.130.30.99",
                    "advertisingRouter": "10.130.30.99",
                    "numOfLinks": 1,
                    "routerLinks": {
                        "link0": {
                            "linkType": "a Transit Network",
                            "designatedRouterAddress": "172.30.0.117",
                            "routerInterfaceAddress": "172.30.0.117",
                        },
                    },
                },
            ],
        },
    },
}


# `show ip ospf route json` payload (sanitized lab capture).
HUB_EGRESS_RIB = {
    "10.9.19.0/24": {
        "routeType": "N", "transit": False, "cost": 20, "area": "0.0.0.0",
        "nexthops": [
            {"ip": "172.30.0.110", "via": "backbone",
             "advertisedRouter": "10.130.30.20"},
        ],
    },
    "10.9.20.0/24": {
        "routeType": "N", "transit": False, "cost": 20, "area": "0.0.0.0",
        "nexthops": [
            {"ip": "172.30.0.100", "via": "backbone",
             "advertisedRouter": "10.130.30.21"},
        ],
    },
    "10.9.21.0/24": {
        "routeType": "N", "transit": False, "cost": 20, "area": "0.0.0.0",
        "nexthops": [
            {"ip": "172.30.0.112", "via": "backbone",
             "advertisedRouter": "10.130.30.30"},
        ],
    },
    "10.130.30.50/32": {
        "routeType": "N", "transit": False, "cost": 20, "area": "0.0.0.0",
        "nexthops": [
            {"ip": "172.30.0.116", "via": "backbone",
             "advertisedRouter": "10.130.30.50"},
        ],
    },
    "172.30.0.0/24": {
        "routeType": "N", "transit": True, "cost": 10, "area": "0.0.0.0",
        "nexthops": [{"ip": " ", "directlyAttachedTo": "backbone"}],
    },
    "10.130.30.21": {
        "routeType": "R ", "cost": 10, "area": "0.0.0.0", "routerType": "asbr",
        "nexthops": [{"ip": "172.30.0.100", "via": "backbone"}],
    },
    "10.130.30.22": {
        "routeType": "R ", "cost": 10, "area": "0.0.0.0", "routerType": "asbr",
        "nexthops": [{"ip": "172.30.0.113", "via": "backbone"}],
    },
    "10.0.24.0/24": {
        "routeType": "N E2", "cost": 10, "type2cost": 20, "tag": 0,
        "nexthops": [
            {"ip": "172.30.0.113", "via": "backbone",
             "advertisedRouter": "10.130.30.22"},
        ],
    },
    "10.42.0.0/16": {
        "routeType": "N E2", "cost": 10, "type2cost": 20, "tag": 0,
        "nexthops": [
            {"ip": "172.30.0.100", "via": "backbone",
             "advertisedRouter": "10.130.30.21"},
            {"ip": "172.30.0.113", "via": "backbone",
             "advertisedRouter": "10.130.30.22"},
        ],
    },
    "10.42.0.0/24": {
        "routeType": "N E2", "cost": 10, "type2cost": 20, "tag": 0,
        "nexthops": [
            {"ip": "172.30.0.113", "via": "backbone",
             "advertisedRouter": "10.130.30.22"},
        ],
    },
    "172.29.0.0/24": {
        "routeType": "N E2", "cost": 10, "type2cost": 20, "tag": 0,
        "nexthops": [
            {"ip": "172.30.0.113", "via": "backbone",
             "advertisedRouter": "10.130.30.22"},
        ],
    },
}
