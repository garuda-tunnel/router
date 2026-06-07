"""Contract tests for monitor import paths and async runtime wiring."""

import asyncio
import unittest
from unittest.mock import patch, MagicMock, AsyncMock
from types import SimpleNamespace


class TestMonitorImportWiring(unittest.IsolatedAsyncioTestCase):
    """Contract tests for monitor import paths and async runtime wiring."""

    async def test_async_main_schedules_four_monitor_tasks(self):
        """async_main calls all four monitor coroutines; real asyncio scheduling is used."""
        import ipt_server.main as ipt_main
        import ipt_server.state as state_mod

        fake_router = MagicMock()
        fake_router._nhg_registry = MagicMock()
        fake_router._member_nhids = MagicMock()

        state_mod.ROUTER = fake_router
        state_mod.CONFIG = SimpleNamespace(
            ws_port=8765,
            route_health=SimpleNamespace(interfaces={}),
            pbr_mark=0x64,
            table=100,
        )

        fake_health_server = AsyncMock()
        fake_health_server.close = MagicMock()
        fake_health_server.wait_closed = AsyncMock()

        fake_ws_server = AsyncMock()
        fake_ws_server.close = MagicMock()
        fake_ws_server.wait_closed = AsyncMock()

        mock_monitor_interfaces = AsyncMock(return_value=None)
        mock_monitor_dns = AsyncMock(return_value=None)
        mock_monitor_health = AsyncMock(return_value=None)
        mock_monitor_nexthops = AsyncMock(return_value=None)

        with (
            patch.object(
                ipt_main.asyncio,
                "start_server",
                new_callable=AsyncMock,
                return_value=fake_health_server,
            ),
            patch.object(
                ipt_main.websockets,
                "serve",
                new_callable=AsyncMock,
                return_value=fake_ws_server,
            ),
            patch(
                "ipt_server.tasks.interface_monitor.refresh_interfaces_snapshot",
                new_callable=AsyncMock,
            ),
            patch.object(ipt_main, "build_route_health_source", return_value=None),
            patch.object(ipt_main, "monitor_interfaces", mock_monitor_interfaces),
            patch.object(ipt_main, "monitor_dns_backend", mock_monitor_dns),
            patch.object(ipt_main, "monitor_route_health", mock_monitor_health),
            patch.object(ipt_main, "monitor_nexthops", mock_monitor_nexthops),
            patch.object(ipt_main, "clean_pbr"),
        ):
            try:
                await asyncio.wait_for(ipt_main.async_main(), timeout=0.3)
            except (asyncio.TimeoutError, asyncio.CancelledError):
                pass

        mock_monitor_interfaces.assert_called_once()
        mock_monitor_dns.assert_called_once()
        mock_monitor_health.assert_called_once()
        mock_monitor_nexthops.assert_called_once()

        # stop_event must be the same object passed to all monitors that accept it
        stop_event = mock_monitor_interfaces.call_args.args[0]
        self.assertIs(stop_event, mock_monitor_dns.call_args.args[0])
        self.assertIs(stop_event, mock_monitor_nexthops.call_args.args[2])

    def test_monitor_interfaces_imports_shared_runtime_state(self):
        """Regression guard: monitor_interfaces reads state from ipt_server.state."""
        import asyncio
        import ipt_server.state as state_mod
        from ipt_server.tasks.interface_monitor import monitor_interfaces
        from unittest.mock import MagicMock, patch

        fake_config = MagicMock()
        fake_config.interfaces = ["eth0"]
        fake_config.routes = []

        fake_router = MagicMock()

        async def _run():
            stop = asyncio.Event()
            stop.set()
            with (
                patch.object(state_mod, "CONFIG", fake_config),
                patch.object(state_mod, "INTERFACE_HEALTH", {}),
                patch.object(state_mod, "ROUTER", fake_router),
                patch("ipt_server.main.apply_pbr"),
                patch("ipt_server.tasks.interface_monitor.IPRoute") as mock_ipr,
            ):
                mock_ipr.return_value.__enter__ = MagicMock(
                    return_value=MagicMock(get_links=lambda: [])
                )
                mock_ipr.return_value.__exit__ = MagicMock(return_value=False)
                await monitor_interfaces(stop)

        try:
            asyncio.run(_run())
        except ImportError as e:
            self.fail(f"monitor_interfaces raised ImportError: {e}")


if __name__ == "__main__":
    unittest.main()
