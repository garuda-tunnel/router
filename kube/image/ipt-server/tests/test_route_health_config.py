"""Tests for RouteHealthInterface, RouteHealthSettings, and MySettings.route_health."""

import unittest
from unittest.mock import patch, PropertyMock
from Config import (
    MySettings,
    RouteHealthInterface,
    RouteHealthSettings,
)


class TestRouteHealthConfig(unittest.TestCase):
    """Tests for RouteHealthInterface, RouteHealthSettings, and MySettings.route_health."""

    def _make_minimal_settings(self, extra=None):
        """Build a minimal valid MySettings dict (no routes needed)."""
        base = dict(
            table=200,
            routes=[],
            ws_port=8080,
            pbr_mark=1,
            interfaces=["eth0"],
            clean_conntrack=False,
            domain_route_ttl=300,
        )
        if extra:
            base.update(extra)
        return base

    def test_required_settings_fail_fast_when_missing(self):
        """Spec-required MySettings fields must not be masked by defaults."""
        base = self._make_minimal_settings()
        required = (
            "clean_conntrack",
            "domain_route_ttl",
            "interfaces",
            "routes",
        )

        for field in required:
            with self.subTest(field=field):
                payload = dict(base)
                payload.pop(field)
                with self.assertRaises(Exception):
                    MySettings(**payload)

    @patch("route.RouteObject.interfaces", new_callable=PropertyMock)
    def test_config_without_route_health_parses_correctly(self, mock_interfaces):
        """Backward compat: config with no route_health key should parse without error."""
        mock_interfaces.return_value = {"eth0": [(1, 0)]}

        settings = MySettings(**self._make_minimal_settings())

        self.assertIsInstance(settings.route_health, RouteHealthSettings)
        self.assertEqual(settings.route_health.interfaces, {})

    @patch("route.RouteObject.interfaces", new_callable=PropertyMock)
    def test_config_with_route_health_interfaces_parses_correctly(
        self, mock_interfaces
    ):
        """Config with route_health.interfaces should parse into correct model instances."""
        mock_interfaces.return_value = {"eth0": [(1, 0)]}

        route_health_data = {
            "interfaces": {
                "wg_uk": {
                    "kind": "ospf",
                    "required_state": "Full",
                    "neighbor_interface": "wg_uk",
                }
            }
        }
        settings = MySettings(
            **self._make_minimal_settings({"route_health": route_health_data})
        )

        self.assertIsInstance(settings.route_health, RouteHealthSettings)
        self.assertIn("wg_uk", settings.route_health.interfaces)
        iface = settings.route_health.interfaces["wg_uk"]
        self.assertIsInstance(iface, RouteHealthInterface)
        self.assertEqual(iface.kind, "ospf")
        self.assertEqual(iface.required_state, "Full")
        self.assertEqual(iface.neighbor_interface, "wg_uk")

    @patch("route.RouteObject.interfaces", new_callable=PropertyMock)
    def test_neighbor_interface_defaults_to_none_when_not_specified(
        self, mock_interfaces
    ):
        """RouteHealthInterface.neighbor_interface should default to None if not provided."""
        mock_interfaces.return_value = {"eth0": [(1, 0)]}

        route_health_data = {
            "interfaces": {
                "wg_us": {
                    "kind": "ospf",
                }
            }
        }
        settings = MySettings(
            **self._make_minimal_settings({"route_health": route_health_data})
        )

        iface = settings.route_health.interfaces["wg_us"]
        self.assertIsNone(iface.neighbor_interface)

    @patch("route.RouteObject.interfaces", new_callable=PropertyMock)
    def test_required_state_defaults_to_full(self, mock_interfaces):
        """RouteHealthInterface.required_state should default to 'Full' if not provided."""
        mock_interfaces.return_value = {"eth0": [(1, 0)]}

        route_health_data = {
            "interfaces": {
                "wg_de": {
                    "kind": "ospf",
                }
            }
        }
        settings = MySettings(
            **self._make_minimal_settings({"route_health": route_health_data})
        )

        iface = settings.route_health.interfaces["wg_de"]
        self.assertEqual(iface.required_state, "Full")


if __name__ == "__main__":
    unittest.main()
