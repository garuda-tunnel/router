import ipaddress

import pytest

from Config import NetRoute, DomainRoute
from route import RouteObject


def test_net_route_builds_route_object_with_logical_gateway():
    route = NetRoute(net="0.0.0.0/0", route={"gw": "10.9.19.2"})
    built = route.routes[0]
    assert built.gw == "10.9.19.2"
    assert built.dev is None


def test_route_without_resolution_cannot_build_route_spec():
    route = RouteObject(net="0.0.0.0/0", gw="10.9.19.2")
    with pytest.raises((ValueError, RuntimeError, KeyError)):
        _ = route.route_spec


def test_domain_route_builds_host_route_with_gateway_v1():
    route = DomainRoute(domain="_DEFAULT", route={"gw": "10.9.19.2"}, route_ttl=60)
    built = route.build_route("8.8.8.8")
    assert built.gw == "10.9.19.2"
    assert built.ttl == 60
    assert built.net == ipaddress.IPv4Network("8.8.8.8/32")
