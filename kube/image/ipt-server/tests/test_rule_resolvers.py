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
        """A /8 CIDR string resolves to a NetRule carrying it verbatim."""
        rule = NetResolver().try_resolve("10.0.0.0/8")
        assert isinstance(rule, NetRule)
        assert rule.net == "10.0.0.0/8"

    def test_cidr_slash_24(self):
        """A /24 CIDR string resolves to a NetRule carrying it verbatim."""
        rule = NetResolver().try_resolve("10.0.0.0/24")
        assert isinstance(rule, NetRule)
        assert rule.net == "10.0.0.0/24"

    def test_bare_ipv4_becomes_slash_32(self):
        """A bare IPv4 is normalized to a /32 NetRule."""
        rule = NetResolver().try_resolve("192.0.2.8")
        assert isinstance(rule, NetRule)
        assert rule.net == "192.0.2.8/32"

    def test_rejects_integer_ip(self):
        """"1" looks like an integer IP but must not resolve as one (three-dot guard)."""
        # "1" is a valid ipaddress.ip_address(1) -> 0.0.0.1, but we guard on three dots.
        assert NetResolver().try_resolve("1") is None

    def test_rejects_hostname_with_dots(self):
        """A hostname with fewer than three dots must not parse as an IP."""
        # 'example.net' has 1 dot, not 3; we must not try to parse it as IP.
        assert NetResolver().try_resolve("example.net") is None

    def test_rejects_four_dots(self):
        """Five dotted fields are not an IPv4 address and must not resolve."""
        assert NetResolver().try_resolve("192.0.2.3.4") is None

    def test_accepts_non_canonical_cidr(self):
        """Host bits in a CIDR are normalized away (strict=False)."""
        # strict=False allows host bits in network address.
        rule = NetResolver().try_resolve("10.0.0.5/24")
        assert isinstance(rule, NetRule)
        assert rule.net == "10.0.0.0/24"

    def test_rejects_bad_cidr_prefix(self):
        """An out-of-range prefix length does not resolve."""
        assert NetResolver().try_resolve("10.0.0.0/99") is None

    def test_rejects_invalid_ip(self):
        """Malformed octets do not resolve."""
        assert NetResolver().try_resolve("999.999.999.999") is None


class TestCountryResolver:
    @pytest.mark.parametrize("raw", ["AM", "am", "Am"])
    def test_iso_code_resolves_uppercased(self, raw):
        """Any case variant of an ISO alpha-2 code resolves to a CountryRule
        with the uppercased code (merged from same-coverage duplicates
        reported by pytest_deduplicate)."""
        rule = CountryResolver().try_resolve(raw)
        assert isinstance(rule, CountryRule)
        assert rule.country == "AM"

    def test_uppercase_ru(self):
        """Another valid ISO code (RU) resolves to a CountryRule."""
        rule = CountryResolver().try_resolve("RU")
        assert isinstance(rule, CountryRule)
        assert rule.country == "RU"

    def test_invalid_iso_code(self):
        """"XX" is not a valid ISO alpha-2 code and does not resolve as country."""
        # 'XX' is a valid two-letter string but not a valid ISO alpha_2 code.
        assert CountryResolver().try_resolve("XX") is None

    @pytest.mark.parametrize("raw", ["A", "AMM"])
    def test_wrong_length_is_not_a_country_code(self, raw):
        """Strings of any length but two are not country codes (merged
        from same-coverage duplicates reported by pytest_deduplicate)."""
        assert CountryResolver().try_resolve(raw) is None

    def test_non_alpha(self):
        """Digits make the string invalid as a country code."""
        assert CountryResolver().try_resolve("A1") is None

    def test_empty_string(self):
        """The empty string resolves to nothing."""
        assert CountryResolver().try_resolve("") is None


class TestRegexResolver:
    def test_valid_regex_with_escape(self):
        """A regex with escapes resolves to a DomainRule carrying it verbatim."""
        rule = RegexResolver().try_resolve(r".*\.test")
        assert isinstance(rule, DomainRule)
        assert rule.domain == r".*\.test"

    def test_valid_regex_any(self):
        """A catch-all regex resolves to a DomainRule."""
        rule = RegexResolver().try_resolve(".*")
        assert isinstance(rule, DomainRule)
        assert rule.domain == ".*"

    def test_valid_regex_literal_hostname(self):
        """A literal-looking hostname compiles as a regex and resolves to a DomainRule."""
        # 'example.net' compiles as regex; the dot is a metachar but it compiles.
        rule = RegexResolver().try_resolve("example.net")
        assert isinstance(rule, DomainRule)
        assert rule.domain == "example.net"

    def test_valid_regex_two_letter(self):
        """Two-letter non-ISO strings fall through to the regex resolver."""
        # Two-letter strings that aren't valid ISO codes reach RegexResolver.
        rule = RegexResolver().try_resolve("XX")
        assert isinstance(rule, DomainRule)
        assert rule.domain == "XX"

    def test_invalid_regex_raises(self):
        """An uncompilable regex raises ValueError naming "invalid regex"."""
        with pytest.raises(ValueError, match="invalid regex"):
            RegexResolver().try_resolve("[invalid")


class TestPipeline:
    def test_default_resolvers_order(self):
        """The default pipeline tries Net, then Country, then Regex."""
        assert len(DEFAULT_RESOLVERS) == 3
        assert isinstance(DEFAULT_RESOLVERS[0], NetResolver)
        assert isinstance(DEFAULT_RESOLVERS[1], CountryResolver)
        assert isinstance(DEFAULT_RESOLVERS[2], RegexResolver)

    def test_cidr_resolves_to_net(self):
        """Through the full pipeline a CIDR becomes a NetRule."""
        rule = resolve_rule("10.0.0.0/8")
        assert isinstance(rule, NetRule)
        assert rule.net == "10.0.0.0/8"

    def test_bare_ip_resolves_to_net_32(self):
        """Through the full pipeline a bare IP becomes a /32 NetRule."""
        rule = resolve_rule("192.0.2.8")
        assert isinstance(rule, NetRule)
        assert rule.net == "192.0.2.8/32"

    def test_valid_iso_resolves_to_country(self):
        """Through the full pipeline an ISO code becomes a CountryRule."""
        rule = resolve_rule("AM")
        assert isinstance(rule, CountryRule)
        assert rule.country == "AM"

    def test_lowercase_iso_resolves_to_country(self):
        """Lowercase ISO codes are normalized then resolved."""
        rule = resolve_rule("am")
        assert isinstance(rule, CountryRule)
        assert rule.country == "AM"

    def test_invalid_iso_falls_through_to_regex(self):
        """A non-ISO two-letter string ends up as a DomainRule."""
        rule = resolve_rule("XX")
        assert isinstance(rule, DomainRule)
        assert rule.domain == "XX"

    def test_three_letter_lowercase_is_regex(self):
        """Lowercase three-letter strings are regex, not country codes."""
        rule = resolve_rule("amm")
        assert isinstance(rule, DomainRule)
        assert rule.domain == "amm"

    def test_regex_with_meta_chars(self):
        """Regex metacharacters survive resolution verbatim."""
        rule = resolve_rule(r".*\.test")
        assert isinstance(rule, DomainRule)
        assert rule.domain == r".*\.test"

    def test_invalid_regex_raises(self):
        """An uncompilable regex raises ValueError naming "invalid regex"."""
        with pytest.raises(ValueError, match="invalid regex"):
            resolve_rule("[invalid")
