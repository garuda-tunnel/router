"""Route selection, config loading and startup wiring (T4).

Overlap suppression by weight, DomainRule specificity selection and tie
behavior, registry misses, unknown config types, background route loading
signalling, and the top-level main() startup sequence with every kernel
boundary patched.
"""

import ipaddress
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import Config
import pytest
from dns_records import ARecord
from route import DNS_PROTO, RouteObject
from tests.support import drain_queue, hermetic_router, stop_workers


def _cfg(routes=None, domain_route_ttl=300):
    return SimpleNamespace(
        table=200,
        clean_conntrack=False,
        domain_route_ttl=domain_route_ttl,
        routes=routes if routes is not None else [],
        interfaces=[],
    )


def _route(net: str, nhid=4, weight=0, dev=None) -> RouteObject:
    return RouteObject(
        net=ipaddress.IPv4Network(net), nhid=nhid, weight=weight, dev=dev
    )


class TestOverlapSuppression(unittest.TestCase):
    def test_covering_route_with_greater_weight_suppresses_insert(self):
        """A new route covered by a strictly larger prefix with strictly
        greater weight is suppressed (the covering route wins)."""
        with patch("subprocess.run"):
            with hermetic_router(_cfg()) as router:
                stop_workers(router)
                router.add_route(_route("10.0.0.0/8", weight=10))
                suppressed = router.add_route(_route("10.200.0.5/32", weight=1))

        self.assertIsNotNone(suppressed)
        self.assertEqual(str(suppressed.net), "10.0.0.0/8")
        self.assertEqual(len(list(router._route_tree)), 1)

    def test_same_start_covering_prefix_suppresses(self):
        """A larger prefix starting at the same address with greater weight
        suppresses the contained route (boundary of the <= comparison;
        found as a surviving mutant by the bounded mutmut run)."""
        with patch("subprocess.run"):
            with hermetic_router(_cfg()) as router:
                stop_workers(router)
                router.add_route(_route("10.0.0.0/8", weight=10))
                suppressed = router.add_route(_route("10.0.0.0/16", weight=1))

        self.assertEqual(str(suppressed.net), "10.0.0.0/8")
        self.assertEqual(len(list(router._route_tree)), 1)

    def test_covering_route_with_equal_or_lower_weight_does_not_suppress(self):
        """Equal or lower weight on the covering prefix does not suppress."""
        with patch("subprocess.run"):
            with hermetic_router(_cfg()) as router:
                stop_workers(router)
                router.add_route(_route("10.0.0.0/8", weight=1))
                inserted = router.add_route(_route("10.200.0.5/32", weight=1))
                self.assertEqual(str(inserted.net), "10.200.0.5/32")
                second = router.add_route(_route("10.200.0.6/32", weight=99))
                self.assertEqual(str(second.net), "10.200.0.6/32")

        self.assertEqual(len(list(router._route_tree)), 3)

    def test_refresh_result_is_not_aliased_to_tree_state(self):
        """The RouteObject returned from add_route is a copy: mutating it
        cannot corrupt the interval-tree state."""
        with patch("subprocess.run"):
            with hermetic_router(_cfg()) as router:
                stop_workers(router)
                result = router.add_route(_route("192.0.2.0/24"))
                result.ttl = 99999
                result.expiration = None

        interval = next(iter(router._route_tree))
        self.assertIsNone(interval.data.ttl)


class TestDomainSelection(unittest.TestCase):
    def _router(self, groups):
        cfg = _cfg(routes=groups)
        ctx = hermetic_router(cfg)
        router = ctx.__enter__()
        self.addCleanup(lambda: (stop_workers(router), ctx.__exit__(None, None, None)))
        return router

    @staticmethod
    def _record(name, ip="192.0.2.10", ttl=300):
        return ARecord(
            {"type": 1, "name": name, "query": name, "content": ip, "ttl": ttl}
        )

    def test_longer_regex_wins_over_shorter(self):
        """The most specific (longest) matching domain regex is selected."""
        generic = Config.RouteActionGroup(
            route=[Config.RouteMember(dev="wg_a")],
            rules=[Config.DomainRule(domain=r".*\.example\.net")],
        )
        specific = Config.RouteActionGroup(
            route=[Config.RouteMember(dev="wg_b")],
            rules=[Config.DomainRule(domain=r"probe\.example\.net")],
        )
        with patch("subprocess.run"):
            router = self._router([generic, specific])
            router._nhg_registry[generic.nhg_descriptor] = 11
            router._nhg_registry[specific.nhg_descriptor] = 22
            rv = router.on_a_record(self._record("probe.example.net"))

        self.assertEqual(rv["ttl"], 300)
        (interval,) = list(router._route_tree)
        self.assertEqual(interval.data.nhid, 22, "the longer regex must win")

    def test_equal_specificity_first_configured_group_wins(self):
        """Equal-length regexes in distinct groups: the first configured
        group is selected."""
        twin_a = Config.RouteActionGroup(
            route=[Config.RouteMember(dev="wg_a")],
            rules=[Config.DomainRule(domain=r"tie\.example\.net")],
        )
        twin_b = Config.RouteActionGroup(
            route=[Config.RouteMember(dev="wg_b")],
            rules=[Config.DomainRule(domain=r"tie\.example\.net")],
        )
        with patch("subprocess.run"):
            router = self._router([twin_a, twin_b])
            router._nhg_registry[twin_a.nhg_descriptor] = 41
            router._nhg_registry[twin_b.nhg_descriptor] = 42
            rv = router.on_a_record(self._record("tie.example.net", "192.0.2.11"))

        self.assertEqual(rv["ttl"], 300)
        (interval,) = list(router._route_tree)
        self.assertEqual(interval.data.nhid, 41, "first configured group wins ties")

    def test_matching_group_missing_from_registry_installs_nothing(self):
        """A matching group without a registered nhid selects no route; the
        reply falls back to the configured domain TTL."""
        group = Config.RouteActionGroup(
            route=[Config.RouteMember(dev="backbone")],
            rules=[Config.DomainRule(domain=r"probe\.example\.net")],
        )
        with patch("subprocess.run"):
            router = self._router([group])  # registry deliberately empty
            rv = router.on_a_record(self._record("probe.example.net"))

        self.assertEqual(rv["ttl"], 300)
        self.assertEqual(len(list(router._route_tree)), 0)

    def test_duplicate_matching_rules_counted_once_per_group(self):
        """Two identical rules in one group match as one candidate; a record
        still installs exactly one route."""
        group = Config.RouteActionGroup(
            route=[Config.RouteMember(dev="backbone")],
            rules=[
                Config.DomainRule(domain=r"probe\.example\.net"),
                Config.DomainRule(domain=r"probe\.example\.net"),
            ],
        )
        with patch("subprocess.run"):
            router = self._router([group])
            router._nhg_registry[group.nhg_descriptor] = 7
            router.on_a_record(self._record("probe.example.net", "192.0.2.12"))

        self.assertEqual(len(list(router._route_tree)), 1)

    def test_negative_ttl_is_unusable_and_falls_back_to_domain_ttl(self):
        """A negative answer TTL is unusable; the configured domain TTL is
        used instead (a DNS-learned route is never permanent)."""
        group = Config.RouteActionGroup(
            route=[Config.RouteMember(dev="backbone")],
            rules=[Config.DomainRule(domain=r"probe\.example\.net")],
        )
        with patch("subprocess.run"):
            router = self._router([group])
            router._nhg_registry[group.nhg_descriptor] = 7
            rv = router.on_a_record(self._record("probe.example.net", ttl=-5))

        self.assertEqual(rv["ttl"], 300)


class TestLoadRoutesVariants(unittest.TestCase):
    def test_unknown_route_type_is_skipped_with_warning(self):
        """Config entries that are neither groups nor known legacy types are
        skipped without aborting the load."""
        group = Config.RouteActionGroup(
            route=[Config.RouteMember(dev="backbone")],
            rules=[Config.NetRule(net="192.0.2.0/24")],
        )
        with patch("subprocess.run"):
            with hermetic_router(_cfg(routes=[object(), group])) as router:
                router._nhg_registry[group.nhg_descriptor] = 9
                router._load_routes()

        self.assertEqual(len(list(router._route_tree)), 1)

    def test_country_rule_expands_ipdb_subnets(self):
        """CountryRule entries expand into one route per subnet via IPDB."""
        group = Config.RouteActionGroup(
            route=[Config.RouteMember(dev="backbone")],
            rules=[
                Config.CountryRule(country="XX"),
                Config.NetRule(net="198.51.100.0/24"),
            ],
        )
        fake_ipdb = MagicMock()
        fake_ipdb.__getitem__.return_value = ["203.0.113.0/25", "203.0.113.128/25"]
        with patch("subprocess.run"):
            with hermetic_router(_cfg(routes=[group]), ipdb=fake_ipdb) as router:
                router._nhg_registry[group.nhg_descriptor] = 9
                router._load_routes()

        nets = {str(i.data.net) for i in router._route_tree}
        self.assertEqual(
            nets,
            {"203.0.113.0/25", "203.0.113.128/25", "198.51.100.0/24"},
        )

    def test_domain_rule_creates_no_static_route(self):
        """DomainRule entries are handled by on_a_record only: loading them
        installs nothing."""
        group = Config.RouteActionGroup(
            route=[Config.RouteMember(dev="backbone")],
            rules=[Config.DomainRule(domain=r".*\.example\.net")],
        )
        with patch("subprocess.run"):
            with hermetic_router(_cfg(routes=[group])) as router:
                router._nhg_registry[group.nhg_descriptor] = 9
                router._load_routes()

        self.assertEqual(len(list(router._route_tree)), 0)

    def test_missing_registry_nhid_skips_group(self):
        """A group missing from the nhg registry is skipped with an error."""
        group = Config.RouteActionGroup(
            route=[Config.RouteMember(dev="backbone")],
            rules=[Config.NetRule(net="192.0.2.0/24")],
        )
        with patch("subprocess.run"):
            with hermetic_router(_cfg(routes=[group])) as router:
                # setup_nexthop_group normally populates the registry;
                # stub it so the registry stays empty and _load_routes
                # must handle the missing-nhid group.
                with patch.object(type(router), "setup_nexthop_group"):
                    router._load_routes()

        self.assertEqual(len(list(router._route_tree)), 0)


class TestBackgroundRouteLoading(unittest.TestCase):
    def test_start_route_loading_signals_completion_on_success(self):
        """start_route_loading loads in a background thread and always sets
        the completion event; loaded routes are observable."""
        group = Config.RouteActionGroup(
            route=[Config.RouteMember(dev="backbone")],
            rules=[Config.NetRule(net="192.0.2.0/24")],
        )
        with patch("subprocess.run"):
            with hermetic_router(_cfg(routes=[group])) as router:
                router._nhg_registry[group.nhg_descriptor] = 9
                with patch("nexthop.flush_owned"), patch("nexthop.create_group"):
                    router.start_route_loading()
                    self.assertTrue(
                        router._routes_loaded.wait(timeout=5.0),
                        "loading completion must be signalled",
                    )
        self.assertEqual(len(list(router._route_tree)), 1)

    def test_start_route_loading_signals_completion_on_failure(self):
        """A failing load still signals completion; no routes are loaded."""
        with patch("subprocess.run"):
            with hermetic_router(_cfg()) as router:
                with patch.object(
                    type(router), "_load_routes", side_effect=RuntimeError("boom")
                ):
                    router.start_route_loading()
                    self.assertTrue(router._routes_loaded.wait(timeout=5.0))
        self.assertEqual(len(list(router._route_tree)), 0)


class TestMainStartup(unittest.TestCase):
    def test_main_runs_startup_sequence_and_constructs_router(self):
        """main() wires the startup order: config, proto names, network
        state, Router construction, then async_main."""
        import asyncio

        import ipt_server.main as ipt_main
        from ipt_server import state

        fake_cfg = _cfg()
        fake_router = MagicMock()
        seen = {}

        def fake_async_run(coro):
            seen["async_ran"] = True
            coro.close()

        with (
            patch.object(ipt_main, "MySettings", return_value=fake_cfg),
            patch.object(ipt_main.rt_protos, "write_rt_protos") as fake_protos,
            patch.object(ipt_main, "startup_apply_network_state") as fake_apply,
            patch.object(ipt_main, "Router", return_value=fake_router) as fake_router_cls,
            patch.object(ipt_main.asyncio, "run", side_effect=fake_async_run),
        ):
            ipt_main.main()

        self.assertIs(state.CONFIG, fake_cfg)
        self.assertIs(state.ROUTER, fake_router)
        fake_protos.assert_called_once()
        fake_apply.assert_called_once()
        fake_router_cls.assert_called_once_with(fake_cfg, ipdb=ipt_main.IPDB)
        self.assertTrue(seen.get("async_ran"))

    def test_main_keyboard_interrupt_shuts_down_gracefully(self):
        """A KeyboardInterrupt from async_main is logged, not re-raised."""
        import ipt_server.main as ipt_main
        from ipt_server import state

        fake_cfg = _cfg()

        def raising_run(coro):
            coro.close()
            raise KeyboardInterrupt

        with (
            patch.object(ipt_main, "MySettings", return_value=fake_cfg),
            patch.object(ipt_main.rt_protos, "write_rt_protos"),
            patch.object(ipt_main, "startup_apply_network_state"),
            patch.object(ipt_main, "Router", return_value=MagicMock()),
            patch.object(ipt_main.asyncio, "run", side_effect=raising_run),
        ):
            ipt_main.main()  # must not raise


if __name__ == "__main__":
    unittest.main()
