"""Tests for ipt_server.monitor_route_health async function."""

import asyncio
import unittest
from unittest.mock import patch, PropertyMock, MagicMock
from Config import MySettings


class TestMonitorRouteHealth(unittest.IsolatedAsyncioTestCase):
    """Tests for ipt_server.monitor_route_health async function."""

    def _make_config_with_health(self, mock_interfaces):
        mock_interfaces.return_value = {"eth0": [(1, 0)]}
        return MySettings(
            table=200,
            ws_port=8080,
            pbr_mark=1,
            interfaces=["eth0"],
            routes=[],
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

    @patch("route.RouteObject.interfaces", new_callable=PropertyMock)
    async def test_unhealthy_to_healthy_calls_replay(self, mock_interfaces):
        """Health transition unhealthy->healthy calls replay_routes_for_interface."""
        import ipt_server.state
        import ipt_server.main

        config = self._make_config_with_health(mock_interfaces)

        mock_router = MagicMock()
        mock_health_source = MagicMock()

        # First call: unhealthy (initial poll), second call: healthy (transition)
        mock_health_source.get_interface_health.side_effect = [
            {"wg_uk": False},
            {"wg_uk": True},
        ]

        ipt_server.state.CONFIG = config
        ipt_server.state.ROUTER = mock_router
        ipt_server.state.INTERFACE_HEALTH.clear()

        stop_after = 0

        async def fake_sleep(_):
            nonlocal stop_after
            stop_after += 1
            if stop_after >= 2:
                raise asyncio.CancelledError()

        with patch(
            "ipt_server.tasks.route_health_monitor.asyncio.sleep",
            side_effect=fake_sleep,
        ):
            try:
                await ipt_server.main.monitor_route_health(mock_health_source)
            except asyncio.CancelledError:
                pass

        mock_router.replay_routes_for_interface.assert_called_once_with("wg_uk")
        mock_router.remove_routes_for_interface.assert_not_called()

    @patch("route.RouteObject.interfaces", new_callable=PropertyMock)
    async def test_healthy_to_unhealthy_calls_remove(self, mock_interfaces):
        """Health transition healthy->unhealthy calls remove_routes_for_interface."""
        import ipt_server.state
        import ipt_server.main

        config = self._make_config_with_health(mock_interfaces)

        mock_router = MagicMock()
        mock_health_source = MagicMock()

        # First call: healthy (initial poll), second call: unhealthy (transition)
        mock_health_source.get_interface_health.side_effect = [
            {"wg_uk": True},
            {"wg_uk": False},
        ]

        ipt_server.state.CONFIG = config
        ipt_server.state.ROUTER = mock_router
        ipt_server.state.INTERFACE_HEALTH.clear()

        stop_after = 0

        async def fake_sleep(_):
            nonlocal stop_after
            stop_after += 1
            if stop_after >= 2:
                raise asyncio.CancelledError()

        with patch(
            "ipt_server.tasks.route_health_monitor.asyncio.sleep",
            side_effect=fake_sleep,
        ):
            try:
                await ipt_server.main.monitor_route_health(mock_health_source)
            except asyncio.CancelledError:
                pass

        mock_router.remove_routes_for_interface.assert_called_once_with("wg_uk")
        mock_router.replay_routes_for_interface.assert_not_called()

    @patch("route.RouteObject.interfaces", new_callable=PropertyMock)
    async def test_stable_healthy_state_calls_no_route_methods(self, mock_interfaces):
        """Stable healthy->healthy state does NOT call replay or remove."""
        import ipt_server.state
        import ipt_server.main

        config = self._make_config_with_health(mock_interfaces)

        mock_router = MagicMock()
        mock_health_source = MagicMock()

        # Both polls healthy: no transition
        mock_health_source.get_interface_health.side_effect = [
            {"wg_uk": True},
            {"wg_uk": True},
        ]

        ipt_server.state.CONFIG = config
        ipt_server.state.ROUTER = mock_router
        ipt_server.state.INTERFACE_HEALTH.clear()

        stop_after = 0

        async def fake_sleep(_):
            nonlocal stop_after
            stop_after += 1
            if stop_after >= 2:
                raise asyncio.CancelledError()

        with patch(
            "ipt_server.tasks.route_health_monitor.asyncio.sleep",
            side_effect=fake_sleep,
        ):
            try:
                await ipt_server.main.monitor_route_health(mock_health_source)
            except asyncio.CancelledError:
                pass

        mock_router.replay_routes_for_interface.assert_not_called()
        mock_router.remove_routes_for_interface.assert_not_called()

    @patch("route.RouteObject.interfaces", new_callable=PropertyMock)
    async def test_none_source_returns_immediately(self, mock_interfaces):
        """monitor_route_health with None source returns without doing anything."""
        import ipt_server.state
        import ipt_server.main

        mock_router = MagicMock()
        ipt_server.state.ROUTER = mock_router

        # Should return immediately, no sleep, no calls
        with patch("ipt_server.tasks.route_health_monitor.asyncio.sleep") as mock_sleep:
            await ipt_server.main.monitor_route_health(None)

        mock_sleep.assert_not_called()
        mock_router.replay_routes_for_interface.assert_not_called()
        mock_router.remove_routes_for_interface.assert_not_called()


if __name__ == "__main__":
    unittest.main()
