"""MySettings environment validation and route-form normalization (T10/T11).

Covers the JSON-env parser branches (bytes, non-string, invalid JSON,
wrong type), interface/nic_attach validation, bool parsing, legacy and
grouped route forms, selector ambiguity, country expansion via a fake
IPDB, and YAML file loading.
"""

import json
from unittest.mock import MagicMock, patch

import pytest
from pydantic import ValidationError

from Config import DomainRoute, MySettings, NetRoute  # noqa: F401


def _env(**overrides):
    env = {
        "IPT_INTERFACES_JSON": '["backbone"]',
        "IPT_ROUTES_JSON": "[]",
        "IPT_CLEAN_CONNTRACK": "false",
        "IPT_DOMAIN_ROUTE_TTL": "300",
    }
    env.update(overrides)
    return env


class TestJsonEnvParsing:
    def test_bytes_env_values_are_decoded(self):
        """A bytes JSON env value (as os.environb delivers) is decoded."""
        settings = MySettings.model_validate(
            _env(IPT_INTERFACES_JSON=b'["backbone", "border"]')
        )
        assert settings.interfaces == ["backbone", "border"]

    def test_non_string_non_bytes_env_is_rejected(self):
        """A JSON field that is neither str/bytes/list/dict is rejected."""
        with pytest.raises(ValidationError, match="must be a JSON string"):
            MySettings.model_validate(_env(IPT_ROUTES_JSON=17))

    def test_invalid_json_env_is_rejected(self):
        """Malformed JSON is rejected naming the env key."""
        with pytest.raises(ValidationError, match="IPT_ROUTES_JSON"):
            MySettings.model_validate(_env(IPT_ROUTES_JSON="{nope"))

    def test_wrong_decoded_type_is_rejected(self):
        """JSON decoding to the wrong shape is rejected with both types."""
        with pytest.raises(ValidationError, match="got str"):
            MySettings.model_validate(_env(IPT_INTERFACES_JSON='"backbone"'))

    def test_pin_json_env_rejects_non_dict_payload(self):
        """IPT_PINNING_EGRESS_JSON must decode to an object, not a list."""
        with pytest.raises(ValidationError, match="IPT_PINNING_EGRESS_JSON"):
            MySettings.model_validate(_env(IPT_PINNING_EGRESS_JSON='["usa"]'))


class TestListValidation:
    def test_interfaces_rejects_non_string_member(self):
        """A non-string interfaces member is rejected with its position."""
        with pytest.raises(ValidationError, match=r"interfaces\[1\] must be a string"):
            MySettings.model_validate(_env(IPT_INTERFACES_JSON='["backbone", 5]'))

    def test_interfaces_rejects_blank_member(self):
        """A blank interfaces member is rejected."""
        with pytest.raises(ValidationError, match="non-empty string"):
            MySettings.model_validate(_env(IPT_INTERFACES_JSON='["backbone", "  "]'))

    def test_interfaces_members_are_stripped(self):
        """Surrounding whitespace in interface names is normalized away."""
        settings = MySettings.model_validate(_env(IPT_INTERFACES_JSON='[" backbone "]'))
        assert settings.interfaces == ["backbone"]

    def test_nic_attach_rejects_unknown_network(self):
        """IPT_NIC_ATTACH only accepts backbone/border."""
        with pytest.raises(ValidationError, match="unknown network"):
            MySettings.model_validate(_env(IPT_NIC_ATTACH='["gi0"]'))

    def test_has_border_reflects_nic_attach(self):
        """has_border is True only when border appears in nic_attach."""
        with_border = MySettings.model_validate(_env(IPT_NIC_ATTACH='["backbone", "border"]'))
        without = MySettings.model_validate(_env(IPT_NIC_ATTACH='["backbone"]'))
        assert with_border.has_border is True
        assert without.has_border is False


class TestBoolParsing:
    def test_true_false_strings_accepted(self):
        """clean_conntrack accepts the true/false strings case-insensitively."""
        assert (
            MySettings.model_validate(_env(IPT_CLEAN_CONNTRACK="TRUE")).clean_conntrack
            is True
        )
        assert (
            MySettings.model_validate(_env(IPT_CLEAN_CONNTRACK="False")).clean_conntrack
            is False
        )

    def test_invalid_bool_string_rejected(self):
        """Any other string is rejected naming both accepted values."""
        with pytest.raises(ValidationError, match="expected 'true' or 'false'"):
            MySettings.model_validate(_env(IPT_CLEAN_CONNTRACK="maybe"))

    def test_non_string_non_bool_rejected(self):
        """Integers are not accepted for bool fields."""
        with pytest.raises(ValidationError, match="boolean or true/false string"):
            MySettings.model_validate(_env(IPT_CLEAN_CONNTRACK=1))

    def test_plain_bool_passes_through(self):
        """Real booleans pass validation untouched."""
        assert (
            MySettings.model_validate(_env(IPT_CLEAN_CONNTRACK=True)).clean_conntrack
            is True
        )


class TestRouteForms:
    def test_domain_route_match_uses_fullmatch(self):
        """DomainRoute.match fullmatches its compiled regex."""
        route = DomainRoute(domain=r".*\.example\.net", route={"dev": "backbone"})
        assert route.match("probe.example.net") is True
        assert route.match("probe.example.net.evil.example") is False

    def test_domain_route_match_rejects_non_strings(self):
        """Non-string input to match() is False, not an error."""
        assert DomainRoute(domain=".*", route={"dev": "backbone"}).match(123) is False

    def test_domain_route_build_route_makes_slash_32(self):
        """DomainRoute.build_route produces a /32 RouteObject with its tags."""
        route = DomainRoute(domain=r".*\.example\.net", route={"gw": "192.0.2.1"})
        built = route.build_route("198.51.100.7")
        assert str(built.net) == "198.51.100.7/32"
        assert built.gw == "192.0.2.1"
        assert built.dev is None

    def test_net_route_rejects_host_bits(self):
        """NetRoute requires a canonical network address (strict parse)."""
        with pytest.raises(ValidationError, match="host bits"):
            NetRoute(net="192.0.2.9/24", route={"gw": "192.0.2.1"})

    def test_net_route_rejects_invalid_cidr(self):
        """An invalid CIDR fails validation at parse time."""
        with pytest.raises(ValidationError):
            NetRoute(net="192.0.2.0/99", route={"gw": "192.0.2.1"})

    def test_legacy_entry_without_selector_is_rejected(self):
        """An entry with no net/domain/country selector is rejected."""
        with pytest.raises(ValidationError, match="no valid key found"):
            MySettings.model_validate(_env(IPT_ROUTES_JSON='[{"route": {"gw": "192.0.2.1"}}]'))

    def test_legacy_entry_with_two_selectors_is_rejected(self):
        """An entry with both net and domain selectors is rejected."""
        with pytest.raises(ValidationError, match="exactly one selector"):
            MySettings.model_validate(
                _env(
                    IPT_ROUTES_JSON=json.dumps(
                        [
                            {
                                "net": "192.0.2.0/24",
                                "domain": ".*",
                                "route": {"gw": "192.0.2.1"},
                            }
                        ]
                    )
                )
            )

    def test_legacy_selector_list_expands_to_entries(self):
        """A list under one selector expands to one route per value."""
        settings = MySettings.model_validate(
            _env(
                IPT_ROUTES_JSON=json.dumps(
                    [
                        {
                            "net": ["192.0.2.0/24", "198.51.100.0/24"],
                            "route": {"gw": "192.0.2.1"},
                        }
                    ]
                )
            )
        )
        assert [str(r.net) for r in settings.routes] == [
            "192.0.2.0/24",
            "198.51.100.0/24",
        ]

    def test_legacy_domain_route_gets_default_ttl(self):
        """Legacy domain routes inherit domain_route_ttl when unspecified."""
        settings = MySettings.model_validate(
            _env(
                IPT_DOMAIN_ROUTE_TTL="123",
                IPT_ROUTES_JSON=json.dumps(
                    [{"domain": "probe\\.example\\.net", "route": {"gw": "192.0.2.1"}}]
                ),
            )
        )
        assert settings.routes[0].route_ttl == 123

    def test_legacy_net_route_keeps_explicit_ttl(self):
        """An explicit route_ttl on a net entry survives parsing."""
        settings = MySettings.model_validate(
            _env(
                IPT_ROUTES_JSON=json.dumps(
                    [
                        {
                            "net": "192.0.2.0/24",
                            "route": {"gw": "192.0.2.1"},
                            "route_ttl": 60,
                        }
                    ]
                )
            )
        )
        assert settings.routes[0].route_ttl == 60


class TestCountryExpansion:
    @pytest.mark.xfail(
        strict=True,
        reason="router-internal#77: legacy country expansion appends every subnet twice",
    )
    def test_country_routes_expand_subnets_via_ipdb(self):
        """Intended contract: CountryRoute entries expand their subnets
        through the IP database exactly once each (router-internal#77)."""
        fake_ipdb = MagicMock()
        fake_ipdb.__getitem__.return_value = ["192.0.2.0/25", "198.51.100.0/25"]
        with patch("ipdb.query.IPDatabase", return_value=fake_ipdb):
            settings = MySettings.model_validate(
                _env(
                    IPT_ROUTES_JSON=json.dumps(
                        [{"country": "XX", "route": {"gw": "192.0.2.1"}}]
                    )
                )
            )
        nets = [str(r.net) for r in settings.routes[0].routes]
        assert nets == ["192.0.2.0/25", "198.51.100.0/25"]



class TestYamlLoad:
    def test_load_reads_yaml_file_into_settings(self, tmp_path):
        """MySettings.load parses a YAML file with alias-style keys."""
        config = tmp_path / "settings.yaml"
        config.write_text(
            "IPT_INTERFACES_JSON: '[\"backbone\"]'\n"
            "IPT_CLEAN_CONNTRACK: 'false'\n"
            "IPT_DOMAIN_ROUTE_TTL: '300'\n"
            "IPT_ROUTES_JSON: '[]'\n"
        )
        settings = MySettings.load(str(config))
        assert settings.interfaces == ["backbone"]
