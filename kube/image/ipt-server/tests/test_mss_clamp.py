"""Tests for the ipt_server_mss forward-clamp table."""

import re
import unittest
from pathlib import Path
from types import SimpleNamespace

import jinja2

TEMPLATE_DIR = Path(__file__).parent.parent / "templates"
ICMP_DROP_RE = re.compile(r"icmp.*(drop|reject)|(drop|reject).*icmp", re.IGNORECASE)


def _render_mss(fixed_mss: int, mss_clamp_enabled: bool = True) -> str:
    env = jinja2.Environment(loader=jinja2.FileSystemLoader(str(TEMPLATE_DIR)))
    template = env.get_template("mss.nft.j2")
    config = SimpleNamespace(fixed_mss=fixed_mss, mss_clamp_enabled=mss_clamp_enabled)
    return template.render(config=config)


class TestMssClampTemplate(unittest.TestCase):
    """Render tests for templates/mss.nft.j2."""

    def test_separate_inet_table_rendered(self):
        """SF4: dedicated inet table, NOT inside ip ipt_server_pbr."""
        out = _render_mss(1240)
        self.assertIn("table inet ipt_server_mss", out)
        # Must NOT extend the PBR table.
        self.assertNotIn("table ip ipt_server_pbr", out)

    def test_chain_named_mss_clamp(self):
        """Chain name must be mss_clamp per spec §3.3."""
        out = _render_mss(1240)
        self.assertIn("chain mss_clamp", out)
        # The nft chain name must not be 'forward' (that collides with the hook name).
        self.assertNotIn("chain forward", out)

    def test_forward_hook_mangle_policy_accept(self):
        """Chain hook/type/priority/policy must be: type filter hook forward priority mangle; policy accept."""
        out = _render_mss(1240)
        self.assertIn("hook forward", out)
        self.assertIn("priority mangle", out)
        self.assertIn("policy accept", out)

    def test_iifname_backbone_rule_present(self):
        """iifname backbone MSS clamp rule must be present."""
        out = _render_mss(1240)
        self.assertIn(
            'iifname "backbone" tcp flags syn tcp option maxseg size set 1240', out
        )

    def test_oifname_backbone_rule_present(self):
        """oifname backbone MSS clamp rule must be present."""
        out = _render_mss(1240)
        self.assertIn(
            'oifname "backbone" tcp flags syn tcp option maxseg size set 1240', out
        )

    def test_no_icmp_drop_in_output(self):
        """AC8 negative: no line drops or rejects ICMP."""
        out = _render_mss(1240)
        for line in out.splitlines():
            self.assertIsNone(
                ICMP_DROP_RE.search(line),
                f"ICMP drop/reject found in line: {line!r}",
            )

    def test_icmp_ptb_transits_no_icmp_match(self):
        """AC8 positive: policy accept and no icmp match means ICMP PTB transits."""
        out = _render_mss(1240)
        self.assertIn("policy accept", out)
        # No icmp match rules that would block PTB
        for line in out.splitlines():
            stripped = line.strip()
            if stripped.startswith("#"):
                continue
            self.assertFalse(
                "icmp" in stripped.lower() and ("drop" in stripped.lower() or "reject" in stripped.lower()),
                f"Unexpected ICMP drop/reject rule: {stripped!r}",
            )

    def test_absent_when_disabled(self):
        """When mss_clamp_enabled=False the table must not be rendered at all."""
        out = _render_mss(1240, mss_clamp_enabled=False)
        self.assertNotIn("maxseg size set", out)
        self.assertNotIn("table inet ipt_server_mss", out)

    def test_custom_mss_value_rendered(self):
        """The configured MSS value appears verbatim in both clamp rules."""
        out = _render_mss(1300)
        self.assertIn("maxseg size set 1300", out)
        self.assertNotIn("maxseg size set 1240", out)


class TestMySettingsMssClampField(unittest.TestCase):
    """Config.py: fixed_mss and mss_clamp_enabled fields."""

    def _make_minimal_env(self, **overrides):
        env = {
            "IPT_INTERFACES_JSON": '["backbone"]',
            "IPT_ROUTES_JSON": "[]",
            "IPT_CLEAN_CONNTRACK": "false",
            "IPT_DOMAIN_ROUTE_TTL": "300",
        }
        env.update(overrides)
        return env

    def test_fixed_mss_default_is_1240(self):
        """fixed_mss must default to 1240 (chain-B floor, >= QUIC 1200)."""
        from Config import MySettings

        settings = MySettings.model_validate(self._make_minimal_env())
        self.assertEqual(settings.fixed_mss, 1240)

    def test_mss_clamp_enabled_default_is_true(self):
        """mss_clamp_enabled must default to True."""
        from Config import MySettings

        settings = MySettings.model_validate(self._make_minimal_env())
        self.assertTrue(settings.mss_clamp_enabled)

    def test_mss_clamp_enabled_false_via_env(self):
        """mss_clamp_enabled can be set to False via IPT_MSS_CLAMP_ENABLED=false."""
        from Config import MySettings

        settings = MySettings.model_validate(
            self._make_minimal_env(IPT_MSS_CLAMP_ENABLED="false")
        )
        self.assertFalse(settings.mss_clamp_enabled)

    def test_fixed_mss_override_via_env(self):
        """fixed_mss can be overridden via IPT_FIXED_MSS."""
        from Config import MySettings

        settings = MySettings.model_validate(
            self._make_minimal_env(IPT_FIXED_MSS="1300")
        )
        self.assertEqual(settings.fixed_mss, 1300)

    def test_no_legacy_mss_clamp_value_field(self):
        """MySettings must NOT expose mss_clamp_value attribute."""
        from Config import MySettings

        settings = MySettings.model_validate(self._make_minimal_env())
        self.assertFalse(hasattr(settings, "mss_clamp_value"))

    def test_no_legacy_ipt_mss_clamp_value_env(self):
        """IPT_MSS_CLAMP_VALUE env var must be silently ignored (not mapped to any field)."""
        from Config import MySettings

        # Should not raise; extra="ignore" swallows unknown keys.
        settings = MySettings.model_validate(
            self._make_minimal_env(IPT_MSS_CLAMP_VALUE="999")
        )
        # And the new field must retain its default, not pick up the legacy value.
        self.assertEqual(settings.fixed_mss, 1240)


class TestRenderMssClampRulesRuntime(unittest.TestCase):
    """Tests for ipt_server.main.render_mss_clamp_rules() using state.CONFIG."""

    def _make_config(self, fixed_mss: int = 1240, mss_clamp_enabled: bool = True):
        from types import SimpleNamespace
        return SimpleNamespace(fixed_mss=fixed_mss, mss_clamp_enabled=mss_clamp_enabled)

    def test_enabled_renders_table_with_fixed_mss(self):
        """render_mss_clamp_rules returns nft table using fixed_mss when enabled."""
        from ipt_server import state
        import ipt_server.main as main_mod
        state.CONFIG = self._make_config(fixed_mss=1240, mss_clamp_enabled=True)
        try:
            out = main_mod.render_mss_clamp_rules()
        finally:
            state.CONFIG = None
        self.assertIn("table inet ipt_server_mss", out)
        self.assertIn("maxseg size set 1240", out)

    def test_disabled_returns_empty_string(self):
        """render_mss_clamp_rules returns empty string when mss_clamp_enabled=False."""
        from ipt_server import state
        import ipt_server.main as main_mod
        state.CONFIG = self._make_config(fixed_mss=1240, mss_clamp_enabled=False)
        try:
            out = main_mod.render_mss_clamp_rules()
        finally:
            state.CONFIG = None
        self.assertEqual(out.strip(), "")
        self.assertNotIn("table inet ipt_server_mss", out)

    def test_does_not_access_mss_clamp_value(self):
        """render_mss_clamp_rules must not raise AttributeError for mss_clamp_value."""
        from ipt_server import state
        import ipt_server.main as main_mod
        # Config with only the new fields — no mss_clamp_value attribute.
        state.CONFIG = self._make_config(fixed_mss=1300, mss_clamp_enabled=True)
        try:
            # If main.py still reads .mss_clamp_value this will raise AttributeError.
            out = main_mod.render_mss_clamp_rules()
        finally:
            state.CONFIG = None
        self.assertIn("maxseg size set 1300", out)


if __name__ == "__main__":
    unittest.main()
