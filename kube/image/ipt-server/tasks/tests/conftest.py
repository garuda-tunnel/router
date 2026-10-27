"""Shared fixtures for tasks unit tests.

The nexthop monitor derives the backbone subnets at runtime from the
pod's PBR-interface IPv4 addresses (#65). `_tick` tests keep that
derivation REAL by feeding a fake netlink boundary: the autouse fixture
below patches `ipt_server.tasks.nexthop_monitor.IPRoute` with a stand-in whose
backbone interface runs 172.30.0.10/24 (the reference lab topology), and
seeds the derived subnets for tests that do not run a tick. Tests that
exercise the derivation itself patch the boundary with their own data.
"""

import ipaddress
import socket

import pytest

import ipt_server.tasks.nexthop_monitor as nexthop_monitor


class _FakeAddr(dict):
    def __init__(self, prefixlen, local):
        super().__init__(prefixlen=prefixlen)
        self._local = local

    def get_attr(self, name):
        return self._local if name in ("IFA_LOCAL", "IFA_ADDRESS") else None


class _FakeNetlink:
    """IPRoute stand-in serving one address per interface name."""

    def __init__(self, addr_map):
        self._map = addr_map
        self._index = {name: i + 1 for i, name in enumerate(addr_map)}

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def link_lookup(self, ifname):
        return [self._index[ifname]] if ifname in self._index else []

    def get_addr(self, index, family):
        if family is not socket.AF_INET:
            return []
        name = next(n for n, i in self._index.items() if i == index)
        return [_FakeAddr(plen, addr) for addr, plen in self._map[name]]


@pytest.fixture(autouse=True)
def _fake_backbone_netlink(monkeypatch):
    """Serve a 172.30.0.10/24 backbone through the netlink boundary.

    Keeps `_refresh_backbone_subnets` real (it runs against this fake)
    while guaranteeing no host netlink socket is opened. The derived
    subnets are also seeded for tests that call `_is_on_backbone` without
    running a tick first.
    """
    monkeypatch.setattr(
        nexthop_monitor,
        "IPRoute",
        lambda: _FakeNetlink({"backbone": [("172.30.0.10", 24)]}),
    )
    monkeypatch.setattr(
        nexthop_monitor,
        "_BACKBONE_SUBNETS",
        (ipaddress.ip_network("172.30.0.0/24"),),
        raising=False,
    )
    # Reset the warn-once marker so log-transition tests start armed.
    monkeypatch.setattr(nexthop_monitor, "_LOGGED_EMPTY_BACKBONE", False, raising=False)
