"""Malformed-input and failure-path behavior of the nexthop monitor helpers.

The OSPF/LSDB payloads arrive from an external FRR sidecar over HTTP; the
resolver family must degrade to "not alive / no owner" on every malformed
shape instead of raising. These tests feed the guard branches directly and
through _probe_gw_alive with scripted vty-bridge responses.
"""

import asyncio
import logging
import ipaddress
import json
import urllib.error
from unittest.mock import MagicMock, patch

import pytest

import ipt_server.tasks.nexthop_monitor as nm
from ipt_server.tasks.nexthop_monitor import (
    _edge_p2p_adjacency_healthy,
    _find_edge_owning_p2p_address,
    _find_router_owning_address,
    _is_on_backbone,
    _probe_dev_alive,
    _probe_gw_alive,
    _refresh_backbone_subnets,
    _resolve_direct_router_nexthop,
    _resolve_router_nexthop,
    _router_neighbor_is_full,
    _router_originates_default,
    _tick,
    _vtysh,
)
from tests.support import FrozenClock  # noqa: F401


def _lsdb(links_by_router):
    """Build a router-LSDB payload: {router_id: {link_id: link_dict}}."""
    return {
        "routerLinkStates": {
            "areas": {
                "0.0.0.0": [
                    {"advertisingRouter": rid, "routerLinks": links}
                    for rid, links in links_by_router.items()
                ]
            }
        }
    }


class TestVtyshFailureModes:
    def test_network_error_returns_none(self):
        """A transport failure to the vty bridge returns None."""
        with patch("ipt_server.tasks.nexthop_monitor.urllib.request.urlopen") as up:
            up.side_effect = urllib.error.URLError("conn refused")
            assert _vtysh("show ip ospf route json") is None

    def test_non_json_body_returns_none(self):
        """A non-JSON body returns None."""
        with patch("ipt_server.tasks.nexthop_monitor.urllib.request.urlopen") as up:
            resp = MagicMock()
            resp.read.return_value = b"<html>not json</html>"
            up.return_value.__enter__.return_value = resp
            assert _vtysh("x") is None

    def test_non_dict_json_returns_none(self):
        """A JSON list (not object) returns None."""
        with patch("ipt_server.tasks.nexthop_monitor.urllib.request.urlopen") as up:
            resp = MagicMock()
            resp.read.return_value = json.dumps([1, 2]).encode()
            up.return_value.__enter__.return_value = resp
            assert _vtysh("x") is None

    def test_valid_json_returns_dict(self):
        """A valid JSON object round-trips."""
        with patch("ipt_server.tasks.nexthop_monitor.urllib.request.urlopen") as up:
            resp = MagicMock()
            resp.read.return_value = json.dumps({"a": 1}).encode()
            up.return_value.__enter__.return_value = resp
            assert _vtysh("x") == {"a": 1}


class TestFindRouterOwningAddressGuards:
    def test_non_dict_lsdb_returns_none(self):
        """A non-dict LSDB yields no owner."""
        assert _find_router_owning_address("10.9.19.2", ["junk"]) is None

    def test_non_dict_areas_returns_none(self):
        """A malformed areas container yields no owner."""
        lsdb = {"routerLinkStates": {"areas": ["junk"]}}
        assert _find_router_owning_address("10.9.19.2", lsdb) is None

    def test_invalid_gw_ip_returns_none(self):
        """A non-IP gw string yields no owner."""
        assert _find_router_owning_address("not-an-ip", _lsdb({})) is None

    def test_non_list_area_and_non_dict_lsa_are_skipped(self):
        """Malformed area/LSA entries are skipped, valid siblings still win."""
        lsdb = {
            "routerLinkStates": {
                "areas": {
                    "0.0.0.0": [
                        "not-a-dict",
                        {
                            "advertisingRouter": "10.130.30.23",
                            "routerLinks": {"1": {"routerInterfaceAddress": "10.9.19.2"}},
                        },
                    ],
                    "198.51.100.63": {"not": "a list"},
                }
            }
        }
        assert _find_router_owning_address("10.9.19.2", lsdb) == "10.130.30.23"

    def test_malformed_routerlinks_and_link_entries_are_skipped(self):
        """Non-dict routerLinks / link values are skipped without raising."""
        lsdb = _lsdb(
            {
                "10.130.30.23": {
                    "bad-links": None,
                    "1": {"routerInterfaceAddress": "10.9.19.2"},
                }
            }
        )
        lsdb["routerLinkStates"]["areas"]["0.0.0.0"][0]["routerLinks"] = "junk"
        assert _find_router_owning_address("10.9.19.2", lsdb) is None

    def test_stub_network_missing_fields_are_skipped(self):
        """A stub link without address/mask is skipped; a later valid stub
        network containing the gw still resolves."""
        lsdb = _lsdb(
            {
                "10.130.30.23": {
                    "1": {"linkType": "Stub Network"},
                    "2": {"linkType": "Stub Network", "networkAddress": None},
                    "3": {
                        "linkType": "Stub Network",
                        "networkAddress": "10.9.19.0",
                        "networkMask": "255.255.255.0",
                    },
                }
            }
        )
        assert _find_router_owning_address("10.9.19.5", lsdb) == "10.130.30.23"

    def test_stub_network_invalid_mask_is_skipped(self):
        """An unparseable stub network mask is skipped."""
        lsdb = _lsdb(
            {
                "10.130.30.23": {
                    "1": {
                        "linkType": "Stub Network",
                        "networkAddress": "10.9.19.0",
                        "networkMask": "not-a-mask",
                    }
                }
            }
        )
        assert _find_router_owning_address("10.9.19.5", lsdb) is None


class TestRouterOriginatesDefaultGuards:
    def test_non_dict_lsdb_returns_false(self):
        """A non-dict external LSDB is not a default origination."""
        assert _router_originates_default("10.130.30.23", None) is False

    def test_non_list_lsas_returns_false(self):
        """A malformed asExternalLinkStates container yields False."""
        lsdb = {"asExternalLinkStates": {"not": "a list"}}
        assert _router_originates_default("10.130.30.23", lsdb) is False

    def test_non_dict_lsa_is_skipped(self):
        """Non-dict LSA entries are skipped; valid matching LSA wins."""
        lsdb = {
            "asExternalLinkStates": [
                "junk",
                {
                    "advertisingRouter": "10.130.30.23",
                    "linkStateId": "0.0.0.0",
                    "networkMask": 0,
                },
            ]
        }
        assert _router_originates_default("10.130.30.23", lsdb) is True


class TestNeighborFullGuards:
    def test_malformed_shapes_return_false(self):
        """Every malformed neighbor payload degrades to not-Full."""
        neighbor_data = {"neighbors": {"10.130.30.23": [{"nbrState": "Full"}]}}
        assert _router_neighbor_is_full("10.130.30.23", neighbor_data) is True
        assert _router_neighbor_is_full("10.130.30.23", "junk") is False
        assert _router_neighbor_is_full("10.130.30.23", {"neighbors": "junk"}) is False
        assert _router_neighbor_is_full(
            "10.130.30.23", {"neighbors": {"10.130.30.23": "junk"}}
        ) is False
        assert _router_neighbor_is_full(
            "10.130.30.23", {"neighbors": {"other": [{"nbrState": "Full"}]}}
        ) is False

    def test_state_field_fallback_and_non_dict_entries(self):
        """The `state` field is accepted and non-dict entries are skipped."""
        data = {"neighbors": {"r": ["junk", {"state": "Full/DROther"}]}}
        assert _router_neighbor_is_full("r", data) is True
        data2 = {"neighbors": {"r": ["junk", {"state": "2-Way"}]}}
        assert _router_neighbor_is_full("r", data2) is False


class TestEdgeP2PResolutionGuards:
    def test_malformed_lsdb_shapes_return_none(self):
        """Malformed LSDB shapes yield no edge ownership (non-dict LSDB and
        non-dict areas both return None; a string routerLinkStates raises
        AttributeError today — reported as a production robustness gap and
        deliberately not encoded as expected behavior)."""
        gw = "10.9.19.2"
        assert _find_edge_owning_p2p_address(gw, "junk") is None
        lsdb = {"routerLinkStates": {"areas": {"a": "junk"}}}
        assert _find_edge_owning_p2p_address(gw, lsdb) is None

    def test_non_p2p_links_do_not_qualify(self):
        """Stub/transit links never make an address an edge tunnel."""
        lsdb = _lsdb(
            {
                "10.130.30.23": {
                    "1": {"routerInterfaceAddress": "10.9.19.2", "linkType": "Stub Network"}
                }
            }
        )
        assert _find_edge_owning_p2p_address("10.9.19.2", lsdb) is None

    def test_malformed_links_are_skipped(self):
        """Non-dict LSA/link/routerLinks entries are skipped."""
        lsdb = _lsdb({"r": {"1": {"linkType": "another Router (p2p)"}}})
        area = lsdb["routerLinkStates"]["areas"]["0.0.0.0"]
        area.insert(0, "junk")
        area.append({"advertisingRouter": "r2", "routerLinks": "junk"})
        assert _find_edge_owning_p2p_address("10.9.19.2", lsdb) is None

    def test_adjacency_healthy_malformed_shapes_return_false(self):
        """_edge_p2p_adjacency_healthy degrades on malformed payloads and
        requires the reciprocal hub-side P2P leg plus a Full hub neighbor."""
        assert _edge_p2p_adjacency_healthy("10.9.19.2", "junk", {}) is False
        bad_areas = {"routerLinkStates": {"areas": "junk"}}
        assert _edge_p2p_adjacency_healthy("10.9.19.2", bad_areas, {}) is False

        gw = "10.9.19.2"
        edge_lsa = {
            "1": {
                "linkType": "another Router (p2p)",
                "routerInterfaceAddress": gw,
                "neighborRouterId": "10.130.30.20",
            }
        }
        hub_lsa = {
            "1": {
                "linkType": "another Router (p2p)",
                "neighborRouterId": "10.130.30.23",
            }
        }
        lsdb = _lsdb({"10.130.30.23": edge_lsa, "10.130.30.20": hub_lsa})
        full_hub = {"neighbors": {"10.130.30.20": [{"nbrState": "Full"}]}}
        assert _edge_p2p_adjacency_healthy(gw, lsdb, full_hub) is True
        init_hub = {"neighbors": {"10.130.30.20": [{"nbrState": "Init"}]}}
        assert _edge_p2p_adjacency_healthy(gw, lsdb, init_hub) is False
        # Without the reciprocal hub-side leg the edge is dead.
        one_sided = _lsdb({"10.130.30.23": edge_lsa})
        assert _edge_p2p_adjacency_healthy(gw, one_sided, full_hub) is False


class TestResolveNexthopGuards:
    def test_resolve_router_nexthop_malformed_shapes(self):
        """_resolve_router_nexthop degrades on malformed RIB entries."""
        good_route = {
            "nexthops": [{"ip": "172.30.0.5", "via": "wg-0"}],
        }
        assert _resolve_router_nexthop("r", {"r": good_route}) == (
            "172.30.0.5",
            "wg-0",
        )
        assert _resolve_router_nexthop("r", "junk") is None
        assert _resolve_router_nexthop("r", {"r": "junk"}) is None
        assert _resolve_router_nexthop("r", {"r": {"nexthops": "junk"}}) is None
        assert _resolve_router_nexthop("r", {"r": {"nexthops": []}}) is None
        assert _resolve_router_nexthop(
            "r", {"r": {"nexthops": ["junk", {"ip": None, "via": None}]}}
        ) is None

    def test_resolve_direct_router_nexthop_malformed_shapes(self):
        """_resolve_direct_router_nexthop degrades on malformed inputs."""
        assert _resolve_direct_router_nexthop("r", "junk", {}) is None
        assert _resolve_direct_router_nexthop("r", {}, "junk") is None
        bad_areas = {"routerLinkStates": {"areas": "junk"}}
        assert _resolve_direct_router_nexthop("r", bad_areas, {}) is None

    def test_resolve_direct_skips_default_route_and_bad_prefix(self):
        """The default route and unparseable prefixes are never matched."""
        lsdb = _lsdb(
            {"r": {"1": {"routerInterfaceAddress": "172.30.0.99"}}}
        )
        rib = {
            "0.0.0.0/0": {"nexthops": [{"ip": "172.30.0.1", "directlyAttachedTo": "wg-0"}]},
            "not-a-prefix": {"nexthops": [{"ip": "172.30.0.1", "directlyAttachedTo": "wg-0"}]},
            "172.30.0.0/24": {
                "nexthops": [{"ip": "172.30.0.5", "directlyAttachedTo": "wg-0"}]
            },
        }
        assert _resolve_direct_router_nexthop("r", lsdb, rib) == ("172.30.0.5", "wg-0")

    def test_resolve_direct_blank_ip_falls_back_to_on_backbone_address(self):
        """A blank .ip falls back to the covering on-backbone interface
        address as the gateway (never a tunnel-edge P2P IP)."""
        lsdb = _lsdb({"r": {"1": {"routerInterfaceAddress": "172.30.0.99"}}})
        rib = {
            "172.30.0.0/24": {"nexthops": [{"ip": "", "via": "wg-0"}]},
        }
        with patch.object(nm, "_BACKBONE_SUBNETS", (ipaddress.ip_network("172.30.0.0/24"),)):
            assert _resolve_direct_router_nexthop("r", lsdb, rib) == (
                "172.30.0.99",
                "wg-0",
            )


class TestProbeGwAliveFailurePaths:
    def _run(self, lsdb_side, rib_side=None, external_side=None, neighbors_side=None, gw="10.9.19.2"):
        responses = {
            "show ip ospf database router json": lsdb_side,
            "show ip ospf route json": rib_side,
            "show ip ospf database external json": external_side,
            "show ip ospf neighbor json": neighbors_side,
        }

        def fake_vtysh(command):
            return responses.get(command)

        with patch("ipt_server.tasks.nexthop_monitor._vtysh", side_effect=fake_vtysh):
            return _probe_gw_alive(gw)

    def test_no_lsdb_returns_false(self):
        """A missing router LSDB reports not alive."""
        assert self._run(None)[0] is False

    def test_no_owner_returns_false(self):
        """An LSDB where nobody owns the gw reports not alive."""
        assert self._run(_lsdb({}))[0] is False

    def test_no_neighbors_returns_false(self):
        """A missing neighbor dump reports not alive."""
        lsdb = _lsdb({"10.130.30.23": {"1": {"routerInterfaceAddress": "10.9.19.2"}}})
        assert self._run(lsdb, neighbors_side=None)[0] is False

    def test_not_full_direct_and_no_edge_adjacency(self):
        """Owner not Full and no healthy edge adjacency reports not alive."""
        lsdb = _lsdb({"10.130.30.23": {"1": {"routerInterfaceAddress": "10.9.19.2"}}})
        neighbors = {"neighbors": {"10.130.30.23": [{"nbrState": "Init"}]}}
        assert self._run(lsdb, neighbors_side=neighbors)[0] is False

    def test_no_rib_returns_false(self):
        """A missing RIB reports not alive even with a Full owner."""
        lsdb = _lsdb({"10.130.30.23": {"1": {"routerInterfaceAddress": "10.9.19.2"}}})
        neighbors = {"neighbors": {"10.130.30.23": [{"nbrState": "Full"}]}}
        assert self._run(lsdb, rib_side=None, neighbors_side=neighbors)[0] is False

    def test_no_external_lsdb_after_no_direct_returns_false(self):
        """No direct nexthop and no external LSDB reports not alive."""
        lsdb = _lsdb({"10.130.30.23": {"1": {"routerInterfaceAddress": "10.9.19.2"}}})
        neighbors = {"neighbors": {"10.130.30.23": [{"nbrState": "Full"}]}}
        rib = {"other": {"nexthops": [{"ip": "172.30.0.5", "via": "wg-0"}]}}
        assert self._run(lsdb, rib_side=rib, external_side=None, neighbors_side=neighbors)[0] is False

    def test_no_default_origination_returns_false(self):
        """No direct nexthop and no default origination reports not alive."""
        lsdb = _lsdb({"10.130.30.23": {"1": {"routerInterfaceAddress": "10.9.19.2"}}})
        neighbors = {"neighbors": {"10.130.30.23": [{"nbrState": "Full"}]}}
        rib = {"other": {"nexthops": [{"ip": "172.30.0.5", "via": "wg-0"}]}}
        external = {"asExternalLinkStates": []}
        assert self._run(lsdb, rib_side=rib, external_side=external, neighbors_side=neighbors)[0] is False

    def test_default_origination_without_nexthop_returns_false(self):
        """Originating default but no router nexthop in RIB: not alive."""
        lsdb = _lsdb({"10.130.30.23": {"1": {"routerInterfaceAddress": "10.9.19.2"}}})
        neighbors = {"neighbors": {"10.130.30.23": [{"nbrState": "Full"}]}}
        rib = {"other": {"nexthops": [{"ip": "172.30.0.5", "via": "wg-0"}]}}
        external = {
            "asExternalLinkStates": [
                {
                    "advertisingRouter": "10.130.30.23",
                    "linkStateId": "0.0.0.0",
                    "networkMask": 0,
                }
            ]
        }
        assert self._run(lsdb, rib_side=rib, external_side=external, neighbors_side=neighbors)[0] is False

    def test_alive_via_direct_nexthop(self):
        """A Full on-backbone owner with a covering RIB route is alive with
        the RIB nexthop."""
        # gw is on the (test-default) 172.30.0.0/24 backbone; its owner is a
        # Full direct neighbor, so the direct-neighbor gate passes.
        lsdb = _lsdb({"10.130.30.23": {"1": {"routerInterfaceAddress": "172.30.0.99"}}})
        neighbors = {"neighbors": {"10.130.30.23": [{"nbrState": "Full"}]}}
        rib = {
            "172.30.0.0/24": {"nexthops": [{"ip": "172.30.0.5", "via": "wg-0"}]}
        }
        alive, gw_ip, dev = self._run(
            lsdb, rib_side=rib, neighbors_side=neighbors, gw="172.30.0.99"
        )
        assert (alive, gw_ip, dev) == (True, "172.30.0.5", "wg-0")


class TestProbeDevAlive:
    def test_present_interface_is_alive(self):
        """A dev whose interface is in the snapshot is alive."""
        from ipt_server import state

        with state.INTERFACES_LOCK:
            saved = dict(state.INTERFACES)
            state.INTERFACES.clear()
            state.INTERFACES.update({"wg-0": 3})
        try:
            assert _probe_dev_alive("wg-0") == (True, None)
        finally:
            with state.INTERFACES_LOCK:
                state.INTERFACES.clear()
                state.INTERFACES.update(saved)

    def test_absent_interface_is_dead(self):
        """A dev missing from the snapshot is dead."""
        from ipt_server import state

        with state.INTERFACES_LOCK:
            saved = dict(state.INTERFACES)
            state.INTERFACES.clear()
        try:
            assert _probe_dev_alive("ghost0") == (False, None)
        finally:
            with state.INTERFACES_LOCK:
                state.INTERFACES.clear()
                state.INTERFACES.update(saved)


class TestBackboneSubnetGuards:
    def test_is_on_backbone_rejects_invalid_address(self):
        """A non-IP address is simply not on the backbone."""
        assert _is_on_backbone("not-an-ip") is False

    def test_refresh_skips_malformed_addresses(self):
        """A malformed interface address is skipped during derivation."""
        with patch("ipt_server.tasks.nexthop_monitor._backbone_addr_prefixes") as prefixes:
            prefixes.return_value = [("garbage", 24), ("172.30.0.10", 24)]
            _refresh_backbone_subnets()
        assert nm._BACKBONE_SUBNETS == (ipaddress.ip_network("172.30.0.0/24"),)

    def test_refresh_failure_yields_empty_tuple(self):
        """A raising derivation leaves the subnets empty (fail closed)."""
        with patch(
            "ipt_server.tasks.nexthop_monitor._backbone_addr_prefixes",
            side_effect=RuntimeError("netlink gone"),
        ):
            _refresh_backbone_subnets()
        assert nm._BACKBONE_SUBNETS == ()

    def test_pbr_interface_names_default_backbone(self):
        """Without configured interfaces the derivation uses backbone."""
        from ipt_server import state

        saved = state.CONFIG
        state.CONFIG = None
        try:
            assert nm._pbr_interface_names() == ("backbone",)
        finally:
            state.CONFIG = saved


class TestTickFailureThresholds:
    def _desc(self):
        from Config import NhgDescriptor, RouteMember

        return NhgDescriptor(
            members=[RouteMember(gw="10.9.19.2"), RouteMember(dev="wg-0")]
        )

    def test_transient_failure_preserves_last_known_liveness(self):
        """Below the failure threshold, the member keeps its last state."""
        desc = self._desc()
        gw_key = ("10.9.19.2", None)
        dev_key = (None, "wg-0")
        member_alive = {gw_key: True, dev_key: True}
        with (
            patch("ipt_server.tasks.nexthop_monitor._probe_gw_alive", return_value=(False, None, None)),
            patch("ipt_server.tasks.nexthop_monitor._probe_dev_alive", return_value=(True, None)),
            patch("nexthop.replace_nexthop_blackhole"),
            patch("nexthop.replace_group"),
        ):
            _tick(
                {desc: 12},
                {gw_key: 10, dev_key: 11},
                member_alive,
                {desc: gw_key},
                {},
                first_tick=False,
            )
        assert member_alive[gw_key] is True, "transient failure keeps last state"

    def test_persistent_failure_blackholes_member(self):
        """At/after the threshold, the member is marked dead."""
        desc = self._desc()
        gw_key = ("10.9.19.2", None)
        dev_key = (None, "wg-0")
        member_alive = {gw_key: True, dev_key: True}
        with (
            patch("ipt_server.tasks.nexthop_monitor._probe_gw_alive", return_value=(False, None, None)),
            patch("ipt_server.tasks.nexthop_monitor._probe_dev_alive", return_value=(True, None)),
            patch("nexthop.replace_nexthop_blackhole") as blackhole,
            patch("nexthop.replace_group"),
            patch("ipt_server.tasks.nexthop_monitor._refresh_backbone_subnets"),
        ):
            _tick(
                {desc: 12},
                {gw_key: 10, dev_key: 11},
                member_alive,
                {desc: gw_key},
                {"10.9.19.2": nm._GW_FAILURE_THRESHOLD},
                first_tick=False,
            )
        assert member_alive[gw_key] is False
        blackhole.assert_called_once_with(10)

    def test_first_tick_replaces_gw_and_dev_members(self):
        """First tick reconciles alive members with probe-resolved values."""
        desc = self._desc()
        gw_key = ("10.9.19.2", None)
        dev_key = (None, "wg-0")
        member_alive = {}
        with (
            patch(
                "ipt_server.tasks.nexthop_monitor._probe_gw_alive",
                return_value=(True, "172.30.0.5", "wg-0"),
            ),
            patch("ipt_server.tasks.nexthop_monitor._probe_dev_alive", return_value=(True, None)),
            patch("nexthop.replace_nexthop") as replace_nh,
            patch("nexthop.replace_device") as replace_dev,
            patch("nexthop.replace_group"),
        ):
            _tick(
                {desc: 12},
                {gw_key: 10, dev_key: 11},
                member_alive,
                {},
                {},
                first_tick=True,
            )
        replace_nh.assert_called_once_with(10, via="172.30.0.5", dev="wg-0")
        replace_dev.assert_called_once_with(11, dev="wg-0")

    def test_recovered_dev_member_is_replaced(self):
        """A dead dev member that becomes alive is replaced via device."""
        desc = self._desc()
        gw_key = ("10.9.19.2", None)
        dev_key = (None, "wg-0")
        member_alive = {gw_key: False, dev_key: False}
        with (
            patch("ipt_server.tasks.nexthop_monitor._probe_gw_alive", return_value=(False, None, None)),
            patch("ipt_server.tasks.nexthop_monitor._probe_dev_alive", return_value=(True, None)),
            patch("nexthop.replace_nexthop_blackhole"),
            patch("nexthop.replace_device") as replace_dev,
            patch("nexthop.replace_group"),
        ):
            _tick(
                {desc: 12},
                {gw_key: 10, dev_key: 11},
                member_alive,
                {},
                {"10.9.19.2": nm._GW_FAILURE_THRESHOLD},
                first_tick=False,
            )
        replace_dev.assert_called_once_with(11, dev="wg-0")


class TestMonitorNexthopsLoop:
    def test_tick_skipped_until_routes_loaded(self):
        """The monitor loop skips ticks while ROUTER or routes are absent."""

        async def run():
            stop = asyncio.Event()
            router = MagicMock()
            router._routes_loaded.is_set.return_value = False
            from ipt_server import state

            state.ROUTER = router
            started = asyncio.Event()
            loop_ref = [None]

            class _StartTripHandler(logging.Handler):
                def emit(self, record):
                    if "Starting nexthop monitor" in record.getMessage():
                        if loop_ref[0] is not None:
                            loop_ref[0].call_soon_threadsafe(started.set)

            loop_ref[0] = asyncio.get_running_loop()
            trip = _StartTripHandler()
            trip_logger = logging.getLogger("ipt_server.tasks.nexthop_monitor")
            saved_level = trip_logger.level
            trip_logger.setLevel(logging.INFO)  # INFO is filtered by default
            trip_logger.addHandler(trip)
            try:
                with patch("ipt_server.tasks.nexthop_monitor._tick") as tick:
                    task = asyncio.create_task(nm.monitor_nexthops({}, {}, stop))
                    # Checkpoint: the monitor loop has provably started
                    # (observed on its loop-entry log record)...
                    await asyncio.wait_for(started.wait(), timeout=5.0)
                    # ...and one iteration has run (the routes-not-loaded
                    # guard inside _do_tick returned without calling _tick).
                    await asyncio.sleep(0)
                    stop.set()
                    await asyncio.wait_for(task, timeout=5.0)
                    return tick.called
            finally:
                trip_logger.removeHandler(trip)
                trip_logger.setLevel(saved_level)

        assert asyncio.run(run()) is False


class TestLsaHasP2PLinkTo:
    def _lsdb(self, links):
        return _lsdb({"10.130.30.23": links})

    def test_malformed_shapes_return_false(self):
        """Malformed payloads degrade to False."""
        from ipt_server.tasks.nexthop_monitor import _lsa_has_p2p_link_to

        assert _lsa_has_p2p_link_to("r", "n", "junk") is False
        bad_areas = {"routerLinkStates": {"areas": "junk"}}
        assert _lsa_has_p2p_link_to("r", "n", bad_areas) is False

    def test_matching_link_returns_true(self):
        """A P2P link to the neighbor router is found."""
        from ipt_server.tasks.nexthop_monitor import _lsa_has_p2p_link_to

        lsdb = self._lsdb(
            {"1": {"linkType": "another Router (p2p)", "neighborRouterId": "10.130.30.20"}}
        )
        assert _lsa_has_p2p_link_to("10.130.30.23", "10.130.30.20", lsdb) is True

    def test_guards_skip_junk_entries(self):
        """Junk areas/LSAs/links and non-matching entries never crash and
        never match."""
        from ipt_server.tasks.nexthop_monitor import _lsa_has_p2p_link_to

        lsdb = {
            "routerLinkStates": {
                "areas": {
                    "0.0.0.0": [
                        "junk",
                        {"advertisingRouter": "someone-else", "routerLinks": {}},
                        {"advertisingRouter": "10.130.30.23", "routerLinks": "junk"},
                        {
                            "advertisingRouter": "10.130.30.23",
                            "routerLinks": {"a": "junk", "b": {"linkType": "Stub Network"}},
                        },
                    ],
                    "198.51.100.63": {"not": "a list"},
                }
            }
        }
        assert _lsa_has_p2p_link_to("10.130.30.23", "10.130.30.20", lsdb) is False

    def test_neighbor_mismatch_returns_false(self):
        """A P2P link to a different neighbor does not match."""
        from ipt_server.tasks.nexthop_monitor import _lsa_has_p2p_link_to

        lsdb = self._lsdb(
            {"1": {"linkType": "another Router (p2p)", "neighborRouterId": "10.130.30.99"}}
        )
        assert _lsa_has_p2p_link_to("10.130.30.23", "10.130.30.20", lsdb) is False


class TestMalformedRouterLinkStates:
    @pytest.mark.xfail(
        strict=True,
        reason="router-internal#75: non-dict routerLinkStates raises "
        "AttributeError instead of degrading to None",
    )
    def test_non_dict_router_link_states_degrades_to_none(self):
        """Intended contract: malformed LSDB data degrades to no owner /
        no edge rather than raising (router-internal#75)."""
        gw = "10.9.19.2"
        assert _find_edge_owning_p2p_address(gw, {"routerLinkStates": "junk"}) is None
        assert _find_router_owning_address(gw, {"routerLinkStates": "junk"}) is None
        from ipt_server.tasks.nexthop_monitor import _lsa_has_p2p_link_to

        assert _lsa_has_p2p_link_to("r", "n", {"routerLinkStates": "junk"}) is False


class TestResolveRouterNexthopEntryGuards:
    def test_non_dict_route_entries_are_skipped(self):
        """Non-dict routes and nexthop members never crash the resolver."""
        rib = {
            "172.30.0.0/24": [
                "junk-route",
            ]
        }
        assert _resolve_router_nexthop("r", {"r": rib}) is None

    def test_direct_resolver_skips_malformed_router_entries(self):
        """The direct resolver skips junk LSAs, links and nexthops."""
        lsdb = {
            "routerLinkStates": {
                "areas": {
                    "0.0.0.0": [
                        "junk",
                        {"advertisingRouter": "r", "routerLinks": "junk"},
                        {
                            "advertisingRouter": "r",
                            "routerLinks": {"x": "junk", "y": {}},
                        },
                    ],
                    "198.51.100.63": {"not": "a list"},
                }
            }
        }
        rib = {"172.30.0.0/24": {"nexthops": ["junk", {}]}}
        assert _resolve_direct_router_nexthop("r", lsdb, rib) is None
