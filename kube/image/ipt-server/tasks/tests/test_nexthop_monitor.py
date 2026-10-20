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
    ROUTER_LSDB_DE_DEAD,
    NEIGHBORS_BOTH_FULL,
    NEIGHBORS_DE_DOWN,
    NEIGHBORS_DE_ABSENT,
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

    def _patch_vtysh(self, router_lsdb, external_lsdb, rib, neighbors=NEIGHBORS_BOTH_FULL):
        def side_effect(command):
            if "database router" in command:
                return router_lsdb
            if "database external" in command:
                return external_lsdb
            if "ospf neighbor" in command:
                return neighbors
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

    def test_outer_de_dead_when_p2p_link_withdrawn_but_rib_resolves(self):
        """Issue #37 reproduction (topology-correct): a dead de edge whose
        hub-side WireGuard interface stays UP keeps 10.9.21.0/24 on-link, so the
        nexthop still RESOLVES in the RIB — but the hub-side de pod
        (10.130.30.30) has WITHDRAWN its reciprocal P2P link to the de-edge
        (10.130.30.33) and the edge's own LSA has aged out. _probe_gw_alive()
        must return DEAD, not alive.

        This is the correct death signal for hub-and-spoke: the edge is two OSPF
        hops away and never a direct neighbor, so a direct-neighbor-Full check
        on the edge owner (the rolled-back 1.2.6 gate) is the wrong test.
        """
        with self._patch_vtysh(
            ROUTER_LSDB_DE_DEAD,
            EXTERNAL_LSDB_BOTH_DEFAULTS,
            RIB_TWO_OUTERS,
            neighbors=NEIGHBORS_DE_DOWN,
        ):
            alive, via, dev = _probe_gw_alive("10.9.21.2")
        assert alive is False, (
            "issue #37: RIB-resolvable-but-P2P-adjacency-withdrawn nexthop must "
            "be DEAD so geo-NHG egress failover can fire"
        )
        assert (via, dev) == (None, None)

    def test_outer_de_dead_when_edge_lsa_absent_but_rib_resolves(self):
        """Same as above (Dead Interval expired, edge LSA fully gone). RIB still
        resolves de via the on-link connected route; probe must still be DEAD.
        NEIGHBORS_DE_ABSENT keeps the hub-side pod Full (only the far edge is
        gone), so this exercises the LSDB death signal, not neighbor state.
        """
        with self._patch_vtysh(
            ROUTER_LSDB_DE_DEAD,
            EXTERNAL_LSDB_BOTH_DEFAULTS,
            RIB_TWO_OUTERS,
            neighbors=NEIGHBORS_DE_ABSENT,
        ):
            alive, via, dev = _probe_gw_alive("10.9.21.2")
        assert alive is False
        assert (via, dev) == (None, None)

    def test_outer_pt_still_alive_when_only_de_edge_dead(self):
        """The gate is per-edge: pt (reciprocal P2P intact, hub-side pod
        10.130.30.20 Full) stays alive even though the de EDGE is dead in the
        same view."""
        with self._patch_vtysh(
            ROUTER_LSDB_DE_DEAD,
            EXTERNAL_LSDB_BOTH_DEFAULTS,
            RIB_TWO_OUTERS,
            neighbors=NEIGHBORS_DE_DOWN,
        ):
            alive, via, dev = _probe_gw_alive("10.9.19.2")
        assert alive is True
        assert via == "172.30.0.3"
        assert dev == "backbone"

    def test_neighbor_bridge_failure_returns_false(self):
        """If the neighbor query itself fails (bridge error), fail closed."""
        def side_effect(command):
            if "database router" in command:
                return ROUTER_LSDB_TWO_OUTERS
            if "ospf neighbor" in command:
                return None  # bridge failure on the neighbor call
            if "ospf route" in command:
                return RIB_TWO_OUTERS
            if "database external" in command:
                return EXTERNAL_LSDB_BOTH_DEFAULTS
            return None
        with patch("tasks.nexthop_monitor._vtysh", side_effect=side_effect):
            alive, via, dev = _probe_gw_alive("10.9.21.2")
        assert alive is False
        assert (via, dev) == (None, None)

    def test_outer_pt_dead_when_rib_drops_it(self):
        """Dead Interval fires: outer_pt's R-route disappears."""
        with self._patch_vtysh(
            ROUTER_LSDB_TWO_OUTERS,
            EXTERNAL_LSDB_BOTH_DEFAULTS,
            RIB_PT_GONE,
        ):
            alive, via, dev = _probe_gw_alive("10.9.19.2")
        assert alive is False

    def test_backbone_transit_gw_alive_on_direct_attach(self):
        """A genuinely on-backbone directly-attached gw (172.30.0.6, owned by a
        Full direct neighbor 10.130.30.30 that advertises it as its own transit
        interface address) is ALIVE via the directly-attached path. On-backbone
        gws are not edge-gated (they are one hop away)."""
        router_lsdb = {
            "routerLinkStates": {
                "areas": {
                    "0.0.0.0": [
                        {
                            "advertisingRouter": "10.130.30.30",
                            "routerLinks": {
                                "link0": {
                                    "linkType": "a Transit Network",
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
        neighbors = {
            "neighbors": {
                "10.130.30.30": [
                    {"state": "Full/-", "address": "172.30.0.6",
                     "ifaceName": "backbone"},
                ],
            },
        }
        with self._patch_vtysh(
            router_lsdb, {"asExternalLinkStates": []}, rib, neighbors=neighbors
        ):
            alive, via, dev = _probe_gw_alive("172.30.0.6")

        assert alive is True
        assert via == "172.30.0.6"
        assert dev == "backbone"

    def test_offbackbone_stub_only_gw_is_dead_no_live_p2p_edge(self):
        """Issue #37 core: an off-backbone gw (10.9.21.2) that is reachable ONLY
        as a member of a lingering /24 stub — with NO live edge advertising it
        as a P2P interface-address — must be judged DEAD, even though the stub
        route still resolves on-link. Under the old stub-trusting behaviour this
        false-alived and blocked failover.
        """
        router_lsdb = {
            "routerLinkStates": {
                "areas": {
                    "0.0.0.0": [
                        {
                            # hub-side pod: only a stub for the tunnel /24 (its
                            # P2P link to the edge has been withdrawn).
                            "advertisingRouter": "10.130.30.30",
                            "routerLinks": {
                                "link0": {
                                    "linkType": "a Transit Network",
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
            "10.9.21.0/24": {
                "routeType": "N",
                "nexthops": [{"ip": "172.30.0.6", "via": "backbone",
                              "advertisedRouter": "10.130.30.30"}],
            },
            "172.30.0.0/24": {
                "transit": True,
                "nexthops": [{"ip": " ", "directlyAttachedTo": "backbone"}],
            },
        }
        neighbors = {
            "neighbors": {
                "10.130.30.30": [
                    {"state": "Full/-", "address": "172.30.0.6",
                     "ifaceName": "backbone"},
                ],
            },
        }
        with self._patch_vtysh(
            router_lsdb, {"asExternalLinkStates": []}, rib, neighbors=neighbors
        ):
            alive, via, dev = _probe_gw_alive("10.9.21.2")

        assert alive is False, (
            "issue #37: an off-backbone stub-only gw with no live P2P edge must "
            "be DEAD so geo-NHG egress failover can fire"
        )
        assert (via, dev) == (None, None)

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

    # usa edge (owner 10.130.30.23) is reached via its HUB-SIDE pod
    # 10.130.30.20, which is the Full DIRECT OSPF neighbor of the hub (the edge
    # itself is two hops away and never a direct neighbor). Healthy egress =
    # hub-side pod Full + reciprocal P2P adjacency present in VXXLCX_ROUTER_LSDB.
    _NEIGHBORS_USA_FULL = {
        "neighbors": {
            "10.130.30.20": [
                {"state": "Full/DR", "address": "172.30.0.35", "ifaceName": "backbone"},
            ],
        },
    }

    def _patch_vtysh(self, router_lsdb, rib):
        def side_effect(command):
            if "database router" in command:
                return router_lsdb
            if "ospf neighbor" in command:
                return self._NEIGHBORS_USA_FULL
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


@pytest.mark.asyncio
async def test_monitor_nexthops_loop_survives_a_tick_that_raises():
    """Regression for the vpn2 blackhole: one exploding tick must NOT kill the
    monitor loop. The loop must call the tick again on the next iteration.

    Mirrors the live failure mode where nexthop_monitor stopped ticking after
    its first tick and never recovered de/pt from blackhole.
    """
    import asyncio
    from types import SimpleNamespace

    from ipt_server import state
    from tasks.nexthop_monitor import monitor_nexthops

    calls = {"n": 0}
    stop = asyncio.Event()

    def fake_tick(*_args, **_kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("boom on tick 1 (e.g. a NetlinkError on replace)")
        if calls["n"] >= 3:
            stop.set()

    loaded = asyncio.Event()
    loaded.set()
    fake_router = SimpleNamespace(_routes_loaded=loaded)

    with (
        patch.object(state, "ROUTER", fake_router),
        patch("tasks.nexthop_monitor._tick", side_effect=fake_tick),
        patch("tasks.nexthop_monitor._TICK_INTERVAL_SECONDS", 0),
    ):
        await asyncio.wait_for(
            monitor_nexthops({}, {}, stop),
            timeout=2,
        )

    assert calls["n"] >= 3, (
        "monitor_nexthops loop died after the first tick raised; it must "
        "keep ticking (this is the exact live regression)"
    )


@pytest.mark.asyncio
async def test_monitor_nexthops_loop_survives_a_hanging_tick():
    """A tick whose worker-thread body HANGS must not wedge the loop forever.

    This is the mechanism the old `try/except Exception` could NOT defend
    against (a hung to_thread(_tick) suspends the coroutine indefinitely with
    no exception) and is the leading hypothesis for the live vpn2 stall. The
    per-tick timeout in run_periodic bounds it so the loop keeps ticking.
    """
    import asyncio
    import time
    from types import SimpleNamespace

    from ipt_server import state
    from tasks.nexthop_monitor import monitor_nexthops

    calls = {"n": 0}
    stop = asyncio.Event()

    def fake_tick(*_args, **_kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            # Block the worker thread well past the per-tick timeout (0.05s)
            # but bounded so the worker frees promptly and does not linger
            # past the test.
            time.sleep(0.4)
        if calls["n"] >= 3:
            stop.set()

    loaded = asyncio.Event()
    loaded.set()
    fake_router = SimpleNamespace(_routes_loaded=loaded)

    with (
        patch.object(state, "ROUTER", fake_router),
        patch("tasks.nexthop_monitor._tick", side_effect=fake_tick),
        patch("tasks.nexthop_monitor._TICK_INTERVAL_SECONDS", 0),
        patch("tasks.nexthop_monitor._TICK_TIMEOUT_SECONDS", 0.05),
    ):
        await asyncio.wait_for(
            monitor_nexthops({}, {}, stop),
            timeout=2,
        )

    assert calls["n"] >= 3, (
        "monitor_nexthops loop did not recover from a hanging tick; the "
        "per-tick timeout must abandon it and keep ticking"
    )


# ---------------------------------------------------------------------------
# vpn2 STILL-BLACKHOLE (2026-07-02): the geo nexthop_monitor _tick must probe
# EVERY member of EVERY group on EVERY tick, independent of startup
# reachability, and recover a member that was dead at startup once its gw
# becomes OSPF-resolvable. Border (directly-attached gw) resolves immediately;
# de/pt (tunnel-IP gws) are unresolvable until OSPF converges. A tick that only
# processes the startup-reachable subset leaves de/pt member nexthops blackhole
# forever.
# ---------------------------------------------------------------------------


class TestTickIteratesEveryMemberEveryTick:
    """_tick must probe all three members (de, pt, border) of a group."""

    def test_first_tick_probes_every_member(self):
        desc = _make_desc(
            {"gw": "10.9.21.2"},   # de
            {"gw": "10.9.19.2"},   # pt
            {"gw": "10.130.30.50"},  # border
        )
        member_nhids = {
            ("10.9.21.2", None): 1,
            ("10.9.19.2", None): 2,
            ("10.130.30.50", None): 3,
        }
        nhg_registry = {desc: 4}
        probed: list[str] = []

        def probe(gw):
            probed.append(gw)
            # border resolves; de/pt dead at startup (unconverged OSPF).
            if gw == "10.130.30.50":
                return (True, "172.30.0.116", "backbone")
            return (False, None, None)

        with (
            patch("tasks.nexthop_monitor._probe_gw_alive", side_effect=probe),
            patch("nexthop.replace_nexthop", MagicMock()),
            patch("nexthop.replace_group", MagicMock()),
            patch("nexthop.replace_nexthop_blackhole", MagicMock()),
        ):
            _tick(
                nhg_registry,
                member_nhids,
                {},
                {},
                {},
                first_tick=True,
            )

        assert {"10.9.21.2", "10.9.19.2", "10.130.30.50"} <= set(probed), (
            "first_tick did not probe every member; probed only "
            f"{sorted(set(probed))} — de/pt were dropped from the member "
            "iteration (the vpn2 blackhole)"
        )

    def test_member_dead_at_startup_recovers_once_alive(self):
        """de/pt dead on first_tick then alive on a later tick must be
        installed (replace_nexthop) with their resolved backbone nexthop."""
        desc = _make_desc(
            {"gw": "10.9.21.2"},   # de
            {"gw": "10.9.19.2"},   # pt
            {"gw": "10.130.30.50"},  # border
        )
        member_nhids = {
            ("10.9.21.2", None): 1,
            ("10.9.19.2", None): 2,
            ("10.130.30.50", None): 3,
        }
        nhg_registry = {desc: 4}
        member_alive: dict = {}
        active_member: dict = {}
        consecutive_failures: dict = {}

        converged = {"on": False}

        def probe(gw):
            if gw == "10.130.30.50":
                return (True, "172.30.0.116", "backbone")
            if not converged["on"]:
                return (False, None, None)
            via = "172.30.0.112" if gw == "10.9.21.2" else "172.30.0.110"
            return (True, via, "backbone")

        installed: list = []

        def rec_nexthop(nhid, via, dev):
            installed.append((nhid, via))

        with (
            patch("tasks.nexthop_monitor._probe_gw_alive", side_effect=probe),
            patch("nexthop.replace_nexthop", side_effect=rec_nexthop),
            patch("nexthop.replace_group", MagicMock()),
            patch("nexthop.replace_nexthop_blackhole", MagicMock()),
        ):
            # first_tick: de/pt dead, border alive.
            _tick(nhg_registry, member_nhids, member_alive, active_member,
                  consecutive_failures, first_tick=True)
            # Drive past the transient failure threshold while still dead.
            for _ in range(_GW_FAILURE_THRESHOLD):
                _tick(nhg_registry, member_nhids, member_alive, active_member,
                      consecutive_failures, first_tick=False)
            # OSPF converges; de/pt become resolvable.
            converged["on"] = True
            _tick(nhg_registry, member_nhids, member_alive, active_member,
                  consecutive_failures, first_tick=False)

        installed_nhids = {nhid for nhid, _ in installed}
        assert 1 in installed_nhids, (
            "de member (nhid=1) dead at startup never recovered once OSPF "
            "converged; _tick must re-probe and install it"
        )
        assert 2 in installed_nhids, (
            "pt member (nhid=2) dead at startup never recovered once OSPF "
            "converged; _tick must re-probe and install it"
        )

    def test_tick_logs_group_members_and_each_member(self, caplog):
        """Observability: _tick must emit a greppable line naming the full
        member set per group and each member as iteration reaches it, so the
        next vpn2 run can prove which members the loop actually probed."""
        import logging as _l

        desc = _make_desc(
            {"gw": "10.9.21.2"},
            {"gw": "10.9.19.2"},
            {"gw": "10.130.30.50"},
        )
        member_nhids = {
            ("10.9.21.2", None): 1,
            ("10.9.19.2", None): 2,
            ("10.130.30.50", None): 3,
        }
        nhg_registry = {desc: 4}

        def probe(gw):
            return (True, "172.30.0.116", "backbone")

        with (
            patch("tasks.nexthop_monitor._probe_gw_alive", side_effect=probe),
            patch("nexthop.replace_nexthop", MagicMock()),
            patch("nexthop.replace_group", MagicMock()),
            patch("nexthop.replace_nexthop_blackhole", MagicMock()),
            caplog.at_level(_l.INFO, logger="tasks.nexthop_monitor"),
        ):
            _tick(nhg_registry, member_nhids, {}, {}, {}, first_tick=True)

        messages = [r.getMessage() for r in caplog.records]
        joined = "\n".join(messages)
        for gw in ("10.9.21.2", "10.9.19.2", "10.130.30.50"):
            assert any(f"member gw={gw}" in m for m in messages), (
                f"expected a per-member iteration line for gw={gw}; got:\n"
                + joined
            )


# ---------------------------------------------------------------------------
# issue #37 rework: HUB-AND-SPOKE edge-liveness gate.
#
# The gw-owning EDGE router-id (e.g. de 10.130.30.33 for gw 10.9.21.2) is TWO
# OSPF hops from the hub ipt-server and is NEVER a direct neighbor. The correct
# liveness signal is the BIDIRECTIONAL P2P adjacency between the edge and its
# hub-side pod (which IS a Full direct neighbor). These fixtures model the real
# vpn2 topology captured live 2026-07-04 (see
# fixtures/ospf_hubspoke_vpn2.py and
# docs/artifacts/2026-07-04-nexthop-monitor-edge-liveness-signal-analysis.md).
# ---------------------------------------------------------------------------

from tasks.tests.fixtures.ospf_hubspoke_vpn2 import (
    HS_ROUTER_LSDB_ALIVE,
    HS_NEIGHBORS_ALIVE,
    HS_RIB_ALIVE,
    HS_ROUTER_LSDB_DE_DEAD,
    HS_NEIGHBORS_DE_DEAD,
    HS_RIB_DE_DEAD,
)


class TestHubSpokeEdgeLivenessGate:
    """The topology-correct gate for hub-and-spoke: an edge gw is alive iff its
    2-hop-away owning edge router-id holds a bidirectional P2P adjacency with a
    hub-side pod that is itself a Full direct neighbor of the hub ipt-server.
    """

    @staticmethod
    def _patch_vtysh(router_lsdb, neighbors, rib, external=None):
        if external is None:
            external = {"asExternalLinkStates": []}

        def side_effect(command):
            if "database router" in command:
                return router_lsdb
            if "database external" in command:
                return external
            if "ospf neighbor" in command:
                return neighbors
            if "ospf route" in command:
                return rib
            return None

        return patch("tasks.nexthop_monitor._vtysh", side_effect=side_effect)

    @staticmethod
    def _without_neighbor(neighbors, router_id):
        return {
            "neighbors": {
                k: v for k, v in neighbors["neighbors"].items() if k != router_id
            }
        }

    # -- edge ALIVE -------------------------------------------------------

    def test_de_edge_alive_is_judged_alive(self):
        """de edge (gw 10.9.21.2, owner 10.130.30.33) up: reciprocal P2P
        adjacency present, hub-side pod 10.130.30.30 is a Full neighbor ->
        ALIVE with an installable backbone nexthop (172.30.0.18)."""
        with self._patch_vtysh(
            HS_ROUTER_LSDB_ALIVE, HS_NEIGHBORS_ALIVE, HS_RIB_ALIVE
        ):
            alive, via, dev = _probe_gw_alive("10.9.21.2")
        assert alive is True
        assert via == "172.30.0.18"
        assert dev == "backbone"

    def test_pt_edge_alive_is_judged_alive(self):
        """pt edge (gw 10.9.19.2, owner 10.130.30.23) up -> ALIVE."""
        with self._patch_vtysh(
            HS_ROUTER_LSDB_ALIVE, HS_NEIGHBORS_ALIVE, HS_RIB_ALIVE
        ):
            alive, via, dev = _probe_gw_alive("10.9.19.2")
        assert alive is True
        assert via == "172.30.0.17"
        assert dev == "backbone"

    def test_de_edge_alive_FAILS_under_old_direct_neighbor_gate(self):
        """RED-GREEN proof #1: the rolled-back 1.2.6 gate
        `_router_neighbor_is_full(owner)` would PRUNE this live edge, because
        the owner 10.130.30.33 is NOT a direct neighbor of the hub (it is 2
        hops away). This test asserts the edge is ALIVE *and* independently
        demonstrates that the old gate would have returned False for it.
        """
        from tasks.nexthop_monitor import _router_neighbor_is_full

        owner = "10.130.30.33"  # de-edge, the gw owner
        # The OLD gate (regression): direct-neighbor-Full on the owner.
        old_gate_alive = _router_neighbor_is_full(owner, HS_NEIGHBORS_ALIVE)
        assert old_gate_alive is False, (
            "sanity: in real hub-and-spoke the edge owner is NOT a direct "
            "neighbor, so the old 1.2.6 gate is structurally always-False"
        )
        # The NEW gate: the edge must be judged ALIVE.
        with self._patch_vtysh(
            HS_ROUTER_LSDB_ALIVE, HS_NEIGHBORS_ALIVE, HS_RIB_ALIVE
        ):
            alive, _, _ = _probe_gw_alive("10.9.21.2")
        assert alive is True, (
            "REGRESSION GUARD: a live edge whose owner is 2 hops away must be "
            "ALIVE; the old direct-neighbor-Full gate would have pruned it and "
            "blackholed geo egress at steady state (issue #37 reopen)"
        )

    # -- edge DEAD --------------------------------------------------------

    def test_de_edge_dead_is_judged_dead_even_though_slash24_still_onlink(self):
        """RED-GREEN proof #2 (original #37): de edge down. Its hub-side wg
        interface stays UP so 10.9.21.0/24 REMAINS in the RIB (advertised by
        the hub-side pod 10.130.30.30) — a naive resolver keeps 'resolving' the
        gw and would false-alive. The reciprocal P2P link to the edge
        10.130.30.33 is GONE from the hub-side pod's LSA and the edge's own LSA
        aged out. The gate MUST return DEAD so failover fires.
        """
        with self._patch_vtysh(
            HS_ROUTER_LSDB_DE_DEAD, HS_NEIGHBORS_DE_DEAD, HS_RIB_DE_DEAD
        ):
            alive, via, dev = _probe_gw_alive("10.9.21.2")
        assert alive is False, (
            "issue #37: a dead edge whose /24 is still on-link via the hub-side "
            "wg interface must be judged DEAD"
        )
        assert (via, dev) == (None, None)

    def test_p2p_edge_dead_when_hub_side_p2p_link_withdrawn_but_slash24_lingers(self):
        """Required P2P-edge dead case: the de owner 10.130.30.33 is NOT a
        direct neighbor, so the probe takes the reciprocal-P2P branch. In the
        dead fixture the hub-side pod 10.130.30.30 remains Full and still
        advertises the 10.9.21.0/24 stub, but its P2P link to .33 is withdrawn;
        the gw must be DEAD, not false-alive via the lingering /24.
        """
        assert "10.130.30.33" not in HS_NEIGHBORS_DE_DEAD["neighbors"]
        assert "10.9.21.0/24" in HS_RIB_DE_DEAD
        with self._patch_vtysh(
            HS_ROUTER_LSDB_DE_DEAD, HS_NEIGHBORS_DE_DEAD, HS_RIB_DE_DEAD
        ):
            alive, via, dev = _probe_gw_alive("10.9.21.2")
        assert alive is False
        assert (via, dev) == (None, None)

    def test_de_edge_dead_slash24_still_resolves_naively(self):
        """Proof that the DEAD fixture is a genuine #37 trap: the gw /24 is
        STILL present and RIB-resolvable in the dead state, so a gate that
        trusted `_resolve_direct_router_nexthop` alone (no adjacency check)
        would keep it alive. This is what makes the previous behaviour wrong.
        """
        from tasks.nexthop_monitor import _resolve_direct_router_nexthop

        # The hub-side pod 10.130.30.30 still advertises 10.9.21.0/24 on-link;
        # resolving *that* owner against the dead RIB still yields a nexthop.
        naive = _resolve_direct_router_nexthop(
            "10.130.30.30", HS_ROUTER_LSDB_DE_DEAD, HS_RIB_DE_DEAD
        )
        assert naive is not None, (
            "the dead-state fixture must keep 10.9.21.0/24 on-link so it is a "
            "faithful #37 reproduction (a naive resolver would false-alive)"
        )

    def test_pt_edge_still_alive_when_only_de_edge_dead(self):
        """The gate is per-edge: with de dead, pt (reciprocal P2P intact, its
        hub-side pod 10.130.30.20 still Full) stays ALIVE in the same view."""
        with self._patch_vtysh(
            HS_ROUTER_LSDB_DE_DEAD, HS_NEIGHBORS_DE_DEAD, HS_RIB_DE_DEAD
        ):
            alive, via, dev = _probe_gw_alive("10.9.19.2")
        assert alive is True
        assert via == "172.30.0.17"
        assert dev == "backbone"

    # -- backbone gw -------------------------------------------------------

    def test_backbone_gw_direct_neighbor_full_is_alive(self):
        """Required backbone case: 172.30.0.x is owned by a direct Full
        neighbor, so it is alive via the direct-neighbor branch."""
        with self._patch_vtysh(
            HS_ROUTER_LSDB_ALIVE, HS_NEIGHBORS_ALIVE, HS_RIB_ALIVE
        ):
            alive, via, dev = _probe_gw_alive("172.30.0.15")
        assert alive is True
        assert via == "172.30.0.15"
        assert dev == "backbone"

    def test_backbone_gw_direct_neighbor_down_is_dead(self):
        """Required backbone dead case: direct owner is not Full -> dead."""
        neighbors = self._without_neighbor(HS_NEIGHBORS_ALIVE, "10.130.30.50")
        with self._patch_vtysh(HS_ROUTER_LSDB_ALIVE, neighbors, HS_RIB_ALIVE):
            alive, via, dev = _probe_gw_alive("172.30.0.15")
        assert alive is False
        assert (via, dev) == (None, None)

    # -- genuinely-local direct-neighbor stub gw (border) ------------------

    def test_border_direct_neighbor_gw_still_alive(self):
        """Required direct-stub-router case: border gw 10.130.30.50 is a
        /32 stub owned by router 10.130.30.50, which IS a Full direct neighbor.
        It is NOT an edge P2P ifaceAddr and must be ALIVE at steady state."""
        with self._patch_vtysh(
            HS_ROUTER_LSDB_ALIVE, HS_NEIGHBORS_ALIVE, HS_RIB_ALIVE
        ):
            alive, via, dev = _probe_gw_alive("10.130.30.50")
        assert alive is True
        assert via == "172.30.0.15"
        assert dev == "backbone"

    def test_regression_1_2_7_border_stub_router_is_not_pruned(self):
        """Regression for the 1.2.7 border bug: forcing the border stub gw
        10.130.30.50 through the P2P-edge path returns dead because no P2P link
        owns that router-id. The correct first branch is direct-neighbor-Full on
        owner 10.130.30.50, so border remains ALIVE and is not pruned."""
        from tasks.nexthop_monitor import _edge_p2p_adjacency_healthy

        assert _edge_p2p_adjacency_healthy(
            "10.130.30.50", HS_ROUTER_LSDB_ALIVE, HS_NEIGHBORS_ALIVE
        ) is False, "sanity: the 1.2.7 P2P-only path would prune border"
        with self._patch_vtysh(
            HS_ROUTER_LSDB_ALIVE, HS_NEIGHBORS_ALIVE, HS_RIB_ALIVE
        ):
            alive, via, dev = _probe_gw_alive("10.130.30.50")
        assert (alive, via, dev) == (True, "172.30.0.15", "backbone")

    def test_border_direct_stub_router_owner_down_is_dead(self):
        """Required border dead case: the stub owner is no longer a Full direct
        neighbor, so border is DEAD rather than falling through to P2P or RIB."""
        neighbors = self._without_neighbor(HS_NEIGHBORS_ALIVE, "10.130.30.50")
        with self._patch_vtysh(HS_ROUTER_LSDB_ALIVE, neighbors, HS_RIB_ALIVE):
            alive, via, dev = _probe_gw_alive("10.130.30.50")
        assert alive is False
        assert (via, dev) == (None, None)

    def test_border_alive_when_de_edge_dead(self):
        """Border stays alive independent of edge health."""
        with self._patch_vtysh(
            HS_ROUTER_LSDB_DE_DEAD, HS_NEIGHBORS_DE_DEAD, HS_RIB_DE_DEAD
        ):
            alive, via, dev = _probe_gw_alive("10.130.30.50")
        assert alive is True
        assert via == "172.30.0.15"
        assert dev == "backbone"


class TestEdgeReciprocalP2pAdjacency:
    """Unit tests for the reciprocal-P2P-adjacency helper: given a gw that is an
    edge's P2P routerInterfaceAddress, is the owning edge two-way with a
    hub-side pod that is a Full direct neighbor?"""

    def test_de_edge_alive_reciprocal_and_hub_full(self):
        from tasks.nexthop_monitor import _edge_p2p_adjacency_healthy

        assert _edge_p2p_adjacency_healthy(
            "10.9.21.2", HS_ROUTER_LSDB_ALIVE, HS_NEIGHBORS_ALIVE
        ) is True

    def test_pt_edge_alive_reciprocal_and_hub_full(self):
        from tasks.nexthop_monitor import _edge_p2p_adjacency_healthy

        assert _edge_p2p_adjacency_healthy(
            "10.9.19.2", HS_ROUTER_LSDB_ALIVE, HS_NEIGHBORS_ALIVE
        ) is True

    def test_de_edge_dead_reciprocal_link_withdrawn(self):
        from tasks.nexthop_monitor import _edge_p2p_adjacency_healthy

        # de-hub pod .30 withdrew its P2P link to .33 and .33's LSA is gone ->
        # no reciprocal adjacency -> not healthy.
        assert _edge_p2p_adjacency_healthy(
            "10.9.21.2", HS_ROUTER_LSDB_DE_DEAD, HS_NEIGHBORS_DE_DEAD
        ) is False

    def test_backbone_gw_returns_none_for_directly_attached_path(self):
        """A backbone gw (border's 172.30.0.15) is directly-attached, one hop
        away -> None so the caller takes the direct-neighbor path (NOT edge
        gating)."""
        from tasks.nexthop_monitor import _edge_p2p_adjacency_healthy

        assert _edge_p2p_adjacency_healthy(
            "172.30.0.15", HS_ROUTER_LSDB_ALIVE, HS_NEIGHBORS_ALIVE
        ) is None

    def test_offbackbone_gw_with_no_live_p2p_owner_is_dead(self):
        """An off-backbone (edge tunnel) gw that is NOT any router's live P2P
        interface-address is a DEAD edge (its LSA aged out; only the hub-side
        /24 stub lingers) -> False, NOT None. This is what stops the dead edge
        from leaking into the directly-attached path via the stub fallback
        (issue #37)."""
        from tasks.nexthop_monitor import _edge_p2p_adjacency_healthy

        assert _edge_p2p_adjacency_healthy(
            "10.9.21.2", HS_ROUTER_LSDB_DE_DEAD, HS_NEIGHBORS_DE_DEAD
        ) is False

    def test_reciprocal_present_but_hub_side_not_full_is_dead(self):
        """If the reciprocal P2P link exists in the LSDB but the hub-side pod is
        NOT a Full direct neighbor (hub-side leg itself broken), the edge is not
        reachable -> not healthy."""
        from tasks.nexthop_monitor import _edge_p2p_adjacency_healthy

        neighbors_no_de_hub = {
            "neighbors": {
                k: v for k, v in HS_NEIGHBORS_ALIVE["neighbors"].items()
                if k != "10.130.30.30"  # drop the de-hub pod's Full adjacency
            }
        }
        assert _edge_p2p_adjacency_healthy(
            "10.9.21.2", HS_ROUTER_LSDB_ALIVE, neighbors_no_de_hub
        ) is False


# ---------------------------------------------------------------------------
# issue #65: the backbone subnet is derived at runtime from the IPv4
# addresses on the pod's PBR interfaces (IPT_INTERFACES_JSON / Config,
# default ["backbone"]) — not hardcoded 172.30.0.0/24. The local stand runs
# 192.0.2.0/26 on its backbone interface; a hardcoded /24 never matches it,
# so the border neighbour is never resolved and its group is blackholed.
# ---------------------------------------------------------------------------

import ipaddress

from types import SimpleNamespace

import tasks.nexthop_monitor as nm
from tasks.nexthop_monitor import (
    _is_on_backbone,
    _probe_gw_alive,
    _resolve_direct_router_nexthop,
    _tick,
)


class TestBackboneSubnetFromInterface:
    """Backbone membership follows the PBR interface's configured subnets."""

    def _refresh(self, prefixes, interfaces=None):
        cfg = SimpleNamespace(
            interfaces=interfaces if interfaces is not None else ["backbone"]
        )
        with (
            patch("ipt_server.state.CONFIG", cfg),
            patch(
                "tasks.nexthop_monitor._backbone_addr_prefixes",
                return_value=list(prefixes),
            ),
        ):
            nm._refresh_backbone_subnets()

    def test_member_on_derived_192_0_2_26_subnet(self):
        """192.0.2.3 is on backbone when the backbone interface runs 192.0.2.0/26."""
        self._refresh([("192.0.2.10", 26)])
        assert _is_on_backbone("192.0.2.3") is True
        assert _is_on_backbone("192.0.2.63") is True
        assert _is_on_backbone("192.0.2.64") is False, "outside the /26"
        # The old hardcoded subnet must no longer qualify on its own.
        assert _is_on_backbone("172.30.0.6") is False

    def test_derived_subnet_resolves_direct_neighbour(self):
        """#65 repro: a direct neighbour 192.0.2.3 on the derived /26 backbone
        is alive and resolvable via the directly-attached fallback."""
        self._refresh([("192.0.2.10", 26)])
        router_lsdb = {
            "routerLinkStates": {
                "areas": {
                    "0.0.0.0": [
                        {
                            "advertisingRouter": "10.130.30.30",
                            "routerLinks": {
                                "link0": {
                                    "linkType": "a Transit Network",
                                    "routerInterfaceAddress": "192.0.2.3",
                                },
                            },
                        },
                    ],
                },
            },
        }
        rib = {
            "192.0.2.0/26": {
                "transit": True,
                "nexthops": [{"ip": " ", "directlyAttachedTo": "backbone"}],
            },
        }
        neighbors = {
            "neighbors": {
                "10.130.30.30": [
                    {
                        "state": "Full/-",
                        "address": "192.0.2.3",
                        "ifaceName": "backbone",
                    },
                ],
            },
        }

        def side_effect(command):
            if "database router" in command:
                return router_lsdb
            if "database external" in command:
                return {"asExternalLinkStates": []}
            if "ospf neighbor" in command:
                return neighbors
            if "ospf route" in command:
                return rib
            return None

        with patch("tasks.nexthop_monitor._vtysh", side_effect=side_effect):
            alive, via, dev = _probe_gw_alive("192.0.2.3")

        assert alive is True, (
            "#65: a direct neighbour on the configured backbone subnet must "
            "resolve, not blackhole its group"
        )
        assert via == "192.0.2.3"
        assert dev == "backbone"

    def test_default_172_30_slash24_stand_still_works(self):
        """A stand actually running 172.30.0.0/24 on its backbone interface
        keeps today's behaviour through the derived path."""
        self._refresh([("172.30.0.9", 24)])
        assert _is_on_backbone("172.30.0.6") is True
        # border directly-attached fallback (Fix 8 fixture) still resolves
        assert _resolve_direct_router_nexthop(
            "10.130.30.50", VXXLCX_ROUTER_LSDB, VXXLCX_RIB
        ) == ("172.30.0.38", "backbone")

    def test_p2p_tunnel_ip_outside_interface_subnet_rejected(self):
        """A tunnel-edge P2P IP outside the derived subnet is never a valid
        fallback gateway (Fix 8 guarantee under derived subnets)."""
        self._refresh([("192.0.2.10", 26)])
        assert _is_on_backbone("10.9.19.2") is False
        rib = {
            "10.9.19.0/28": {
                "nexthops": [
                    {"ip": " ", "directlyAttachedTo": "backbone"},
                ]
            },
        }
        # usa edge 10.130.30.23 only advertises tunnel IP 10.9.19.2; the
        # blank-.ip fallback must refuse it -> None.
        assert (
            _resolve_direct_router_nexthop("10.130.30.23", VXXLCX_ROUTER_LSDB, rib)
            is None
        )

    def test_no_interface_address_fails_closed(self):
        """No backbone interface / no address -> nothing is on backbone."""
        self._refresh([])
        assert _is_on_backbone("172.30.0.6") is False
        assert _is_on_backbone("192.0.2.3") is False

    def test_tick_re_evaluates_subnets_each_tick(self):
        """_tick refreshes the derived subnets every tick (the interface may
        appear after start)."""
        reader = MagicMock(return_value=[("192.0.2.10", 26)])
        desc = _make_desc({"gw": "10.9.19.2"})
        with (
            patch("tasks.nexthop_monitor._backbone_addr_prefixes", reader),
            patch(
                "tasks.nexthop_monitor._probe_gw_alive",
                return_value=(False, None, None),
            ),
            patch("nexthop.replace_nexthop_blackhole", MagicMock()),
            patch("nexthop.replace_group", MagicMock()),
        ):
            _tick({desc: 4}, {("10.9.19.2", None): 1}, {}, {}, {}, first_tick=True)
            _tick({desc: 4}, {("10.9.19.2", None): 1}, {}, {}, {}, first_tick=False)

        assert reader.call_count == 2, "subnets must be re-derived every tick"


class TestBackboneSubnetRefreshLogging:
    """PR #66 review: a silent empty _BACKBONE_SUBNETS blackholes backbone
    neighbours with no visible log (exactly how #65 went unnoticed). The
    refresh must WARN once on the transition to empty and INFO once per
    subnet change — not once per tick."""

    def _refresh(self, prefixes):
        cfg = SimpleNamespace(interfaces=["backbone"])
        with (
            patch("ipt_server.state.CONFIG", cfg),
            patch(
                "tasks.nexthop_monitor._backbone_addr_prefixes",
                return_value=list(prefixes),
            ),
        ):
            nm._refresh_backbone_subnets()

    def _refresh_raises(self, exc):
        cfg = SimpleNamespace(interfaces=["backbone"])
        with (
            patch("ipt_server.state.CONFIG", cfg),
            patch("tasks.nexthop_monitor._backbone_addr_prefixes", side_effect=exc),
        ):
            nm._refresh_backbone_subnets()

    def test_warns_once_on_transition_to_empty(self, caplog):
        import logging as _l

        with caplog.at_level(_l.WARNING, logger="tasks.nexthop_monitor"):
            self._refresh([])  # transition to empty -> one WARNING
            self._refresh([])  # still empty -> no repeat
            warnings = [r for r in caplog.records if r.levelno == _l.WARNING]
            assert len(warnings) == 1, "empty backbone must WARN once, not per tick"
            assert "backbone" in warnings[0].getMessage()

            self._refresh([("192.0.2.10", 26)])  # recovers -> arm the flag again
            self._refresh([])  # a NEW empty stretch warns again
            warnings = [r for r in caplog.records if r.levelno == _l.WARNING]
            assert len(warnings) == 2, "re-emptying after recovery must WARN again"

    def test_warns_once_when_derivation_fails(self, caplog):
        import logging as _l

        with caplog.at_level(_l.WARNING, logger="tasks.nexthop_monitor"):
            self._refresh_raises(RuntimeError("netlink down"))
            self._refresh_raises(RuntimeError("netlink down"))
        warnings = [r for r in caplog.records if r.levelno == _l.WARNING]
        assert len(warnings) == 1, "a failing derivation must WARN once"

    def test_logs_info_once_when_subnets_change(self, caplog):
        import logging as _l

        with caplog.at_level(_l.INFO, logger="tasks.nexthop_monitor"):
            self._refresh([("192.0.2.10", 26)])
            self._refresh([("192.0.2.10", 26)])  # unchanged -> no repeat
            self._refresh([("172.30.0.9", 24)])  # change -> INFO again
        infos = [
            r.getMessage()
            for r in caplog.records
            if r.levelno == _l.INFO
            and ("192.0.2.0/26" in r.getMessage() or "172.30.0.0/24" in r.getMessage())
        ]
        assert len(infos) == 2, f"expected one INFO per change, got: {infos}"
        assert "192.0.2.0/26" in infos[0]
        assert "172.30.0.0/24" in infos[1]


class TestBackboneAddrPrefixReader:
    """_backbone_addr_prefixes maps pyroute2 netlink state to (addr, prefixlen)
    pairs for the configured PBR interfaces only."""

    class _FakeAddr(dict):
        def __init__(self, prefixlen, local):
            super().__init__(prefixlen=prefixlen)
            self._local = local

        def get_attr(self, name):
            return self._local if name in ("IFA_LOCAL", "IFA_ADDRESS") else None

    class _FakeIPRoute:
        def __init__(self, addr_map):
            self._map = addr_map
            self._index = {name: i + 1 for i, name in enumerate(addr_map)}

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def link_lookup(self, ifname):
            return [self._index[ifname]] if ifname in self._index else []

        def get_addr(self, index, family):
            name = next(n for n, i in self._index.items() if i == index)
            return [
                TestBackboneAddrPrefixReader._FakeAddr(plen, addr)
                for addr, plen in self._map[name]
            ]

    def test_reads_all_configured_pbr_interfaces(self):
        fake = self._FakeIPRoute(
            {"backbone": [("192.0.2.10", 26)], "border": [("172.30.0.9", 24)]}
        )
        cfg = SimpleNamespace(interfaces=["backbone", "border"])
        with (
            patch("ipt_server.state.CONFIG", cfg),
            patch("tasks.nexthop_monitor.IPRoute", return_value=fake),
        ):
            prefixes = nm._backbone_addr_prefixes()
        assert sorted(prefixes) == [("172.30.0.9", 24), ("192.0.2.10", 26)]

    def test_defaults_to_backbone_without_config(self):
        fake = self._FakeIPRoute({"backbone": [("172.30.0.9", 24)]})
        with (
            patch("ipt_server.state.CONFIG", None),
            patch("tasks.nexthop_monitor.IPRoute", return_value=fake),
        ):
            prefixes = nm._backbone_addr_prefixes()
        assert prefixes == [("172.30.0.9", 24)]

    def test_missing_interface_yields_nothing(self):
        fake = self._FakeIPRoute({})
        cfg = SimpleNamespace(interfaces=["backbone"])
        with (
            patch("ipt_server.state.CONFIG", cfg),
            patch("tasks.nexthop_monitor.IPRoute", return_value=fake),
        ):
            assert nm._backbone_addr_prefixes() == []
