"""Contract tests for async runtime wiring in ipt_server.main.

Synchronization contract of these tests: async_main is never stopped by a
wall-clock timeout. Either a fake monitor sets the shared stop_event when
called (clean shutdown through the finally block), or the async_main task
is cancelled after every spawned monitor has verifiably started. The M7
test drives a real interface-monitor tick against known shared state.
"""

import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch


def _monitor_setting_stop(delay_first_await=False):
    """Return a fake monitor factory that sets any asyncio.Event argument."""
    calls = []

    def factory(*args, **kwargs):
        events = [a for a in args if isinstance(a, asyncio.Event)]

        async def _run():
            if delay_first_await:
                await asyncio.sleep(0)
            calls.append(args)
            for event in events:
                event.set()

        return _run()

    factory.calls = calls
    return factory


def _never_returns_monitor():
    """Return a fake monitor that never finishes (mirrors production loops)."""

    def factory(*args, **kwargs):
        async def _run():
            while True:
                await asyncio.Event().wait()

        return _run()

    return factory


def _fake_servers():
    health = AsyncMock()
    health.close = MagicMock()
    health.wait_closed = AsyncMock()
    ws = AsyncMock()
    ws.close = MagicMock()
    ws.wait_closed = AsyncMock()
    return health, ws


def _minimal_state_config(**extra):
    cfg = SimpleNamespace(
        ws_port=8765,
        route_health=SimpleNamespace(interfaces={}),
        pbr_mark=0x64,
        table=100,
    )
    for key, value in extra.items():
        setattr(cfg, key, value)
    return cfg


class TestAsyncMainWiring(unittest.IsolatedAsyncioTestCase):
    async def test_async_main_schedules_four_monitor_tasks_and_stops_cleanly(self):
        """async_main awaits all four monitor coroutines with real asyncio
        scheduling; a monitor-side stop_event ends the runtime through the
        graceful shutdown path (servers closed, no cancellation)."""
        import ipt_server.main as ipt_main
        import ipt_server.state as state_mod

        state_mod.ROUTER = MagicMock()
        state_mod.ROUTER._nhg_registry = {}
        state_mod.ROUTER._member_nhids = {}
        state_mod.CONFIG = _minimal_state_config()

        fake_health_server, fake_ws_server = _fake_servers()

        monitors = {
            name: _monitor_setting_stop()
            for name in (
                "monitor_interfaces",
                "monitor_dns_backend",
                "monitor_route_health",
                "monitor_nexthops",
            )
        }

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
            patch.object(ipt_main, "clean_pbr"),
        ):
            # plain-function patches: the factory returns the coroutine that
            # _spawn_background schedules (an AsyncMock side_effect would
            # swallow it instead of returning it).
            for name, factory in monitors.items():
                patcher = patch.object(ipt_main, name, new=factory)
                patcher.start()
                self.addCleanup(patcher.stop)

            await asyncio.wait_for(ipt_main.async_main(), timeout=5.0)

        for name, factory in monitors.items():
            self.assertEqual(len(factory.calls), 1, f"{name} awaited once")

        # stop_event must be the same object passed to all monitors that accept it
        stop_event = monitors["monitor_interfaces"].calls[0][0]
        self.assertIs(stop_event, monitors["monitor_dns_backend"].calls[0][0])
        self.assertIs(stop_event, monitors["monitor_nexthops"].calls[0][2])
        fake_ws_server.close.assert_called_once()
        fake_health_server.close.assert_called_once()

    async def test_async_main_retains_all_monitor_tasks_across_gc(self):
        """Regression test for the live egress-loop-death bug.

        All four monitor tasks must be spawned via the retaining helper
        (ipt_main._spawn_background), and must still be alive (not
        GC-eligible) after a forced gc.collect() while still pending.
        async_main is cancelled only after every monitor has started.
        """
        import gc
        import weakref

        import ipt_server.main as ipt_main
        import ipt_server.state as state_mod

        state_mod.ROUTER = MagicMock()
        state_mod.ROUTER._nhg_registry = {}
        state_mod.ROUTER._member_nhids = {}
        state_mod.CONFIG = _minimal_state_config()

        fake_health_server, fake_ws_server = _fake_servers()

        started = asyncio.Event()
        started_count = {"n": 0}

        def factory(*args, **kwargs):
            async def _run():
                started_count["n"] += 1
                if started_count["n"] == 4:
                    started.set()
                # Await something with no external anchor so an unretained
                # task is genuinely GC-eligible (mirrors the live failure).
                while True:
                    await asyncio.Event().wait()

            return _run()

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
            patch.object(ipt_main, "monitor_interfaces", new=factory),
            patch.object(ipt_main, "monitor_dns_backend", new=factory),
            patch.object(ipt_main, "monitor_route_health", new=factory),
            patch.object(ipt_main, "monitor_nexthops", new=factory),
            patch.object(ipt_main, "clean_pbr"),
            patch.object(ipt_main, "_spawn_background", side_effect=tracking_spawn),
        ):
            main_task = asyncio.create_task(ipt_main.async_main())
            await asyncio.wait_for(started.wait(), timeout=5.0)
            main_task.cancel()
            await asyncio.gather(main_task, return_exceptions=True)

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
        must also survive GC and be tracked in the retention container."""
        import gc
        import weakref

        import ipt_server.main as ipt_main
        import ipt_server.state as state_mod

        state_mod.ROUTER = MagicMock()
        state_mod.ROUTER._nhg_registry = {}
        state_mod.ROUTER._member_nhids = {}
        state_mod.CONFIG = _minimal_state_config(
            pinning_egress={"usa": object()},
            pinning_ttl=86400,
            pinning_api_port=0,
        )

        fake_health_server, fake_ws_server = _fake_servers()

        monitors = {
            name: _monitor_setting_stop()
            for name in (
                "monitor_interfaces",
                "monitor_dns_backend",
                "monitor_route_health",
                "monitor_nexthops",
            )
        }

        fake_manager = MagicMock()
        fake_reconciler = MagicMock()
        http_task_holder = {}

        async def fake_setup_pinning(cfg, interfaces_view, stop_event):
            async def _never_returns():
                while True:
                    await asyncio.Event().wait()

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
            patch.object(ipt_main, "clean_pbr"),
            patch("pinning.bootstrap.setup_pinning", side_effect=fake_setup_pinning),
        ):
            for name, factory in monitors.items():
                patcher = patch.object(ipt_main, name, new=factory)
                patcher.start()
                self.addCleanup(patcher.stop)

            await asyncio.wait_for(ipt_main.async_main(), timeout=5.0)

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


class TestAsyncMainStartupHealth(unittest.IsolatedAsyncioTestCase):
    async def test_unhealthy_startup_interface_removes_its_routes(self):
        """Startup: a gated interface that is unhealthy at boot has its
        routes removed before the runtime starts serving."""
        import ipt_server.main as ipt_main
        import ipt_server.state as state_mod

        state_mod.ROUTER = MagicMock()
        state_mod.ROUTER._nhg_registry = {}
        state_mod.ROUTER._member_nhids = {}
        state_mod.CONFIG = _minimal_state_config(
            route_health=SimpleNamespace(interfaces={"wg_uk": object()})
        )
        state_mod.INTERFACE_HEALTH.clear()

        fake_health_server, fake_ws_server = _fake_servers()
        removed = []

        def remove_routes(iface):
            removed.append(iface)

        state_mod.ROUTER.remove_routes_for_interface = remove_routes

        fake_source = MagicMock()
        fake_source.get_interface_health.return_value = {"wg_uk": False}

        monitors = {
            name: _monitor_setting_stop()
            for name in (
                "monitor_interfaces",
                "monitor_dns_backend",
                "monitor_route_health",
                "monitor_nexthops",
            )
        }

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
            patch.object(ipt_main, "build_route_health_source", return_value=fake_source),
            patch.object(ipt_main, "clean_pbr"),
        ):
            for name, factory in monitors.items():
                patcher = patch.object(ipt_main, name, new=factory)
                patcher.start()
                self.addCleanup(patcher.stop)

            await asyncio.wait_for(ipt_main.async_main(), timeout=5.0)

        self.assertEqual(removed, ["wg_uk"])
        self.assertEqual(state_mod.INTERFACE_HEALTH, {"wg_uk": False})


class TestMonitorInterfacesSharedState(unittest.IsolatedAsyncioTestCase):
    """M7: monitor_interfaces must act on shared runtime state, not merely
    import it. One controlled tick is driven against a known link state
    through fake IPRoute/nft/ip -batch boundaries, with a REAL Router and
    the REAL apply_pbr in the path (the same shape as the flush/replay
    scenario in tests/test_router_route_classes.py::TestFlushReplay);
    the published snapshot, the applied PBR ruleset and the replayed
    route commands are asserted."""

    @staticmethod
    def _link(ifindex, name, operstate="up"):
        """Build a pyroute2-shaped link (dict subclass with get_attr)."""

        class _Link(dict):
            def __init__(self):
                super().__init__(index=ifindex)
                self._attrs = {"IFLA_IFNAME": name, "IFLA_OPERSTATE": operstate}

            def get_attr(self, key):
                return self._attrs.get(key)

        return _Link()

    async def test_first_tick_publishes_snapshot_applies_pbr_and_replays(self):
        import threading

        from route import DNS_METRIC, DNS_PROTO, STATIC_METRIC, STATIC_PROTO
        from tests.support import hermetic_router, stop_workers

        from ipt_server import state
        from ipt_server import main as ipt_main
        from ipt_server.tasks import interface_monitor

        batches = []

        def fake_run(argv, **kwargs):
            if argv[:2] == ["ip", "-batch"]:
                batches.append(kwargs.get("input") or "")
            return MagicMock(returncode=0, stderr="", stdout="")

        def nft_cmd(cmd, *args, **kwargs):
            if cmd == "list tables":
                return (0, {"data": []}, "")
            return (0, "", "")

        nft = MagicMock()
        nft.cmd.side_effect = nft_cmd
        ipr = MagicMock()
        # `with IPRoute() as ipr:` must bind the same object so rule/get_rules
        # calls are observable on it.
        ipr.__enter__ = MagicMock(return_value=ipr)
        ipr.__exit__ = MagicMock(return_value=False)
        ipr.get_rules.return_value = []

        cfg = SimpleNamespace(
            table=200,
            clean_conntrack=False,
            domain_route_ttl=300,
            routes=[],
            interfaces=["backbone"],
            pbr_mark=512,
            dns_mark=513,
        )

        links = [self._link(1, "lo"), self._link(5, "backbone")]

        def make_iproute():
            class _FakeIPRoute:
                def __enter__(self):
                    return self

                def __exit__(self, *exc):
                    return False

                def get_links(self):
                    return list(links)

            return _FakeIPRoute

        # The replay lands inside the first tick; signal completion through
        # the real Router.replay_routes so the scenario stops deterministically.
        replay_done = threading.Event()

        with (
            patch.object(interface_monitor, "_INTERVAL_SECONDS", 0.05),
            patch.object(interface_monitor, "IPRoute", make_iproute()),
            patch.object(ipt_main, "IPRoute", return_value=ipr),
            patch.object(ipt_main.nftables, "Nftables", return_value=nft),
            patch("subprocess.run", side_effect=fake_run),
        ):
            with hermetic_router(cfg) as router:
                real_replay = router.replay_routes

                def replay_signal():
                    real_replay()
                    replay_done.set()

                router.replay_routes = replay_signal
                router.add_route(_static_route("203.0.113.0/24"))
                router.add_route(
                    _dns_route("192.0.2.40/32", ttl=300), immediate=True
                )

                saved = (state.CONFIG, state.ROUTER)
                state.CONFIG, state.ROUTER = cfg, router
                try:

                    async def scenario():
                        stop = asyncio.Event()
                        task = asyncio.create_task(
                            interface_monitor.monitor_interfaces(stop)
                        )
                        await asyncio.to_thread(replay_done.wait, 5.0)
                        stop.set()
                        await asyncio.wait_for(task, timeout=5.0)

                    await asyncio.wait_for(scenario(), timeout=10.0)
                finally:
                    state.CONFIG, state.ROUTER = saved

        # Published snapshot: the tick's link state is in shared state.
        self.assertEqual(state.INTERFACES, {"lo": 1, "backbone": 5})
        # PBR effect: the real apply_pbr added the fwmark rule and applied
        # the rendered nft ruleset through the faked boundary.
        ipr.rule.assert_called_with("add", fwmark=512, table=200)
        applied_ruleset = nft.cmd.call_args_list[-1].args[0]
        self.assertIn("table ip ipt_server_pbr", applied_ruleset)
        # Route effect: both in-memory classes were replayed after the flush.
        replayed = [
            b for b in batches if "proto 4 metric 100" in b and "proto 250 metric 200" in b
        ]
        self.assertTrue(
            replayed, f"expected both classes replayed in one batch, got: {batches}"
        )


def _static_route(net, nhid=4):
    import ipaddress

    from route import STATIC_METRIC, STATIC_PROTO, RouteObject

    return RouteObject(
        net=ipaddress.IPv4Network(net),
        nhid=nhid,
        proto=STATIC_PROTO,
        metric=STATIC_METRIC,
    )


def _dns_route(net, ttl=300, nhid=4):
    import ipaddress

    from route import DNS_METRIC, DNS_PROTO, RouteObject

    r = _static_route(net, nhid)
    r.proto = DNS_PROTO
    r.metric = DNS_METRIC
    r.ttl = ttl
    return r


if __name__ == "__main__":
    unittest.main()
