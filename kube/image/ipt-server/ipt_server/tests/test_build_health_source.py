"""Tests for ipt_server.build_route_health_source factory function."""

import unittest
from unittest.mock import patch, PropertyMock
from Config import MySettings
from route_health import FrrVtyshOspfHealthSource


class TestBuildRouteHealthSource(unittest.TestCase):
    """Tests for ipt_server.build_route_health_source factory function."""

    @patch("route.RouteObject.interfaces", new_callable=PropertyMock)
    def test_returns_none_when_no_gated_interfaces(self, mock_interfaces):
        """build_route_health_source returns None when route_health.interfaces is empty."""
        mock_interfaces.return_value = {"eth0": [(1, 0)]}

        config = MySettings(
            table=200,
            routes=[],
            ws_port=8080,
            pbr_mark=1,
            interfaces=["eth0"],
            clean_conntrack=False,
            domain_route_ttl=300,
        )

        import ipt_server.main

        result = ipt_server.main.build_route_health_source(config)
        self.assertIsNone(result)

    @patch("route.RouteObject.interfaces", new_callable=PropertyMock)
    def test_returns_frr_source_when_gated_interfaces_exist(self, mock_interfaces):
        """build_route_health_source returns FrrVtyshOspfHealthSource when interfaces configured."""
        mock_interfaces.return_value = {"eth0": [(1, 0)]}

        config = MySettings(
            table=200,
            routes=[],
            ws_port=8080,
            pbr_mark=1,
            interfaces=["eth0"],
            clean_conntrack=False,
            domain_route_ttl=300,
            route_health={
                "interfaces": {
                    "wg_uk": {
                        "kind": "ospf",
                        "required_state": "Full",
                        "neighbor_interface": "wg_uk",
                    }
                }
            },
        )

        import ipt_server.main

        result = ipt_server.main.build_route_health_source(config)
        self.assertIsInstance(result, FrrVtyshOspfHealthSource)


if __name__ == "__main__":
    unittest.main()
