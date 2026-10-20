"""Tests for the WebSocket echo hook (ipt_server.main.echo) and its budget.

These moved beside ipt_server.main: they test main.echo, not the DNS
backend monitor task (see S7 in the 2026-10-20 test audit).
"""

import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import patch


class _SingleMessageWebSocket:
    def __init__(self, message):
        self._message = message
        self._yielded = False
        self.sent = []

    def __aiter__(self):
        return self

    async def __anext__(self):
        if self._yielded:
            raise StopAsyncIteration
        self._yielded = True
        return self._message

    async def send(self, message):
        self.sent.append(message)


class TestWebsocketHook(unittest.IsolatedAsyncioTestCase):
    """echo() reads the apply budget from state.CONFIG.ws_route_apply_budget_seconds.

    Each test installs a SimpleNamespace stand-in so process_a_record_with_budget
    has the attribute it expects. Tests that exercise the budget timeout pick a
    value tight enough to trigger the cutoff; the rest leave a generous default.
    """

    def setUp(self):
        # Shared-state restore is enforced suite-wide (component conftest);
        # here we only install the stand-in for the duration of the test.
        from ipt_server import state

        state.CONFIG = SimpleNamespace(ws_route_apply_budget_seconds=1.0)

    async def test_echo_processes_a_record_and_replies_with_json(self):
        """A well-formed A-record message is routed through process_a_record and answered with its JSON TTL payload."""
        import ipt_server.main

        websocket = _SingleMessageWebSocket(
            '{"query":"probe.example.net.","name":"probe.example.net.","content":"192.0.2.68","type":1,"ttl":300}'
        )

        with patch("ipt_server.main.process_a_record") as mock_process:
            mock_process.return_value = {"ttl": 123}
            await ipt_server.main.echo(websocket)

        mock_process.assert_called_once_with(
            {
                "query": "probe.example.net.",
                "name": "probe.example.net.",
                "content": "192.0.2.68",
                "type": 1,
                "ttl": 300,
            }
        )
        self.assertEqual(websocket.sent, ['{"ttl": 123}'])

    async def test_echo_replies_with_error_on_invalid_json(self):
        """A non-JSON message is answered with an Invalid JSON error, not a crash."""
        import ipt_server.main

        websocket = _SingleMessageWebSocket("not-json")

        await ipt_server.main.echo(websocket)

        self.assertEqual(websocket.sent, ["Error: Invalid JSON"])

    async def test_echo_logs_websocket_connect_and_disconnect_at_info(self):
        """echo() emits INFO logs for connection establishment and closure.

        Validates: lifecycle events (connect + disconnect) are observable at INFO level
        so production operators can diagnose connection issues without DEBUG noise.
        Code: ipt_server.main::echo

        Assertion: at least one INFO log appears in the assertLogs context.

        Method:
        1. Arrange: _SingleMessageWebSocket (no real remote_address — echo uses getattr default)
        2. Patch process_a_record to return a minimal response
        3. Wrap echo() call in assertLogs(level='INFO')
        4. Assert connect or disconnect INFO log was emitted
        """
        import ipt_server.main

        websocket = _SingleMessageWebSocket(
            '{"query":"probe.example.net.","name":"probe.example.net.","content":"192.0.2.68","type":1,"ttl":300}'
        )

        with patch("ipt_server.main.process_a_record") as mock_process:
            mock_process.return_value = {"ttl": 100}
            with self.assertLogs("", level="INFO") as log_ctx:
                await ipt_server.main.echo(websocket)

        lifecycle_msgs = [
            msg
            for msg in log_ctx.output
            if "INFO" in msg
            and ("websocket" in msg.lower() or "connection" in msg.lower())
        ]
        self.assertTrue(
            lifecycle_msgs,
            f"Expected at least one INFO log about WebSocket lifecycle, got: {log_ctx.output}",
        )

    async def test_echo_does_not_block_event_loop_during_process_a_record(self):
        """echo() runs process_a_record in a worker thread so the event loop
        stays free: a second request completes while the first one's handler
        is still blocked.

        Method (gates, no timing):
        1. The first request's process_a_record blocks on a gate only the
           test driver opens.
        2. While it is provably blocked, a second request is served to
           completion.
        3. Only then is the gate released and the first reply awaited.
        If echo() ran the handler inline, the loop thread would be stuck in
        the gate and the hang guards would fail the test in bounded time.
        """
        import threading

        import ipt_server.main

        entered = threading.Event()
        gate = threading.Event()
        order = []

        def gated_process(msg):
            if msg.get("query", "").rstrip(".") == "alpha.example.net":
                entered.set()
                order.append("handler_entered")
                released = gate.wait(timeout=5.0)
                order.append("handler_released" if released else "handler_timed_out")
                return {"ttl": 11}
            return {"ttl": 22}

        ws1 = _SingleMessageWebSocket(
            '{"query":"alpha.example.net.","name":"alpha.example.net.","content":"192.0.2.1","type":1,"ttl":60}'
        )
        ws2 = _SingleMessageWebSocket(
            '{"query":"beta.example.net.","name":"beta.example.net.","content":"198.51.100.2","type":1,"ttl":60}'
        )

        from ipt_server import state

        # Generous budget: this test is about concurrency, not the cutoff.
        state.CONFIG.ws_route_apply_budget_seconds = 5.0

        with patch("ipt_server.main.process_a_record", side_effect=gated_process):
            first = asyncio.create_task(ipt_server.main.echo(ws1))
            try:
                # The first handler has provably started and is blocked.
                await asyncio.to_thread(entered.wait, 5.0)
                # The second request completes while the first is blocked.
                await asyncio.wait_for(ipt_server.main.echo(ws2), timeout=5.0)
                order.append("second_completed")
                self.assertEqual(ws2.sent, ['{"ttl": 22}'])
                self.assertEqual(ws1.sent, [], "first reply must still be pending")
            finally:
                gate.set()
            await asyncio.wait_for(first, timeout=5.0)

        self.assertEqual(order[:2], ["handler_entered", "second_completed"])
        self.assertEqual(ws1.sent, ['{"ttl": 11}'])
        self.assertNotIn("handler_timed_out", order)

    async def test_echo_returns_degraded_ttl_when_process_a_record_exceeds_budget(self):
        """A processing call exceeding ws_route_apply_budget_seconds yields the
        degraded TTL=1 answer.

        The handler blocks on a gate for 6x the budget (the fault the
        production budget bounds); the degraded reply arriving before the
        gate is released proves the budget fired while the handler was
        still blocked."""
        import threading

        import ipt_server.main

        websocket = _SingleMessageWebSocket(
            '{"query":"slow.example.net.","name":"slow.example.net.","content":"203.0.113.4","type":1,"ttl":300}'
        )
        entered = threading.Event()
        gate = threading.Event()

        def gated_process(_msg):
            entered.set()
            gate.wait(timeout=0.3)  # bounded fault: 6x the 0.05s budget
            return {"ttl": 300}

        from ipt_server import state

        state.CONFIG.ws_route_apply_budget_seconds = 0.05

        with patch("ipt_server.main.process_a_record", side_effect=gated_process):
            await asyncio.wait_for(ipt_server.main.echo(websocket), timeout=5.0)
            self.assertTrue(entered.is_set(), "handler must have started")
        gate.set()  # release any lingering worker before teardown

        self.assertEqual(websocket.sent, ['{"ttl": 1, "degraded": true}'])

    async def test_echo_returns_normal_ttl_when_process_a_record_finishes_within_budget(
        self,
    ):
        """A processing call within the budget passes the real TTL through."""
        """A processing call within the budget passes the real TTL through."""
        import ipt_server.main

        websocket = _SingleMessageWebSocket(
            '{"query":"fast.example.net.","name":"fast.example.net.","content":"203.0.113.8","type":1,"ttl":300}'
        )

        from ipt_server import state

        state.CONFIG.ws_route_apply_budget_seconds = 0.2

        with patch("ipt_server.main.process_a_record", return_value={"ttl": 123}):
            await asyncio.wait_for(ipt_server.main.echo(websocket), timeout=1)

        self.assertEqual(websocket.sent, ['{"ttl": 123}'])


if __name__ == "__main__":
    unittest.main()
