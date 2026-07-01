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

    async def test_async_main_retains_all_monitor_tasks_across_gc(self):
        """Regression test for the vpn2 egress-loop-death bug.

        All four monitor tasks must be spawned via the retaining helper
        (ipt_main._spawn_background), and must still be alive (not
        GC-eligible) after a forced gc.collect() while still pending.
        """
        import gc
        import weakref

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

        async def _never_returns(*a, **k):
            # Await something with no external anchor so an unretained task
            # is genuinely GC-eligible after the first tick (mirrors the
            # live failure mode), while a retained task survives.
            while True:
                await asyncio.Event().wait()

        weak_refs = []
        real_spawn_background = ipt_main._spawn_background

        def tracking_spawn(coro):
            task = real_spawn_background(coro)
            weak_refs.append(weakref.ref(task))
            return task

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
            patch.object(ipt_main, "monitor_interfaces", side_effect=_never_returns),
            patch.object(ipt_main, "monitor_dns_backend", side_effect=_never_returns),
            patch.object(ipt_main, "monitor_route_health", side_effect=_never_returns),
            patch.object(ipt_main, "monitor_nexthops", side_effect=_never_returns),
            patch.object(ipt_main, "clean_pbr"),
            patch.object(ipt_main, "_spawn_background", side_effect=tracking_spawn),
        ):
            try:
                await asyncio.wait_for(ipt_main.async_main(), timeout=0.2)
            except (asyncio.TimeoutError, asyncio.CancelledError):
                pass

        try:
            self.assertEqual(
                len(weak_refs),
                4,
                "expected all four monitor tasks to be spawned via _spawn_background",
            )
            gc.collect()
            for ref in weak_refs:
                self.assertIsNotNone(
                    ref(),
                    "a monitor task was GC-eligible after gc.collect(); "
                    "_spawn_background must retain a strong reference for "
                    "the process lifetime",
                )
        finally:
            # Cleanup: these tasks sleep forever on an unset Event; cancel
            # them so they don't leak across tests / warn on loop teardown.
            for ref in weak_refs:
                task = ref()
                if task is not None:
                    task.cancel()
            live = [ref() for ref in weak_refs if ref() is not None]
            if live:
                await asyncio.gather(*live, return_exceptions=True)

    async def test_async_main_retains_pinning_http_task_across_gc(self):
        """Regression test: the pinning http_task returned by setup_pinning
        must also survive GC, not just be discarded as `_http_task`."""
        import gc
        import weakref

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
            pinning_egress={"usa": object()},
            pinning_ttl=86400,
            pinning_api_port=0,
        )

        fake_health_server = AsyncMock()
        fake_health_server.close = MagicMock()
        fake_health_server.wait_closed = AsyncMock()

        fake_ws_server = AsyncMock()
        fake_ws_server.close = MagicMock()
        fake_ws_server.wait_closed = AsyncMock()

        async def _quick(*a, **k):
            return None

        fake_manager = MagicMock()
        fake_reconciler = MagicMock()

        async def _never_returns():
            while True:
                await asyncio.Event().wait()

        http_task_holder = {}

        async def fake_setup_pinning(cfg, interfaces_view, stop_event):
            t = asyncio.create_task(_never_returns())
            http_task_holder["task"] = t
            return fake_manager, fake_reconciler, t

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
            patch.object(ipt_main, "monitor_interfaces", side_effect=_quick),
            patch.object(ipt_main, "monitor_dns_backend", side_effect=_quick),
            patch.object(ipt_main, "monitor_route_health", side_effect=_quick),
            patch.object(ipt_main, "monitor_nexthops", side_effect=_quick),
            patch.object(ipt_main, "clean_pbr"),
            patch("pinning.bootstrap.setup_pinning", side_effect=fake_setup_pinning),
        ):
            try:
                await asyncio.wait_for(ipt_main.async_main(), timeout=0.2)
            except (asyncio.TimeoutError, asyncio.CancelledError):
                pass

        try:
            self.assertIn("task", http_task_holder, "setup_pinning was never called")
            task = http_task_holder["task"]
            ref = weakref.ref(task)
            del task
            gc.collect()
            self.assertIsNotNone(
                ref(),
                "pinning http_task was GC-eligible; async_main must retain "
                "a strong reference to it (not discard it as `_http_task`)",
            )
            self.assertIn(
                ref(),
                ipt_main._BACKGROUND_TASKS,
                "pinning http_task must be tracked in the same retention "
                "container as the monitor tasks",
            )
            self.assertIs(state_mod.PINNING_MANAGER, fake_manager)
            self.assertIs(state_mod.PINNING_RECONCILER, fake_reconciler)
        finally:
            task = http_task_holder.get("task")
            if task is not None and not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)

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
