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

Conventions: all Routers are built through tests.support.hermetic_router
(fake Conntrack patched before construction; workers joined in the owning
thread). Time is FrozenClock — TTL expiry is an input, never elapsed wall
time. The ip -batch subprocess boundary is the kernel contract and is the
only place command text is asserted.
"""

import ipaddress
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import Config
from dns_records import ARecord
from route import (
    DNS_METRIC,
    DNS_PROTO,
    STATIC_METRIC,
    STATIC_PROTO,
    RouteObject,
)
from tests.support import (
    FakeConntrack,
    FrozenClock,
    drain_queue,
    hermetic_router,
    stop_workers,
)


def _cfg(routes=None, interfaces=()):
    """Minimal Router config namespace."""
    return SimpleNamespace(
        table=200,
        clean_conntrack=False,
        domain_route_ttl=300,
        routes=routes or [],
        interfaces=list(interfaces),
    )


def _intervals_for(router, route: RouteObject) -> list:
    """Read the route-tree intervals covering route's address range."""
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


class _FrozenClockTest(unittest.TestCase):
    """Base: reset the shared frozen clock around each test."""

    def setUp(self):
        FrozenClock.offset = 0.0

    tearDown = setUp


class TestStaticAndDnsCoexist(_FrozenClockTest):
    def test_collision_keeps_both_entries_and_dns_expiry_removes_only_dns(self):
        """Static /32 + DNS answer for the same /32: both installed with their
        class metrics; expiry deletes only the DNS entry, static stays."""
        with patch("route.datetime", FrozenClock), patch("subprocess.run"):
            with hermetic_router(_cfg()) as router:
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
                FrozenClock.offset = 3600.0
                by_metric = {i.data.metric: i for i in intervals}
                self.assertFalse(by_metric[STATIC_METRIC].data.expired)
                self.assertTrue(by_metric[DNS_METRIC].data.expired)

                stop_workers(router)
                drain_queue(router)
                router._cleanup_pass()
                queued = drain_queue(router)
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

    def test_dns_refresh_does_not_touch_static_interval(self):
        """A DNS refresh never refreshes/resets the STATIC interval."""
        with patch("route.datetime", FrozenClock), patch("subprocess.run"):
            with hermetic_router(_cfg()) as router:
                router.add_route(_static("192.0.2.11/32"))
                router.add_route(_dns("192.0.2.11/32", ttl=300))

                FrozenClock.offset = 50.0
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

    def test_static_never_expires_even_with_expiration_set(self):
        """RouteObject.expired is always False for the STATIC class."""
        from datetime import datetime

        route = _static("192.0.2.12/32")
        route.expiration = datetime.now()  # defensive: even if set
        self.assertFalse(route.expired)

    def test_same_class_dns_refresh_keeps_max_ttl_semantics(self):
        """DNS-vs-DNS refresh keeps the pre-#62 reset_expiration semantics."""
        with patch("route.datetime", FrozenClock), patch("subprocess.run"):
            with hermetic_router(_cfg()) as router:
                first = router.add_route(_dns("192.0.2.13/32", ttl=300))
                self.assertEqual(first.ttl, 300)

                FrozenClock.offset = 100.0
                second = router.add_route(_dns("192.0.2.13/32", ttl=120))
                self.assertEqual(second.ttl, 300, "refresh keeps the max TTL")
                self.assertGreater(second.expiration, first.expiration)

                FrozenClock.offset = 500.0  # past the renewed expiration
                stop_workers(router)
                drain_queue(router)
                router._cleanup_pass()
                queued = drain_queue(router)
                self.assertEqual(len(queued), 1)
                self.assertEqual(queued[0][0], "del")
                self.assertEqual(queued[0][1]["proto"], DNS_PROTO)
                self.assertEqual(_intervals_for(router, first), [])


class TestDnsTtlFallback(_FrozenClockTest):
    def _router_with_domain_rule(self):
        group = Config.RouteActionGroup(
            route=[Config.RouteMember(dev="backbone")],
            rules=[Config.DomainRule(domain=r"probe\.example\.net")],
        )
        router_ctx = hermetic_router(
            _cfg(routes=[group]),
        )
        router = router_ctx.__enter__()
        router._nhg_registry[group.nhg_descriptor] = 7
        self.addCleanup(lambda: (stop_workers(router), router_ctx.__exit__(None, None, None)))
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
        """A zero answer TTL must fall back to the configured domain TTL —
        a DNS-learned route is never permanent (#68)."""
        with patch("route.datetime", FrozenClock), patch("subprocess.run"):
            router = self._router_with_domain_rule()
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

    def test_missing_ttl_falls_back_to_domain_route_ttl(self):
        """A missing answer TTL must fall back to the configured domain TTL."""
        with patch("route.datetime", FrozenClock), patch("subprocess.run"):
            router = self._router_with_domain_rule()
            rv = router.on_a_record(self._record("192.0.2.78", None))
            self.assertEqual(rv["ttl"], 300)
            intervals = _intervals_for(router, _static("192.0.2.78/32"))
            self.assertEqual(intervals[0].data.ttl, 300)
            self.assertIsNotNone(intervals[0].data.expiration)


class TestKernelCommandClassTags(unittest.TestCase):
    def test_del_and_replace_queue_commands_carry_proto_and_metric(self):
        """The queue worker renders class proto+metric into every ip line."""
        processed = threading.Event()
        seen = {}

        def fake_run(argv, **kwargs):
            if argv[:2] == ["ip", "-batch"]:
                seen["input"] = kwargs.get("input")
                processed.set()
            return MagicMock(returncode=0, stderr="", stdout="")

        with patch("subprocess.run", side_effect=fake_run):
            with hermetic_router(_cfg()) as router:
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
                # The fake subprocess boundary signals synchronously from the
                # worker thread when the batch is processed.
                self.assertTrue(
                    processed.wait(timeout=5.0),
                    "worker must process the queued delete",
                )

        self.assertIn(
            "route del 192.0.2.10/32 table 200 proto 250 metric 200",
            seen["input"],
        )

    def test_immediate_apply_and_replay_carry_proto_and_metric(self):
        """Immediate applies and full replays carry the class proto+metric."""
        with patch("subprocess.run") as run:
            run.return_value = MagicMock(returncode=0, stderr="")
            with hermetic_router(_cfg()) as router:
                router.add_route(_dns("192.0.2.10/32"), immediate=True)
                batch = run.call_args[1]["input"]
                self.assertIn(
                    "route replace 192.0.2.10/32 nhid 4 table 200 "
                    "proto 250 metric 200",
                    batch,
                )

                router.add_route(_static("203.0.113.0/24"), immediate=False)
                stop_workers(router)
                drain_queue(router)
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


class TestLoadRoutesStaticClass(unittest.TestCase):
    def test_load_routes_installs_static_class(self):
        """Config NetRule routes are installed with the STATIC class tags."""
        group = Config.RouteActionGroup(
            route=[Config.RouteMember(dev="backbone")],
            rules=[Config.NetRule(net="203.0.113.0/24")],
        )
        with patch("subprocess.run"):
            with hermetic_router(_cfg(routes=[group])) as router:
                router._nhg_registry[group.nhg_descriptor] = 9
                router._load_routes()

        intervals = _intervals_for(router, _static("203.0.113.0/24"))
        self.assertEqual(len(intervals), 1)
        self.assertEqual(intervals[0].data.proto, STATIC_PROTO)
        self.assertEqual(intervals[0].data.metric, STATIC_METRIC)
        self.assertIsNone(intervals[0].data.expiration)


class TestQueuedDeleteRace(_FrozenClockTest):
    """PR #69 review: an expiry-queued delete must not remove a re-added
    route, and a same-class refresh must re-apply the kernel entry.

    Synchronization is event-based: the worker's kernel batch is gated shut
    while it holds _routes_lock, and released only after the inserter thread
    has been started, so the delete-before-replace ordering is a proven
    critical-section property, not a timing accident.
    """

    def _run_worker_once(self, router, processed):
        """Run the real queue worker until it has executed its kernel batch.

        The caller wraps ``router._ip_batch`` with an Event-signalling fake
        first; this helper starts the worker, waits for that signal, then
        shuts the worker down deterministically.
        """
        router._shutdown_event.clear()
        worker = threading.Thread(
            target=router._process_route_commands_iproute2, daemon=True
        )
        worker.start()
        self.assertTrue(
            processed.wait(timeout=5.0), "worker must process the queued batch"
        )
        router._shutdown_event.set()
        worker.join(timeout=5.0)
        self.assertFalse(worker.is_alive(), "worker must exit after shutdown")
        return worker

    def test_immediate_refresh_reapplies_to_kernel(self):
        """An immediate same-class refresh re-applies (idempotent replace)."""
        with patch("route.datetime", FrozenClock), patch("subprocess.run") as run:
            run.return_value = MagicMock(returncode=0, stderr="")
            with hermetic_router(_cfg()) as router:
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

    def test_stale_queued_delete_spares_replacement_and_refresh_reapplies(self):
        """Deterministic race replay: worker paused, expire + queue delete,
        install replacement, run the old delete, send an identical answer —
        the kernel spec stays present; the STATIC route is untouched."""
        with patch("route.datetime", FrozenClock), patch("subprocess.run") as run:
            run.return_value = MagicMock(returncode=0, stderr="")
            with hermetic_router(_cfg()) as router:
                stop_workers(router)  # pause the worker for deterministic steps
                router.add_route(_static("192.0.2.31/32"))
                drain_queue(router)
                router.add_route(_dns("192.0.2.30/32", ttl=300), immediate=True)
                self.assertIn("route replace 192.0.2.30/32", run.call_args[1]["input"])

                # Expire the DNS route; the sweep queues its delete.
                FrozenClock.offset = 3600.0
                router._cleanup_pass()
                queued = drain_queue(router)
                self.assertEqual([cmd for cmd, _ in queued], ["del"])

                # A new identical answer re-inserts and installs the route.
                FrozenClock.offset = 0.0
                run.reset_mock()
                router.add_route(_dns("192.0.2.30/32", ttl=300), immediate=True)
                self.assertIn("route replace 192.0.2.30/32", run.call_args[1]["input"])

                # Run the (now stale) queued delete through the real worker.
                # A fully-skipped delete emits no batch, so a FIFO sentinel
                # replace follows it: when the sentinel's kernel batch runs,
                # the stale delete has already been validated and dropped
                # inside the same critical section.
                processed = threading.Event()
                real_batch = router._ip_batch

                def batch_notifying(lines):
                    try:
                        real_batch(lines)
                    finally:
                        processed.set()

                router._ip_batch = batch_notifying
                router._route_queue.put(queued[0])
                router._route_queue.put(
                    (
                        "replace",
                        {
                            "dst": "198.51.100.7",
                            "dst_len": 32,
                            "nhid": 4,
                            "proto": DNS_PROTO,
                            "priority": DNS_METRIC,
                        },
                    )
                )
                self._run_worker_once(router, processed)

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

    def test_insert_attempted_after_validation_is_not_deleted(self):
        """Validation and the kernel delete are one critical section: an
        insert that starts while the delete batch is executing blocks on
        _routes_lock and applies after the delete — never before it.

        The gate here is a lock-acquisition signal: the router's lock is
        wrapped so the test observes the inserter actually ACQUIRING the
        lock (i.e. it was contending while the worker held it), and the
        worker's kernel batch is gated shut until that acquisition is
        observed."""
        with patch("route.datetime", FrozenClock), patch("subprocess.run") as run:
            run.return_value = MagicMock(returncode=0, stderr="")
            with hermetic_router(_cfg()) as router:
                stop_workers(router)
                router.add_route(_dns("192.0.2.33/32", ttl=300), immediate=True)
                drain_queue(router)

                # Expire the DNS route; the sweep queues its delete. Do NOT
                # re-add it yet: the insert must happen after the worker's
                # stale-check has already validated the delete.
                FrozenClock.offset = 3600.0
                router._cleanup_pass()
                queued = drain_queue(router)
                self.assertEqual([cmd for cmd, _ in queued], ["del"])

                # Gate the worker's first kernel batch shut: while the gate
                # is closed the worker provably holds _routes_lock, so any
                # add_route() must block until the delete batch completed.
                journal = []
                real_batch = router._ip_batch
                batch_entered = threading.Event()
                gate = threading.Event()
                inserter_started = threading.Event()
                insert_done = threading.Event()
                first = {"done": False}

                def gated_batch(lines):
                    if not first["done"]:
                        first["done"] = True
                        batch_entered.set()
                        # Hold the critical section until the test driver
                        # has observed the inserter's lock acquisition.
                        self.assertTrue(
                            gate.wait(timeout=5.0),
                            "gate must be released by the test driver",
                        )
                    real_batch(lines)
                    journal.append(tuple(lines))

                # Signaling lock wrapper: signals inside acquire() at the
                # moment a contending acquisition is ATTEMPTED while the
                # worker holds the lock (i.e. the inserter is really
                # blocked on _routes_lock, not merely started).
                real_lock = router._routes_lock
                lock_attempts = {"n": 0}
                lock_attempted = threading.Event()

                class _SignalingLock:
                    def acquire(self, blocking=True, timeout=-1):
                        lock_attempts["n"] += 1
                        if (
                            batch_entered.is_set()
                            and not lock_attempted.is_set()
                            and lock_attempts["n"] > 1
                        ):
                            lock_attempted.set()
                        return real_lock.acquire(blocking, timeout)

                    def release(self):
                        real_lock.release()

                    def __enter__(self):
                        self.acquire()
                        return self

                    def __exit__(self, *exc):
                        self.release()

                router._routes_lock = _SignalingLock()

                def run_insert():
                    inserter_started.set()
                    router.add_route(_dns("192.0.2.33/32", ttl=300), immediate=True)
                    insert_done.set()

                router._ip_batch = gated_batch
                router._route_queue.put(queued[0])
                router._shutdown_event.clear()
                worker = threading.Thread(
                    target=router._process_route_commands_iproute2, daemon=True
                )
                try:
                    worker.start()
                    self.assertTrue(
                        batch_entered.wait(timeout=5.0),
                        "worker must enter the delete batch",
                    )
                    inserter = threading.Thread(target=run_insert, daemon=True)
                    try:
                        inserter.start()
                        self.assertTrue(
                            inserter_started.wait(timeout=5.0),
                            "inserter must be launched while the gate is closed",
                        )
                        # Gate on an actual lock-acquisition attempt: the
                        # inserter's acquire() has run while the worker
                        # still holds the lock, so it is provably blocked
                        # on _routes_lock.
                        self.assertTrue(
                            lock_attempted.wait(timeout=5.0),
                            "inserter must attempt to acquire _routes_lock "
                            "while the worker holds it",
                        )
                        gate.set()
                    finally:
                        inserter.join(timeout=5.0)
                        self.assertFalse(
                            inserter.is_alive(), "inserter thread must finish"
                        )
                    self.assertTrue(
                        insert_done.wait(timeout=5.0), "the insert must complete"
                    )
                finally:
                    router._shutdown_event.set()
                    worker.join(timeout=5.0)
                    self.assertFalse(worker.is_alive(), "worker must exit")

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

    def test_same_prefix_different_group_readd_is_preserved(self):
        """A stale delete matches the kernel delete identity (net, proto,
        metric) — a same-prefix DNS route re-added through a different group
        (different nhid) must suppress the delete, because the kernel del
        would remove it regardless of nhid."""
        with patch("route.datetime", FrozenClock), patch("subprocess.run") as run:
            run.return_value = MagicMock(returncode=0, stderr="")
            with hermetic_router(_cfg()) as router:
                stop_workers(router)
                # Original DNS route through group nhid=4.
                router.add_route(_dns("192.0.2.34/32", nhid=4, ttl=300))
                drain_queue(router)

                # It expires; the sweep queues its delete.
                FrozenClock.offset = 3600.0
                router._cleanup_pass()
                queued = drain_queue(router)
                self.assertEqual([cmd for cmd, _ in queued], ["del"])

                # The same /32 is re-answered through a different group
                # (nhid=7) and immediately installed.
                FrozenClock.offset = 0.0
                readded = _dns("192.0.2.34/32", nhid=7, ttl=300)
                router.add_route(readded, immediate=True)

                # The old delete runs through the real worker. A fully
                # skipped delete emits no batch, so a FIFO sentinel replace
                # follows it: its kernel batch proves the stale delete was
                # validated and dropped first.
                processed = threading.Event()
                real_batch = router._ip_batch

                def batch_notifying(lines):
                    try:
                        real_batch(lines)
                    finally:
                        processed.set()

                router._ip_batch = batch_notifying
                router._route_queue.put(queued[0])
                router._route_queue.put(
                    (
                        "replace",
                        {
                            "dst": "198.51.100.7",
                            "dst_len": 32,
                            "nhid": 4,
                            "proto": DNS_PROTO,
                            "priority": DNS_METRIC,
                        },
                    )
                )
                self._run_worker_once(router, processed)

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

        cfg = _cfg(interfaces=["backbone"])
        cfg.pbr_mark = 512
        cfg.dns_mark = 513

        # The replay lands inside the first tick; signal completion through
        # the Router method itself so the scenario stops deterministically
        # instead of polling a wall-clock deadline.
        replay_done = threading.Event()

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
            with hermetic_router(cfg) as router:
                real_replay = router.replay_routes

                def replay_signal():
                    real_replay()
                    replay_done.set()

                router.replay_routes = replay_signal
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
                    await asyncio.to_thread(replay_done.wait, 5.0)
                    stop.set()
                    await asyncio.wait_for(task, timeout=5.0)

                try:
                    asyncio.run(scenario())
                finally:
                    state.CONFIG, state.ROUTER = saved
                    state.INTERFACE_HEALTH.clear()
                    state.INTERFACE_HEALTH.update(saved_health)

        def replayed() -> bool:
            return any(
                "proto 4 metric 100" in b and "proto 250 metric 200" in b
                for b in batches
            )

        return ipr, replayed()

    def test_reapply_flushes_table_and_replays_both_classes(self):
        """After a PBR-triggered table flush, both route classes are
        re-installed in one replay batch."""
        _, replayed = self._run_reapply(nft_rc=0)
        self.assertTrue(
            replayed,
            "after the table flush every in-memory route of both classes "
            "must be re-installed in one replay batch",
        )

    def test_reapply_flushes_table_and_replays_even_when_pbr_apply_fails(self):
        """A failed PBR reapply must not leave the flushed table empty."""
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
