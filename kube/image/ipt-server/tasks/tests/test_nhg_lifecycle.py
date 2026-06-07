"""NHG lifecycle behavioral tests: setup, failover initialization.

Validates: Router.setup_nexthop_group() creates and registers kernel nexthop objects.
Code: Router.py
"""

import subprocess
from types import SimpleNamespace
from unittest.mock import patch, MagicMock, call

import pytest

from Config import RouteActionGroup, RouteMember, NhgDescriptor
from Router import Router


def _make_config(routes):
    """Build a minimal config SimpleNamespace with the given routes list."""
    return SimpleNamespace(
        table=200,
        clean_conntrack=False,
        domain_route_ttl=300,
        routes=routes,
        interfaces=[],
    )


def _make_router(config):
    """Create a Router instance with background threads suppressed."""
    with patch("Router.Router._process_route_commands_iproute2"):
        router = Router(config, ipdb=MagicMock())
    router._shutdown_event.set()
    return router


def _make_group(members, rules=None):
    """Build a RouteActionGroup from member dicts."""
    if rules is None:
        rules = [".*"]
    return RouteActionGroup(route=members, rules=rules)


# ---------------------------------------------------------------------------
# 1. setup flushes then creates
# ---------------------------------------------------------------------------


def test_nhg_setup_flushes_then_creates():
    """Flush is called before any nexthop create during setup.

    Validates: Router.setup_nexthop_group() calls flush_owned once and before creates.
    Code: Router.setup_nexthop_group
    Assertion: flush_owned precedes create/create_device/create_group in call order.
    """
    group = _make_group([{"gw": "10.9.19.2"}, {"dev": "border"}])
    config = _make_config([group])
    router = _make_router(config)

    call_log = []

    def log_flush():
        call_log.append("flush")

    def log_create(*a, **kw):
        call_log.append("create")

    def log_create_device(*a, **kw):
        call_log.append("create_device")

    def log_create_blackhole(*a, **kw):
        call_log.append("create_blackhole")

    def log_create_group(*a, **kw):
        call_log.append("create_group")

    with (
        patch("nexthop.flush_owned", side_effect=log_flush),
        patch("nexthop.create", side_effect=log_create),
        patch("nexthop.create_device", side_effect=log_create_device),
        patch("nexthop.create_blackhole", side_effect=log_create_blackhole),
        patch("nexthop.create_group", side_effect=log_create_group),
        patch("ipt_server.state.INTERFACES", {"border": 5}),
        patch("ipt_server.state.INTERFACES_LOCK", MagicMock()),
    ):
        router.setup_nexthop_group()

    assert call_log[0] == "flush", "flush_owned must be called first"
    assert "flush" not in call_log[1:], "flush_owned must be called exactly once"
    assert any(
        t in call_log for t in ("create_blackhole", "create_device")
    ), "at least one member create"
    assert "create_group" in call_log, "group must be created"
    assert len(router._nhg_registry) == 1, "one descriptor registered"
    assert group.nhg_descriptor in router._nhg_registry


# ---------------------------------------------------------------------------
# 2. flush failure aborts
# ---------------------------------------------------------------------------


def test_nhg_startup_flush_failure_aborts():
    """RuntimeError from flush_owned propagates and no creates happen.

    Validates: Router.setup_nexthop_group() re-raises flush_owned RuntimeError.
    Code: Router.setup_nexthop_group
    Assertion: RuntimeError raised; no nexthop.create* called.
    """
    group = _make_group([{"gw": "10.9.19.2"}])
    config = _make_config([group])
    router = _make_router(config)

    mock_create = MagicMock()
    mock_create_device = MagicMock()
    mock_create_group = MagicMock()

    with (
        patch("nexthop.flush_owned", side_effect=RuntimeError("flush failed")),
        patch("nexthop.create", mock_create),
        patch("nexthop.create_device", mock_create_device),
        patch("nexthop.create_group", mock_create_group),
    ):
        with pytest.raises(RuntimeError, match="flush failed"):
            router.setup_nexthop_group()

    mock_create.assert_not_called()
    mock_create_device.assert_not_called()
    mock_create_group.assert_not_called()


# ---------------------------------------------------------------------------
# 3. creates member per unique key
# ---------------------------------------------------------------------------


def test_nhg_creates_member_per_unique_key():
    """Each unique (gw, dev) pair gets exactly one member nexthop object.

    Validates: Router.setup_nexthop_group() creates one nexthop per unique member.
    gw members are always seeded as blackhole; monitor first_tick reconciles.
    Code: Router.setup_nexthop_group
    Assertion: gw member = blackhole, dev member = create_device, 1 group create.
    """
    group = _make_group([{"gw": "10.9.19.2"}, {"dev": "border"}])
    config = _make_config([group])
    router = _make_router(config)

    mock_create = MagicMock()
    mock_create_blackhole = MagicMock()
    mock_create_device = MagicMock()
    mock_create_group = MagicMock()

    with (
        patch("nexthop.flush_owned"),
        patch("nexthop.create", mock_create),
        patch("nexthop.create_device", mock_create_device),
        patch("nexthop.create_blackhole", mock_create_blackhole),
        patch("nexthop.create_group", mock_create_group),
        patch("ipt_server.state.INTERFACES", {"border": 5}),
        patch("ipt_server.state.INTERFACES_LOCK", MagicMock()),
    ):
        router.setup_nexthop_group()

    mock_create.assert_not_called(), "gw member must not call create (always blackhole)"
    assert mock_create_blackhole.call_count == 1, "one gw member blackhole"
    assert mock_create_device.call_count == 1, "one dev member create"
    assert mock_create_group.call_count == 1, "one group create"


# ---------------------------------------------------------------------------
# 4. group uses highest-priority alive member
# ---------------------------------------------------------------------------


def test_nhg_creates_group_with_highest_priority_alive():
    """Group nhid points to the first alive member (highest priority).

    With the new blackhole-on-init contract, gw is always dead at startup.
    The first alive member is therefore dev (if present and interface exists).
    Monitor first_tick will reconcile the gw once OSPF converges.

    Validates: Router.setup_nexthop_group() picks dev member nhid for group
    when gw is seeded as blackhole (always) and dev is alive.
    Code: Router.setup_nexthop_group
    Assertion: create_group called with dev member's nhid.
    """
    group = _make_group([{"gw": "10.9.19.2"}, {"dev": "border"}])
    config = _make_config([group])
    router = _make_router(config)

    created_nhids = {}  # nhid -> type

    def track_create_blackhole(nhid, **kw):
        created_nhids[nhid] = "blackhole"

    def track_create_device(nhid, dev):
        created_nhids[nhid] = "dev"

    group_calls = []

    def track_create_group(nhid, member_nhid, **kw):
        group_calls.append((nhid, member_nhid))

    with (
        patch("nexthop.flush_owned"),
        patch("nexthop.create"),
        patch("nexthop.create_device", side_effect=track_create_device),
        patch("nexthop.create_blackhole", side_effect=track_create_blackhole),
        patch("nexthop.create_group", side_effect=track_create_group),
        patch("ipt_server.state.INTERFACES", {"border": 5}),
        patch("ipt_server.state.INTERFACES_LOCK", MagicMock()),
    ):
        router.setup_nexthop_group()

    assert len(group_calls) == 1
    _group_nhid, active_nhid = group_calls[0]
    assert created_nhids[active_nhid] == "dev", (
        f"Group should use dev member (first alive at startup — gw always blackholed), "
        f"but used nhid {active_nhid} which is {created_nhids.get(active_nhid)}"
    )


# ---------------------------------------------------------------------------
# 5. gw not converged creates blackhole; fallback to dev member
# ---------------------------------------------------------------------------


def test_nhg_gw_not_converged_creates_blackhole():
    """Unreachable gw member becomes blackhole; group falls back to alive dev member.

    Validates: Router.setup_nexthop_group() creates blackhole for failed gw and picks dev member.
    Code: Router.setup_nexthop_group
    Assertion: gw member created as blackhole; group uses dev member nhid.
    """
    group = _make_group([{"gw": "10.9.19.2"}, {"dev": "border"}])
    config = _make_config([group])
    router = _make_router(config)

    blackhole_nhids = set()
    device_nhids = {}

    def track_blackhole(nhid, **kw):
        blackhole_nhids.add(nhid)

    def track_create_device(nhid, dev):
        device_nhids[nhid] = dev

    group_calls = []

    def track_create_group(nhid, member_nhid, **kw):
        group_calls.append((nhid, member_nhid))

    with (
        patch("nexthop.flush_owned"),
        patch("nexthop.create"),  # should not be called for gw
        patch("nexthop.create_device", side_effect=track_create_device),
        patch("nexthop.create_blackhole", side_effect=track_blackhole),
        patch("nexthop.create_group", side_effect=track_create_group),
        patch(
            "subprocess.run",
            side_effect=subprocess.CalledProcessError(1, "ip"),
        ),
        patch("ipt_server.state.INTERFACES", {"border": 5}),
        patch("ipt_server.state.INTERFACES_LOCK", MagicMock()),
    ):
        router.setup_nexthop_group()

    # gw member must be blackhole
    gw_key = ("10.9.19.2", None)
    gw_nhid = router._member_nhids[gw_key]
    assert gw_nhid in blackhole_nhids, "gw member should be created as blackhole"

    # dev member must be alive (create_device)
    dev_key = (None, "border")
    dev_nhid = router._member_nhids[dev_key]
    assert dev_nhid in device_nhids, "dev member should be created as device"

    # Group should use dev member (first alive fallback)
    assert len(group_calls) == 1
    _, active_nhid = group_calls[0]
    assert (
        active_nhid == dev_nhid
    ), f"Group should fall back to dev member nhid={dev_nhid}, got {active_nhid}"
