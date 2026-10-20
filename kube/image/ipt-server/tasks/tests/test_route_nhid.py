"""Unit tests for RouteObject nhid-only route specification.

Validates: RouteObject.route_spec uses nhid exclusively for kernel route installation.
Code: route.py
"""

import ipaddress
import pytest

from Config import RouteMember
from route import RouteObject


def test_route_spec_always_uses_nhid():
    """Route spec uses nhid when assigned.

    Validates: RouteObject.route_spec returns {"nhid": N, ...} when nhid is set.
    Code: route.RouteObject.route_spec
    Assertion: spec["nhid"] equals assigned nhid value.
    """
    r = RouteObject(net="192.0.2.10/32", nhid=100)
    spec = r.route_spec
    assert spec["nhid"] == 100
    assert spec["dst"] == "192.0.2.10"
    assert spec["dst_len"] == 32
    assert spec["family"] == r.family
    assert spec["proto"] == r.proto
    assert spec["type"] == r.type
    assert spec["priority"] == r.metric


def test_route_spec_without_nhid_raises():
    """Route spec raises RuntimeError when nhid is not assigned.

    Validates: accessing route_spec on a RouteObject with nhid=None raises RuntimeError.
    Code: route.RouteObject.route_spec
    Assertion: RuntimeError is raised with a message referencing nhid.
    """
    r = RouteObject(net="10.0.0.0/8")
    with pytest.raises(RuntimeError, match="nhid"):
        _ = r.route_spec


def test_config_rejects_default_sentinel():
    """RouteMember rejects _DEFAULT dev sentinel.

    Validates: _DEFAULT sentinel is rejected at config validation.
    Code: Config.RouteMember
    Assertion: ValueError raised with message referencing _DEFAULT.
    """
    with pytest.raises(ValueError, match="_DEFAULT"):
        RouteMember(dev="_DEFAULT")
