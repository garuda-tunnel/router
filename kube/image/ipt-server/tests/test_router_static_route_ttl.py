"""Regression guard: a DNS refresh must not expire a permanent static route.

router-internal#61: a permanent (ttl=None) route installed by _load_routes
for a NetRule CIDR was downgraded to the DNS answer TTL when on_a_record
refreshed the same /32 with the same nhid; _cleanup_expired_routes then
deleted it and nothing re-installed it.

Rules under test:
- permanent existing route + TTL refresh -> stays permanent
- TTL existing route + permanent refresh -> becomes permanent
- TTL vs TTL keeps today's reset_expiration semantics (max TTL, renewed
  expiration, expiry cleanup unchanged)
"""

import ipaddress
import time
import unittest
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from Router import Router
from route import RouteObject


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


class TestStaticRouteTtlDowngrade(unittest.TestCase):
    def setUp(self):
        _Clock.offset = 0.0

    tearDown = setUp

    def test_dns_refresh_of_permanent_route_keeps_it_permanent(self):
        """A TTL refresh of a static route must not give it an expiration (#61)."""
        net = ipaddress.IPv4Network("192.0.2.10/32")
        with patch("route.datetime", _Clock), patch("subprocess.run"):
            router = _make_router()
            try:
                static = RouteObject(net=net, nhid=4, metric=200)  # like _load_routes
                inserted = router.add_route(static)
                self.assertIsNone(inserted.ttl)
                self.assertIsNone(inserted.expiration)

                dns = RouteObject(net=net, nhid=4, metric=200, ttl=300)
                refreshed = router.add_route(dns)
                self.assertIsNone(
                    refreshed.ttl, "refresh must not set a TTL on a permanent route"
                )
                self.assertIsNone(
                    refreshed.expiration,
                    "refresh must not set an expiration on a permanent route",
                )

                # Far past the DNS TTL the static route must not be expired,
                # and a cleanup pass must keep it and queue no deletion.
                _Clock.offset = 3600.0
                intervals = _intervals_for(router, static)
                self.assertEqual(len(intervals), 1)
                self.assertFalse(intervals[0].data.expired)

                _stop_workers(router)
                _drain_queue(router)
                router._cleanup_pass()
                self.assertEqual(_drain_queue(router), [])
                intervals = _intervals_for(router, static)
                self.assertEqual(len(intervals), 1)
                self.assertIsNone(intervals[0].data.expiration)
            finally:
                _stop_workers(router)

    def test_permanent_refresh_of_ttl_route_makes_it_permanent(self):
        """A permanent refresh over an expiring route removes its expiration."""
        net = ipaddress.IPv4Network("192.0.2.11/32")
        with patch("route.datetime", _Clock), patch("subprocess.run"):
            router = _make_router()
            try:
                first = router.add_route(
                    RouteObject(net=net, nhid=4, metric=200, ttl=300)
                )
                self.assertIsNotNone(first.expiration)

                refreshed = router.add_route(RouteObject(net=net, nhid=4, metric=200))
                self.assertIsNone(refreshed.ttl)
                self.assertIsNone(refreshed.expiration)

                _Clock.offset = 3600.0
                _stop_workers(router)
                _drain_queue(router)
                router._cleanup_pass()
                self.assertEqual(_drain_queue(router), [])
                self.assertEqual(len(_intervals_for(router, first)), 1)
            finally:
                _stop_workers(router)

    def test_ttl_refresh_and_expiry_unchanged(self):
        """TTL-vs-TTL refresh keeps max-TTL renewal and expiry cleanup semantics."""
        net = ipaddress.IPv4Network("192.0.2.12/32")
        with patch("route.datetime", _Clock), patch("subprocess.run"):
            router = _make_router()
            try:
                first = router.add_route(
                    RouteObject(net=net, nhid=4, metric=200, ttl=300)
                )
                self.assertEqual(first.ttl, 300)
                self.assertIsNotNone(first.expiration)

                _Clock.offset = 100.0
                second = router.add_route(
                    RouteObject(net=net, nhid=4, metric=200, ttl=120)
                )
                self.assertEqual(second.ttl, 300, "refresh keeps the max TTL")
                self.assertGreater(
                    second.expiration, first.expiration, "refresh renews expiration"
                )
                self.assertFalse(_intervals_for(router, first)[0].data.expired)

                _Clock.offset = 500.0  # past the renewed expiration
                self.assertTrue(_intervals_for(router, first)[0].data.expired)
                _stop_workers(router)
                _drain_queue(router)
                router._cleanup_pass()
                queued = _drain_queue(router)
                self.assertEqual(len(queued), 1)
                cmd, spec = queued[0]
                self.assertEqual(cmd, "del")
                self.assertEqual(spec["dst"], "192.0.2.12")
                self.assertEqual(spec["dst_len"], 32)
                self.assertEqual(_intervals_for(router, first), [])
            finally:
                _stop_workers(router)


if __name__ == "__main__":
    unittest.main()
