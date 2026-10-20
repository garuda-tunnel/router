"""Shared fixtures for tasks unit tests.

The legacy OSPF fixtures model the vpn2 stand, whose backbone interface runs
172.30.0.0/24. nexthop_monitor derives the backbone subnets at runtime from
the pod's PBR-interface IPv4 addresses (#65), so the default snapshot those
fixtures rely on is provided here; tests that exercise the derivation itself
override it.
"""

import ipaddress

import pytest

import tasks.nexthop_monitor as nexthop_monitor


@pytest.fixture(autouse=True)
def _legacy_backbone_subnet(monkeypatch):
    """Default the runtime backbone subnets to the vpn2 stand's 172.30.0.0/24."""
    monkeypatch.setattr(
        nexthop_monitor,
        "_BACKBONE_SUBNETS",
        (ipaddress.ip_network("172.30.0.0/24"),),
        raising=False,
    )
    # Reset the warn-once marker so log-transition tests start armed.
    monkeypatch.setattr(nexthop_monitor, "_LOGGED_EMPTY_BACKBONE", False, raising=False)
