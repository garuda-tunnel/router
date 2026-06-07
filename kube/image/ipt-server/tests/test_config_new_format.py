"""Tests for new Config v2 models: RouteMember, NhgDescriptor, RouteActionGroup, Rule types."""

import unittest

from Config import (
    RouteMember,
    NhgDescriptor,
    RouteActionGroup,
    DomainRule,
    NetRule,
    CountryRule,
    MySettings,
)
from pydantic import ValidationError


class TestConfigNewFormat(unittest.TestCase):
    """Tests for new Config v2 models: RouteMember, NhgDescriptor, RouteActionGroup, Rule types."""

    def test_route_action_group_parses_ordered_members(self):
        """RouteActionGroup parses route list as ordered member sequence.

        Validates: RouteActionGroup(route=[{gw: X}, {dev: border}]) builds NhgDescriptor with 2 members.
        Code: Config.RouteActionGroup
        Assertion: descriptor.members has 2 items in declaration order.
        """
        group = RouteActionGroup(
            route=[{"gw": "10.9.19.2"}, {"dev": "border"}],
            rules=[".*"],
        )
        descriptor = group.nhg_descriptor
        self.assertIsInstance(descriptor, NhgDescriptor)
        self.assertEqual(len(descriptor.members), 2)
        self.assertEqual(descriptor.members[0].gw, "10.9.19.2")
        self.assertIsNone(descriptor.members[0].dev)
        self.assertEqual(descriptor.members[1].dev, "border")
        self.assertIsNone(descriptor.members[1].gw)

    def test_route_action_group_single_member_nhg(self):
        """RouteActionGroup with single dev member still yields NhgDescriptor.

        Validates: RouteActionGroup(route=[{dev: border}]) -> NhgDescriptor with 1 member.
        Code: Config.RouteActionGroup
        Assertion: descriptor.members has exactly 1 item with dev='border'.
        """
        group = RouteActionGroup(
            route=[{"dev": "border"}],
            rules=["AM"],
        )
        descriptor = group.nhg_descriptor
        self.assertIsInstance(descriptor, NhgDescriptor)
        self.assertEqual(len(descriptor.members), 1)
        self.assertEqual(descriptor.members[0].dev, "border")

    def test_route_member_rejects_default_sentinel(self):
        """RouteMember rejects '_DEFAULT' as dev value.

        Validates: RouteMember(dev='_DEFAULT') raises ValueError.
        Code: Config.RouteMember.validate_exactly_one
        Assertion: ValidationError raised mentioning _DEFAULT sentinel removal.
        """
        with self.assertRaises(ValidationError) as ctx:
            RouteMember(dev="_DEFAULT")
        self.assertIn("_DEFAULT", str(ctx.exception))

    def test_route_member_rejects_both_gw_and_dev(self):
        """RouteMember rejects entries with both gw and dev set.

        Validates: RouteMember(gw='10.0.0.1', dev='border') raises ValueError.
        Code: Config.RouteMember.validate_exactly_one
        Assertion: ValidationError raised.
        """
        with self.assertRaises(ValidationError):
            RouteMember(gw="10.0.0.1", dev="border")

    def test_route_member_rejects_neither_gw_nor_dev(self):
        """RouteMember rejects entries with neither gw nor dev set.

        Validates: RouteMember(gw=None, dev=None) raises ValueError.
        Code: Config.RouteMember.validate_exactly_one
        Assertion: ValidationError raised.
        """
        with self.assertRaises(ValidationError):
            RouteMember(gw=None, dev=None)

    def test_domain_rule_has_no_weight(self):
        """DomainRule does not accept a weight field.

        Validates: DomainRule has no weight attribute.
        Code: Config.DomainRule
        Assertion: DomainRule instance lacks 'weight' attribute.
        """
        rule = DomainRule(domain=".*\\.ru")
        self.assertFalse(hasattr(rule, "weight"))

    def test_net_rule_has_no_weight(self):
        """NetRule does not accept a weight field.

        Validates: NetRule has no weight attribute.
        Code: Config.NetRule
        Assertion: NetRule instance lacks 'weight' attribute.
        """
        rule = NetRule(net="10.0.0.0/8")
        self.assertFalse(hasattr(rule, "weight"))

    def test_country_rule_has_no_weight(self):
        """CountryRule does not accept a weight field.

        Validates: CountryRule has no weight attribute.
        Code: Config.CountryRule
        Assertion: CountryRule instance lacks 'weight' attribute.
        """
        rule = CountryRule(country="AM")
        self.assertFalse(hasattr(rule, "weight"))

    def test_domain_rule_specificity_equals_regex_length(self):
        """DomainRule specificity equals the length of the domain regex string.

        Validates: longer domain regex yields higher specificity value.
        Code: Config.DomainRule.specificity
        Assertion: specific.specificity > generic.specificity.
        """
        generic = DomainRule(domain=".*")
        specific = DomainRule(domain=".*\\.example\\.com")
        self.assertEqual(generic.specificity, len(".*"))
        self.assertEqual(specific.specificity, len(".*\\.example\\.com"))
        self.assertGreater(specific.specificity, generic.specificity)

    def test_nhg_descriptor_equality_and_hash(self):
        """NhgDescriptor equality and hash are based on ordered member tuples.

        Validates: two NhgDescriptors with same members are equal and share hash.
        Code: Config.NhgDescriptor.__eq__ and __hash__
        Assertion: equal descriptors produce same hash; reordered members are not equal.
        """
        a = NhgDescriptor(
            members=[RouteMember(gw="10.9.19.2"), RouteMember(dev="border")]
        )
        b = NhgDescriptor(
            members=[RouteMember(gw="10.9.19.2"), RouteMember(dev="border")]
        )
        c = NhgDescriptor(
            members=[RouteMember(dev="border"), RouteMember(gw="10.9.19.2")]
        )
        self.assertEqual(a, b)
        self.assertEqual(hash(a), hash(b))
        self.assertNotEqual(a, c)


    def test_route_action_group_rejects_dict_rules(self):
        """Old dict format for rules is rejected with a migration-friendly error.

        Validates: RouteActionGroup(..., rules=[{"net": "1.0.0.0/8"}]) raises ValidationError
        whose message mentions that the string format is required.
        Code: Config.RouteActionGroup._normalize_rules
        """
        with self.assertRaises(ValidationError) as ctx:
            RouteActionGroup(
                route=[{"dev": "border"}],
                rules=[{"net": "1.0.0.0/8"}],
            )
        msg = str(ctx.exception)
        self.assertIn("must be a string", msg)
        self.assertIn("dict format was removed", msg)

    def test_route_action_group_bare_strings_infer_types(self):
        """Bare-string rules are resolved via rule_resolvers into typed Rule objects.

        Validates: a mixed list of strings yields the right Rule subclasses in order.
        Code: Config.RouteActionGroup._normalize_rules
        """
        group = RouteActionGroup(
            route=[{"dev": "border"}],
            rules=["1.0.0.0/8", "AM", ".*\\.ru"],
        )
        self.assertEqual(len(group.rules), 3)
        self.assertIsInstance(group.rules[0], NetRule)
        self.assertEqual(group.rules[0].net, "1.0.0.0/8")
        self.assertIsInstance(group.rules[1], CountryRule)
        self.assertEqual(group.rules[1].country, "AM")
        self.assertIsInstance(group.rules[2], DomainRule)
        self.assertEqual(group.rules[2].domain, ".*\\.ru")

    def test_route_action_group_bare_ip_promotes_to_slash_32(self):
        """A bare IPv4 string is normalized to /32 CIDR by NetResolver."""
        group = RouteActionGroup(
            route=[{"dev": "border"}],
            rules=["8.8.8.8"],
        )
        self.assertIsInstance(group.rules[0], NetRule)
        self.assertEqual(group.rules[0].net, "8.8.8.8/32")

    def test_route_action_group_rejects_invalid_regex(self):
        """Strings that cannot be parsed as CIDR, country, or regex raise ValidationError."""
        with self.assertRaises(ValidationError) as ctx:
            RouteActionGroup(
                route=[{"dev": "border"}],
                rules=["[invalid"],
            )
        self.assertIn("invalid regex", str(ctx.exception))

    def test_route_action_group_rejects_non_string_item(self):
        """Non-string non-dict items also produce the migration-friendly error."""
        with self.assertRaises(ValidationError) as ctx:
            RouteActionGroup(
                route=[{"dev": "border"}],
                rules=[123],
            )
        self.assertIn("must be a string", str(ctx.exception))

    def test_route_action_group_lowercase_country_normalized_to_upper(self):
        """CountryResolver uppercases the alpha_2 code before creating CountryRule."""
        group = RouteActionGroup(
            route=[{"dev": "border"}],
            rules=["am"],
        )
        self.assertIsInstance(group.rules[0], CountryRule)
        self.assertEqual(group.rules[0].country, "AM")

    def test_route_action_group_model_dump_round_trips(self):
        """RouteActionGroup.model_dump() result can be used to re-construct the model."""
        group = RouteActionGroup(
            route=[{"dev": "border"}],
            rules=["AM", "1.0.0.0/8", ".*\\.ru"],
        )
        dumped = group.model_dump()
        group2 = RouteActionGroup(**dumped)
        self.assertEqual(len(group2.rules), 3)
        self.assertIsInstance(group2.rules[0], CountryRule)
        self.assertIsInstance(group2.rules[1], NetRule)
        self.assertIsInstance(group2.rules[2], DomainRule)


class TestMySettingsBareStringRules(unittest.TestCase):
    """End-to-end: MySettings parses YAML-like input with bare-string rules."""

    def _settings(self, routes):
        return MySettings(
            table=200,
            pbr_mark=200,
            dns_mark=201,
            interfaces=["wg-firezone"],
            clean_conntrack=False,
            domain_route_ttl=100,
            nic_attach=["backbone", "border"],
            routes=routes,
        )

    def test_grouped_format_with_bare_string_rules_parses(self):
        settings = self._settings(
            [
                {
                    "route": [{"dev": "border"}],
                    "rules": ["AM", "1.0.0.0/8", ".*\\.ru"],
                },
            ]
        )
        self.assertEqual(len(settings.routes), 1)
        group = settings.routes[0]
        self.assertIsInstance(group, RouteActionGroup)
        self.assertEqual(len(group.rules), 3)
        self.assertIsInstance(group.rules[0], CountryRule)
        self.assertIsInstance(group.rules[1], NetRule)
        self.assertIsInstance(group.rules[2], DomainRule)

    def test_grouped_format_rejects_dict_rules_with_clear_message(self):
        with self.assertRaises(Exception) as ctx:
            self._settings(
                [
                    {
                        "route": [{"dev": "border"}],
                        "rules": [{"net": "1.0.0.0/8"}],
                    },
                ]
            )
        self.assertIn("must be a string", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
