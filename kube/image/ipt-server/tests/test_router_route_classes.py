"""Route-class behavior: STATIC (config) and DNS-learned routes coexist (#68).

Operator decision for router-internal#68: static and DNS-learned routes are
separated by kernel route class (proto + metric), not by "ttl is None". On a
same-/32 collision both kernel entries coexist (FIB key includes priority);
the STATIC entry has the lower metric and wins. Replaces the #61/#62
"permanent wins" logic and its tests (test_router_static_route_ttl.py).

Verified kernel semantics (root netns experiment, iproute2 CLI — the same
commands this code issues): (table, dst, tos, priority) is the FIB key, a
`route del` carrying proto+metric removes only its own entry, and the lower
metric is preferred for forwarding.
"""

import ipaddress
import threading
import unittest
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import time

import Config
from Router import Router
from dns_records import ARecord
from route import (
    DNS_METRIC,
    DNS_PROTO,
    STATIC_METRIC,
    STATIC_PROTO,
    RouteObject,
)


class _Clock(datetime):
    """datetime stand-in for route.py whose now() runs `offset` seconds ahead."""

    offset = 0.0

    @classmethod
    def now(cls, tz=None):
        return datetime.fromtimestamp(time.time() + cls.offset, tz)


def _make_router() -> Router:
    cfg = SimpleNamespace(
        table=200,
        clean_conntrack=False,
        domain_route_ttl=300,
        routes=[],
        interfaces=[],
    )
    router = Router(cfg, ipdb=MagicMock())
    router._conntrack = MagicMock()
    router._conntrack.dump_entries.return_value = []
    return router


def _drain_queue(router: Router) -> list:
    items = []
    while True:
        try:
            items.append(router._route_queue.get_nowait())
        except Exception:
            return items


def _stop_workers(router: Router) -> None:
    router._shutdown_event.set()
    router._route_thread.join(timeout=2.0)
    router._cleanup_thread.join(timeout=2.0)


def _intervals_for(router: Router, route: RouteObject) -> list:
    return list(router._route_tree[route.net_start : route.net_end + 1])


def _static(net: str, nhid: int = 4) -> RouteObject:
    """Build a route exactly as Router._load_routes builds a NetRule route."""
    return RouteObject(
        net=ipaddress.IPv4Network(net),
        nhid=nhid,
        proto=STATIC_PROTO,
        metric=STATIC_METRIC,
    )


def _dns(net: str, nhid: int = 4, ttl=300) -> RouteObject:
    """Build a route exactly as Router.on_a_record builds a domain route."""
    r = _static(net, nhid)
    r.proto = DNS_PROTO
    r.metric = DNS_METRIC
    r.ttl = ttl
    return r


class TestStaticAndDnsCoexist(unittest.TestCase):
    def setUp(self):
        _Clock.offset = 0.0

    tearDown = setUp

    def test_collision_keeps_both_entries_and_dns_expiry_removes_only_dns(self):
        """Static /32 + DNS answer for the same /32: both installed with their
        class metrics; expiry deletes only the DNS entry, static stays."""
        with patch("route.datetime", _Clock), patch("subprocess.run"):
            router = _make_router()
            try:
                inserted_static = router.add_route(_static("192.0.2.10/32"))
                inserted_dns = router.add_route(_dns("192.0.2.10/32", ttl=300))

                self.assertIsNone(inserted_static.expiration)
                self.assertIsNotNone(inserted_dns.expiration)

                intervals = _intervals_for(router, inserted_static)
                self.assertEqual(
                    {i.data.metric for i in intervals},
                    {STATIC_METRIC, DNS_METRIC},
                    "both class entries must coexist in the route tree",
                )
                self.assertLess(
                    STATIC_METRIC,
                    DNS_METRIC,
                    "static must have the lower (winning) metric",
                )
                self.assertEqual(
                    {i.data.proto for i in intervals}, {STATIC_PROTO, DNS_PROTO}
                )

                # Past the DNS TTL the static entry must not be expired, the
                # DNS entry must be, and cleanup must queue exactly one del.
                _Clock.offset = 3600.0
                by_metric = {i.data.metric: i for i in intervals}
                self.assertFalse(by_metric[STATIC_METRIC].data.expired)
                self.assertTrue(by_metric[DNS_METRIC].data.expired)

                _stop_workers(router)
                _drain_queue(router)
                router._cleanup_pass()
                queued = _drain_queue(router)
                self.assertEqual(len(queued), 1)
                cmd, spec = queued[0]
                self.assertEqual(cmd, "del")
                self.assertEqual(spec["dst"], "192.0.2.10")
                self.assertEqual(spec["proto"], DNS_PROTO)
                self.assertEqual(spec["priority"], DNS_METRIC)

                remaining = _intervals_for(router, inserted_static)
                self.assertEqual(len(remaining), 1)
                self.assertEqual(remaining[0].data.proto, STATIC_PROTO)
                self.assertIsNone(remaining[0].data.expiration)
            finally:
                _stop_workers(router)

    def test_dns_refresh_does_not_touch_static_interval(self):
        """A DNS refresh never refreshes/resets the STATIC interval."""
        with patch("route.datetime", _Clock), patch("subprocess.run"):
            router = _make_router()
            try:
                router.add_route(_static("192.0.2.11/32"))
                router.add_route(_dns("192.0.2.11/32", ttl=300))

                _Clock.offset = 50.0
                refreshed = router.add_route(_dns("192.0.2.11/32", ttl=60))
                self.assertEqual(refreshed.metric, DNS_METRIC)

                intervals = _intervals_for(router, _static("192.0.2.11/32"))
                self.assertEqual(len(intervals), 2)
                for interval in intervals:
                    if interval.data.proto == STATIC_PROTO:
                        self.assertIsNone(
                            interval.data.ttl,
                            "static interval must keep ttl None",
                        )
                        self.assertIsNone(
                            interval.data.expiration,
                            "static interval must never gain an expiration",
                        )
                    else:
                        self.assertEqual(interval.data.ttl, 300)
                        self.assertIsNotNone(interval.data.expiration)
            finally:
                _stop_workers(router)

    def test_static_never_expires_even_with_expiration_set(self):
        """RouteObject.expired is always False for the STATIC class."""
        route = _static("192.0.2.12/32")
        route.expiration = datetime.now()  # defensive: even if set
        self.assertFalse(route.expired)

    def test_same_class_dns_refresh_keeps_max_ttl_semantics(self):
        """DNS-vs-DNS refresh keeps the pre-#62 reset_expiration semantics."""
        with patch("route.datetime", _Clock), patch("subprocess.run"):
            router = _make_router()
            try:
                first = router.add_route(_dns("192.0.2.13/32", ttl=300))
                self.assertEqual(first.ttl, 300)

                _Clock.offset = 100.0
                second = router.add_route(_dns("192.0.2.13/32", ttl=120))
                self.assertEqual(second.ttl, 300, "refresh keeps the max TTL")
                self.assertGreater(second.expiration, first.expiration)

                _Clock.offset = 500.0  # past the renewed expiration
                _stop_workers(router)
                _drain_queue(router)
                router._cleanup_pass()
                queued = _drain_queue(router)
                self.assertEqual(len(queued), 1)
                self.assertEqual(queued[0][0], "del")
                self.assertEqual(queued[0][1]["proto"], DNS_PROTO)
                self.assertEqual(_intervals_for(router, first), [])
            finally:
                _stop_workers(router)


class TestDnsTtlFallback(unittest.TestCase):
    def _router_with_domain_rule(self) -> Router:
        group = Config.RouteActionGroup(
            route=[Config.RouteMember(dev="backbone")],
            rules=[Config.DomainRule(domain=r"probe\.example\.net")],
        )
        cfg = SimpleNamespace(
            table=200,
            clean_conntrack=False,
            domain_route_ttl=300,
            routes=[group],
            interfaces=[],
        )
        router = Router(cfg, ipdb=MagicMock())
        router._conntrack = MagicMock()
        router._conntrack.dump_entries.return_value = []
        router._nhg_registry[group.nhg_descriptor] = 7
        return router

    @staticmethod
    def _record(ip: str, ttl) -> ARecord:
        return ARecord(
            {
                "type": 1,
                "name": "probe.example.net",
                "query": "probe.example.net",
                "content": ip,
                "ttl": ttl,
            }
        )

    def test_ttl_zero_falls_back_to_domain_route_ttl(self):
        with patch("route.datetime", _Clock), patch("subprocess.run"):
            router = self._router_with_domain_rule()
            try:
                rv = router.on_a_record(self._record("192.0.2.77", 0))
                self.assertEqual(rv["ttl"], 300)

                intervals = _intervals_for(router, _static("192.0.2.77/32"))
                self.assertEqual(len(intervals), 1)
                self.assertEqual(intervals[0].data.proto, DNS_PROTO)
                self.assertEqual(intervals[0].data.metric, DNS_METRIC)
                self.assertEqual(intervals[0].data.ttl, 300)
                self.assertIsNotNone(
                    intervals[0].data.expiration,
                    "a DNS route must never be permanent",
                )
            finally:
                _stop_workers(router)

    def test_missing_ttl_falls_back_to_domain_route_ttl(self):
        with patch("route.datetime", _Clock), patch("subprocess.run"):
            router = self._router_with_domain_rule()
            try:
                rv = router.on_a_record(self._record("192.0.2.78", None))
                self.assertEqual(rv["ttl"], 300)
                intervals = _intervals_for(router, _static("192.0.2.78/32"))
                self.assertEqual(intervals[0].data.ttl, 300)
                self.assertIsNotNone(intervals[0].data.expiration)
            finally:
                _stop_workers(router)


class TestKernelCommandClassTags(unittest.TestCase):
    def test_del_and_replace_queue_commands_carry_proto_and_metric(self):
        """The queue worker renders class proto+metric into every ip line."""
        with patch("subprocess.run") as run:
            run.return_value = MagicMock(returncode=0, stderr="")
            router = _make_router()
            try:
                router._route_queue.put(
                    (
                        "del",
                        {
                            "dst": "192.0.2.10",
                            "dst_len": 32,
                            "proto": DNS_PROTO,
                            "priority": DNS_METRIC,
                        },
                    )
                )
                deadline = time.time() + 2.0
                while time.time() < deadline and not run.called:
                    time.sleep(0.02)
                batch = run.call_args[1]["input"]
                self.assertIn(
                    "route del 192.0.2.10/32 table 200 proto 250 metric 200", batch
                )
            finally:
                _stop_workers(router)

    def test_immediate_apply_and_replay_carry_proto_and_metric(self):
        with patch("subprocess.run") as run:
            run.return_value = MagicMock(returncode=0, stderr="")
            router = _make_router()
            try:
                router.add_route(_dns("192.0.2.10/32"), immediate=True)
                batch = run.call_args[1]["input"]
                self.assertIn(
                    "route replace 192.0.2.10/32 nhid 4 table 200 "
                    "proto 250 metric 200",
                    batch,
                )

                router.add_route(_static("203.0.113.0/24"), immediate=False)
                _stop_workers(router)
                _drain_queue(router)
                run.reset_mock()
                run.return_value = MagicMock(returncode=0, stderr="")
                router.replay_routes()
                batch = run.call_args[1]["input"]
                self.assertIn(
                    "route replace 203.0.113.0/24 nhid 4 table 200 "
                    "proto 4 metric 100",
                    batch,
                )
                self.assertNotIn("proto 3", batch)
            finally:
                _stop_workers(router)


class TestLoadRoutesStaticClass(unittest.TestCase):
    def test_load_routes_installs_static_class(self):
        """Config NetRule routes are installed with the STATIC class tags."""
        group = Config.RouteActionGroup(
            route=[Config.RouteMember(dev="backbone")],
            rules=[Config.NetRule(net="203.0.113.0/24")],
        )
        cfg = SimpleNamespace(
            table=200,
            clean_conntrack=False,
            domain_route_ttl=300,
            routes=[group],
            interfaces=[],
        )
        with patch("subprocess.run"):
            router = Router(cfg, ipdb=MagicMock())
            router._conntrack = MagicMock()
            router._nhg_registry[group.nhg_descriptor] = 9
            try:
                router._load_routes()
            finally:
                _stop_workers(router)

        intervals = _intervals_for(router, _static("203.0.113.0/24"))
        self.assertEqual(len(intervals), 1)
        self.assertEqual(intervals[0].data.proto, STATIC_PROTO)
        self.assertEqual(intervals[0].data.metric, STATIC_METRIC)
        self.assertIsNone(intervals[0].data.expiration)


class TestQueuedDeleteRace(unittest.TestCase):
    """PR #69 review: an expiry-queued delete must not remove a re-added
    route, and a same-class refresh must re-apply the kernel entry."""

    def setUp(self):
        _Clock.offset = 0.0

    tearDown = setUp

    def test_immediate_refresh_reapplies_to_kernel(self):
        """An immediate same-class refresh re-applies (idempotent replace)."""
        with patch("route.datetime", _Clock), patch("subprocess.run") as run:
            run.return_value = MagicMock(returncode=0, stderr="")
            router = _make_router()
            try:
                router.add_route(_dns("192.0.2.32/32", ttl=300), immediate=True)
                run.reset_mock()

                refreshed = router.add_route(
                    _dns("192.0.2.32/32", ttl=120), immediate=True
                )
                self.assertEqual(refreshed.ttl, 300)
                self.assertIn(
                    "route replace 192.0.2.32/32 nhid 4 table 200 proto 250 metric 200",
                    run.call_args[1]["input"],
                    "refresh must re-apply the kernel route",
                )
            finally:
                _stop_workers(router)

    def test_stale_queued_delete_spares_replacement_and_refresh_reapplies(self):
        """Deterministic race replay: worker paused, expire + queue delete,
        install replacement, run the old delete, send an identical answer —
        the kernel spec stays present; the STATIC route is untouched."""
        with patch("route.datetime", _Clock), patch("subprocess.run") as run:
            run.return_value = MagicMock(returncode=0, stderr="")
            router = _make_router()
            _stop_workers(router)  # pause the worker for deterministic steps
            try:
                router.add_route(_static("192.0.2.31/32"))
                _drain_queue(router)
                router.add_route(_dns("192.0.2.30/32", ttl=300), immediate=True)
                self.assertIn("route replace 192.0.2.30/32", run.call_args[1]["input"])

                # Expire the DNS route; the sweep queues its delete.
                _Clock.offset = 3600.0
                router._cleanup_pass()
                queued = _drain_queue(router)
                self.assertEqual([cmd for cmd, _ in queued], ["del"])

                # A new identical answer re-inserts and installs the route.
                _Clock.offset = 0.0
                run.reset_mock()
                router.add_route(_dns("192.0.2.30/32", ttl=300), immediate=True)
                self.assertIn("route replace 192.0.2.30/32", run.call_args[1]["input"])

                # Run the (now stale) queued delete through the real worker.
                router._route_queue.put(queued[0])
                router._shutdown_event.clear()
                worker = threading.Thread(
                    target=router._process_route_commands_iproute2, daemon=True
                )
                worker.start()
                deadline = time.time() + 2.0
                while time.time() < deadline and not router._route_queue.empty():
                    time.sleep(0.01)
                time.sleep(0.1)  # let the batch branch run
                router._shutdown_event.set()
                worker.join(timeout=2.0)

                del_batches = [
                    c.kwargs["input"]
                    for c in run.call_args_list
                    if "route del" in (c.kwargs.get("input") or "")
                ]
                self.assertEqual(del_batches, [], "stale delete must be skipped")

                # A later identical answer (refresh) re-applies the route.
                run.reset_mock()
                refreshed = router.add_route(
                    _dns("192.0.2.30/32", ttl=300), immediate=True
                )
                self.assertEqual(refreshed.ttl, 300)
                self.assertIn(
                    "route replace 192.0.2.30/32 nhid 4 table 200 proto 250 metric 200",
                    run.call_args[1]["input"],
                )

                # STATIC untouched throughout.
                intervals = _intervals_for(router, _static("192.0.2.31/32"))
                self.assertEqual(len(intervals), 1)
                self.assertEqual(intervals[0].data.proto, STATIC_PROTO)
                self.assertIsNone(intervals[0].data.expiration)
            finally:
                _stop_workers(router)

    def test_insert_attempted_after_validation_is_not_deleted(self):
        """Validation and the kernel delete are one critical section: an
        insert that starts while the delete batch is executing blocks on
        _routes_lock and applies after the delete — never before it."""
        with patch("route.datetime", _Clock), patch("subprocess.run") as run:
            run.return_value = MagicMock(returncode=0, stderr="")
            router = _make_router()
            _stop_workers(router)
            try:
                router.add_route(_dns("192.0.2.33/32", ttl=300), immediate=True)
                _drain_queue(router)

                # Expire the DNS route; the sweep queues its delete. Do NOT
                # re-add it yet: the insert must happen after the worker's
                # stale-check has already validated the delete.
                _Clock.offset = 3600.0
                router._cleanup_pass()
                queued = _drain_queue(router)
                self.assertEqual([cmd for cmd, _ in queued], ["del"])

                # Slow down the first kernel batch (the delete) so the insert
                # provably overlaps the worker's critical section.
                journal = []
                real_batch = router._ip_batch
                first = {"done": False}
                insert_done = threading.Event()

                def slow_batch(lines):
                    if not first["done"]:
                        first["done"] = True
                        time.sleep(0.3)  # worker holds the section; insert blocks
                        inserter = threading.Thread(
                            target=lambda: (
                                router.add_route(
                                    _dns("192.0.2.33/32", ttl=300), immediate=True
                                ),
                                insert_done.set(),
                            ),
                            daemon=True,
                        )
                        inserter.start()
                        # give the inserter time to run (it must block on
                        # the lock while we still hold it)
                        time.sleep(0.2)
                    real_batch(lines)
                    journal.append(tuple(lines))

                router._ip_batch = slow_batch
                router._route_queue.put(queued[0])
                router._shutdown_event.clear()
                worker = threading.Thread(
                    target=router._process_route_commands_iproute2, daemon=True
                )
                worker.start()
                # Wait until the journal shows both batches (delete + insert).
                deadline = time.time() + 5.0
                while time.time() < deadline and len(journal) < 2:
                    time.sleep(0.02)
                router._shutdown_event.set()
                worker.join(timeout=2.0)
                insert_done.wait(timeout=2.0)

                self.assertEqual(
                    len(journal),
                    2,
                    "both the delete and the re-insert replace must execute",
                )
                self.assertEqual(
                    journal[0],
                    ("route del 192.0.2.33/32 table 200 proto 250 metric 200",),
                    "the delete must execute first (insert blocked on the lock)",
                )
                self.assertEqual(
                    journal[1],
                    (
                        "route replace 192.0.2.33/32 nhid 4 table 200 "
                        "proto 250 metric 200",
                    ),
                    "the insert's replace must land after the delete",
                )
                intervals = _intervals_for(router, _dns("192.0.2.33/32"))
                self.assertEqual(len(intervals), 1)
            finally:
                _stop_workers(router)

    def test_same_prefix_different_group_readd_is_preserved(self):
        """A stale delete matches the kernel delete identity (net, proto,
        metric) — a same-prefix DNS route re-added through a different group
        (different nhid) must suppress the delete, because the kernel del
        would remove it regardless of nhid."""
        with patch("route.datetime", _Clock), patch("subprocess.run") as run:
            run.return_value = MagicMock(returncode=0, stderr="")
            router = _make_router()
            _stop_workers(router)
            try:
                # Original DNS route through group nhid=4.
                router.add_route(_dns("192.0.2.34/32", nhid=4, ttl=300))
                _drain_queue(router)

                # It expires; the sweep queues its delete.
                _Clock.offset = 3600.0
                router._cleanup_pass()
                queued = _drain_queue(router)
                self.assertEqual([cmd for cmd, _ in queued], ["del"])

                # The same /32 is re-answered through a different group
                # (nhid=7) and immediately installed.
                _Clock.offset = 0.0
                readded = _dns("192.0.2.34/32", nhid=7, ttl=300)
                router.add_route(readded, immediate=True)

                # The old delete runs through the real worker.
                router._route_queue.put(queued[0])
                router._shutdown_event.clear()
                worker = threading.Thread(
                    target=router._process_route_commands_iproute2, daemon=True
                )
                worker.start()
                deadline = time.time() + 2.0
                while time.time() < deadline and not router._route_queue.empty():
                    time.sleep(0.01)
                time.sleep(0.1)
                router._shutdown_event.set()
                worker.join(timeout=2.0)

                del_batches = [
                    c.kwargs["input"]
                    for c in run.call_args_list
                    if "route del" in (c.kwargs.get("input") or "")
                ]
                self.assertEqual(
                    del_batches,
                    [],
                    "delete must be suppressed for a same-identity route "
                    "even through a different group",
                )
                intervals = _intervals_for(router, readded)
                self.assertEqual(len(intervals), 1)
                self.assertEqual(intervals[0].data.nhid, 7)
            finally:
                _stop_workers(router)


class TestFlushReplay(unittest.TestCase):
    """Runtime PBR reapply flushes the whole managed table via clean_pbr();
    every in-memory route of both classes must be re-installed after the
    flush — including when the PBR apply itself fails, and even when some
    interface is gated unhealthy (grouped nhid-only routes have dev=None and
    a per-interface replay would skip them) (PR #69 review).

    Driven through the real monitor tick chain:
    interface_monitor._tick -> apply_pbr (real) -> replay
    -> Router.replay_routes, with only the kernel boundary mocked
    (pyroute2 IPRoute, nftables, ip -batch subprocess)."""

    def _run_reapply(self, nft_rc: int, interface_health=None):
        import asyncio

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
            return (nft_rc, "", "boom" if nft_rc else "")

        nft = MagicMock()
        nft.cmd.side_effect = nft_cmd
        ipr = MagicMock()

        cfg = SimpleNamespace(
            table=200,
            clean_conntrack=False,
            domain_route_ttl=300,
            routes=[],
            interfaces=["backbone"],
            pbr_mark=512,
            dns_mark=513,
        )

        def replayed() -> bool:
            return any(
                "proto 4 metric 100" in b and "proto 250 metric 200" in b
                for b in batches
            )

        # Everything that can touch the kernel (Router setup installs
        # included) runs inside the patches — no real ip -batch may escape.
        with (
            patch.object(interface_monitor, "_INTERVAL_SECONDS", 0.05),
            patch.object(
                interface_monitor,
                "_poll_links_once",
                side_effect=lambda: {"backbone": "up"},
            ),
            patch.object(ipt_main, "IPRoute", return_value=ipr),
            patch.object(ipt_main.nftables, "Nftables", return_value=nft),
            patch("subprocess.run", side_effect=fake_run),
        ):
            router = _make_router()
            try:
                router.add_route(_static("203.0.113.0/24"), immediate=True)
                router.add_route(_dns("192.0.2.40/32", ttl=300), immediate=True)

                saved = (state.CONFIG, state.ROUTER)
                saved_health = dict(state.INTERFACE_HEALTH)
                state.CONFIG, state.ROUTER = cfg, router
                if interface_health is not None:
                    state.INTERFACE_HEALTH.clear()
                    state.INTERFACE_HEALTH.update(interface_health)

                async def scenario():
                    stop = asyncio.Event()
                    task = asyncio.create_task(
                        interface_monitor.monitor_interfaces(stop)
                    )
                    loop = asyncio.get_running_loop()
                    deadline = loop.time() + 3.0
                    while loop.time() < deadline and not replayed():
                        await asyncio.sleep(0.02)
                    stop.set()
                    await asyncio.wait_for(task, timeout=5.0)

                try:
                    asyncio.run(scenario())
                finally:
                    state.CONFIG, state.ROUTER = saved
                    state.INTERFACE_HEALTH.clear()
                    state.INTERFACE_HEALTH.update(saved_health)
            finally:
                _stop_workers(router)
        return ipr, replayed()

    def test_reapply_flushes_table_and_replays_both_classes(self):
        _, replayed = self._run_reapply(nft_rc=0)
        self.assertTrue(
            replayed,
            "after the table flush every in-memory route of both classes "
            "must be re-installed in one replay batch",
        )

    def test_reapply_flushes_table_and_replays_even_when_pbr_apply_fails(self):
        _, replayed = self._run_reapply(nft_rc=1)
        self.assertTrue(
            replayed,
            "a failed PBR reapply must not leave the flushed table empty: "
            "in-memory routes must still be replayed",
        )

    def test_reapply_with_unhealthy_interface_still_replays_nhid_routes(self):
        """With a gated interface unhealthy, the post-flush replay must
        still reinstall nhid-only STATIC/DNS routes (per-interface replay
        skips them because their dev is None; nexthop groups handle
        unhealthy members themselves)."""
        _, replayed = self._run_reapply(
            nft_rc=0, interface_health={"wg_out1": False}
        )
        self.assertTrue(
            replayed,
            "an unhealthy gated interface must not reduce the post-flush "
            "replay to a per-interface replay that skips nhid-only routes",
        )


if __name__ == "__main__":
    unittest.main()
