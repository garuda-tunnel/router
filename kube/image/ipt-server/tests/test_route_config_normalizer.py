"""Route-entry normalization contract (route_config.normalize_route_entry).

The delivery contract accepts only object-form routes: {gw: ip} or
{dev: name}, single or list; legacy flat keys and string syntax are
rejected with actionable messages. Drives MySettings.parse_routes through
real validation (the parser delegates here).
"""

import pytest
from pydantic import ValidationError

from Config import MySettings
from route_config import normalize_route_entry


def test_accepts_single_gw_object_form():
    """A {route: {gw: ip}} entry passes through unchanged."""
    assert normalize_route_entry({"route": {"gw": "192.0.2.1"}}) == {
        "route": {"gw": "192.0.2.1"}
    }


def test_accepts_single_dev_object_form():
    """A {route: {dev: name}} entry passes through unchanged."""
    assert normalize_route_entry({"route": {"dev": "backbone"}}) == {
        "route": {"dev": "backbone"}
    }


def test_accepts_member_list_with_gw_and_dev_entries():
    """A list of members, each with exactly one of gw/dev, is accepted."""
    entry = {"route": [{"gw": "192.0.2.1"}, {"dev": "border"}]}
    assert normalize_route_entry(entry) == entry


def test_rejects_string_route_syntax():
    """String route syntax is rejected with the object-form hint."""
    with pytest.raises(ValueError, match="object-form route"):
        normalize_route_entry({"route": "192.0.2.1"})


def test_rejects_route_with_both_gw_and_dev():
    """A route object with both gw and dev is ambiguous and rejected."""
    with pytest.raises(ValueError, match="both gw and dev"):
        normalize_route_entry({"route": {"gw": "192.0.2.1", "dev": "border"}})


def test_rejects_non_dict_list_member():
    """A non-dict list member is rejected naming its position."""
    with pytest.raises(ValueError, match=r"route\[0\] must be a dict"):
        normalize_route_entry({"route": ["192.0.2.1"]})


def test_rejects_list_member_with_both_gw_and_dev():
    """A list member with both gw and dev is rejected naming its position."""
    with pytest.raises(ValueError, match=r"route\[1\] cannot have both"):
        normalize_route_entry(
            {"route": [{"gw": "192.0.2.1"}, {"gw": "192.0.2.2", "dev": "border"}]}
        )


def test_rejects_list_member_with_neither_gw_nor_dev():
    """A list member with neither gw nor dev is rejected naming its position."""
    with pytest.raises(ValueError, match=r"route\[0\] must have gw or dev"):
        normalize_route_entry({"route": [{}]})


def test_rejects_legacy_flat_keys():
    """Legacy flat keys (next_hop/interface/gw) are rejected with the hint."""
    for legacy in ("next_hop", "interface", "gw"):
        with pytest.raises(ValueError, match="legacy route key"):
            normalize_route_entry({legacy: "192.0.2.1"})


def test_rejects_entry_without_route_key():
    """An entry without a route key is rejected with the contract message."""
    with pytest.raises(ValueError, match="must contain a 'route' key"):
        normalize_route_entry({"net": "192.0.2.0/24"})


def test_my_settings_rejects_string_route_end_to_end():
    """End-to-end: MySettings surfaces the object-form error for string
    routes (parse_routes delegates to normalize_route_entry)."""
    with pytest.raises(ValidationError, match="object-form route"):
        MySettings.model_validate(
            {
                "IPT_INTERFACES_JSON": '["backbone"]',
                "IPT_CLEAN_CONNTRACK": "false",
                "IPT_DOMAIN_ROUTE_TTL": "300",
                "IPT_ROUTES_JSON": '[{"net": "192.0.2.0/24", "route": "192.0.2.1"}]',
            }
        )


def test_my_settings_accepts_legacy_net_route_with_object_gw():
    """End-to-end: a legacy net entry with object-form route parses into a
    NetRoute carrying the configured gw."""
    settings = MySettings.model_validate(
        {
            "IPT_INTERFACES_JSON": '["backbone"]',
            "IPT_CLEAN_CONNTRACK": "false",
            "IPT_DOMAIN_ROUTE_TTL": "300",
            "IPT_ROUTES_JSON": (
                '[{"net": "192.0.2.0/24", "route": {"gw": "192.0.2.1"}}]'
            ),
        }
    )
    (route,) = settings.routes
    assert str(route.net) == "192.0.2.0/24"
    assert route.route.gw == "192.0.2.1"
    assert route.route.dev is None
