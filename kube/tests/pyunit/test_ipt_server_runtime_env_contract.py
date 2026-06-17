"""Runtime environment contract tests for the ipt_server Python module.

Locks the runtime contract for the ipt_server process:
- Settings load from IPT_* environment variables with strict type validation
- Routes use the {selector, route: {gw|dev}} shape; legacy flat keys rejected
- DNS DNAT rules resolve pdns backend by hostname and reconcile IP changes
- pdns websocket connection constants are fixed runtime defaults
- Border rules preserve mark+mask semantics and startup failure is critical
"""

import sys
import importlib
from typing import Any

import pytest

from conftest import REPO_ROOT

IPT_FILES = REPO_ROOT / "kube" / "image" / "ipt-server"

# Stub local source modules that are not importable outside the container image.
# Only stub modules that are not installed in the test environment — do NOT
# stub installed packages (pydantic, pyroute2, etc.) as that breaks other tests
# that run in the same pytest session.
_LOCAL_STUBS = [
    "ip_database",  # container-local IPDB; not available in test env
    # "dns_records" is intentionally NOT stubbed — dns_records.py is a real
    # source file importable from the ipt-server tree; stubbing it breaks
    # test_ipt_server_gateway_runtime.py which imports ARecord directly.
    # "route" is intentionally NOT stubbed — test_ipt_server_gateway_runtime.py
    # imports the real route module; stubbing it poisons RouteObject for other tests.
    # ipt_server.py does not import route at module level so no stub is needed here.
    # "route_config" is intentionally NOT stubbed — it is a real importable source
    # file with no external dependencies; Config.py imports normalize_route_entry
    # from it and must get the real function, not a MagicMock.
    "route_health",  # ipt-server local module
]
for _stub in _LOCAL_STUBS:
    if _stub not in sys.modules:
        from unittest.mock import MagicMock

        sys.modules[_stub] = MagicMock()


# ---------------------------------------------------------------------------
# Import attempt — expected to raise AttributeError (function not yet defined)
# ---------------------------------------------------------------------------

try:
    import ipt_server as _ipt_server_module  # noqa: F401

    _ipt_server_main_module = importlib.import_module("ipt_server.main")

    load_settings_from_env = getattr(
        _ipt_server_main_module, "load_settings_from_env", None
    )
    render_dns_dnat_rules = getattr(
        _ipt_server_main_module, "render_dns_dnat_rules", None
    )
    reconcile_dns_backend = getattr(
        _ipt_server_main_module, "reconcile_dns_backend", None
    )
    dns_backend_accepts_queries = getattr(
        _ipt_server_main_module, "dns_backend_accepts_queries", None
    )
    build_pdns_runtime_config = getattr(
        _ipt_server_main_module, "build_pdns_runtime_config", None
    )
    render_border_rules = getattr(_ipt_server_main_module, "render_border_rules", None)
    startup_apply_network_state = getattr(
        _ipt_server_main_module, "startup_apply_network_state", None
    )
except Exception:
    load_settings_from_env = None
    render_dns_dnat_rules = None
    reconcile_dns_backend = None
    build_pdns_runtime_config = None
    render_border_rules = None
    startup_apply_network_state = None
    dns_backend_accepts_queries = None
    _ipt_server_main_module = None


def _require(fn, name: str):
    """Assert that a function exists; fail with a descriptive message if not."""
    assert fn is not None, (
        f"ipt_server.{name} must exist — this function is part of the new "
        f"env-driven runtime contract and must be implemented in Task 2+"
    )
    return fn


# ---------------------------------------------------------------------------
# Base env dict used across route validation tests
# ---------------------------------------------------------------------------

_BASE_ENV: dict[str, str] = {
    "IPT_INTERFACES_JSON": '["backbone"]',
    "IPT_WORKLOAD_CLIENT_CIDRS_JSON": '["10.20.0.0/24"]',
    "IPT_NIC_ATTACH": '["backbone"]',
    "IPT_CLEAN_CONNTRACK": "true",
    "IPT_DOMAIN_ROUTE_TTL": "300",
}


def test_settings_contract_has_no_ipt_db_field_or_requirement(monkeypatch):
    """MySettings contract must not include legacy db/IPT_DB configuration."""
    monkeypatch.setenv("IPT_INTERFACES_JSON", '["backbone"]')
    monkeypatch.setenv("IPT_WORKLOAD_CLIENT_CIDRS_JSON", "[]")
    monkeypatch.setenv("IPT_NIC_ATTACH", '["backbone"]')
    monkeypatch.setenv("IPT_ROUTES_JSON", "[]")
    monkeypatch.setenv("IPT_CLEAN_CONNTRACK", "true")
    monkeypatch.setenv("IPT_DOMAIN_ROUTE_TTL", "300")

    from Config import MySettings

    cfg = MySettings()
    assert "db" not in MySettings.model_fields
    assert not hasattr(cfg, "db")


def test_settings_load_from_env_prefix(monkeypatch):
    """Direct MySettings() must map IPT_* env keys into typed fields."""
    monkeypatch.setenv("IPT_INTERFACES_JSON", '["backbone"]')
    monkeypatch.setenv("IPT_WORKLOAD_CLIENT_CIDRS_JSON", "[]")
    monkeypatch.setenv("IPT_NIC_ATTACH", '["backbone"]')
    monkeypatch.setenv("IPT_ROUTES_JSON", "[]")
    monkeypatch.setenv("IPT_CLEAN_CONNTRACK", "true")
    monkeypatch.setenv("IPT_DOMAIN_ROUTE_TTL", "300")

    from Config import MySettings

    cfg = MySettings()
    assert cfg.interfaces == ["backbone"], (
        "MySettings must parse IPT_INTERFACES_JSON into interfaces list"
    )
    assert cfg.clean_conntrack is True, (
        "MySettings must parse IPT_CLEAN_CONNTRACK as boolean true"
    )
    assert cfg.domain_route_ttl == 300, (
        "MySettings must parse IPT_DOMAIN_ROUTE_TTL as integer"
    )


# ===========================================================================
# Settings loading from environment
# ===========================================================================


class TestLoadSettingsFromEnv:
    """load_settings_from_env must parse the full required env contract.

    Validates: ipt_server.load_settings_from_env
    Assertion: all required env vars are accepted; settings object has correct fields.
    """

    def test_config_loads_required_env_contract(self):
        """Full required env set must produce a valid settings object.

        Validates that load_settings_from_env accepts the complete required
        environment and populates the interfaces field correctly.
        """
        fn = _require(load_settings_from_env, "load_settings_from_env")
        env = {
            "IPT_INTERFACES_JSON": '["backbone"]',
            "IPT_WORKLOAD_CLIENT_CIDRS_JSON": '["10.20.0.0/24"]',
            "IPT_ROUTES_JSON": '[{"net": "0.0.0.0/0", "route": {"gw": "10.9.19.2"}}]',
            "IPT_NIC_ATTACH": '["backbone", "border"]',
            "IPT_CLEAN_CONNTRACK": "true",
            "IPT_DOMAIN_ROUTE_TTL": "300",
        }
        settings = fn(env)
        assert settings.interfaces == ["backbone"]

    def test_missing_required_env_vars_fail_fast(self):
        """Missing required env vars must raise KeyError or ValueError immediately.

        Fail-fast on startup prevents silent misconfiguration in production.
        """
        fn = _require(load_settings_from_env, "load_settings_from_env")
        with pytest.raises((KeyError, ValueError)):
            fn({})

    def test_invalid_scalar_env_values_fail_fast(self):
        """Non-numeric TTL and non-boolean CLEAN_CONNTRACK must raise ValueError.

        Type validation must happen at load time, not at first use.
        """
        fn = _require(load_settings_from_env, "load_settings_from_env")
        base = {
            **_BASE_ENV,
            "IPT_ROUTES_JSON": "[]",
        }
        with pytest.raises(ValueError):
            fn({**base, "IPT_DOMAIN_ROUTE_TTL": "not-a-number"})
        with pytest.raises(ValueError):
            fn({**base, "IPT_CLEAN_CONNTRACK": "maybe"})

    def test_env_driven_startup_keeps_rpdb_and_conntrack_behavior(self):
        """Startup settings must preserve clean_conntrack flag and interfaces list.

        These drive kernel state (conntrack flush, PBR interface set) and must
        survive the migration from YAML-based to env-based config.
        """
        fn = _require(load_settings_from_env, "load_settings_from_env")
        env = {
            **_BASE_ENV,
            "IPT_ROUTES_JSON": "[]",
        }
        settings = fn(env)
        assert settings.clean_conntrack is True
        assert settings.interfaces == ["backbone"]


# ===========================================================================
# Route shape validation
# ===========================================================================


class TestRoutesEnvValidation:
    """Routes loaded from IPT_ROUTES_JSON must use the new selector+route shape.

    Validates: ipt_server.load_settings_from_env route parsing
    Assertion: legacy top-level keys (next_hop, interface, gw, wrapper dict) rejected;
    exactly-one-selector and exactly-one-target constraints enforced.
    """

    def test_routes_env_requires_exactly_one_selector_and_one_target(self):
        """Routes with both selector types or both target types must be rejected.

        The contract requires exactly one of (net, domain, country) as selector
        and exactly one of (gw, dev) inside route: as target.
        """
        fn = _require(load_settings_from_env, "load_settings_from_env")
        # Two selectors: net + domain
        with pytest.raises(ValueError):
            fn(
                {
                    **_BASE_ENV,
                    "IPT_ROUTES_JSON": '[{"net": "1.1.1.1/32", "domain": "x", "route": {"gw": "10.0.0.1"}}]',
                }
            )
        # Two targets: gw + dev
        with pytest.raises(ValueError):
            fn(
                {
                    **_BASE_ENV,
                    "IPT_ROUTES_JSON": '[{"net": "1.1.1.1/32", "route": {"gw": "10.0.0.1", "dev": "eth0"}}]',
                }
            )

    def test_routes_env_rejects_legacy_top_level_route_keys_and_wrappers(self):
        """Legacy flat next_hop/interface/gw keys and {routes: [...]} wrapper rejected.

        These were valid in the old contract; the new contract uses the
        {selector, route: {gw|dev}} shape exclusively.
        """
        fn = _require(load_settings_from_env, "load_settings_from_env")
        with pytest.raises(ValueError):
            fn(
                {
                    **_BASE_ENV,
                    "IPT_ROUTES_JSON": '[{"net": "1.1.1.1/32", "next_hop": "10.0.0.1"}]',
                }
            )
        with pytest.raises(ValueError):
            fn(
                {
                    **_BASE_ENV,
                    "IPT_ROUTES_JSON": '[{"net": "1.1.1.1/32", "interface": "eth0"}]',
                }
            )
        with pytest.raises(ValueError):
            fn(
                {
                    **_BASE_ENV,
                    "IPT_ROUTES_JSON": '[{"net": "1.1.1.1/32", "gw": "10.0.0.1"}]',
                }
            )
        with pytest.raises(ValueError):
            fn(
                {
                    **_BASE_ENV,
                    "IPT_ROUTES_JSON": '{"routes": []}',
                }
            )

    def test_routes_env_preserves_ttl_and_route_ttl_semantics(self):
        """Per-route ttl and route_ttl fields must be preserved through env parsing.

        ttl controls NetRoute cache lifetime; route_ttl controls DomainRoute
        DNS-derived entry lifetime. Both must survive the env→settings round-trip.
        """
        fn = _require(load_settings_from_env, "load_settings_from_env")
        settings = fn(
            {
                **_BASE_ENV,
                "IPT_ROUTES_JSON": (
                    '[{"net": "1.1.1.1/32", "ttl": 60, "route": {"gw": "10.0.0.1"}},'
                    ' {"domain": "example.com", "route_ttl": 300, "route": {"gw": "10.0.0.1"}}]'
                ),
            }
        )
        assert settings.routes[0].ttl == 60
        assert settings.routes[1].route_ttl == 300


# ===========================================================================
# DNS DNAT — hostname-resolved backend
# ===========================================================================


class TestDnsDnatRules:
    """DNS DNAT rules must resolve pdns backend by hostname, not hardcoded IP.

    Validates: ipt_server.render_dns_dnat_rules, ipt_server.reconcile_dns_backend
    Assertion: rendered rules use resolved IP; reconciler re-applies on IP change.
    """

    def test_dns_dnat_uses_resolved_pdns_hostname(self, monkeypatch):
        """Rendered DNS DNAT rules must use the IP resolved from pdns hostname.

        This decouples the rule from a hardcoded address and allows pdns to
        restart on a new IP within ipt_internal_service_net.
        """
        fn = _require(render_dns_dnat_rules, "render_dns_dnat_rules")
        monkeypatch.setattr(
            _ipt_server_main_module, "resolve_backend_hostname", lambda *_: "172.31.0.3"
        )
        rules = fn()
        assert "172.31.0.3:1053" in rules

    def test_dns_backend_hostname_can_be_overridden_from_env(self, monkeypatch):
        """Kubernetes sidecar deployments must resolve pdns through configured pod IP.

        Docker compose keeps the default garuda_pdns hostname. Kubernetes runs
        PowerDNS in the same pod network namespace, where there is no garuda_pdns
        DNS name, so the chart must be able to point the reconciler at the pod IP.
        """
        fn = _require(
            _ipt_server_main_module.resolve_backend_hostname,
            "resolve_backend_hostname",
        )
        seen: list[str] = []

        def fake_gethostbyname(hostname: str) -> str:
            seen.append(hostname)
            return "192.0.2.10"

        monkeypatch.setenv("IPT_DNS_BACKEND_HOST", "192.0.2.10")
        monkeypatch.setattr(_ipt_server_main_module.socket, "gethostbyname", fake_gethostbyname)

        assert fn() == "192.0.2.10"
        assert seen == ["192.0.2.10"]

    def test_dns_dnat_reconciles_when_resolved_backend_ip_changes(self, monkeypatch):
        """DNS DNAT reconciler must re-apply rules when resolved IP changes.

        Ensures the DNAT rule tracks pdns container restarts with new IPs.
        """
        fn = _require(reconcile_dns_backend, "reconcile_dns_backend")
        ips = iter(["172.31.0.3", "172.31.0.9"])
        monkeypatch.setattr(
            _ipt_server_main_module, "resolve_backend_hostname", lambda *_: next(ips)
        )
        applied: list[str] = []
        monkeypatch.setattr(
            _ipt_server_main_module,
            "apply_dns_dnat_rules",
            lambda ip: applied.append(ip),
        )
        monkeypatch.setattr(
            _ipt_server_main_module,
            "dns_backend_accepts_queries",
            lambda *_args, **_kwargs: True,
            raising=False,
        )
        fn()
        fn()
        assert applied == ["172.31.0.3", "172.31.0.9"]

    def test_dns_dnat_rules_include_reply_path_masquerade_for_backend(
        self, monkeypatch
    ):
        """DNS backend NAT must SNAT traffic toward the pdns backend IP.

        Validates: ipt_server.render_dns_dnat_rules
        Code: modules/ipt_server/kube/image/ipt-server/ipt_server/main.py::_render_dns_dnat_ruleset

        Assertion: rendered ruleset must contain a postrouting chain with masquerade
        rules scoped to the resolved backend IP.

        SNAT is mandatory (not optional) because garuda_pdns runs in its own namespace
        and has no route knowledge for client subnets. Without masquerade, reply traffic
        has no defined path back to the intercepted client flow.

        Method:
        1. Arrange: monkeypatch resolve_backend_hostname to return a fixed IP
        2. Act: call render_dns_dnat_rules()
        3. Assert: postrouting chain present; masquerade rules scoped to backend IP
        """
        fn = _require(render_dns_dnat_rules, "render_dns_dnat_rules")
        monkeypatch.setattr(
            _ipt_server_main_module,
            "resolve_backend_hostname",
            lambda *_: "172.31.0.3",
        )
        rules = fn()
        assert "chain postrouting" in rules
        assert "ip daddr 172.31.0.3 udp dport 1053 masquerade" in rules
        assert "ip daddr 172.31.0.3 tcp dport 1053 masquerade" in rules

    def test_dns_reply_path_nat_is_scoped_only_to_backend_ip(self, monkeypatch):
        """Reply-path NAT must not widen into a generic port-53 masquerade rule.

        Validates: ipt_server.render_dns_dnat_rules
        Code: modules/ipt_server/kube/image/ipt-server/ipt_server/main.py::_render_dns_dnat_ruleset

        Assertion: the postrouting masquerade rules must use ip daddr <backend_ip>,
        not ip daddr != 127.0.0.0/8. A broad masquerade on port 53 would cause
        recursive upstream traffic from garuda_pdns to be SNATted again on the
        way out, breaking the topology separation between intercepted client traffic
        and recursive upstream DNS queries.

        Method:
        1. Arrange: monkeypatch resolve_backend_hostname to return a fixed IP
        2. Act: call render_dns_dnat_rules()
        3. Assert: broad daddr != 127.0.0.0/8 masquerade absent
        """
        fn = _require(render_dns_dnat_rules, "render_dns_dnat_rules")
        monkeypatch.setattr(
            _ipt_server_main_module,
            "resolve_backend_hostname",
            lambda *_: "172.31.0.3",
        )
        rules = fn()
        assert "ip daddr != 127.0.0.0/8 udp dport 1053 masquerade" not in rules
        assert "ip daddr != 127.0.0.0/8 tcp dport 1053 masquerade" not in rules

    def test_dns_dnat_reconcile_skips_unready_backend(self, monkeypatch):
        """Resolved backend IP alone must not trigger NAT installation.

        Validates: ipt_server.reconcile_dns_backend
        Code: modules/ipt_server/kube/image/ipt-server/ipt_server/main.py::reconcile_dns_backend

        Assertion: if the backend resolves but dns_backend_accepts_queries returns False,
        apply_dns_dnat_rules must not be called.

        Method:
        1. Arrange: stub resolve_backend_hostname to return an IP; stub
           dns_backend_accepts_queries to return False
        2. Act: call reconcile_dns_backend()
        3. Assert: apply_dns_dnat_rules was not called
        """
        fn = _require(reconcile_dns_backend, "reconcile_dns_backend")
        monkeypatch.setattr(
            _ipt_server_main_module,
            "resolve_backend_hostname",
            lambda *_: "172.31.0.3",
        )
        monkeypatch.setattr(
            _ipt_server_main_module,
            "dns_backend_accepts_queries",
            lambda *_args, **_kwargs: False,
            raising=False,
        )
        applied: list[str] = []
        monkeypatch.setattr(
            _ipt_server_main_module,
            "apply_dns_dnat_rules",
            lambda ip: applied.append(ip),
        )
        fn()
        assert applied == []

    def test_dns_dnat_reconcile_applies_once_backend_becomes_ready(self, monkeypatch):
        """The monitor loop must converge when the backend starts accepting queries.

        Validates: ipt_server.reconcile_dns_backend
        Code: modules/ipt_server/kube/image/ipt-server/ipt_server/main.py::reconcile_dns_backend

        Assertion: reconcile_dns_backend called twice — first with unready backend
        (apply skipped), second with ready backend (apply triggered).

        Method:
        1. Arrange: resolve always returns the same IP; readiness iterator yields
           [False, True]; capture apply_dns_dnat_rules calls
        2. Act: call reconcile_dns_backend() twice
        3. Assert: applied == ["172.31.0.3"] (only the second call triggers apply)
        """
        fn = _require(reconcile_dns_backend, "reconcile_dns_backend")
        monkeypatch.setattr(
            _ipt_server_main_module,
            "resolve_backend_hostname",
            lambda *_: "172.31.0.3",
        )
        # Use itertools.repeat so extra calls beyond the test scenario do not
        # raise StopIteration and produce a confusing failure message.
        import itertools

        readiness = iter(itertools.chain([False, True], itertools.repeat(True)))
        monkeypatch.setattr(
            _ipt_server_main_module,
            "dns_backend_accepts_queries",
            lambda *_args, **_kwargs: next(readiness),
            raising=False,
        )
        applied: list[str] = []
        monkeypatch.setattr(
            _ipt_server_main_module,
            "apply_dns_dnat_rules",
            lambda ip: applied.append(ip),
        )
        fn()
        fn()
        assert applied == ["172.31.0.3"]


# ===========================================================================
# dns_backend_accepts_queries — TCP readiness probe
# ===========================================================================


class TestDnsBackendAcceptsQueries:
    """dns_backend_accepts_queries must probe TCP :53 and return False on failure.

    Validates: ipt_server.dns_backend_accepts_queries
    Code: modules/ipt_server/kube/image/ipt-server/ipt_server/main.py::dns_backend_accepts_queries
    """

    def test_returns_true_when_connection_succeeds(self, monkeypatch):
        """Returns True when socket.create_connection succeeds.

        Method:
        1. Arrange: monkeypatch socket.create_connection to succeed (no-op context manager)
        2. Act: call dns_backend_accepts_queries("172.31.0.3")
        3. Assert: returns True
        """
        from unittest.mock import MagicMock

        fn = _require(dns_backend_accepts_queries, "dns_backend_accepts_queries")
        mock_sock = MagicMock()
        mock_sock.__enter__ = lambda s: s
        mock_sock.__exit__ = MagicMock(return_value=False)
        monkeypatch.setattr(
            _ipt_server_main_module.socket,
            "create_connection",
            lambda *_args, **_kwargs: mock_sock,
        )
        assert fn("172.31.0.3") is True

    def test_returns_false_when_connection_refused(self, monkeypatch):
        """Returns False and logs a warning when socket.create_connection raises OSError.

        Method:
        1. Arrange: monkeypatch socket.create_connection to raise ConnectionRefusedError
        2. Act: call dns_backend_accepts_queries("172.31.0.3")
        3. Assert: returns False; no exception propagates
        """
        fn = _require(dns_backend_accepts_queries, "dns_backend_accepts_queries")
        monkeypatch.setattr(
            _ipt_server_main_module.socket,
            "create_connection",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(
                ConnectionRefusedError("connection refused")
            ),
        )
        assert fn("172.31.0.3") is False


# ===========================================================================
# pdns fixed runtime constants
# ===========================================================================


class TestPdnsRuntimeConstants:
    """pdns runtime config must use hardcoded websocket constants — no env config needed.

    Validates: ipt_server.build_pdns_runtime_config
    Assertion: websocket_host and websocket_port are fixed; empty env is sufficient.
    """

    def test_pdns_fixed_websocket_constants_need_no_pdns_env(self, monkeypatch):
        """pdns config must work with empty env, using hardcoded ws constants.

        Eliminates the PDNS_WS_PORT / PDNS_GARUDA_INTERFACES env vars that
        previously had to be threaded through the module and compose.
        """
        fn = _require(build_pdns_runtime_config, "build_pdns_runtime_config")
        cfg = fn({})
        assert cfg.websocket_host == "127.0.0.1", (
            "websocket_host must be hardcoded to '127.0.0.1'"
        )
        assert cfg.websocket_port == 8765, "websocket_port must be hardcoded to 8765"


# ===========================================================================
# Border rules — mark+mask semantics
# ===========================================================================


class TestBorderRulesRuntime:
    """render_border_rules must produce nftables masquerade rules for border egress.

    Validates: ipt_server.render_border_rules
    Assertion: oifname border masquerade present when has_border is True; empty when False.
    """

    def test_border_rules_render_masquerade_when_border_attached(self, monkeypatch):
        """Rendered border rules must use oifname border masquerade when border is attached.

        The border interface is the contract for outbound NAT; the masquerade
        rule on that interface is what provides internet egress for transit clients.
        """
        fn = _require(render_border_rules, "render_border_rules")
        import ipt_server.state as _state_mod
        from unittest.mock import MagicMock as _MM

        fake_cfg = _MM()
        fake_cfg.has_border = True
        monkeypatch.setattr(_state_mod, "CONFIG", fake_cfg)
        rules = fn()
        assert 'oifname "border" masquerade' in rules

    def test_border_rules_empty_when_border_not_attached(self, monkeypatch):
        """render_border_rules returns empty string when has_border is False."""
        fn = _require(render_border_rules, "render_border_rules")
        import ipt_server.state as _state_mod
        from unittest.mock import MagicMock as _MM

        fake_cfg = _MM()
        fake_cfg.has_border = False
        monkeypatch.setattr(_state_mod, "CONFIG", fake_cfg)
        rules = fn()
        assert rules == ""


# ===========================================================================
# Startup — border apply failure is critical
# ===========================================================================


class TestStartupContract:
    """startup_apply_network_state must propagate border apply failures.

    Validates: ipt_server.startup_apply_network_state
    Assertion: RuntimeError from apply_border_rules propagates to caller —
    misconfigured border rules must not silently leave the host in a broken state.
    """

    def test_border_apply_failure_is_startup_critical(self, monkeypatch):
        """apply_border_rules failure must propagate as RuntimeError at startup.

        Swallowing border rule failures would leave clients unmasqueraded while
        the service appears healthy.
        """
        fn = _require(startup_apply_network_state, "startup_apply_network_state")
        # apply_pbr() runs before apply_border_rules(); stub it so the test
        # doesn't touch kernel routing state (requires root/real netns).
        monkeypatch.setattr(_ipt_server_main_module, "apply_pbr", lambda: None)
        monkeypatch.setattr(
            _ipt_server_main_module,
            "apply_border_rules",
            lambda: (_ for _ in ()).throw(RuntimeError("boom")),
        )
        # CONFIG is not set at test time (it's assigned in main()); inject a
        # minimal stand-in so startup_apply_network_state can read has_border.
        from unittest.mock import MagicMock as _MM

        fake_cfg = _MM()
        fake_cfg.has_border = True
        import ipt_server.state as _state_mod

        monkeypatch.setattr(_state_mod, "CONFIG", fake_cfg)
        with pytest.raises(RuntimeError):
            fn()

    def test_startup_applies_dns_dnat_reconciliation(self, monkeypatch):
        """startup_apply_network_state must call reconcile_dns_backend.

        DNS DNAT must be installed at startup so that clients can resolve
        hostnames through garuda_pdns from the moment the service is healthy.
        Skipping this call leaves the DNS path broken until garuda_pdns is
        recreated (which would trigger reconciliation via the monitor loop).
        """
        fn = _require(startup_apply_network_state, "startup_apply_network_state")
        from unittest.mock import MagicMock as _MM

        fake_cfg = _MM()
        fake_cfg.has_border = False
        import ipt_server.state as _state_mod

        monkeypatch.setattr(_state_mod, "CONFIG", fake_cfg)
        monkeypatch.setattr(_ipt_server_main_module, "apply_pbr", lambda: None)
        monkeypatch.setattr(_ipt_server_main_module, "apply_border_rules", lambda: None)
        called = []
        monkeypatch.setattr(
            _ipt_server_main_module,
            "reconcile_dns_backend",
            lambda: called.append(True),
        )
        fn()
        assert called, "startup_apply_network_state must call reconcile_dns_backend()"


def test_ipt_mysettings_has_no_dataplane_ip_field() -> None:
    """MySettings must not declare dataplane_ip — the field is unused dead code."""
    from Config import MySettings

    assert "dataplane_ip" not in MySettings.model_fields, (
        "MySettings.dataplane_ip is legacy dead code; nothing in production reads it"
    )
