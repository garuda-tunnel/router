"""Interface monitor behavior: snapshot publication and tick reconciliation (T5/T6).

Drives real monitor_interfaces loops against scripted kernel-link frames
(fake IPRoute boundary). Every scenario ends deterministically: either the
fake boundary recorded the expected effect (replay/PBR call) or a scripted
poll count is reached — no polling deadlines, no sleeps.
"""

import asyncio
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from ipt_server import state
from ipt_server.tasks import interface_monitor


def _link(ifindex, name, operstate="up"):
    """Build a pyroute2-shaped link (dict subclass with get_attr)."""

    class _Link(dict):
        def __init__(self):
            super().__init__(index=ifindex)
            self._attrs = {"IFLA_IFNAME": name, "IFLA_OPERSTATE": operstate}

        def get_attr(self, key):
            return self._attrs.get(key)

    return _Link()


def _unnamed_link(ifindex):
    class _Link(dict):
        def __init__(self):
            super().__init__(index=ifindex)
            self._attrs = {}

        def get_attr(self, key):
            return self._attrs.get(key)

    return _Link()


def run_monitor(
    frames,
    cfg,
    *,
    health=None,
    router="default",
    stop_after_polls=None,
    stop_when=None,
):
    """Run one monitor_interfaces scenario and return its observations.

    frames: list of link frames; poll N serves frame min(N, len-1).
    router: recording fake by default; None exercises the ROUTER-absent path.
    stop_when: predicate over the recorded effects that ends the run
        (condition-wait, not a deadline race).
    stop_after_polls: ends the run after N polls (for no-effect scenarios).
    """
    import time as _time

    observations = {"effects": [], "polls": 0}
    effects_cond = threading.Condition()
    polls_cond = threading.Condition()

    def record_effect(name):
        with effects_cond:
            observations["effects"].append(name)
            effects_cond.notify_all()

    def record_poll():
        with polls_cond:
            observations["polls"] += 1
            polls_cond.notify_all()

    loop_ref = [None]

    def make_iproute():
        class _FakeIPRoute:
            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

            def get_links(self):
                record_poll()
                index = observations["polls"] - 1
                return list(frames[min(index, len(frames) - 1)])

        return _FakeIPRoute

    if router is None:
        fake_router = None
    else:

        def full_replay():
            record_effect("full_replay")

        def iface_replay(iface):
            record_effect(f"iface_replay:{iface}")

        fake_router = SimpleNamespace(
            replay_routes=full_replay,
            replay_routes_for_interface=iface_replay,
        )

    def default_apply_pbr():
        record_effect("pbr_reapply")

    def wait_effects(predicate, timeout=5.0):
        deadline = _time.monotonic() + timeout
        with effects_cond:
            while not predicate(observations["effects"]):
                remaining = deadline - _time.monotonic()
                if remaining <= 0:
                    return False
                effects_cond.wait(remaining)
        return True

    def wait_polls(count, timeout=5.0):
        deadline = _time.monotonic() + timeout
        with polls_cond:
            while observations["polls"] < count:
                remaining = deadline - _time.monotonic()
                if remaining <= 0:
                    return False
                polls_cond.wait(remaining)
        return True

    async def scenario():
        loop_ref[0] = asyncio.get_running_loop()
        stop = asyncio.Event()

        state.CONFIG = cfg
        state.INTERFACE_HEALTH = dict(health or {})
        state.ROUTER = fake_router

        with (
            patch.object(interface_monitor, "IPRoute", make_iproute()),
            patch.object(interface_monitor, "_INTERVAL_SECONDS", 0.001),
            patch("ipt_server.main.apply_pbr", side_effect=default_apply_pbr),
        ):
            task = asyncio.create_task(interface_monitor.monitor_interfaces(stop))
            if stop_when is not None:
                reached = await asyncio.to_thread(wait_effects, stop_when)
            else:
                reached = await asyncio.to_thread(wait_polls, stop_after_polls)
            stop.set()
            await asyncio.wait_for(task, timeout=5.0)
            observations["stop_condition_reached"] = reached

    asyncio.run(scenario())
    return observations


class TestInterfaceMonitorTicks(unittest.TestCase):
    def _cfg(self, interfaces=("backbone",), route_devs=()):
        routes = [
            SimpleNamespace(route=[SimpleNamespace(dev=dev)]) for dev in route_devs
        ]
        return SimpleNamespace(interfaces=list(interfaces), routes=routes)

    def test_pbr_interface_appearance_reapplies_pbr_and_replays_all(self):
        """A PBR interface appearing triggers PBR reapply and a full route
        replay; the snapshot is published to shared state."""
        observations = run_monitor(
            [[_link(5, "backbone")]],
            self._cfg(),
            stop_when=lambda effects: "full_replay" in effects,
        )
        self.assertTrue(observations["stop_condition_reached"])
        self.assertIn("pbr_reapply", observations["effects"])
        self.assertIn("full_replay", observations["effects"])
        self.assertEqual(state.INTERFACES, {"backbone": 5})

    def test_pbr_interface_state_change_reapplies_pbr(self):
        """An operstate change on a PBR interface triggers another PBR
        reapply (and the post-flush full replay)."""
        frames = [
            [_link(5, "backbone", "up")],
            [_link(5, "backbone", "down")],
        ]
        observations = run_monitor(
            frames,
            self._cfg(),
            stop_when=lambda effects: effects.count("pbr_reapply") >= 2,
        )
        self.assertTrue(observations["stop_condition_reached"])
        self.assertGreaterEqual(observations["effects"].count("pbr_reapply"), 2)

    def test_route_only_interface_appearance_replays_routes(self):
        """A route-only (non-PBR) interface appearing triggers a route replay
        but never a PBR reapply."""
        observations = run_monitor(
            [[_link(7, "wg_out")]],
            self._cfg(interfaces=(), route_devs=("wg_out",)),
            stop_when=lambda effects: "full_replay" in effects,
        )
        self.assertTrue(observations["stop_condition_reached"])
        self.assertIn("full_replay", observations["effects"])
        self.assertNotIn("pbr_reapply", observations["effects"])

    def test_unhealthy_gate_limits_replay_to_healthy_interfaces(self):
        """With an unhealthy gated interface, replay is per-interface and
        skips the unhealthy one."""
        observations = run_monitor(
            [[_link(7, "wg_out"), _link(8, "wg_bad")]],
            # No PBR interface changes: a route-only change takes the
            # replay_healthy_routes path where the unhealthy gate applies.
            self._cfg(interfaces=(), route_devs=("wg_out", "wg_bad")),
            health={"wg_bad": False},
            stop_when=lambda effects: "iface_replay:wg_out" in effects,
        )
        self.assertTrue(observations["stop_condition_reached"])
        self.assertEqual(observations["effects"], ["iface_replay:wg_out"])

    def test_stable_state_across_ticks_triggers_nothing_new(self):
        """After the first appearance, an identical second tick performs no
        further replay or PBR reapply."""
        observations = run_monitor(
            [[_link(5, "backbone")], [_link(5, "backbone")]],
            self._cfg(),
            stop_after_polls=2,
        )
        self.assertEqual(observations["effects"], ["pbr_reapply", "full_replay"])

    def test_disappeared_interface_is_removed_from_snapshot(self):
        """A disappeared link is removed from the published snapshot."""
        observations = run_monitor(
            [[_link(5, "backbone")], []],
            self._cfg(),
            stop_after_polls=2,
        )
        self.assertEqual(state.INTERFACES, {})

    def test_missing_router_does_not_call_replays(self):
        """state.ROUTER absent (early startup): ticks complete and no replay
        is attempted."""
        observations = run_monitor(
            [[_link(7, "wg_out")]],
            self._cfg(interfaces=(), route_devs=("wg_out",)),
            router=None,
            stop_after_polls=3,
        )
        self.assertEqual(observations["effects"], [])
        self.assertEqual(observations["polls"], 3)

    def test_unnamed_links_are_ignored_in_snapshot(self):
        """Links without IFLA_IFNAME never enter the published snapshot."""
        observations = run_monitor(
            [[_link(5, "backbone"), _unnamed_link(9)]],
            self._cfg(),
            stop_when=lambda effects: "full_replay" in effects,
        )
        self.assertTrue(observations["stop_condition_reached"])
        self.assertEqual(state.INTERFACES, {"backbone": 5})


class TestDevInterfaceExtraction(unittest.TestCase):
    def test_grouped_and_legacy_routes_collect_devs(self):
        """Grouped (list) and legacy (object) route forms both contribute
        their dev names; duplicates collapse."""
        routes = [
            SimpleNamespace(route=[SimpleNamespace(dev="wg_out")]),
            SimpleNamespace(route=[SimpleNamespace(dev="wg_out")]),
            SimpleNamespace(route=SimpleNamespace(dev="legacy0")),
        ]
        devs = interface_monitor._dev_interfaces_from_routes(routes)
        self.assertEqual(devs, {"wg_out", "legacy0"})

    def test_routes_without_dev_contribute_nothing(self):
        """Entries without a usable route/dev contribute no interfaces."""
        routes = [
            SimpleNamespace(route=None),
            SimpleNamespace(route=[SimpleNamespace(dev=None)]),
            object(),
        ]
        self.assertEqual(interface_monitor._dev_interfaces_from_routes(routes), set())


class TestSnapshotHelpers(unittest.TestCase):
    def test_publish_snapshot_replaces_content(self):
        """_publish_snapshot replaces the previous content under the lock."""
        state.INTERFACES.update({"old0": 1})
        interface_monitor._publish_snapshot({"new0": 5})
        self.assertEqual(dict(state.INTERFACES), {"new0": 5})

    def test_refresh_interfaces_snapshot_publishes_poll_result(self):
        """refresh_interfaces_snapshot awaits a worker-thread poll and
        publishes the result."""
        frames = [[_link(2, "wg0")]]

        def make_iproute():
            class _FakeIPRoute:
                def __enter__(self):
                    return self

                def __exit__(self, *exc):
                    return False

                def get_links(self):
                    return list(frames[0])

            return _FakeIPRoute

        async def run():
            with patch.object(interface_monitor, "IPRoute", make_iproute()):
                await interface_monitor.refresh_interfaces_snapshot()

        asyncio.run(run())
        self.assertEqual(dict(state.INTERFACES), {"wg0": 2})


if __name__ == "__main__":
    unittest.main()
