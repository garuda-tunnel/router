import importlib.util
import ipaddress

_spec = importlib.util.spec_from_file_location(
    "_garuda_orig_nexthop_monitor", "/tasks/nexthop_monitor.py"
)
_orig = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_orig)


def _find_router_owning_address(gw, router_lsdb):
    if not isinstance(router_lsdb, dict):
        return None
    areas = router_lsdb.get("routerLinkStates", {}).get("areas", {})
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


_orig._find_router_owning_address = _find_router_owning_address
globals().update(
    {k: v for k, v in vars(_orig).items() if k != "_find_router_owning_address"}
)
