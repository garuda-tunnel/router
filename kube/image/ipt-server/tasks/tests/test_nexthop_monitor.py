"""Unit tests for nexthop_monitor OSPF-aware single-active-member selection.

Validates: nexthop_monitor correctly switches nhg active member on liveness changes.
Code: tasks/nexthop_monitor.py
"""

from unittest.mock import patch, MagicMock, call

import pytest

from Config import NhgDescriptor, RouteMember
from tasks.nexthop_monitor import _probe_gw_alive, _tick, _GW_FAILURE_THRESHOLD


def _make_desc(*members):
    """Build an NhgDescriptor from dicts with gw= or dev= keys."""
    return NhgDescriptor(members=[RouteMember(**m) for m in members])


def _make_state(
    desc, gw_key=None, dev_key=None, gw_nhid=10, dev_nhid=11, group_nhid=12
):
    """Build nhg_registry, member_nhids for a two-member group."""
    nhg_registry = {desc: group_nhid}
    member_nhids = {}
    if gw_key is not None:
        member_nhids[gw_key] = gw_nhid
    if dev_key is not None:
        member_nhids[dev_key] = dev_nhid
    return nhg_registry, member_nhids


# ---------------------------------------------------------------------------
# _probe_gw_alive unit tests
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# 1. test_monitor_ospf_alive_keeps_primary
# ---------------------------------------------------------------------------


def test_monitor_ospf_alive_keeps_primary():
    """No replace calls when gw member remains alive and nothing changes.

    Validates: nexthop_monitor makes no replace calls in stable-alive state.
    Code: tasks/nexthop_monitor.py::_tick
    Assertion: nexthop.replace_nexthop_blackhole and replace_group not called.
    """
    gw = "10.9.19.2"
    desc = _make_desc({"gw": gw}, {"dev": "border"})
    gw_key = (gw, None)
    dev_key = (None, "border")
    nhg_registry, member_nhids = _make_state(
        desc, gw_key=gw_key, dev_key=dev_key, gw_nhid=10, dev_nhid=11, group_nhid=12
    )

    member_alive = {gw_key: True, dev_key: True}
    active_member = {desc: gw_key}
    consecutive_failures = {}

    mock_blackhole = MagicMock()
    mock_replace_group = MagicMock()
    mock_replace_nexthop = MagicMock()

    with (
        patch(
            "tasks.nexthop_monitor._probe_gw_alive",
            return_value=(True, "172.30.0.5", "backbone"),
        ),
        patch("tasks.nexthop_monitor._probe_dev_alive", return_value=(True, None)),
        patch("nexthop.replace_nexthop_blackhole", mock_blackhole),
        patch("nexthop.replace_nexthop", mock_replace_nexthop),
        patch("nexthop.replace_group", mock_replace_group),
    ):
        _tick(
            nhg_registry,
            member_nhids,
            member_alive,
            active_member,
            consecutive_failures,
            first_tick=False,
        )

    mock_blackhole.assert_not_called()
    mock_replace_group.assert_not_called()


# ---------------------------------------------------------------------------
# 2. test_monitor_ospf_dead_switches_to_fallback
# ---------------------------------------------------------------------------


def test_monitor_ospf_dead_switches_to_fallback():
    """When gw dies and dev is alive, group switches to dev member.

    Validates: nexthop_monitor blackholes gw member and switches group to dev.
    Code: tasks/nexthop_monitor.py::_tick
    Assertion: replace_nexthop_blackhole(gw_nhid) and replace_group(group_nhid, dev_nhid) called.
    """
    gw = "10.9.19.2"
    desc = _make_desc({"gw": gw}, {"dev": "border"})
    gw_key = (gw, None)
    dev_key = (None, "border")
    gw_nhid, dev_nhid, group_nhid = 10, 11, 12
    nhg_registry, member_nhids = _make_state(
        desc,
        gw_key=gw_key,
        dev_key=dev_key,
        gw_nhid=gw_nhid,
        dev_nhid=dev_nhid,
        group_nhid=group_nhid,
    )

    # Seeded state: gw was alive and primary active
    member_alive = {gw_key: True, dev_key: True}
    active_member = {desc: gw_key}
    consecutive_failures = {gw: _GW_FAILURE_THRESHOLD}  # already at threshold

    mock_blackhole = MagicMock()
    mock_replace_group = MagicMock()

    with (
        patch(
            "tasks.nexthop_monitor._probe_gw_alive", return_value=(False, None, None)
        ),
        patch("tasks.nexthop_monitor._probe_dev_alive", return_value=(True, None)),
        patch("nexthop.replace_nexthop_blackhole", mock_blackhole),
        patch("nexthop.replace_group", mock_replace_group),
    ):
        _tick(
            nhg_registry,
            member_nhids,
            member_alive,
            active_member,
            consecutive_failures,
            first_tick=False,
        )

    mock_blackhole.assert_called_once_with(gw_nhid)
    mock_replace_group.assert_called_once_with(group_nhid, dev_nhid)


# ---------------------------------------------------------------------------
# 3. test_monitor_ospf_recovery_switches_to_primary
# ---------------------------------------------------------------------------


def test_monitor_ospf_recovery_switches_to_primary():
    """When gw recovers and is higher priority, group switches back to gw member.

    Validates: nexthop_monitor restores gw member and switches group to primary.
    Code: tasks/nexthop_monitor.py::_tick
    Assertion: replace_nexthop(gw_nhid, ...) and replace_group(group_nhid, gw_nhid) called.
    """
    gw = "10.9.19.2"
    desc = _make_desc({"gw": gw}, {"dev": "border"})
    gw_key = (gw, None)
    dev_key = (None, "border")
    gw_nhid, dev_nhid, group_nhid = 10, 11, 12
    nhg_registry, member_nhids = _make_state(
        desc,
        gw_key=gw_key,
        dev_key=dev_key,
        gw_nhid=gw_nhid,
        dev_nhid=dev_nhid,
        group_nhid=group_nhid,
    )

    # Seeded state: gw was dead, dev was active
    member_alive = {gw_key: False, dev_key: True}
    active_member = {desc: dev_key}
    consecutive_failures = {}

    mock_replace_nexthop = MagicMock()
    mock_replace_group = MagicMock()

    with (
        patch(
            "tasks.nexthop_monitor._probe_gw_alive",
            return_value=(True, "172.30.0.5", "backbone"),
        ),
        patch("tasks.nexthop_monitor._probe_dev_alive", return_value=(True, None)),
        patch("nexthop.replace_nexthop", mock_replace_nexthop),
        patch("nexthop.replace_group", mock_replace_group),
    ):
        _tick(
            nhg_registry,
            member_nhids,
            member_alive,
            active_member,
            consecutive_failures,
            first_tick=False,
        )

    mock_replace_nexthop.assert_called_once_with(
        gw_nhid, via="172.30.0.5", dev="backbone"
    )
    mock_replace_group.assert_called_once_with(group_nhid, gw_nhid)


# ---------------------------------------------------------------------------
# 4. test_monitor_vtysh_transient_failure_no_change
# ---------------------------------------------------------------------------


def test_monitor_vtysh_transient_failure_no_change():
    """Two consecutive probe failures below threshold cause no group change.

    Validates: nexthop_monitor preserves last-known alive state below failure threshold.
    Code: tasks/nexthop_monitor.py::_tick
    Assertion: failure count < threshold -> no replace_nexthop_blackhole, no replace_group.
    """
    gw = "10.9.19.2"
    desc = _make_desc({"gw": gw}, {"dev": "border"})
    gw_key = (gw, None)
    dev_key = (None, "border")
    gw_nhid, dev_nhid, group_nhid = 10, 11, 12
    nhg_registry, member_nhids = _make_state(
        desc,
        gw_key=gw_key,
        dev_key=dev_key,
        gw_nhid=gw_nhid,
        dev_nhid=dev_nhid,
        group_nhid=group_nhid,
    )

    # Seeded: gw alive, gw is primary active
    member_alive = {gw_key: True, dev_key: True}
    active_member = {desc: gw_key}
    consecutive_failures = {}

    mock_blackhole = MagicMock()
    mock_replace_group = MagicMock()

    # Run 2 consecutive failure ticks (below threshold of 3)
    for _ in range(2):
        with (
            patch(
                "tasks.nexthop_monitor._probe_gw_alive",
                return_value=(False, None, None),
            ),
            patch("tasks.nexthop_monitor._probe_dev_alive", return_value=(True, None)),
            patch("nexthop.replace_nexthop_blackhole", mock_blackhole),
            patch("nexthop.replace_group", mock_replace_group),
        ):
            _tick(
                nhg_registry,
                member_nhids,
                member_alive,
                active_member,
                consecutive_failures,
                first_tick=False,
            )

    assert consecutive_failures.get(gw, 0) == 2
    mock_blackhole.assert_not_called()
    mock_replace_group.assert_not_called()


# ---------------------------------------------------------------------------
# 5. test_monitor_vtysh_3_failures_failclosed
# ---------------------------------------------------------------------------


def test_monitor_vtysh_3_failures_failclosed():
    """Three consecutive probe failures at threshold treat gw as dead.

    Validates: nexthop_monitor fails closed on reaching _GW_FAILURE_THRESHOLD.
    Code: tasks/nexthop_monitor.py::_tick
    Assertion: replace_nexthop_blackhole called, group switched to fallback.
    """
    gw = "10.9.19.2"
    desc = _make_desc({"gw": gw}, {"dev": "border"})
    gw_key = (gw, None)
    dev_key = (None, "border")
    gw_nhid, dev_nhid, group_nhid = 10, 11, 12
    nhg_registry, member_nhids = _make_state(
        desc,
        gw_key=gw_key,
        dev_key=dev_key,
        gw_nhid=gw_nhid,
        dev_nhid=dev_nhid,
        group_nhid=group_nhid,
    )

    # Seeded: gw alive, primary active
    member_alive = {gw_key: True, dev_key: True}
    active_member = {desc: gw_key}
    consecutive_failures = {}

    blackhole_calls = []
    group_calls = []

    # Run _GW_FAILURE_THRESHOLD consecutive failures
    for i in range(_GW_FAILURE_THRESHOLD):
        with (
            patch(
                "tasks.nexthop_monitor._probe_gw_alive",
                return_value=(False, None, None),
            ),
            patch("tasks.nexthop_monitor._probe_dev_alive", return_value=(True, None)),
            patch(
                "nexthop.replace_nexthop_blackhole", side_effect=blackhole_calls.append
            ),
            patch(
                "nexthop.replace_group",
                side_effect=lambda g, m: group_calls.append((g, m)),
            ),
        ):
            _tick(
                nhg_registry,
                member_nhids,
                member_alive,
                active_member,
                consecutive_failures,
                first_tick=False,
            )

    assert consecutive_failures.get(gw, 0) == _GW_FAILURE_THRESHOLD
    assert gw_nhid in blackhole_calls, "gw member should be blackholed after threshold"
    assert (group_nhid, dev_nhid) in group_calls, "group should switch to dev member"


# ---------------------------------------------------------------------------
# 6. test_monitor_dev_gateway_change_updates_nexthop
# ---------------------------------------------------------------------------


def test_monitor_dev_gateway_change_updates_nexthop():
    """Dev member disappearance triggers blackhole; group switches to alive gw member.

    Validates: nexthop_monitor handles dev= member going down while gw= remains alive.
    Code: tasks/nexthop_monitor.py::_tick
    Assertion: replace_nexthop_blackhole(dev_nhid) called; replace_group switches to gw member.
    """
    gw = "10.9.19.2"
    desc = _make_desc({"gw": gw}, {"dev": "border"})
    gw_key = (gw, None)
    dev_key = (None, "border")
    gw_nhid, dev_nhid, group_nhid = 10, 11, 12
    nhg_registry, member_nhids = _make_state(
        desc,
        gw_key=gw_key,
        dev_key=dev_key,
        gw_nhid=gw_nhid,
        dev_nhid=dev_nhid,
        group_nhid=group_nhid,
    )

    # Seeded: dev was active (gw was dead), now dev disappears but gw is alive
    member_alive = {gw_key: False, dev_key: True}
    active_member = {desc: dev_key}
    consecutive_failures = {}

    mock_blackhole = MagicMock()
    mock_replace_group = MagicMock()
    mock_replace_nexthop = MagicMock()

    with (
        patch(
            "tasks.nexthop_monitor._probe_gw_alive",
            return_value=(True, "172.30.0.5", "backbone"),
        ),
        patch("tasks.nexthop_monitor._probe_dev_alive", return_value=(False, None)),
        patch("nexthop.replace_nexthop_blackhole", mock_blackhole),
        patch("nexthop.replace_nexthop", mock_replace_nexthop),
        patch("nexthop.replace_group", mock_replace_group),
    ):
        _tick(
            nhg_registry,
            member_nhids,
            member_alive,
            active_member,
            consecutive_failures,
            first_tick=False,
        )

    # dev gone -> blackhole dev member
    mock_blackhole.assert_called_once_with(dev_nhid)
    # gw recovered -> replace with live nexthop
    mock_replace_nexthop.assert_called_once_with(
        gw_nhid, via="172.30.0.5", dev="backbone"
    )
    # group switches to gw (now highest-priority alive)
    mock_replace_group.assert_called_once_with(group_nhid, gw_nhid)


# ---------------------------------------------------------------------------
# 7. test_monitor_stable_state_noop
# ---------------------------------------------------------------------------


def test_monitor_stable_state_noop():
    """No nexthop calls when all members hold the same state between ticks.

    Validates: nexthop_monitor is a complete no-op when nothing changes.
    Code: tasks/nexthop_monitor.py::_tick
    Assertion: zero replace_* calls in stable state.
    """
    gw = "10.9.19.2"
    desc = _make_desc({"gw": gw}, {"dev": "border"})
    gw_key = (gw, None)
    dev_key = (None, "border")
    gw_nhid, dev_nhid, group_nhid = 10, 11, 12
    nhg_registry, member_nhids = _make_state(
        desc,
        gw_key=gw_key,
        dev_key=dev_key,
        gw_nhid=gw_nhid,
        dev_nhid=dev_nhid,
        group_nhid=group_nhid,
    )

    # Both members alive, gw is active
    member_alive = {gw_key: True, dev_key: True}
    active_member = {desc: gw_key}
    consecutive_failures = {}

    mock_blackhole = MagicMock()
    mock_replace_nexthop = MagicMock()
    mock_replace_device = MagicMock()
    mock_replace_group = MagicMock()

    with (
        patch(
            "tasks.nexthop_monitor._probe_gw_alive",
            return_value=(True, "172.30.0.5", "backbone"),
        ),
        patch("tasks.nexthop_monitor._probe_dev_alive", return_value=(True, None)),
        patch("nexthop.replace_nexthop_blackhole", mock_blackhole),
        patch("nexthop.replace_nexthop", mock_replace_nexthop),
        patch("nexthop.replace_device", mock_replace_device),
        patch("nexthop.replace_group", mock_replace_group),
    ):
        _tick(
            nhg_registry,
            member_nhids,
            member_alive,
            active_member,
            consecutive_failures,
            first_tick=False,
        )

    mock_blackhole.assert_not_called()
    mock_replace_nexthop.assert_not_called()
    mock_replace_device.assert_not_called()
    mock_replace_group.assert_not_called()


# ---------------------------------------------------------------------------
# _find_router_owning_address tests
# ---------------------------------------------------------------------------

from tasks.nexthop_monitor import _find_router_owning_address
from tasks.tests.fixtures.ospf_lsdb_vpn2 import (
    ROUTER_LSDB_TWO_OUTERS,
)


class TestFindRouterOwningAddress:
    """_find_router_owning_address returns the router-id whose Router LSA
    declares the given IP as a p2p or stub-network routerInterfaceAddress.
    """

    def test_finds_outer_pt_for_10_9_19_2(self):
        router_id = _find_router_owning_address(
            "10.9.19.2", ROUTER_LSDB_TWO_OUTERS
        )
        assert router_id == "10.130.30.23"

    def test_finds_outer_de_for_10_9_21_2(self):
        router_id = _find_router_owning_address(
            "10.9.21.2", ROUTER_LSDB_TWO_OUTERS
        )
        assert router_id == "10.130.30.33"

    def test_finds_router_from_stub_network_containing_gateway(self):
        lsdb = {
            "routerLinkStates": {
                "areas": {
                    "0.0.0.0": [
                        {
                            "advertisingRouter": "10.130.30.33",
                            "routerLinks": {
                                "link0": {
                                    "linkType": "Stub Network",
                                    "networkAddress": "10.9.21.0",
                                    "networkMask": "255.255.255.0",
                                },
                            },
                        },
                    ],
                },
            },
        }

        router_id = _find_router_owning_address("10.9.21.2", lsdb)

        assert router_id == "10.130.30.33"

    def test_finds_wg_uk_rutestvpn_for_10_9_19_1(self):
        """Address owned by a non-ASBR router still resolves correctly."""
        router_id = _find_router_owning_address(
            "10.9.19.1", ROUTER_LSDB_TWO_OUTERS
        )
        assert router_id == "10.130.30.20"

    def test_returns_none_when_address_is_not_in_any_lsa(self):
        assert _find_router_owning_address(
            "192.0.2.99", ROUTER_LSDB_TWO_OUTERS
        ) is None

    def test_returns_none_when_lsdb_is_empty(self):
        empty = {"routerLinkStates": {"areas": {}}}
        assert _find_router_owning_address("10.9.19.2", empty) is None

    def test_returns_none_when_lsdb_structure_is_malformed(self):
        assert _find_router_owning_address("10.9.19.2", {}) is None
        assert _find_router_owning_address("10.9.19.2", None) is None

    def test_ignores_neighborRouterId_matches(self):
        """When X appears only as neighborRouterId (not
        routerInterfaceAddress) — does NOT count as ownership.
        """
        # In ROUTER_LSDB_TWO_OUTERS, 10.130.30.20 appears as
        # neighborRouterId of outer_pt's link1 — we must not return
        # outer_pt as the "owner" of 10.130.30.20.
        router_id = _find_router_owning_address(
            "10.130.30.20", ROUTER_LSDB_TWO_OUTERS
        )
        # 10.130.30.20 is NOT a routerInterfaceAddress of anybody in this
        # fixture — it's a router-id used as neighborRouterId. Must not
        # return outer_pt (which only references it as neighbor).
        assert router_id is None


# ---------------------------------------------------------------------------
# _router_originates_default tests
# ---------------------------------------------------------------------------

from tasks.nexthop_monitor import _router_originates_default
from tasks.tests.fixtures.ospf_lsdb_vpn2 import (
    EXTERNAL_LSDB_BOTH_DEFAULTS,
    EXTERNAL_LSDB_ONLY_PT,
    EXTERNAL_LSDB_NONE,
)


class TestRouterOriginatesDefault:
    """_router_originates_default returns True iff an AS-external LSA
    with linkStateId '0.0.0.0' and networkMask 0 exists with the given
    advertisingRouter.
    """

    def test_true_when_outer_pt_originates_in_both_defaults_fixture(self):
        assert _router_originates_default(
            "10.130.30.23", EXTERNAL_LSDB_BOTH_DEFAULTS
        ) is True

    def test_true_when_outer_de_originates_in_both_defaults_fixture(self):
        assert _router_originates_default(
            "10.130.30.33", EXTERNAL_LSDB_BOTH_DEFAULTS
        ) is True

    def test_false_when_outer_de_does_not_originate_in_only_pt_fixture(self):
        assert _router_originates_default(
            "10.130.30.33", EXTERNAL_LSDB_ONLY_PT
        ) is False

    def test_false_when_no_external_lsas_present(self):
        assert _router_originates_default(
            "10.130.30.23", EXTERNAL_LSDB_NONE
        ) is False

    def test_false_for_non_default_external_lsa(self):
        """LSA for 10.9.20.0/24 with advertisingRouter=10.9.20.2 must not
        qualify as a default originator.
        """
        assert _router_originates_default(
            "10.9.20.2", EXTERNAL_LSDB_BOTH_DEFAULTS
        ) is False

    def test_false_on_malformed_lsdb(self):
        assert _router_originates_default("10.130.30.23", {}) is False
        assert _router_originates_default("10.130.30.23", None) is False


# ---------------------------------------------------------------------------
# _resolve_router_nexthop tests
# ---------------------------------------------------------------------------

from tasks.nexthop_monitor import _resolve_router_nexthop
from tasks.tests.fixtures.ospf_lsdb_vpn2 import (
    RIB_TWO_OUTERS,
    RIB_PT_GONE,
)


class TestResolveRouterNexthop:
    """_resolve_router_nexthop returns (ip, via_iface) of the first
    nexthop listed for the router-id's R-route in the OSPF RIB.
    """

    def test_resolves_outer_pt(self):
        assert _resolve_router_nexthop(
            "10.130.30.23", RIB_TWO_OUTERS
        ) == ("172.30.0.3", "backbone")

    def test_resolves_outer_de(self):
        assert _resolve_router_nexthop(
            "10.130.30.33", RIB_TWO_OUTERS
        ) == ("172.30.0.4", "backbone")

    def test_returns_none_when_router_absent_from_rib(self):
        assert _resolve_router_nexthop(
            "10.130.30.23", RIB_PT_GONE
        ) is None

    def test_returns_none_on_malformed_rib(self):
        assert _resolve_router_nexthop("10.130.30.23", {}) is None
        assert _resolve_router_nexthop("10.130.30.23", None) is None

    def test_returns_none_when_route_has_no_nexthops(self):
        rib = {"10.130.30.23": {"routeType": "R ", "nexthops": []}}
        assert _resolve_router_nexthop("10.130.30.23", rib) is None


# ---------------------------------------------------------------------------
# _probe_gw_alive integration tests
# ---------------------------------------------------------------------------

from unittest.mock import patch


class TestProbeGwAlive:
    """End-to-end _probe_gw_alive using the three fixture vtysh views."""

    def _patch_vtysh(self, router_lsdb, external_lsdb, rib):
        def side_effect(command):
            if "database router" in command:
                return router_lsdb
            if "database external" in command:
                return external_lsdb
            if "ospf route" in command:
                return rib
            return None
        return patch("tasks.nexthop_monitor._vtysh", side_effect=side_effect)

    def test_outer_pt_alive_when_both_advertise_default(self):
        with self._patch_vtysh(
            ROUTER_LSDB_TWO_OUTERS,
            EXTERNAL_LSDB_BOTH_DEFAULTS,
            RIB_TWO_OUTERS,
        ):
            alive, via, dev = _probe_gw_alive("10.9.19.2")
        assert alive is True
        assert via == "172.30.0.3"
        assert dev == "backbone"

    def test_outer_de_alive_when_both_advertise_default(self):
        with self._patch_vtysh(
            ROUTER_LSDB_TWO_OUTERS,
            EXTERNAL_LSDB_BOTH_DEFAULTS,
            RIB_TWO_OUTERS,
        ):
            alive, via, dev = _probe_gw_alive("10.9.21.2")
        assert alive is True
        assert via == "172.30.0.4"
        assert dev == "backbone"

    def test_outer_de_alive_via_direct_adjacency_even_when_not_default_originator(self):
        # de (10.9.21.2, owned by 10.130.30.33) is a directly-adjacent OSPF
        # neighbour with a routable RIB entry (10.9.21.0/24 -> 172.30.0.4), so
        # it is reachable regardless of whether it originates a default route.
        # The direct-adjacency short-circuit in _probe_gw_alive resolves it
        # BEFORE the default-originator fallback is consulted; the resolver
        # returns the OSPF-RIB nexthop (172.30.0.4), which is kernel-installable
        # (Fix A). The former "dead unless default-originator" expectation is
        # obsolete: default origination is only a fallback for gws with no
        # direct RIB adjacency.
        with self._patch_vtysh(
            ROUTER_LSDB_TWO_OUTERS,
            EXTERNAL_LSDB_ONLY_PT,
            RIB_TWO_OUTERS,
        ):
            alive, via, dev = _probe_gw_alive("10.9.21.2")
        assert alive is True
        assert via == "172.30.0.4"
        assert dev == "backbone"

    def test_outer_pt_dead_when_rib_drops_it(self):
        """Dead Interval fires: outer_pt's R-route disappears."""
        with self._patch_vtysh(
            ROUTER_LSDB_TWO_OUTERS,
            EXTERNAL_LSDB_BOTH_DEFAULTS,
            RIB_PT_GONE,
        ):
            alive, via, dev = _probe_gw_alive("10.9.19.2")
        assert alive is False

    def test_stub_owner_alive_on_direct_backbone_transit_network(self):
        router_lsdb = {
            "routerLinkStates": {
                "areas": {
                    "0.0.0.0": [
                        {
                            "advertisingRouter": "10.130.30.30",
                            "routerLinks": {
                                "link0": {
                                    "linkType": "another Router (point-to-point)",
                                    "routerInterfaceAddress": "172.30.0.6",
                                },
                                "link1": {
                                    "linkType": "Stub Network",
                                    "networkAddress": "10.9.21.0",
                                    "networkMask": "255.255.255.0",
                                },
                            },
                        },
                    ],
                },
            },
        }
        rib = {
            "172.30.0.0/24": {
                "transit": True,
                "nexthops": [{"ip": " ", "directlyAttachedTo": "backbone"}],
            },
        }
        with self._patch_vtysh(router_lsdb, {"asExternalLinkStates": []}, rib):
            alive, via, dev = _probe_gw_alive("10.9.21.2")

        assert alive is True
        assert via == "172.30.0.6"
        assert dev == "backbone"

    def test_unknown_gw_returns_false(self):
        with self._patch_vtysh(
            ROUTER_LSDB_TWO_OUTERS,
            EXTERNAL_LSDB_BOTH_DEFAULTS,
            RIB_TWO_OUTERS,
        ):
            alive, _, _ = _probe_gw_alive("192.0.2.99")
        assert alive is False

    def test_vty_bridge_failure_returns_false(self):
        with patch("tasks.nexthop_monitor._vtysh", return_value=None):
            alive, via, dev = _probe_gw_alive("10.9.19.2")
        assert alive is False
        assert (via, dev) == (None, None)


# ---------------------------------------------------------------------------
# Fix A: _resolve_direct_router_nexthop must return the OSPF-RIB nexthop
# ---------------------------------------------------------------------------

from tasks.nexthop_monitor import _resolve_direct_router_nexthop
from tasks.tests.fixtures.vxxlcx_ospf import VXXLCX_ROUTER_LSDB, VXXLCX_RIB
from tasks.tests.fixtures.vpn2_ospf_egress import VPN2_ROUTER_LSDB, VPN2_RIB


class TestResolveDirectRouterNexthopReturnsOspfRibNexthop:
    """Fix A: resolver must return the OSPF-RIB nexthops[0].ip, not the
    owning router's own tunnel interface-address."""

    def test_returns_ospf_rib_nexthop_for_tunnel_edge_usa(self):
        # usa edge router 10.130.30.23 advertises only tunnel IP 10.9.19.2;
        # OSPF RIB carries routable 172.30.0.35 for 10.9.19.0/28.
        assert _resolve_direct_router_nexthop(
            "10.130.30.23", VXXLCX_ROUTER_LSDB, VXXLCX_RIB
        ) == ("172.30.0.35", "backbone")

    def test_returns_ospf_rib_nexthop_for_tunnel_edge_mexico(self):
        assert _resolve_direct_router_nexthop(
            "10.130.30.33", VXXLCX_ROUTER_LSDB, VXXLCX_RIB
        ) == ("172.30.0.36", "backbone")

    def test_returns_ospf_rib_nexthop_for_tunnel_edge_de_vpn2(self):
        assert _resolve_direct_router_nexthop(
            "10.130.30.33", VPN2_ROUTER_LSDB, VPN2_RIB
        ) == ("172.30.0.112", "backbone")

    def test_returns_ospf_rib_nexthop_for_tunnel_edge_pt_vpn2(self):
        assert _resolve_direct_router_nexthop(
            "10.130.30.23", VPN2_ROUTER_LSDB, VPN2_RIB
        ) == ("172.30.0.110", "backbone")

    def test_border_directly_attached_unchanged_vxxlcx(self):
        # border 10.130.30.50 owns backbone iface 172.30.0.38; transit
        # 172.30.0.0/24 has nexthops[0].ip==" " -> guard falls back to iface addr.
        assert _resolve_direct_router_nexthop(
            "10.130.30.50", VXXLCX_ROUTER_LSDB, VXXLCX_RIB
        ) == ("172.30.0.38", "backbone")

    def test_border_directly_attached_unchanged_vpn2(self):
        assert _resolve_direct_router_nexthop(
            "10.130.30.50", VPN2_ROUTER_LSDB, VPN2_RIB
        ) == ("172.30.0.116", "backbone")


class TestRegressionEgressBlackholeFieldSelectionBug:
    """Regression for the field-selection defect: resolver used to return the
    edge's own tunnel IP (kernel-rejected), now returns the OSPF-RIB nexthop."""

    def test_usa_no_longer_returns_tunnel_ip_10_9_19_2(self):
        gw, dev = _resolve_direct_router_nexthop(
            "10.130.30.23", VXXLCX_ROUTER_LSDB, VXXLCX_RIB
        )
        assert gw != "10.9.19.2", (
            "field-selection bug: resolver returned edge tunnel IP "
            "(kernel-rejected 'Nexthop has invalid gateway'), must return "
            "OSPF-RIB nexthops[0].ip 172.30.0.35"
        )
        assert gw == "172.30.0.35"


class TestResolverLongestPrefixMatchAndSkipDefault:
    """Fix A invariants 1-2: the resolver must skip 0.0.0.0/0 and pick the
    longest-prefix covering route, even when a broad prefix precedes the
    specific tunnel prefix in dict order (FRR JSON order is non-contractual)."""

    def test_broad_prefix_before_specific_still_picks_specific(self):
        # dict order deliberately puts the default and a broad /16 BEFORE the
        # specific /28 that carries the correct backbone nexthop for usa.
        router_lsdb = VXXLCX_ROUTER_LSDB
        rib = {
            "0.0.0.0/0": {"nexthops": [{"ip": "172.30.0.38", "via": "backbone"}]},
            "10.0.0.0/8": {"nexthops": [{"ip": "172.30.0.99", "via": "backbone"}]},
            "10.9.19.0/28": {"nexthops": [{"ip": "172.30.0.35", "via": "backbone"}]},
        }
        assert _resolve_direct_router_nexthop(
            "10.130.30.23", router_lsdb, rib
        ) == ("172.30.0.35", "backbone"), (
            "resolver must LPM-select the specific /28 and skip the default/broad "
            "prefixes even though they appear first in dict order"
        )

    def test_default_route_never_matched(self):
        # Only 0.0.0.0/0 present -> nothing to match -> None (never the default gw).
        rib = {"0.0.0.0/0": {"nexthops": [{"ip": "172.30.0.38", "via": "backbone"}]}}
        assert _resolve_direct_router_nexthop(
            "10.130.30.23", VXXLCX_ROUTER_LSDB, rib
        ) is None


class TestResolverEcmpFirstUsableNexthop:
    """Fix A invariant 3: iterate nexthops, pick the first USABLE one; a blank
    first nexthop must not shadow a later routable one."""

    def test_blank_first_nexthop_skipped_for_later_usable(self):
        rib = {
            "10.9.19.0/28": {"nexthops": [
                {"ip": " ", "directlyAttachedTo": "backbone"},   # blank first
                {"ip": "172.30.0.35", "via": "backbone"},         # usable second
            ]},
        }
        assert _resolve_direct_router_nexthop(
            "10.130.30.23", VXXLCX_ROUTER_LSDB, rib
        ) == ("172.30.0.35", "backbone")


class TestResolverFallbackNeverTunnelIp:
    """Fix A robust-fallback guard: the directly-attached fallback must only
    return an on-backbone address, never a tunnel-edge P2P IP."""

    def test_fallback_rejected_for_non_backbone_address(self):
        # A directly-attached route whose covering address is the tunnel IP
        # 10.9.19.2 must NOT be returned as a gateway (not on backbone).
        rib = {
            "10.9.19.0/28": {"nexthops": [
                {"ip": " ", "directlyAttachedTo": "backbone"},
            ]},
        }
        # usa edge 10.130.30.23 only advertises 10.9.19.2 (a tunnel IP); with a
        # blank-.ip directly-attached route the fallback must refuse it -> None.
        assert _resolve_direct_router_nexthop(
            "10.130.30.23", VXXLCX_ROUTER_LSDB, rib
        ) is None


class TestProbeGwAliveTunnelEdgeInstallable:
    """_probe_gw_alive returns an installable (True, backbone-ip, backbone)
    tuple for a tunnel-edge egress after Fix A."""

    def _patch_vtysh(self, router_lsdb, rib):
        def side_effect(command):
            if "database router" in command:
                return router_lsdb
            if "ospf route" in command:
                return rib
            if "database external" in command:
                return {"asExternalLinkStates": []}
            return None
        return patch("tasks.nexthop_monitor._vtysh", side_effect=side_effect)

    def test_usa_probe_returns_installable_backbone_nexthop(self):
        with self._patch_vtysh(VXXLCX_ROUTER_LSDB, VXXLCX_RIB):
            alive, nh_ip, nh_dev = _probe_gw_alive("10.9.19.2")
        assert (alive, nh_ip, nh_dev) == (True, "172.30.0.35", "backbone")


def test_probe_logs_branch_on_unknown_gw(caplog):
    import logging as _l
    with patch("tasks.nexthop_monitor._vtysh",
               side_effect=lambda c: VXXLCX_ROUTER_LSDB if "database router" in c else VXXLCX_RIB):
        with caplog.at_level(_l.DEBUG, logger="tasks.nexthop_monitor"):
            alive, _, _ = _probe_gw_alive("192.0.2.99")  # not owned by any router
    assert alive is False
    assert any("no owning router" in r.message.lower() or "192.0.2.99" in r.message
               for r in caplog.records), "expected a diagnostic line for the False branch"
