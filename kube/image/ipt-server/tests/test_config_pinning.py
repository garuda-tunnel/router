"""Validate Pydantic parsing for IPT_PINNING_EGRESS_JSON env input.

Validates: MySettings.pinning_egress accepts a JSON object whose values are
RouteMember-shaped dicts; missing var resolves to an empty mapping.
Code: Config.py::MySettings.pinning_egress
"""
import json
import pytest
from pydantic import ValidationError

import Config


def _base_env(monkeypatch, **overrides):
    """Set the minimum env required for MySettings to construct."""
    defaults = {
        "IPT_INTERFACES_JSON": json.dumps(["wg-firezone"]),
        "IPT_NIC_ATTACH": json.dumps(["backbone"]),
        "IPT_CLEAN_CONNTRACK": "false",
        "IPT_DOMAIN_ROUTE_TTL": "300",
        "IPT_ROUTES_JSON": json.dumps([]),
    }
    defaults.update(overrides)
    for key, value in defaults.items():
        monkeypatch.setenv(key, value)


def test_pinning_egress_defaults_to_empty(monkeypatch):
    """When IPT_PINNING_EGRESS_JSON is unset, the catalog is an empty dict."""
    _base_env(monkeypatch)
    monkeypatch.delenv("IPT_PINNING_EGRESS_JSON", raising=False)
    cfg = Config.MySettings()
    assert cfg.pinning_egress == {}


def test_pinning_egress_parses_json_object(monkeypatch):
    """A JSON object env var is parsed into a Dict[str, RouteMember]."""
    _base_env(
        monkeypatch,
        IPT_PINNING_EGRESS_JSON=json.dumps(
            {"hub": {"gw": "192.0.2.1"}, "usa": {"dev": "border"}}
        ),
    )
    cfg = Config.MySettings()
    assert set(cfg.pinning_egress.keys()) == {"hub", "usa"}
    assert cfg.pinning_egress["hub"].gw == "192.0.2.1"
    assert cfg.pinning_egress["hub"].dev is None
    assert cfg.pinning_egress["usa"].dev == "border"
    assert cfg.pinning_egress["usa"].gw is None


def test_pinning_egress_rejects_reserved_auto_key(monkeypatch):
    """The literal key 'auto' is reserved for the no-pin selection."""
    _base_env(
        monkeypatch,
        IPT_PINNING_EGRESS_JSON=json.dumps({"auto": {"gw": "192.0.2.1"}}),
    )
    with pytest.raises(ValidationError) as excinfo:
        Config.MySettings()
    assert "auto" in str(excinfo.value).lower()


def test_pinning_egress_rejects_reserved_auto_case_insensitive(monkeypatch):
    """Case variants of 'auto' are also reserved."""
    _base_env(
        monkeypatch,
        IPT_PINNING_EGRESS_JSON=json.dumps({"Auto": {"gw": "192.0.2.1"}}),
    )
    with pytest.raises(ValidationError):
        Config.MySettings()


def test_pinning_egress_rejects_non_slug_keys(monkeypatch):
    """Keys must match ^[a-z0-9_-]+$ to be URL/HTML safe without escaping."""
    _base_env(
        monkeypatch,
        IPT_PINNING_EGRESS_JSON=json.dumps(
            {"Hub Yandex": {"gw": "192.0.2.1"}}
        ),
    )
    with pytest.raises(ValidationError) as excinfo:
        Config.MySettings()
    assert "slug" in str(excinfo.value).lower() or "match" in str(excinfo.value).lower()


def test_pinning_egress_accepts_underscore_and_dash(monkeypatch):
    """Hyphens and underscores are valid in slug keys."""
    _base_env(
        monkeypatch,
        IPT_PINNING_EGRESS_JSON=json.dumps(
            {"hub_a": {"gw": "192.0.2.1"}, "ros-home": {"dev": "border"}}
        ),
    )
    cfg = Config.MySettings()
    assert "hub_a" in cfg.pinning_egress
    assert "ros-home" in cfg.pinning_egress


def test_pinning_ttl_defaults_to_24h(monkeypatch):
    """Without IPT_PINNING_TTL set, default is 86400 seconds."""
    _base_env(monkeypatch)
    monkeypatch.delenv("IPT_PINNING_TTL", raising=False)
    cfg = Config.MySettings()
    assert cfg.pinning_ttl == 86400


def test_pinning_ttl_parses_integer(monkeypatch):
    """IPT_PINNING_TTL accepts an integer-as-string from env."""
    _base_env(monkeypatch, IPT_PINNING_TTL="3600")
    cfg = Config.MySettings()
    assert cfg.pinning_ttl == 3600


def test_pinning_api_port_defaults_to_80(monkeypatch):
    """Without IPT_PINNING_API_PORT set, default is 80."""
    _base_env(monkeypatch)
    monkeypatch.delenv("IPT_PINNING_API_PORT", raising=False)
    cfg = Config.MySettings()
    assert cfg.pinning_api_port == 80


def test_pinning_portal_anchor_addr_default(monkeypatch):
    """Default value: 1.1.1.1 (Cloudflare DNS public anycast IP, used
    as the well-known portal anchor; tcp/1111 does not collide with
    any Cloudflare service port)."""
    _base_env(monkeypatch)
    cfg = Config.MySettings()
    assert cfg.pinning_portal_anchor_addr == "1.1.1.1"


def test_pinning_portal_anchor_port_default(monkeypatch):
    """Default value: 1111. Match-all by anchor IP + this port in
    the prerouting REDIRECT rule."""
    _base_env(monkeypatch)
    cfg = Config.MySettings()
    assert cfg.pinning_portal_anchor_port == 1111
