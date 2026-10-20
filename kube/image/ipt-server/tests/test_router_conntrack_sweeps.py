"""Conntrack-aware expiry sweeps and destination cleanup (T1/T2).

The expiry sweep must retain expired routes whose prefix still carries an
active conntrack flow (source or destination), delete the rest, and never
delete STATIC entries. Destination cleanup (used after route deletes)
removes only matching flows, drains the dump before deleting, and survives
per-entry errors. Fixed clock + FakeConntrack keep everything deterministic.
"""

import ipaddress
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from route import DNS_PROTO, STATIC_PROTO, RouteObject
from tests.support import (
    FakeConntrack,
    FrozenClock,
    ct_entry,
    drain_queue,
    hermetic_router,
    stop_workers,
)


def _cfg(clean_conntrack=False):
    return SimpleNamespace(
        table=200,
        clean_conntrack=clean_conntrack,
        domain_route_ttl=300,
        routes=[],
        interfaces=[],
    )


def _dns(net: str, ttl=100) -> RouteObject:
    return RouteObject(
        net=ipaddress.IPv4Network(net),
        nhid=4,
        proto=DNS_PROTO,
        metric=200,
        ttl=ttl,
    )


def _static(net: str) -> RouteObject:
    return RouteObject(
        net=ipaddress.IPv4Network(net), nhid=4, proto=STATIC_PROTO, metric=100
    )


class TestExpirySweepRetention(unittest.TestCase):
    def setUp(self):
        FrozenClock.offset = 0.0

    tearDown = setUp

    def _expired_router(self, entries=()):
        ctx = hermetic_router(_cfg(), conntrack=FakeConntrack(entries=entries))
        router = ctx.__enter__()
        self.addCleanup(ctx.__exit__, None, None, None)
        return router

    def test_active_source_flow_retains_expired_route(self):
        """An expired DNS route with an active conntrack flow (src inside its
        prefix) is retained; the unrelated expired route is deleted."""
        with patch("route.datetime", FrozenClock):
            router = self._expired_router(
                entries=[ct_entry("203.0.113.5", "192.0.2.99")]
            )
            stop_workers(router)
            router.add_route(_dns("203.0.113.0/24"))
            router.add_route(_dns("198.51.100.0/24"))
            drain_queue(router)

            FrozenClock.offset = 3600.0
            router._cleanup_pass()
            queued = drain_queue(router)
            deleted = [spec["dst"] for cmd, spec in queued if cmd == "del"]

        self.assertEqual(deleted, ["198.51.100.0"], "only the flowless route dies")
        remaining = {str(i.data.net) for i in router._route_tree}
        self.assertEqual(remaining, {"203.0.113.0/24"})

    def test_active_destination_flow_retains_expired_route(self):
        """A flow whose destination falls inside the expired prefix retains it."""
        with patch("route.datetime", FrozenClock):
            router = self._expired_router(
                entries=[ct_entry("192.0.2.7", "198.51.100.9")]
            )
            stop_workers(router)
            router.add_route(_dns("198.51.100.0/24"))
            drain_queue(router)

            FrozenClock.offset = 3600.0
            router._cleanup_pass()
            queued = drain_queue(router)

        self.assertEqual(
            [cmd for cmd, _ in queued], [], "route with active dst flow is kept"
        )

    @pytest.mark.xfail(
        strict=True, reason="router-internal#76: one flow must retain every covering interval"
    )
    def test_contained_expired_route_is_retained_with_covering_route(self):
        """Intended retention contract: an expired route whose prefix
        contains an active conntrack flow is retained — for EVERY covering
        interval, not only the first scanned (router-internal#76)."""
        with patch("route.datetime", FrozenClock):
            router = self._expired_router(
                entries=[ct_entry("203.0.113.200", "192.0.2.1")]
            )
            stop_workers(router)
            router.add_route(_dns("203.0.113.0/24"))
            router.add_route(_dns("203.0.113.128/25"))
            drain_queue(router)

            FrozenClock.offset = 3600.0
            router._cleanup_pass()
            queued = drain_queue(router)
            remaining = {str(i.data.net) for i in router._route_tree}

        self.assertEqual(remaining, {"203.0.113.0/24", "203.0.113.128/25"})
        self.assertEqual(queued, [], "no covering route with a flow may be deleted")

    def test_malformed_conntrack_entry_does_not_block_sweep(self):
        """A malformed entry (unparseable address) is skipped; the sweep still
        deletes the flowless expired route."""
        bad = MagicMock()
        bad.tuple_orig.saddr = MagicMock(side_effect=ValueError("no text"))
        with patch("route.datetime", FrozenClock):
            router = self._expired_router(entries=[bad])
            stop_workers(router)
            router.add_route(_dns("203.0.113.0/24"))
            drain_queue(router)

            FrozenClock.offset = 3600.0
            router._cleanup_pass()
            queued = drain_queue(router)

        self.assertEqual([cmd for cmd, _ in queued], ["del"])

    @pytest.mark.xfail(
        strict=True,
        reason="router-internal#78: dump-failure policy undecided; safe behavior is retain",
    )
    def test_dump_failure_retains_expired_routes(self):
        """Safe behavior while the dump-failure policy is undecided
        (router-internal#78): when conntrack cannot be observed, expired
        routes are retained and retried on the next sweep instead of being
        deleted with zero knowledge about active flows."""
        with patch("route.datetime", FrozenClock):
            router = self._expired_router()
            router._conntrack.dump_error = OSError("netlink down")
            stop_workers(router)
            router.add_route(_dns("203.0.113.0/24"))
            drain_queue(router)

            FrozenClock.offset = 3600.0
            router._cleanup_pass()
            queued = drain_queue(router)
            remaining = {str(i.data.net) for i in router._route_tree}

        self.assertEqual(remaining, {"203.0.113.0/24"})
        self.assertEqual(queued, [], "unobservable conntrack must not delete routes")

    def test_static_route_survives_sweep_with_flows_and_expiry(self):
        """STATIC routes are never expired even when the sweep runs."""
        with patch("route.datetime", FrozenClock):
            router = self._expired_router(entries=[])
            stop_workers(router)
            router.add_route(_static("203.0.113.0/24"))
            drain_queue(router)
            drain_queue(router)

            FrozenClock.offset = 3600.0
            router._cleanup_pass()
            queued = drain_queue(router)

        self.assertEqual(queued, [])
        self.assertEqual(len(list(router._route_tree)), 1)


class TestRemoveConntrackForDestination(unittest.TestCase):
    def _router(self, conntrack):
        ctx = hermetic_router(
            _cfg(clean_conntrack=True), conntrack=conntrack
        )
        router = ctx.__enter__()
        self.addCleanup(ctx.__exit__, None, None, None)
        return router

    def test_deletes_only_flows_matching_destination(self):
        """Only flows with src or dst inside the destination tree are deleted."""
        from intervaltree import IntervalTree

        ct = FakeConntrack(
            entries=[
                ct_entry("192.0.2.7", "203.0.113.9"),  # dst matches
                ct_entry("203.0.113.8", "198.51.100.1"),  # src matches
                ct_entry("192.0.2.1", "198.51.100.2"),  # no match
            ]
        )
        router = self._router(ct)
        tree = IntervalTree()
        net = ipaddress.ip_network("203.0.113.0/24")
        tree.addi(
            int(net.network_address), int(net.broadcast_address) + 1, net
        )

        router.remove_conntrack_entries_for_destination(tree)

        self.assertEqual(len(ct.deleted), 2)

    def test_disabled_cleanup_is_a_noop(self):
        """With clean_conntrack=False the cleanup returns without dumping."""
        from intervaltree import IntervalTree

        ct = FakeConntrack()
        ctx = hermetic_router(_cfg(clean_conntrack=False), conntrack=ct)
        with ctx as router:
            router.remove_conntrack_entries_for_destination(IntervalTree())
        self.assertEqual(ct.dump_calls, 0)
        self.assertEqual(ct.deleted, [])

    def test_dump_drained_before_deletes_execute(self):
        """All dump entries are materialized before the first delete runs
        (the pyroute2 event-loop-cleanup regression)."""
        from intervaltree import IntervalTree

        order = []

        class Scripted(FakeConntrack):
            def dump_entries(self):
                order.append("dump")
                return iter(
                    [ct_entry("192.0.2.7", "203.0.113.0")]
                )

            def entry(self, cmd, tuple_orig=None):
                order.append(f"entry:{cmd}")
                return super().entry(cmd, tuple_orig=tuple_orig)

        ct = Scripted()
        router = self._router(ct)
        tree = IntervalTree()
        net = ipaddress.ip_network("203.0.113.0/32")
        tree.addi(
            int(net.network_address), int(net.broadcast_address) + 1, net
        )

        router.remove_conntrack_entries_for_destination(tree)

        self.assertEqual(order, ["dump", "entry:del"])

    def test_one_delete_failure_does_not_stop_later_deletes(self):
        """A failing entry delete is logged and the remaining deletes run."""
        from intervaltree import IntervalTree

        ct = FakeConntrack(
            entries=[
                ct_entry("192.0.2.7", "203.0.113.9"),
                ct_entry("192.0.2.8", "203.0.113.10"),
            ],
            delete_errors={0: OSError("busy")},
        )
        router = self._router(ct)
        tree = IntervalTree()
        net = ipaddress.ip_network("203.0.113.0/24")
        tree.addi(
            int(net.network_address), int(net.broadcast_address) + 1, net
        )

        router.remove_conntrack_entries_for_destination(tree)

        self.assertEqual(ct.delete_attempts, 2, "both deletes must be attempted")
        self.assertEqual(
            len(ct.deleted), 1, "the entry after the failing one must still delete"
        )

    def test_dump_exception_propagates(self):
        """An exception from the dump itself is re-raised to the caller."""
        from intervaltree import IntervalTree

        ct = FakeConntrack(dump_error=OSError("netlink down"))
        router = self._router(ct)
        tree = IntervalTree()
        net = ipaddress.ip_network("203.0.113.0/32")
        tree.addi(
            int(net.network_address), int(net.broadcast_address) + 1, net
        )

        with self.assertRaises(OSError):
            router.remove_conntrack_entries_for_destination(tree)

    def test_malformed_entry_is_skipped_with_warning(self):
        """An unparseable flow entry is skipped; healthy flows still delete."""
        from intervaltree import IntervalTree

        bad = MagicMock()
        bad.tuple_orig.saddr = "not-an-ip"
        bad.tuple_orig.daddr = "192.0.2.1"
        ct = FakeConntrack(entries=[bad])
        router = self._router(ct)
        tree = IntervalTree()
        net = ipaddress.ip_network("203.0.113.0/32")
        tree.addi(
            int(net.network_address), int(net.broadcast_address) + 1, net
        )

        router.remove_conntrack_entries_for_destination(tree)

        self.assertEqual(ct.deleted, [])


if __name__ == "__main__":
    unittest.main()
