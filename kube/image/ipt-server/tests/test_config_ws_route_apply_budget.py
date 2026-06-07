"""Contract tests for the ws_route_apply_budget_seconds field on MySettings.

Background: ipt_server bounds the websocket DNS A-record handling latency so
that a slow PBR install (subprocess `ip -batch` on cold netlink) cannot
indefinitely block the PowerDNS Lua hook that is waiting on `ws:receive`.
The previous implementation hardcoded the budget at 0.1s. Production
telemetry showed p99 add_s = 130ms which exceeded the 100ms budget (~1.86%
of domain-matched A-records degraded), so the budget moved into MySettings
as an internal tunable with a sensible default. It is intentionally NOT
exposed through the Ansible role or Terraform module; operators who need to
override it can either patch Config.py or rely on the Pydantic env-prefix
mechanism.

The Lua hook waits at most 0.25s in ws:receive (see powerdns/etc/hook.lua);
raising the Python budget above the Lua receive
timeout is pointless because Lua will give up first. The default is set to
0.2s to leave ~50ms headroom for ws round-trip and JSON serialization.
"""

import unittest

from Config import MySettings


class TestMySettingsWsRouteApplyBudgetField(unittest.TestCase):
    """Contract tests for the ws_route_apply_budget_seconds field on MySettings."""

    def _make_minimal_env(self, **overrides):
        env = {
            "IPT_INTERFACES_JSON": '["backbone"]',
            "IPT_ROUTES_JSON": "[]",
            "IPT_CLEAN_CONNTRACK": "false",
            "IPT_DOMAIN_ROUTE_TTL": "300",
        }
        env.update(overrides)
        return env

    def test_default_is_two_hundred_milliseconds(self):
        """Default budget is 0.2s (200ms) per evidence-based recommendation.

        Telemetry showed:
          - p50 add_s = 3ms
          - p95 add_s = 6ms
          - p99 add_s = 130ms (exceeds previous 100ms budget)
          - p99.9 add_s = 357ms

        Setting the default to 200ms covers ~p99.5 of observed latency while
        staying below the Lua receive timeout (0.25s).
        """
        settings = MySettings.model_validate(self._make_minimal_env())
        self.assertEqual(settings.ws_route_apply_budget_seconds, 0.2)

    def test_must_be_positive(self):
        """A non-positive budget would deadlock or skip route application entirely."""
        env = self._make_minimal_env()
        env["ws_route_apply_budget_seconds"] = 0.0
        with self.assertRaises(Exception):
            MySettings.model_validate(env)


if __name__ == "__main__":
    unittest.main()
