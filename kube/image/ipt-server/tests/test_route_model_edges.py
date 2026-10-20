"""Edge cases of the small first-party models (route.py, dns_records.py, lib.py).

TTL lifecycle at exact expiry instants, normalization of host bits and
address boundaries, error contracts of ARecord, and the TimeMeasure
context manager.
"""

import ipaddress
from datetime import datetime, timedelta

import pytest

from dns_records import ARecord
from lib import TimeMeasure, timeit
from route import RouteObject
from tests.support import FrozenClock


class TestRouteTtlEdges:
    def test_non_static_route_without_expiration_is_not_expired(self):
        """A DNS-class route with expiration=None is not expired (never armed)."""
        route = RouteObject(net="192.0.2.1/32", proto=250, metric=200)
        assert route.expired is False

    def test_reset_with_positive_ttl_arms_first_expiration(self):
        """reset_expiration(ttl) on a ttl-less route arms the deadline."""
        route = RouteObject(net="192.0.2.1/32", proto=250, metric=200)
        route.reset_expiration(100)
        assert route.ttl == 100
        assert route.expiration is not None
        assert route.expired is False

    def test_reset_without_ttl_and_no_stored_ttl_disarms(self):
        """reset_expiration(None) with no stored TTL clears the deadline."""
        route = RouteObject(net="192.0.2.1/32", proto=250, metric=200, ttl=50)
        route.expiration = datetime.now() + timedelta(seconds=50)
        route.ttl = None
        route.reset_expiration(None)
        assert route.expiration is None

    def test_reset_with_ttl_keeps_stored_max_ttl(self):
        """A smaller refresh TTL never shortens the stored TTL."""
        route = RouteObject(net="192.0.2.1/32", proto=250, metric=200, ttl=300)
        route.reset_expiration(60)
        assert route.ttl == 300

    def test_reset_with_longer_ttl_raises_stored_ttl(self):
        """A longer refresh TTL raises the stored TTL (max semantics both ways)."""
        route = RouteObject(net="192.0.2.1/32", proto=250, metric=200, ttl=300)
        route.reset_expiration(600)
        assert route.ttl == 600

    def test_reset_without_ttl_rearms_from_stored_ttl(self):
        """reset_expiration(None) with a stored TTL re-arms the deadline
        (found as a surviving mutant by the bounded mutmut run)."""
        route = RouteObject(net="192.0.2.1/32", proto=250, metric=200, ttl=300)
        route.expiration = None
        route.reset_expiration(None)
        assert route.expiration is not None

    def test_expiry_boundary_is_inclusive_of_deadline(self):
        """Exactly at the deadline the route is not yet expired (strict >);
        one tick past it, it is. Controlled by a truly fixed clock."""

        from unittest.mock import patch

        class FixedNow(datetime):
            instant = [None]

            @classmethod
            def now(cls, tz=None):
                return datetime.fromtimestamp(cls.instant[0], tz)

        import time as _time

        with patch("route.datetime", FixedNow):
            FixedNow.instant[0] = _time.time()
            route = RouteObject(net="192.0.2.1/32", proto=250, metric=200)
            route.expiration = datetime.fromtimestamp(FixedNow.instant[0])
            assert route.expired is False
            FixedNow.instant[0] += 1.0
            assert route.expired is True

    def test_net_string_is_normalized_with_host_bits(self):
        """A CIDR string with host bits normalizes to its network."""
        route = RouteObject(net="203.0.113.9/24")
        assert str(route.net) == "203.0.113.0/24"
        assert route.net_start == int(ipaddress.ip_address("203.0.113.0"))
        assert route.net_end == int(ipaddress.ip_address("203.0.113.255"))

    def test_boundary_addresses_are_accepted(self):
        """Smallest and largest IPv4 boundaries construct correctly."""
        assert str(RouteObject(net="0.0.0.0/32").net) == "0.0.0.0/32"
        assert str(RouteObject(net="255.255.255.255/32").net) == "255.255.255.255/32"

    def test_invalid_network_input_raises(self):
        """Non-IPv4 input fails at construction with ValueError."""
        try:
            RouteObject(net="2001:db8::/32")
        except ValueError:
            pass
        else:
            raise AssertionError("IPv6 must be rejected by the IPv4-only model")


class TestInterfacesSnapshot:
    @pytest.mark.xfail(
        strict=True,
        reason="router-internal#74: stacked @classmethod @property returns a "
        "bound method on the runtime Python 3.14, not the documented dict",
    )
    def test_interfaces_property_returns_read_only_snapshot(self):
        """Intended contract (route.py docstring): RouteObject.interfaces
        is a read-only dict snapshot of ifname -> [(ifindex, None)]; the
        property access itself must yield the dict, and mutating the
        returned dict must not touch shared state (router-internal#74)."""
        from ipt_server import state

        state.INTERFACES.update({"backbone": 3, "wg0": 7})
        snapshot = RouteObject.interfaces
        assert isinstance(snapshot, dict)
        assert snapshot == {"backbone": [(3, None)], "wg0": [(7, None)]}
        snapshot["evil"] = [(1, None)]
        assert "evil" not in state.INTERFACES


class TestARecord:
    def _msg(self, **overrides):
        msg = {
            "type": 1,
            "name": "probe.example.net",
            "query": "probe.example.net",
            "content": "192.0.2.10",
            "ttl": 300,
        }
        msg.update(overrides)
        return msg

    def test_trailing_dots_are_stripped(self):
        """FQDN trailing dots are stripped from name and query."""
        record = ARecord(self._msg(name="probe.example.net.", query="probe.example.net."))
        assert record.name == "probe.example.net"
        assert record.query == "probe.example.net"

    def test_non_a_type_is_rejected(self):
        """Only type 1 (A) records construct; anything else raises."""
        try:
            ARecord(self._msg(type=28))
        except ValueError as exc:
            assert "Invalid record type" in str(exc)
        else:
            raise AssertionError("non-A type must be rejected")

    def test_missing_ttl_defaults_to_none(self):
        """An absent TTL field parses to None (fallback path)."""
        msg = self._msg()
        del msg["ttl"]
        assert ARecord(msg).ttl is None

    def test_ip_property_parses_content(self):
        """The ip property exposes the content as an IPv4Network."""
        record = ARecord(self._msg())
        assert record.ip == ipaddress.ip_network("192.0.2.10")


class TestLibHelpers:
    def test_timeit_logs_execution_time(self, caplog):
        """timeit logs the wrapped call's duration and returns its value."""
        import logging

        @timeit
        def work(value):
            return value * 2

        with caplog.at_level(logging.INFO, logger="lib"):
            assert work(21) == 42
        assert any("work" in r.getMessage() for r in caplog.records)

    def test_time_measure_logs_block_duration(self, caplog):
        """TimeMeasure logs the labeled block duration."""
        import logging

        with caplog.at_level(logging.INFO, logger="lib"):
            with TimeMeasure("labeled block"):
                sum(range(10))
        assert any(
            "labeled block took" in r.getMessage() for r in caplog.records
        )
