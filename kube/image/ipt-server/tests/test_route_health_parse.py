"""Tests for FrrVtyshOspfHealthSource._parse_neighbor_health (pure function)."""

import unittest
from types import SimpleNamespace
from route_health import FrrVtyshOspfHealthSource


def _make_iface(required_state="Full", neighbor_interface=None):
    """Helper: build a gated interface config SimpleNamespace."""
    return SimpleNamespace(
        required_state=required_state,
        neighbor_interface=neighbor_interface,
    )


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


class TestParseNeighborHealth(unittest.TestCase):
    """Tests for FrrVtyshOspfHealthSource._parse_neighbor_health (pure function)."""

    def _src(self):
        return FrrVtyshOspfHealthSource()

    def test_full_adjacency_reports_healthy(self):
        """Interface with Full/DR neighbor matching required 'Full' state is healthy."""
        gated = {
            "wg_uk": _make_iface(required_state="Full", neighbor_interface="wg_uk")
        }
        result = self._src()._parse_neighbor_health(_SAMPLE_OSPF_JSON, gated)
        self.assertTrue(result["wg_uk"])

    def test_no_matching_neighbor_reports_unhealthy(self):
        """Interface not present in OSPF neighbor data is reported unhealthy."""
        gated = {
            "wg_de": _make_iface(required_state="Full", neighbor_interface="wg_de")
        }
        result = self._src()._parse_neighbor_health(_SAMPLE_OSPF_JSON, gated)
        self.assertFalse(result["wg_de"])

    def test_wrong_state_reports_unhealthy(self):
        """Neighbor in Init state does not satisfy Full required_state."""
        ospf_data = {
            "neighbors": {"10.9.19.3": [{"state": "Init/DR", "ifaceName": "wg_uk"}]}
        }
        gated = {
            "wg_uk": _make_iface(required_state="Full", neighbor_interface="wg_uk")
        }
        result = self._src()._parse_neighbor_health(ospf_data, gated)
        self.assertFalse(result["wg_uk"])

    def test_full_dr_matches_required_full_prefix(self):
        """'Full/DR' state starts with 'Full' so it satisfies required_state='Full'."""
        gated = {
            "wg_uk": _make_iface(required_state="Full", neighbor_interface="wg_uk")
        }
        result = self._src()._parse_neighbor_health(_SAMPLE_OSPF_JSON, gated)
        self.assertTrue(result["wg_uk"])

    def test_full_backup_matches_required_full_prefix(self):
        """'Full/Backup' state starts with 'Full' so it satisfies required_state='Full'."""
        ospf_data = {
            "neighbors": {"10.9.19.4": [{"state": "Full/Backup", "ifaceName": "wg_uk"}]}
        }
        gated = {
            "wg_uk": _make_iface(required_state="Full", neighbor_interface="wg_uk")
        }
        result = self._src()._parse_neighbor_health(ospf_data, gated)
        self.assertTrue(result["wg_uk"])

    def test_empty_ospf_data_all_unhealthy(self):
        """Empty OSPF dict causes all gated interfaces to be unhealthy (fail-closed)."""
        gated = {
            "wg_uk": _make_iface(required_state="Full", neighbor_interface="wg_uk"),
            "wg_de": _make_iface(required_state="Full", neighbor_interface="wg_de"),
        }
        result = self._src()._parse_neighbor_health({}, gated)
        self.assertFalse(result["wg_uk"])
        self.assertFalse(result["wg_de"])

    def test_malformed_ospf_data_all_unhealthy(self):
        """Non-dict neighbor entries in OSPF data cause all interfaces to be unhealthy."""
        gated = {
            "wg_uk": _make_iface(required_state="Full", neighbor_interface="wg_uk")
        }
        result = self._src()._parse_neighbor_health({"neighbors": "bad"}, gated)
        self.assertFalse(result["wg_uk"])

    def test_neighbor_interface_used_for_matching(self):
        """neighbor_interface field is used to match ifaceName in OSPF data."""
        # wg_uk is the logical name, but ospf reports on wg_uk_phys
        ospf_data = {
            "neighbors": {
                "10.9.19.5": [{"state": "Full/DR", "ifaceName": "wg_uk_phys"}]
            }
        }
        gated = {
            "wg_uk": _make_iface(required_state="Full", neighbor_interface="wg_uk_phys")
        }
        result = self._src()._parse_neighbor_health(ospf_data, gated)
        self.assertTrue(result["wg_uk"])


if __name__ == "__main__":
    unittest.main()
