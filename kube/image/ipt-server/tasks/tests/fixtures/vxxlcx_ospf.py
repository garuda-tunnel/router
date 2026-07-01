"""Live-captured OSPF LSDB / RIB fixtures from v-xxl-cx (prod) frr-sidecar.

Captured read-only via the FRR vty bridge on the live ipt-server pod:

    vtysh -c 'show ip ospf database router json'  -> VXXLCX_ROUTER_LSDB
    vtysh -c 'show ip ospf route json'            -> VXXLCX_RIB

Verbatim JSON (only RFC1918 172.30.0.0/24 backbone + CGNAT 10.x addresses,
per router-internal/AGENTS.md).  Source dumps: /tmp/vxxl_lsdb/*.json and the
empirical artifact docs/artifacts/2026-07-01-ipt-server-egress-empirical-vpn2-vs-vxxlcx.md
(§1c/§1d).

Egress → owning router → advertised iface addr → OSPF-RIB prefix → nexthops[0].ip:
    usa    10.130.30.23  10.9.19.2   10.9.19.0/28  172.30.0.35
    mexico 10.130.30.33  10.9.27.2   10.9.27.0/28  172.30.0.36
    border 10.130.30.50  172.30.0.38 172.30.0.0/24 " " (directly-attached)
"""
from __future__ import annotations


# `show ip ospf database router json` — verbatim from
# /tmp/vxxl_lsdb/show_ip_ospf_database_router_json.json.
VXXLCX_ROUTER_LSDB = {
    "routerId": "10.130.30.99",
    "routerLinkStates": {
        "areas": {
            "0.0.0.0": [
                {
                    "asbr": True,
                    "lsaType": "router-LSA",
                    "linkStateId": "10.9.25.2",
                    "advertisingRouter": "10.9.25.2",
                    "numOfLinks": 2,
                    "routerLinks": {
                        "link0": {
                            "linkType": "another Router (point-to-point)",
                            "neighborRouterId": "10.130.30.21",
                            "routerInterfaceAddress": "10.9.25.2",
                        },
                        "link1": {
                            "linkType": "Stub Network",
                            "networkAddress": "10.9.25.0",
                            "networkMask": "255.255.255.240",
                        },
                    },
                },
                {
                    "lsaType": "router-LSA",
                    "linkStateId": "10.130.30.20",
                    "advertisingRouter": "10.130.30.20",
                    "numOfLinks": 3,
                    "routerLinks": {
                        "link0": {
                            "linkType": "a Transit Network",
                            "designatedRouterAddress": "172.30.0.39",
                            "routerInterfaceAddress": "172.30.0.35",
                        },
                        "link1": {
                            "linkType": "another Router (point-to-point)",
                            "neighborRouterId": "10.130.30.23",
                            "routerInterfaceAddress": "10.9.19.1",
                        },
                        "link2": {
                            "linkType": "Stub Network",
                            "networkAddress": "10.9.19.0",
                            "networkMask": "255.255.255.240",
                        },
                    },
                },
                {
                    "asbr": True,
                    "lsaType": "router-LSA",
                    "linkStateId": "10.130.30.21",
                    "advertisingRouter": "10.130.30.21",
                    "numOfLinks": 3,
                    "routerLinks": {
                        "link0": {
                            "linkType": "a Transit Network",
                            "designatedRouterAddress": "172.30.0.39",
                            "routerInterfaceAddress": "172.30.0.34",
                        },
                        "link1": {
                            "linkType": "another Router (point-to-point)",
                            "neighborRouterId": "10.9.25.2",
                            "routerInterfaceAddress": "10.9.25.1",
                        },
                        "link2": {
                            "linkType": "Stub Network",
                            "networkAddress": "10.9.25.0",
                            "networkMask": "255.255.255.240",
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
                            "designatedRouterAddress": "172.30.0.39",
                            "routerInterfaceAddress": "172.30.0.37",
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
                            "networkMask": "255.255.255.240",
                        },
                    },
                },
                {
                    "lsaType": "router-LSA",
                    "linkStateId": "10.130.30.32",
                    "advertisingRouter": "10.130.30.32",
                    "numOfLinks": 3,
                    "routerLinks": {
                        "link0": {
                            "linkType": "a Transit Network",
                            "designatedRouterAddress": "172.30.0.39",
                            "routerInterfaceAddress": "172.30.0.36",
                        },
                        "link1": {
                            "linkType": "another Router (point-to-point)",
                            "neighborRouterId": "10.130.30.33",
                            "routerInterfaceAddress": "10.9.27.1",
                        },
                        "link2": {
                            "linkType": "Stub Network",
                            "networkAddress": "10.9.27.0",
                            "networkMask": "255.255.255.240",
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
                            "neighborRouterId": "10.130.30.32",
                            "routerInterfaceAddress": "10.9.27.2",
                        },
                        "link1": {
                            "linkType": "Stub Network",
                            "networkAddress": "10.9.27.0",
                            "networkMask": "255.255.255.240",
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
                            "designatedRouterAddress": "172.30.0.39",
                            "routerInterfaceAddress": "172.30.0.38",
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
                            "designatedRouterAddress": "172.30.0.39",
                            "routerInterfaceAddress": "172.30.0.39",
                        },
                    },
                },
            ],
        },
    },
}


# `show ip ospf route json` — verbatim from
# /tmp/vxxl_lsdb/show_ip_ospf_route_json.json.
VXXLCX_RIB = {
    "10.9.19.0/28": {
        "routeType": "N", "transit": False, "cost": 20, "area": "0.0.0.0",
        "nexthops": [
            {"ip": "172.30.0.35", "via": "backbone",
             "advertisedRouter": "10.130.30.20"},
        ],
    },
    "10.9.25.0/28": {
        "routeType": "N", "transit": False, "cost": 20, "area": "0.0.0.0",
        "nexthops": [
            {"ip": "172.30.0.34", "via": "backbone",
             "advertisedRouter": "10.130.30.21"},
        ],
    },
    "10.9.27.0/28": {
        "routeType": "N", "transit": False, "cost": 20, "area": "0.0.0.0",
        "nexthops": [
            {"ip": "172.30.0.36", "via": "backbone",
             "advertisedRouter": "10.130.30.32"},
        ],
    },
    "10.130.30.50/32": {
        "routeType": "N", "transit": False, "cost": 20, "area": "0.0.0.0",
        "nexthops": [
            {"ip": "172.30.0.38", "via": "backbone",
             "advertisedRouter": "10.130.30.50"},
        ],
    },
    "172.30.0.0/24": {
        "routeType": "N", "transit": True, "cost": 10, "area": "0.0.0.0",
        "nexthops": [{"ip": " ", "directlyAttachedTo": "backbone"}],
    },
    "10.9.25.2": {
        "routeType": "R ", "cost": 20, "area": "0.0.0.0", "routerType": "asbr",
        "nexthops": [{"ip": "172.30.0.34", "via": "backbone"}],
    },
    "10.130.30.21": {
        "routeType": "R ", "cost": 10, "area": "0.0.0.0", "routerType": "asbr",
        "nexthops": [{"ip": "172.30.0.34", "via": "backbone"}],
    },
    "10.130.30.22": {
        "routeType": "R ", "cost": 10, "area": "0.0.0.0", "routerType": "asbr",
        "nexthops": [{"ip": "172.30.0.37", "via": "backbone"}],
    },
    "10.0.24.0/24": {
        "routeType": "N E2", "cost": 10, "type2cost": 20, "tag": 0,
        "nexthops": [
            {"ip": "172.30.0.37", "via": "backbone",
             "advertisedRouter": "10.130.30.22"},
        ],
    },
    "10.9.20.0/24": {
        "routeType": "N E1", "cost": 21, "tag": 0,
        "nexthops": [
            {"ip": "172.30.0.34", "via": "backbone",
             "advertisedRouter": "10.9.25.2"},
        ],
    },
    "10.42.0.0/16": {
        "routeType": "N E2", "cost": 10, "type2cost": 20, "tag": 0,
        "nexthops": [
            {"ip": "172.30.0.34", "via": "backbone",
             "advertisedRouter": "10.130.30.21"},
            {"ip": "172.30.0.37", "via": "backbone",
             "advertisedRouter": "10.130.30.22"},
        ],
    },
    "10.42.0.0/24": {
        "routeType": "N E2", "cost": 10, "type2cost": 20, "tag": 0,
        "nexthops": [
            {"ip": "172.30.0.37", "via": "backbone",
             "advertisedRouter": "10.130.30.22"},
        ],
    },
}
