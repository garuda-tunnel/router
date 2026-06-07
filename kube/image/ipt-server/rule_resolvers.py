"""Rule type inference for ipt_server settings.

Each resolver decides whether a raw string value belongs to its domain and
returns a concrete Rule instance; otherwise it returns None and the pipeline
tries the next resolver. The pipeline order is: NetResolver (CIDR/IPv4) ->
CountryResolver (ISO-3166-1 alpha-2) -> RegexResolver (fallback, always wins
or raises).
"""

from __future__ import annotations

import ipaddress
import re
from abc import ABC, abstractmethod

import pycountry

from Config import CountryRule, DomainRule, NetRule, Rule


class RuleResolver(ABC):
    """Base class for rule resolvers. Each resolver owns one input shape."""

    @abstractmethod
    def try_resolve(self, value: str) -> Rule | None:
        """Return a Rule if value belongs to this resolver's domain, else None."""


class NetResolver(RuleResolver):
    """Resolves CIDR strings and bare IPv4 addresses into NetRule."""

    def try_resolve(self, value: str) -> Rule | None:
        if "/" in value:
            try:
                net = ipaddress.ip_network(value, strict=False)
            except ValueError:
                return None
            return NetRule(net=str(net))

        if value.count(".") == 3:
            try:
                addr = ipaddress.ip_address(value)
            except ValueError:
                return None
            return NetRule(net=f"{addr}/32")

        return None


class CountryResolver(RuleResolver):
    """Resolves ISO-3166-1 alpha-2 country codes (case-insensitive) into CountryRule."""

    def try_resolve(self, value: str) -> Rule | None:
        if len(value) != 2:
            return None
        normalized = value.upper()
        if not normalized.isalpha():
            return None
        if pycountry.countries.get(alpha_2=normalized) is None:
            return None
        return CountryRule(country=normalized)


class RegexResolver(RuleResolver):
    """Terminal fallback: compiles value as regex, returns DomainRule or raises."""

    def try_resolve(self, value: str) -> Rule | None:
        try:
            re.compile(value)
        except re.error as exc:
            raise ValueError(f"invalid regex: {exc}") from exc
        return DomainRule(domain=value)


DEFAULT_RESOLVERS: list[RuleResolver] = [
    NetResolver(),
    CountryResolver(),
    RegexResolver(),
]


def resolve_rule(
    value: str,
    pipeline: list[RuleResolver] = DEFAULT_RESOLVERS,
) -> Rule:
    """Walk the resolver pipeline; return the first matching Rule.

    The terminal resolver (RegexResolver) always returns a DomainRule or raises,
    so in practice this function either returns or raises.
    """
    for resolver in pipeline:
        result = resolver.try_resolve(value)
        if result is not None:
            return result
    raise ValueError(f"no resolver matched value {value!r}")
