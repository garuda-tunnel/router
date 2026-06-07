"""Integration tests for FrrVtyshOspfHealthSource.get_interface_health."""

import unittest
from unittest.mock import patch, MagicMock
from types import SimpleNamespace
from route_health import FrrVtyshOspfHealthSource


# FRR JSON sample with one Full/DR neighbor on wg_uk
_SAMPLE_OSPF_JSON = {
    "neighbors": {
        "10.9.19.2": [
            {
                "priority": 1,
                "state": "Full/DR",
                "address": "10.9.19.2",
                "ifaceName": "wg_uk",
                "retransmitCounter": 0,
                "requestCounter": 0,
                "dbSummaryCounter": 0,
            }
        ]
    }
}


def _make_iface(required_state="Full", neighbor_interface=None):
    """Helper: build a gated interface config SimpleNamespace."""
    return SimpleNamespace(
        required_state=required_state,
        neighbor_interface=neighbor_interface,
    )


class TestGetInterfaceHealthIntegration(unittest.TestCase):
    """Integration tests for FrrVtyshOspfHealthSource.get_interface_health."""

    def test_get_interface_health_returns_healthy_for_full_neighbor(self):
        """get_interface_health returns True for interface with Full OSPF neighbor."""
        import json

        mock_result = MagicMock()
        mock_result.stdout = json.dumps(_SAMPLE_OSPF_JSON)
        gated = {
            "wg_uk": _make_iface(required_state="Full", neighbor_interface="wg_uk")
        }
        src = FrrVtyshOspfHealthSource()
        with patch("subprocess.run", return_value=mock_result):
            result = src.get_interface_health(gated)
        self.assertTrue(result["wg_uk"])

    def test_get_interface_health_fail_closed_on_subprocess_error(self):
        """get_interface_health returns all False when subprocess fails (fail-closed)."""
        gated = {
            "wg_uk": _make_iface(required_state="Full", neighbor_interface="wg_uk"),
            "wg_de": _make_iface(required_state="Full", neighbor_interface="wg_de"),
        }
        src = FrrVtyshOspfHealthSource()
        with patch("subprocess.run", side_effect=OSError("no vtysh")):
            result = src.get_interface_health(gated)
        self.assertFalse(result["wg_uk"])
        self.assertFalse(result["wg_de"])


if __name__ == "__main__":
    unittest.main()
