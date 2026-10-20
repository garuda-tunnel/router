"""Per-interface replay and removal keep class tags and interface scoping (T3).

replay_routes_for_interface replays only the routes whose dev matches;
remove_routes_for_interface deletes only that interface's kernel entries
while leaving the in-memory definitions intact. Unresolved routes (no
nhid) never abort their siblings.
"""

import ipaddress
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from route import DNS_PROTO, STATIC_PROTO, RouteObject
from tests.support import drain_queue, hermetic_router, stop_workers


def _cfg():
    return SimpleNamespace(
        table=200,
        clean_conntrack=False,
        domain_route_ttl=300,
        routes=[],
        interfaces=[],
    )


def _route(net: str, dev=None, proto=STATIC_PROTO, metric=100) -> RouteObject:
    return RouteObject(
        net=ipaddress.IPv4Network(net),
        nhid=4,
        dev=dev,
        proto=proto,
        metric=metric,
    )


class TestPerInterfaceReplayAndRemove(unittest.TestCase):
    def _batches(self):
        batches = []

        def fake_run(argv, **kwargs):
            if argv[:2] == ["ip", "-batch"]:
                batches.append(kwargs.get("input") or "")
            return MagicMock(returncode=0, stderr="", stdout="")

        return batches, fake_run

    def test_replay_for_interface_replays_only_matching_dev(self):
        """Only routes of the given dev are replayed; other interfaces and
        nhid-only (dev=None) routes are untouched by the per-interface form."""
        batches, fake_run = self._batches()
        with patch("subprocess.run", side_effect=fake_run):
            with hermetic_router(_cfg()) as router:
                stop_workers(router)
                router.add_route(_route("192.0.2.0/24", dev="wg_out"))
                router.add_route(_route("198.51.100.0/24", dev="wg_in"))
                router.add_route(_route("203.0.113.0/24"))  # nhid-only
                drain_queue(router)

                router.replay_routes_for_interface("wg_out")

        self.assertEqual(len(batches), 1)
        self.assertIn("route replace 192.0.2.0/24 nhid 4", batches[0])
        self.assertNotIn("198.51.100.0/24", batches[0])
        self.assertNotIn("203.0.113.0/24", batches[0])

    def test_replay_full_skips_unresolved_routes_without_aborting(self):
        """A route with nhid=None is skipped by full replay; valid siblings
        still land in the kernel batch."""
        batches, fake_run = self._batches()
        with patch("subprocess.run", side_effect=fake_run):
            with hermetic_router(_cfg()) as router:
                stop_workers(router)
                router.add_route(_route("192.0.2.0/24", dev="wg_out"))
                unresolved = _route("198.51.100.0/24", dev="wg_in")
                router.add_route(unresolved)
                # Simulate a member that lost its nhid after insertion
                # (add_route itself would reject a nhid-less route).
                unresolved.nhid = None
                drain_queue(router)

                router.replay_routes()

        self.assertEqual(len(batches), 1)
        self.assertIn("route replace 192.0.2.0/24", batches[0])
        self.assertNotIn("198.51.100.0", batches[0])

    def test_replay_full_and_per_interface_carry_class_tags(self):
        """Both replay forms render proto+metric for each route class."""
        batches, fake_run = self._batches()
        with patch("subprocess.run", side_effect=fake_run):
            with hermetic_router(_cfg()) as router:
                stop_workers(router)
                router.add_route(_route("192.0.2.0/24", dev="wg_out"))
                dns = _route("192.0.2.7/32", proto=DNS_PROTO, metric=200)
                dns.ttl = 300
                router.add_route(dns)
                drain_queue(router)

                router.replay_routes()

        self.assertIn("proto 4 metric 100", batches[0])
        self.assertIn("proto 250 metric 200", batches[0])

    def test_remove_for_interface_deletes_only_that_interfaces_entries(self):
        """Kernel deletes are scoped to the interface; other interfaces'
        entries and the in-memory definitions survive."""
        batches, fake_run = self._batches()
        with patch("subprocess.run", side_effect=fake_run):
            with hermetic_router(_cfg()) as router:
                stop_workers(router)
                router.add_route(_route("192.0.2.0/24", dev="wg_out"))
                router.add_route(_route("198.51.100.0/24", dev="wg_in"))
                drain_queue(router)

                router.remove_routes_for_interface("wg_out")

        self.assertEqual(len(batches), 1)
        self.assertIn("route del 192.0.2.0/24 table 200", batches[0])
        self.assertNotIn("198.51.100.0/24", batches[0])
        # In-memory definitions are untouched by kernel removal.
        self.assertEqual(len(list(router._route_tree)), 2)

    def test_replay_and_remove_for_unknown_interface_are_empty_batches(self):
        """An interface with no routes produces an empty batch (no ip call)."""
        batches, fake_run = self._batches()
        with patch("subprocess.run", side_effect=fake_run):
            with hermetic_router(_cfg()) as router:
                stop_workers(router)
                router.add_route(_route("192.0.2.0/24", dev="wg_out"))
                drain_queue(router)

                router.replay_routes_for_interface("ghost")
                router.remove_routes_for_interface("ghost")

        self.assertEqual(batches, [])


if __name__ == "__main__":
    unittest.main()
