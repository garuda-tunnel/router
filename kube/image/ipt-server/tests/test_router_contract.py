"""Kernel-boundary contract: route adds are idempotent ``replace`` operations.

Replaces the old source-scanning guard ("Router.py must not contain 'add'
literals"): a source scan can pass while the emitted kernel commands are
wrong. The behavioral contract is that installing the same route twice
leaves the kernel with one entry (replace semantics, no duplicate 'add'),
verified through the ip -batch boundary fake — the command text there IS
the contract the kernel executes.
"""

import ipaddress
import unittest
from unittest.mock import MagicMock, patch

from route import (
    DNS_METRIC,
    DNS_PROTO,
    STATIC_METRIC,
    STATIC_PROTO,
    RouteObject,
)
from tests.support import drain_queue, hermetic_router, stop_workers


def _cfg():
    from types import SimpleNamespace

    return SimpleNamespace(
        table=200,
        clean_conntrack=False,
        domain_route_ttl=300,
        routes=[],
        interfaces=[],
    )


class TestRouteAddIsIdempotentReplace(unittest.TestCase):
    def test_repeated_add_emits_single_replace_and_no_add(self):
        """Installing the same route twice yields exactly one kernel
        'replace' and never an 'add' — the watcher design requires uniform
        replace semantics so a re-added route heals rather than duplicates."""
        batches = []

        def fake_run(argv, **kwargs):
            if argv[:2] == ["ip", "-batch"]:
                batches.append(kwargs.get("input") or "")
            return MagicMock(returncode=0, stderr="", stdout="")

        route = RouteObject(
            net=ipaddress.IPv4Network("203.0.113.8/32"),
            nhid=3,
            proto=STATIC_PROTO,
            metric=STATIC_METRIC,
        )

        with patch("subprocess.run", side_effect=fake_run):
            with hermetic_router(_cfg()) as router:
                router.add_route(route, immediate=True)
                router.add_route(route, immediate=True)

        replaces = [b for b in batches if "route replace 203.0.113.8/32" in b]
        self.assertEqual(
            len(replaces), 2, "each add applies an idempotent replace"
        )
        for batch in batches:
            self.assertNotIn(
                "route add ",
                batch,
                "route installation must never emit non-idempotent 'add'",
            )

    def test_queued_add_replaces_without_duplicating_kernel_state(self):
        """A non-immediate add queues exactly one 'replace' per distinct
        route; a refresh of the same route queues nothing new."""
        with patch("subprocess.run"):
            with hermetic_router(_cfg()) as router:
                stop_workers(router)  # keep the queue observable, not drained
                first = RouteObject(
                    net=ipaddress.IPv4Network("198.51.100.20/32"),
                    nhid=5,
                    proto=DNS_PROTO,
                    metric=DNS_METRIC,
                    ttl=300,
                )
                router.add_route(first)
                router.add_route(first)  # same identity: refresh, no new spec
                stop_workers(router)
                queued = drain_queue(router)

        self.assertEqual(len(queued), 1, "one kernel operation per route")
        cmd, spec = queued[0]
        self.assertEqual(cmd, "replace")
        self.assertEqual(spec["dst"], "198.51.100.20")
        self.assertEqual(spec["proto"], DNS_PROTO)
        self.assertEqual(spec["priority"], DNS_METRIC)


if __name__ == "__main__":
    unittest.main()
