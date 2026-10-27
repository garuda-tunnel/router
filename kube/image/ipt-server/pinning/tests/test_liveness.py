"""Liveness probe logic for the pinning subsystem.

Validates that liveness state transitions trigger reconciler updates and
that gw vs dev-only egresses use the right probe path.
Code: pinning/liveness.py::probe_egress
"""
from unittest.mock import MagicMock, patch

from pinning import liveness


def _gw_target(gw):
    t = MagicMock()
    t.gw = gw
    t.dev = None
    return t


def _dev_target(dev):
    t = MagicMock()
    t.gw = None
    t.dev = dev
    return t


def test_probe_egress_gw_alive_returns_probe_result():
    """For gw-based egress, probe_egress delegates to _probe_gw_alive."""
    target = _gw_target("192.0.2.1")
    with patch.object(
        liveness, "_probe_gw_alive", return_value=(True, "192.0.2.1", "wg-edge")
    ) as probe:
        alive, nh_ip, nh_dev = liveness.probe_egress(target, interfaces=set())
    probe.assert_called_once_with("192.0.2.1")
    assert (alive, nh_ip, nh_dev) == (True, "192.0.2.1", "wg-edge")


def test_probe_egress_dev_alive_when_interface_present():
    """For dev-only egress, alive iff dev is in the current interface set."""
    target = _dev_target("border")
    alive, nh_ip, nh_dev = liveness.probe_egress(target, interfaces={"border"})
    assert alive is True
    assert nh_ip is None
    assert nh_dev == "border"


def test_probe_egress_dev_dead_when_interface_absent():
    """A missing interface yields alive=False."""
    target = _dev_target("border")
    alive, nh_ip, nh_dev = liveness.probe_egress(target, interfaces=set())
    assert alive is False
    assert nh_dev == "border"


def test_probe_gw_alive_shares_module_with_running_monitor():
    """Liveness probes via the module object the running app refreshes (#83).

    ipt_server/main.py runs ``ipt_server.tasks.nexthop_monitor``; if pinning
    imported a second module object (the pre-1.4.3 top-level
    ``tasks.nexthop_monitor``), only main.py's copy got ``_BACKBONE_SUBNETS``
    refreshed and the pinning copy judged every on-backbone gateway dead.
    """
    import sys

    from ipt_server.tasks import nexthop_monitor

    assert liveness._probe_gw_alive.__module__ == "ipt_server.tasks.nexthop_monitor"
    assert sys.modules[liveness._probe_gw_alive.__module__] is nexthop_monitor


def test_probe_egress_gw_on_backbone_alive_after_monitor_refresh():
    """A gw on a backbone subnet refreshed by the monitor is alive (#83).

    Reproduces the live 1.4.2 border-pin blackhole: the owning router is a
    Full direct OSPF neighbor and the gw sits on the backbone subnet the
    monitor just refreshed, so liveness must NOT fail closed.
    """
    import ipaddress

    from ipt_server.tasks import nexthop_monitor

    gw = "172.30.0.99"
    owner = "10.130.30.23"
    lsdb = {
        "routerLinkStates": {
            "areas": {
                "0.0.0.0": [
                    {
                        "advertisingRouter": owner,
                        "routerLinks": {"1": {"routerInterfaceAddress": gw}},
                    },
                ]
            }
        }
    }
    neighbors = {"neighbors": {owner: [{"nbrState": "Full"}]}}
    rib = {
        "172.30.0.0/24": {
            "nexthops": [{"ip": "172.30.0.5", "directlyAttachedTo": "wg-0"}]
        }
    }

    saved_subnets = nexthop_monitor._BACKBONE_SUBNETS
    saved_warned = nexthop_monitor._LOGGED_EMPTY_BACKBONE
    try:
        with patch.object(
            nexthop_monitor,
            "_backbone_addr_prefixes",
            return_value=[("172.30.0.10", 24)],
        ):
            nexthop_monitor._refresh_backbone_subnets()
        assert nexthop_monitor._BACKBONE_SUBNETS == (
            ipaddress.ip_network("172.30.0.0/24"),
        )

        def fake_vtysh(command):
            return {
                "show ip ospf database router json": lsdb,
                "show ip ospf neighbor json": neighbors,
                "show ip ospf route json": rib,
            }.get(command)

        with patch.object(nexthop_monitor, "_vtysh", side_effect=fake_vtysh):
            alive, nh_ip, nh_dev = liveness.probe_egress(
                _gw_target(gw), interfaces=set()
            )
    finally:
        nexthop_monitor._BACKBONE_SUBNETS = saved_subnets
        nexthop_monitor._LOGGED_EMPTY_BACKBONE = saved_warned

    assert alive is True
    assert (nh_ip, nh_dev) == ("172.30.0.5", "wg-0")
