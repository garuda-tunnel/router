"""Tests for the ipt_server_mss forward-clamp table (Task 4: separate inet table)."""

import re
import unittest
from pathlib import Path
from types import SimpleNamespace

import jinja2

TEMPLATE_DIR = Path(__file__).parent.parent / "templates"
ICMP_DROP_RE = re.compile(r"icmp.*(drop|reject)|(drop|reject).*icmp", re.IGNORECASE)


def _render_mss(mss_clamp_value: int) -> str:
    env = jinja2.Environment(loader=jinja2.FileSystemLoader(str(TEMPLATE_DIR)))
    template = env.get_template("mss.nft.j2")
    config = SimpleNamespace(mss_clamp_value=mss_clamp_value)
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

    def test_absent_when_zero(self):
        """When mss_clamp_value=0 the table must not be rendered (disabled state)."""
        out = _render_mss(0)
        self.assertNotIn("maxseg size set", out)
        self.assertNotIn("table inet ipt_server_mss", out)

    def test_custom_mss_value_rendered(self):
        """The configured MSS value appears verbatim in both clamp rules."""
        out = _render_mss(1300)
        self.assertIn("maxseg size set 1300", out)
        self.assertNotIn("maxseg size set 1240", out)


class TestMySettingsMssClampField(unittest.TestCase):
    """Config.py: mss_clamp_value field presence and defaults."""

    def _make_minimal_env(self, **overrides):
        env = {
            "IPT_INTERFACES_JSON": '["backbone"]',
            "IPT_ROUTES_JSON": "[]",
            "IPT_CLEAN_CONNTRACK": "false",
            "IPT_DOMAIN_ROUTE_TTL": "300",
        }
        env.update(overrides)
        return env

    def test_mss_clamp_value_default_is_1240(self):
        """mss_clamp_value must default to 1240 (chain-B floor, >= QUIC 1200)."""
        from Config import MySettings

        settings = MySettings.model_validate(self._make_minimal_env())
        self.assertEqual(settings.mss_clamp_value, 1240)

    def test_mss_clamp_value_zero_disables(self):
        """mss_clamp_value=0 is a valid off-switch."""
        from Config import MySettings

        settings = MySettings.model_validate(
            self._make_minimal_env(IPT_MSS_CLAMP_VALUE="0")
        )
        self.assertEqual(settings.mss_clamp_value, 0)

    def test_mss_clamp_value_override(self):
        """mss_clamp_value can be overridden via env."""
        from Config import MySettings

        settings = MySettings.model_validate(
            self._make_minimal_env(IPT_MSS_CLAMP_VALUE="1300")
        )
        self.assertEqual(settings.mss_clamp_value, 1300)


if __name__ == "__main__":
    unittest.main()
