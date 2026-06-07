"""Tests for WebSocket hook (echo) in ipt_server.main."""

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
        from ipt_server import state

        self._saved_config = state.CONFIG
        state.CONFIG = SimpleNamespace(ws_route_apply_budget_seconds=1.0)

    def tearDown(self):
        from ipt_server import state

        state.CONFIG = self._saved_config

    async def test_echo_processes_a_record_and_replies_with_json(self):
        import ipt_server.main

        websocket = _SingleMessageWebSocket(
            '{"query":"ozon.ru.","name":"ozon.ru.","content":"185.73.193.68","type":1,"ttl":300}'
        )

        with patch("ipt_server.main.process_a_record") as mock_process:
            mock_process.return_value = {"ttl": 123}
            await ipt_server.main.echo(websocket)

        mock_process.assert_called_once_with(
            {
                "query": "ozon.ru.",
                "name": "ozon.ru.",
                "content": "185.73.193.68",
                "type": 1,
                "ttl": 300,
            }
        )
        self.assertEqual(websocket.sent, ['{"ttl": 123}'])

    async def test_echo_replies_with_error_on_invalid_json(self):
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
            '{"query":"ozon.ru.","name":"ozon.ru.","content":"185.73.193.68","type":1,"ttl":300}'
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
        """echo() runs process_a_record in a thread so the event loop stays free.

        Validates: asyncio.to_thread is used for process_a_record so that
        concurrent WebSocket connections are not serialised behind a slow
        blocking call.

        Code: ipt_server.main::echo

        Method:
        1. Make process_a_record sleep for a noticeable duration (simulating
           slow kernel work).
        2. Schedule two concurrent echo() calls.
        3. Assert the total elapsed wall-clock time is close to the single
           call duration (i.e. they ran concurrently, not serially).
        """
        import time
        import ipt_server.main

        SLOW_MS = 0.15  # 150 ms simulated blocking work per call

        def slow_process_a_record(_msg):
            time.sleep(SLOW_MS)
            return {"ttl": 99}

        ws1 = _SingleMessageWebSocket(
            '{"query":"a.com.","name":"a.com.","content":"1.1.1.1","type":1,"ttl":60}'
        )
        ws2 = _SingleMessageWebSocket(
            '{"query":"b.com.","name":"b.com.","content":"2.2.2.2","type":1,"ttl":60}'
        )

        from ipt_server import state

        # Budget must exceed SLOW_MS so the normal TTL is returned (not degraded).
        # This test validates concurrency, not the budget cutoff.
        state.CONFIG.ws_route_apply_budget_seconds = SLOW_MS * 3

        with patch(
            "ipt_server.main.process_a_record", side_effect=slow_process_a_record
        ):
            t0 = asyncio.get_event_loop().time()
            await asyncio.gather(
                ipt_server.main.echo(ws1),
                ipt_server.main.echo(ws2),
            )
            elapsed = asyncio.get_event_loop().time() - t0

        # If both calls ran concurrently (to_thread), total time ≈ SLOW_MS.
        # If they ran serially (blocking), total time ≈ 2 * SLOW_MS.
        self.assertLess(
            elapsed,
            SLOW_MS * 1.8,
            f"echo() blocked event loop: {elapsed:.3f}s >= {SLOW_MS * 1.8:.3f}s "
            f"(expected concurrent execution ~{SLOW_MS:.3f}s)",
        )
        self.assertEqual(ws1.sent, ['{"ttl": 99}'])
        self.assertEqual(ws2.sent, ['{"ttl": 99}'])

    async def test_echo_returns_degraded_ttl_when_process_a_record_exceeds_budget(self):
        import ipt_server.main

        websocket = _SingleMessageWebSocket(
            '{"query":"slow.example.","name":"slow.example.","content":"1.2.3.4","type":1,"ttl":300}'
        )

        def slow_process(_msg):
            import time

            time.sleep(0.2)
            return {"ttl": 300}

        from ipt_server import state

        state.CONFIG.ws_route_apply_budget_seconds = 0.05

        with patch("ipt_server.main.process_a_record", side_effect=slow_process):
            await asyncio.wait_for(ipt_server.main.echo(websocket), timeout=1)

        self.assertEqual(websocket.sent, ['{"ttl": 1, "degraded": true}'])

    async def test_echo_returns_normal_ttl_when_process_a_record_finishes_within_budget(
        self,
    ):
        import ipt_server.main

        websocket = _SingleMessageWebSocket(
            '{"query":"fast.example.","name":"fast.example.","content":"5.6.7.8","type":1,"ttl":300}'
        )

        from ipt_server import state

        state.CONFIG.ws_route_apply_budget_seconds = 0.2

        with patch("ipt_server.main.process_a_record", return_value={"ttl": 123}):
            await asyncio.wait_for(ipt_server.main.echo(websocket), timeout=1)

        self.assertEqual(websocket.sent, ['{"ttl": 123}'])


if __name__ == "__main__":
    unittest.main()
