"""Contract tests for the dns_mark field on MySettings."""

import unittest
from Config import MySettings


class TestMySettingsDnsMarkField(unittest.TestCase):
    """Contract tests for the dns_mark field on MySettings."""

    def _make_minimal_env(self, **overrides):
        env = {
            "IPT_INTERFACES_JSON": '["backbone"]',
            "IPT_ROUTES_JSON": "[]",
            "IPT_CLEAN_CONNTRACK": "false",
            "IPT_DOMAIN_ROUTE_TTL": "300",
        }
        env.update(overrides)
        return env

    def test_dns_mark_default_is_0x201(self):
        """dns_mark defaults to 513 (0x201) when not overridden."""
        settings = MySettings.model_validate(self._make_minimal_env())
        self.assertEqual(settings.dns_mark, 0x201)

    def test_dns_mark_is_distinct_from_pbr_mark_default(self):
        """Defaults of dns_mark and pbr_mark must differ so ip rule does not collide."""
        settings = MySettings.model_validate(self._make_minimal_env())
        self.assertNotEqual(settings.dns_mark, settings.pbr_mark)

    def test_dns_mark_override(self):
        """dns_mark can be overridden via model_validate input dict."""
        env = self._make_minimal_env()
        env["dns_mark"] = 769  # 0x301
        settings = MySettings.model_validate(env)
        self.assertEqual(settings.dns_mark, 0x301)


if __name__ == "__main__":
    unittest.main()
