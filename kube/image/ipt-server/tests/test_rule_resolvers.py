"""Tests for rule_resolvers: NetResolver, CountryResolver, RegexResolver, pipeline."""

from __future__ import annotations

import pytest

from Config import CountryRule, DomainRule, NetRule
from rule_resolvers import (
    CountryResolver,
    DEFAULT_RESOLVERS,
    NetResolver,
    RegexResolver,
    resolve_rule,
)


class TestNetResolver:
    def test_cidr_slash_8(self):
        rule = NetResolver().try_resolve("1.0.0.0/8")
        assert isinstance(rule, NetRule)
        assert rule.net == "1.0.0.0/8"

    def test_cidr_slash_24(self):
        rule = NetResolver().try_resolve("10.0.0.0/24")
        assert isinstance(rule, NetRule)
        assert rule.net == "10.0.0.0/24"

    def test_bare_ipv4_becomes_slash_32(self):
        rule = NetResolver().try_resolve("8.8.8.8")
        assert isinstance(rule, NetRule)
        assert rule.net == "8.8.8.8/32"

    def test_rejects_integer_ip(self):
        # "1" is a valid ipaddress.ip_address(1) -> 0.0.0.1, but we guard on three dots.
        assert NetResolver().try_resolve("1") is None

    def test_rejects_hostname_with_dots(self):
        # 'facebook.com' has 1 dot, not 3; we must not try to parse it as IP.
        assert NetResolver().try_resolve("facebook.com") is None

    def test_rejects_four_dots(self):
        assert NetResolver().try_resolve("1.2.3.4.5") is None

    def test_accepts_non_canonical_cidr(self):
        # strict=False allows host bits in network address.
        rule = NetResolver().try_resolve("10.0.0.5/24")
        assert isinstance(rule, NetRule)
        assert rule.net == "10.0.0.0/24"

    def test_rejects_bad_cidr_prefix(self):
        assert NetResolver().try_resolve("10.0.0.0/99") is None

    def test_rejects_invalid_ip(self):
        assert NetResolver().try_resolve("999.999.999.999") is None


class TestCountryResolver:
    def test_uppercase_am(self):
        rule = CountryResolver().try_resolve("AM")
        assert isinstance(rule, CountryRule)
        assert rule.country == "AM"

    def test_lowercase_am(self):
        rule = CountryResolver().try_resolve("am")
        assert isinstance(rule, CountryRule)
        assert rule.country == "AM"

    def test_mixed_case_am(self):
        rule = CountryResolver().try_resolve("Am")
        assert isinstance(rule, CountryRule)
        assert rule.country == "AM"

    def test_uppercase_ru(self):
        rule = CountryResolver().try_resolve("RU")
        assert isinstance(rule, CountryRule)
        assert rule.country == "RU"

    def test_invalid_iso_code(self):
        # 'XX' is a valid two-letter string but not a valid ISO alpha_2 code.
        assert CountryResolver().try_resolve("XX") is None

    def test_wrong_length_short(self):
        assert CountryResolver().try_resolve("A") is None

    def test_wrong_length_long(self):
        assert CountryResolver().try_resolve("AMM") is None

    def test_non_alpha(self):
        assert CountryResolver().try_resolve("A1") is None

    def test_empty_string(self):
        assert CountryResolver().try_resolve("") is None


class TestRegexResolver:
    def test_valid_regex_with_escape(self):
        rule = RegexResolver().try_resolve(r".*\.ru")
        assert isinstance(rule, DomainRule)
        assert rule.domain == r".*\.ru"

    def test_valid_regex_any(self):
        rule = RegexResolver().try_resolve(".*")
        assert isinstance(rule, DomainRule)
        assert rule.domain == ".*"

    def test_valid_regex_literal_hostname(self):
        # 'facebook.com' compiles as regex; the dot is a metachar but it compiles.
        rule = RegexResolver().try_resolve("facebook.com")
        assert isinstance(rule, DomainRule)
        assert rule.domain == "facebook.com"

    def test_valid_regex_two_letter(self):
        # Two-letter strings that aren't valid ISO codes reach RegexResolver.
        rule = RegexResolver().try_resolve("XX")
        assert isinstance(rule, DomainRule)
        assert rule.domain == "XX"

    def test_invalid_regex_raises(self):
        with pytest.raises(ValueError, match="invalid regex"):
            RegexResolver().try_resolve("[invalid")


class TestPipeline:
    def test_default_resolvers_order(self):
        assert len(DEFAULT_RESOLVERS) == 3
        assert isinstance(DEFAULT_RESOLVERS[0], NetResolver)
        assert isinstance(DEFAULT_RESOLVERS[1], CountryResolver)
        assert isinstance(DEFAULT_RESOLVERS[2], RegexResolver)

    def test_cidr_resolves_to_net(self):
        rule = resolve_rule("10.0.0.0/8")
        assert isinstance(rule, NetRule)
        assert rule.net == "10.0.0.0/8"

    def test_bare_ip_resolves_to_net_32(self):
        rule = resolve_rule("8.8.8.8")
        assert isinstance(rule, NetRule)
        assert rule.net == "8.8.8.8/32"

    def test_valid_iso_resolves_to_country(self):
        rule = resolve_rule("AM")
        assert isinstance(rule, CountryRule)
        assert rule.country == "AM"

    def test_lowercase_iso_resolves_to_country(self):
        rule = resolve_rule("am")
        assert isinstance(rule, CountryRule)
        assert rule.country == "AM"

    def test_invalid_iso_falls_through_to_regex(self):
        rule = resolve_rule("XX")
        assert isinstance(rule, DomainRule)
        assert rule.domain == "XX"

    def test_three_letter_lowercase_is_regex(self):
        rule = resolve_rule("amm")
        assert isinstance(rule, DomainRule)
        assert rule.domain == "amm"

    def test_regex_with_meta_chars(self):
        rule = resolve_rule(r".*\.ru")
        assert isinstance(rule, DomainRule)
        assert rule.domain == r".*\.ru"

    def test_invalid_regex_raises(self):
        with pytest.raises(ValueError, match="invalid regex"):
            resolve_rule("[invalid")
