"""DomainRule matching is full-string, not prefix: verified through the
public Router.on_a_record behavior.

A rule like ``.*\\.example\\.net`` must select the route for
``probe.example.net`` but NOT for ``probe.example.net.suffix.test.net``
(an extra suffix) nor for a mere prefix-bearing name. Replaces the earlier
source-scan test that grepped Router.py for ``fullmatch`` — a scan cannot
fail when the runtime behavior breaks.
"""

import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import Config
from dns_records import ARecord
from route import DNS_PROTO
from tests.support import hermetic_router


def _record(name: str, ip: str, ttl=300) -> ARecord:
    return ARecord(
        {"type": 1, "name": name, "query": name, "content": ip, "ttl": ttl}
    )


class TestDomainRuleFullmatchBehavior(unittest.TestCase):
    def _router(self, pattern):
        group = Config.RouteActionGroup(
            route=[Config.RouteMember(dev="backbone")],
            rules=[Config.DomainRule(domain=pattern)],
        )
        ctx = hermetic_router(
            SimpleNamespace(
                table=200,
                clean_conntrack=False,
                domain_route_ttl=300,
                routes=[group],
                interfaces=[],
            )
        )
        router = ctx.__enter__()
        router._nhg_registry[group.nhg_descriptor] = 7
        self.addCleanup(ctx.__exit__, None, None, None)
        return router

    def test_exact_name_matches_and_installs_dns_route(self):
        """A record whose name equals the rule pattern selects the route:
        a DNS-class /32 is installed through the group's nhid."""
        with patch("subprocess.run"):
            router = self._router(r".*\.example\.net")
            rv = router.on_a_record(_record("probe.example.net", "192.0.2.10"))

        self.assertEqual(rv["ttl"], 300)
        intervals = list(router._route_tree)
        self.assertEqual(len(intervals), 1)
        self.assertEqual(str(intervals[0].data.net), "192.0.2.10/32")
        self.assertEqual(intervals[0].data.proto, DNS_PROTO)
        self.assertEqual(intervals[0].data.nhid, 7)

    def test_extra_suffix_does_not_match(self):
        """``.*\\.example\\.net`` must NOT match a name with an extra suffix
        (prefix-only matching would hijack unrelated domains). The reply TTL
        still falls back to the configured domain TTL; the discriminating
        oracle is that no route is installed."""
        with patch("subprocess.run"):
            router = self._router(r".*\.example\.net")
            rv = router.on_a_record(
                _record("probe.example.net.suffix.test", "192.0.2.11")
            )

        self.assertEqual(rv["ttl"], 300)
        self.assertEqual(
            len(list(router._route_tree)), 0, "no route may be installed"
        )

    def test_prefix_only_name_does_not_match(self):
        """A name that only shares a prefix with the pattern domain must
        not match: 'xprobe.example.net' is not 'probe.example.net'."""
        with patch("subprocess.run"):
            router = self._router(r"probe\.example\.net")
            rv = router.on_a_record(_record("xprobe.example.net", "192.0.2.12"))

        self.assertEqual(rv["ttl"], 300)
        self.assertEqual(
            len(list(router._route_tree)), 0, "no route may be installed"
        )


if __name__ == "__main__":
    unittest.main()
