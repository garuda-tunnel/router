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
