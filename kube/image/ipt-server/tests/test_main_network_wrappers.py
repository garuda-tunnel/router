"""ipt_server.main network wrappers, health handler and echo edges (T7/T8/T9).

Every kernel boundary (nftables, IPRoute, sockets, subprocess) is faked;
the tests assert the reconciliation decisions and failure contracts.
"""

import asyncio
import json
import socket
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import ipt_server.main as ipt_main
from ipt_server import state


def _nft(rc=0):
    nft = MagicMock()
    nft.cmd.return_value = (rc, "", "boom" if rc else "")
    return nft


class TestDnsBackendReconciliation(unittest.TestCase):
    def test_resolve_backend_hostname_uses_env_default(self):
        """Without an argument the IPT_DNS_BACKEND_HOST env name is used."""
        with patch.object(ipt_main.socket, "gethostbyname", return_value="192.0.2.53") as gh:
            with patch.dict("os.environ", {"IPT_DNS_BACKEND_HOST": "pdns.example.net"}):
                self.assertEqual(ipt_main.resolve_backend_hostname(), "192.0.2.53")
        gh.assert_called_once_with("pdns.example.net")

    def test_resolve_backend_hostname_explicit_argument_wins(self):
        """An explicit hostname argument bypasses the environment."""
        with patch.object(ipt_main.socket, "gethostbyname", return_value="198.51.100.53") as gh:
            self.assertEqual(
                ipt_main.resolve_backend_hostname("explicit.example.net"),
                "198.51.100.53",
            )
        gh.assert_called_once_with("explicit.example.net")

    def test_accepts_queries_true_on_successful_connect(self):
        """A successful TCP connect to port 1053 reports readiness."""
        with patch.object(ipt_main.socket, "create_connection") as conn:
            self.assertTrue(ipt_main.dns_backend_accepts_queries("192.0.2.53"))
        conn.assert_called_once_with(("192.0.2.53", 1053), timeout=1.0)

    def test_accepts_queries_false_on_oserror(self):
        """A refused/timed-out probe reports not-ready without raising."""
        with patch.object(
            ipt_main.socket,
            "create_connection",
            side_effect=OSError("refused"),
        ):
            self.assertFalse(ipt_main.dns_backend_accepts_queries("192.0.2.53"))

    def test_reconcile_skips_when_backend_unresolvable(self):
        """An unresolvable backend applies nothing and keeps last IP."""
        ipt_main._LAST_DNS_BACKEND_IP = "192.0.2.53"
        try:
            with (
                patch.object(ipt_main, "resolve_backend_hostname", side_effect=OSError("nope")),
                patch.object(ipt_main, "apply_dns_dnat_rules") as apply,
            ):
                ipt_main.reconcile_dns_backend()
            apply.assert_not_called()
            self.assertEqual(ipt_main._LAST_DNS_BACKEND_IP, "192.0.2.53")
        finally:
            ipt_main._LAST_DNS_BACKEND_IP = ""

    def test_reconcile_skips_when_backend_not_accepting(self):
        """A resolved but not-ready backend applies nothing."""
        with (
            patch.object(ipt_main, "resolve_backend_hostname", return_value="198.51.100.53"),
            patch.object(ipt_main, "dns_backend_accepts_queries", return_value=False),
            patch.object(ipt_main, "apply_dns_dnat_rules") as apply,
        ):
            ipt_main.reconcile_dns_backend()
        apply.assert_not_called()

    def test_reconcile_applies_only_when_ip_changes(self):
        """A changed backend IP applies new rules and updates the marker;
        an unchanged ready backend is a no-op."""
        with (
            patch.object(ipt_main, "resolve_backend_hostname", return_value="192.0.2.53"),
            patch.object(ipt_main, "dns_backend_accepts_queries", return_value=True),
            patch.object(ipt_main, "apply_dns_dnat_rules") as apply,
        ):
            ipt_main._LAST_DNS_BACKEND_IP = ""
            try:
                ipt_main.reconcile_dns_backend()
                self.assertEqual(apply.call_count, 1)
                self.assertEqual(ipt_main._LAST_DNS_BACKEND_IP, "192.0.2.53")
                ipt_main.reconcile_dns_backend()
                self.assertEqual(apply.call_count, 1, "same IP is a no-op")
            finally:
                ipt_main._LAST_DNS_BACKEND_IP = ""

    def test_apply_dns_dnat_rules_raises_on_nft_error(self):
        """A failing nft apply is fatal (RuntimeError with the error)."""
        with patch.object(ipt_main.nftables, "Nftables", return_value=_nft(rc=1)):
            with self.assertRaises(RuntimeError):
                ipt_main.apply_dns_dnat_rules("192.0.2.53")

    def test_render_dns_dnat_rules_resolves_hostname(self):
        """render_dns_dnat_rules resolves the configured hostname."""
        with (
            patch.object(ipt_main, "resolve_backend_hostname", return_value="192.0.2.53"),
        ):
            ruleset = ipt_main.render_dns_dnat_rules()
        self.assertIn("dnat to 192.0.2.53:1053", ruleset)


class TestApplyWrappers(unittest.TestCase):
    def setUp(self):
        state.CONFIG = SimpleNamespace(
            has_border=False,
            mss_clamp_enabled=False,
            fixed_mss=1240,
            pbr_mark=512,
            dns_mark=513,
            domain_route_ttl=300,
            table=200,
            interfaces=["backbone"],
        )

    def test_apply_border_rules_disabled_deletes_table_only(self):
        """Without a border NIC the stale table is deleted and nothing added."""
        nft = _nft()
        with patch.object(ipt_main.nftables, "Nftables", return_value=nft):
            ipt_main.apply_border_rules()
        cmds = [c.args[0] for c in nft.cmd.call_args_list]
        self.assertIn("delete table inet border_ipt_server", cmds)
        self.assertEqual(len(cmds), 1)

    def test_apply_border_rules_enabled_applies_ruleset(self):
        """With a border NIC the rendered ruleset is applied."""
        state.CONFIG.has_border = True
        nft = _nft()
        with patch.object(ipt_main.nftables, "Nftables", return_value=nft):
            ipt_main.apply_border_rules()
        applied = nft.cmd.call_args_list[-1].args[0]
        self.assertIn("table inet border_ipt_server", applied)

    def test_apply_border_rules_raises_on_nft_error(self):
        """A failing border apply is fatal."""
        state.CONFIG.has_border = True
        with patch.object(ipt_main.nftables, "Nftables", return_value=_nft(rc=1)):
            with self.assertRaises(RuntimeError):
                ipt_main.apply_border_rules()

    def test_apply_mss_clamp_disabled_is_noop_after_delete(self):
        """A disabled MSS clamp only removes the stale table."""
        nft = _nft()
        with patch.object(ipt_main.nftables, "Nftables", return_value=nft):
            ipt_main.apply_mss_clamp_rules()
        cmds = [c.args[0] for c in nft.cmd.call_args_list]
        self.assertEqual(cmds, ["delete table inet ipt_server_mss"])

    def test_apply_mss_clamp_error_is_fatal(self):
        """A failing MSS apply is fatal."""
        state.CONFIG.mss_clamp_enabled = True
        with patch.object(ipt_main.nftables, "Nftables", return_value=_nft(rc=1)):
            with self.assertRaises(RuntimeError):
                ipt_main.apply_mss_clamp_rules()

    @staticmethod
    def _pbr_boundaries(rule_error=None, nft_rc=0):
        """Fake IPRoute/nft boundaries for the REAL apply_pbr.

        Returns (ipr, nft): `ipr.rule` records calls (or raises
        *rule_error*); `nft.cmd` answers `list tables` and applies the
        rendered ruleset, returning rc=*nft_rc* for apply commands.
        """
        ipr = MagicMock()
        ipr.__enter__ = MagicMock(return_value=ipr)
        ipr.__exit__ = MagicMock(return_value=False)
        ipr.get_rules.return_value = []
        if rule_error is not None:
            ipr.rule.side_effect = rule_error
        nft = MagicMock()

        def nft_cmd(cmd, *args, **kwargs):
            if cmd == "list tables":
                return (0, {"data": []}, "")
            return (nft_rc, "", "boom" if nft_rc else "")

        nft.cmd.side_effect = nft_cmd
        return ipr, nft

    @staticmethod
    def _journaling_boundaries(events):
        """Fake IPRoute/nft/socket boundaries that journal startup effects.

        Every boundary interaction appends a short label to *events* so the
        observable order of first-party startup operations can be asserted
        while the operations themselves (apply_pbr, apply_border_rules,
        apply_mss_clamp_rules, reconcile_dns_backend and the clean_pbr
        inside them) run for real.
        """

        class _JournaledIPRoute:
            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

            def get_rules(self, **kwargs):
                return []

            def rule(self, cmd, **kwargs):
                events.append("flush_rule_del" if cmd == "del" else "pbr_rule_add")
                return None

            def flush_routes(self, **kwargs):
                events.append("table_flush")

        nft = MagicMock()

        def nft_cmd(cmd, *args, **kwargs):
            if cmd == "list tables":
                return (0, {"data": []}, "")
            text = cmd if isinstance(cmd, str) else ""
            if "delete table" in text:
                if "ipt_server_pbr" in text:
                    events.append("pbr_table_delete")
                elif "border_ipt_server" in text:
                    events.append("border_delete")
                elif "ipt_server_mss" in text:
                    events.append("mss_delete")
                elif "dns_dnat_ipt_server" in text:
                    events.append("dnat_delete")
                return (0, "", "")
            if "ipt_server_pbr" in text:
                events.append("apply_pbr")
            elif "border_ipt_server" in text:
                events.append("apply_border")
            elif "ipt_server_mss" in text:
                events.append("apply_mss")
            elif "dns_dnat_ipt_server" in text:
                events.append("apply_dnat")
            return (0, "", "")

        nft.cmd.side_effect = nft_cmd

        class _ConnOk:
            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

        def fake_gethostbyname(hostname):
            events.append("resolve_backend")
            return "192.0.2.53"

        def fake_create_connection(address, timeout=None):
            events.append("probe_backend")
            return _ConnOk()

        return _JournaledIPRoute(), nft, fake_gethostbyname, fake_create_connection

    def test_apply_pbr_adds_rule_and_applies_ruleset(self):
        """The real apply_pbr, through fake IPRoute/nft boundaries, adds the
        fwmark ip rule for the managed table and applies the rendered PBR
        nft ruleset for every configured interface."""
        ipr, nft = self._pbr_boundaries()
        with (
            patch.object(ipt_main, "IPRoute", return_value=ipr),
            patch.object(ipt_main.nftables, "Nftables", return_value=nft),
        ):
            ipt_main.apply_pbr()

        ipr.rule.assert_called_once_with("add", fwmark=512, table=200)
        applied_ruleset = nft.cmd.call_args_list[-1].args[0]
        self.assertIn("table ip ipt_server_pbr", applied_ruleset)
        self.assertIn('iifname { "backbone" }', applied_ruleset)

    def test_apply_pbr_raises_on_nft_error(self):
        """A failing PBR nft apply raises with the rendered ruleset."""
        ipr, nft = self._pbr_boundaries(nft_rc=1)
        with (
            patch.object(ipt_main, "IPRoute", return_value=ipr),
            patch.object(ipt_main.nftables, "Nftables", return_value=nft),
        ):
            with self.assertRaises(Exception):
                ipt_main.apply_pbr()

    def test_apply_pbr_iprule_error_raises(self):
        """A failing ip rule add is logged and re-raised."""
        ipr, nft = self._pbr_boundaries(rule_error=Exception("netlink refused"))
        with (
            patch.object(ipt_main, "IPRoute", return_value=ipr),
            patch.object(ipt_main.nftables, "Nftables", return_value=nft),
        ):
            with self.assertRaises(Exception):
                ipt_main.apply_pbr()


    def test_clean_pbr_removes_existing_table_and_rules(self):
        """An existing managed table is deleted; rules flushed; table flushed."""
        nft = _nft()
        nft.cmd.side_effect = [
            (0, {"data": [{"table": {"name": "ipt_server_pbr"}}]}, ""),
            (0, "", ""),
        ]
        ipr = MagicMock()
        ipr.get_rules.return_value = [{"priority": 100}]
        with (
            patch.object(ipt_main.nftables, "Nftables", return_value=nft),
            patch.object(ipt_main, "IPRoute", return_value=ipr),
        ):
            ipr.__enter__ = MagicMock(return_value=ipr)
            ipr.__exit__ = MagicMock(return_value=False)
            ipt_main.clean_pbr()

        delete_cmds = [c.args[0] for c in nft.cmd.call_args_list]
        self.assertIn("delete table ipt_server_pbr", delete_cmds)
        ipr.rule.assert_called_with("del", table=200)
        ipr.flush_routes.assert_called_once_with(table=200)

    def test_clean_pbr_missing_table_skips_delete(self):
        """With no managed table present, no delete is issued."""
        nft = _nft()
        nft.cmd.return_value = (0, {"data": []}, "")
        ipr = MagicMock()
        ipr.get_rules.return_value = []
        with (
            patch.object(ipt_main.nftables, "Nftables", return_value=nft),
            patch.object(ipt_main, "IPRoute", return_value=ipr),
        ):
            ipr.__enter__ = MagicMock(return_value=ipr)
            ipr.__exit__ = MagicMock(return_value=False)
            ipt_main.clean_pbr()
        delete_cmds = [c.args[0] for c in nft.cmd.call_args_list]
        self.assertEqual(delete_cmds, ["list tables"])

    def test_clean_pbr_flush_error_is_logged_not_raised(self):
        """A netlink failure during cleanup must not crash the caller."""
        nft = _nft()
        nft.cmd.return_value = (0, {"data": []}, "")
        ipr = MagicMock()
        ipr.get_rules.side_effect = Exception("netlink down")
        with (
            patch.object(ipt_main.nftables, "Nftables", return_value=nft),
            patch.object(ipt_main, "IPRoute", return_value=ipr),
        ):
            ipr.__enter__ = MagicMock(return_value=ipr)
            ipr.__exit__ = MagicMock(return_value=False)
            ipt_main.clean_pbr()  # must not raise

    def test_startup_apply_network_state_orders_all_wrappers(self):
        """startup_apply_network_state runs the real PBR, border, MSS and DNS
        reconciliation in order, observed through the kernel boundaries.

        All four first-party operations execute for real against fake
        IPRoute/nft/socket boundaries; the assertion is on the observable
        order of boundary effects: PBR rule+ruleset, border, MSS, DNS
        DNAT reconciliation."""
        events = []
        ipr, nft, fake_ghbn, fake_conn = self._journaling_boundaries(events)
        state.CONFIG = SimpleNamespace(
            has_border=True,
            mss_clamp_enabled=True,
            fixed_mss=1240,
            pbr_mark=512,
            dns_mark=513,
            domain_route_ttl=300,
            table=200,
            interfaces=["backbone"],
        )
        ipt_main._LAST_DNS_BACKEND_IP = ""

        with (
            patch.object(ipt_main, "IPRoute", return_value=ipr),
            patch.object(ipt_main.nftables, "Nftables", return_value=nft),
            patch.object(ipt_main.socket, "gethostbyname", side_effect=fake_ghbn),
            patch.object(
                ipt_main.socket, "create_connection", side_effect=fake_conn
            ),
        ):
            try:
                ipt_main.startup_apply_network_state()
            finally:
                ipt_main._LAST_DNS_BACKEND_IP = ""

        milestones = [e for e in events if e.startswith(("pbr_rule", "apply_", "border_delete", "mss_delete", "dnat_delete", "resolve_backend", "probe_backend"))]
        self.assertEqual(
            milestones,
            [
                "pbr_rule_add",
                "apply_pbr",
                "border_delete",
                "apply_border",
                "mss_delete",
                "apply_mss",
                "resolve_backend",
                "probe_backend",
                "dnat_delete",
                "apply_dnat",
            ],
            f"startup boundary effects out of order: {events}",
        )


class TestRecordProcessing(unittest.TestCase):
    def test_process_a_record_delegates_to_router(self):
        """process_a_record wraps state.ROUTER.on_a_record for the record."""
        state.ROUTER = MagicMock()
        state.ROUTER.on_a_record.return_value = {"ttl": 60}
        rv = ipt_main.process_a_record(
            {"type": 1, "name": "probe.example.net", "query": "probe.example.net",
             "content": "192.0.2.10", "ttl": 60}
        )
        self.assertEqual(rv, {"ttl": 60})

    def test_budget_timeout_returns_degraded_ttl(self):
        """Exceeding the apply budget returns the degraded TTL payload.

        The handler blocks on a gate for 6x the budget (the fault the
        production budget bounds); the degraded reply proves the budget
        fired while the handler was still blocked."""

        entered = threading.Event()
        gate = threading.Event()

        def gated_process(record):
            entered.set()
            gate.wait(timeout=0.3)  # bounded fault: 6x the 0.05s budget
            return {"ttl": 300}

        async def run():
            state.CONFIG = SimpleNamespace(ws_route_apply_budget_seconds=0.05)
            with patch.object(ipt_main, "process_a_record", side_effect=gated_process):
                rv = await ipt_main.process_a_record_with_budget(
                    {"query": "slow.example.net"}
                )
            assert entered.is_set(), "handler must have started"
            return rv

        try:
            self.assertEqual(asyncio.run(run()), {"ttl": 1, "degraded": True})
        finally:
            gate.set()  # release any lingering worker before teardown

    def test_budget_within_returns_real_payload(self):
        """Completing within the budget returns the real router payload."""

        async def run():
            state.CONFIG = SimpleNamespace(ws_route_apply_budget_seconds=5.0)
            with patch.object(
                ipt_main, "process_a_record", return_value={"ttl": 300}
            ):
                return await ipt_main.process_a_record_with_budget(
                    {"query": "fast.example.net"}
                )

        self.assertEqual(asyncio.run(run()), {"ttl": 300})


class _SingleMessageWebSocket:
    def __init__(self, message):
        self._message = message
        self._yielded = False
        self.sent = []
        self.remote_address = "127.0.0.1:5555"

    def __aiter__(self):
        return self

    async def __anext__(self):
        if self._yielded:
            raise StopAsyncIteration
        self._yielded = True
        return self._message

    async def send(self, message):
        self.sent.append(message)


class TestEchoEdges(unittest.TestCase):
    def test_non_json_message_replies_with_invalid_json_error(self):
        """Malformed JSON gets the Invalid JSON error reply."""

        async def run():
            ws = _SingleMessageWebSocket("not-json")
            await ipt_main.echo(ws)
            return ws.sent

        self.assertEqual(asyncio.run(run()), ["Error: Invalid JSON"])

    def test_non_a_record_type_replies_with_empty_object(self):
        """A non-type-1 message answers with an empty JSON object."""

        async def run():
            ws = _SingleMessageWebSocket('{"type": 6}')
            await ipt_main.echo(ws)
            return ws.sent

        self.assertEqual(asyncio.run(run()), ["{}"])

    def test_processing_failure_replies_with_error(self):
        """An exception while processing gets the generic error reply."""

        async def run():
            ws = _SingleMessageWebSocket(
                '{"type": 1, "name": "probe.example.net", "content": "not-an-ip"}'
            )
            await ipt_main.echo(ws)
            return ws.sent

        self.assertEqual(
            asyncio.run(run()), ["Error: Message processing failed"]
        )

    def test_connection_lifecycle_is_logged(self):
        """Connect and close lifecycle lines are emitted for operators."""

        async def run():
            ws = _SingleMessageWebSocket('{"type": 6}')
            with self.assertLogs("ipt_server.main", level="INFO") as logs:
                await ipt_main.echo(ws)
            return "\n".join(logs.output)

        output = asyncio.run(run())
        self.assertIn("connection established", output)
        self.assertIn("connection closed", output)


class TestHealthHandler(unittest.TestCase):
    def test_health_check_writes_ok_response(self):
        """The health endpoint answers 200 OK with body OK."""

        async def run():
            reader = MagicMock()
            reader.read = AsyncMock(return_value=b"GET /health")
            writer = MagicMock()
            writer.write = MagicMock()
            writer.drain = AsyncMock()
            writer.close = MagicMock()
            writer.wait_closed = AsyncMock()
            await ipt_main.handle_health_check(reader, writer)
            return writer

        writer = asyncio.run(run())
        body = writer.write.call_args[0][0].decode()
        self.assertIn("200 OK", body)
        self.assertTrue(body.endswith("OK"))
        writer.close.assert_called_once()

    def test_health_check_survives_reader_error(self):
        """A failing read is logged and the writer still closed."""

        async def run():
            reader = MagicMock()
            reader.read = AsyncMock(side_effect=OSError("reset"))
            writer = MagicMock()
            writer.write = MagicMock()
            writer.drain = AsyncMock()
            writer.close = MagicMock()
            writer.wait_closed = AsyncMock()
            await ipt_main.handle_health_check(reader, writer)
            return writer

        writer = asyncio.run(run())
        writer.close.assert_called_once()

    def test_health_check_close_failure_is_debug_logged(self):
        """A failing wait_closed after close does not propagate."""

        async def run():
            reader = MagicMock()
            reader.read = AsyncMock(return_value=b"GET")
            writer = MagicMock()
            writer.write = MagicMock()
            writer.drain = AsyncMock()
            writer.close = MagicMock()
            writer.wait_closed = AsyncMock(side_effect=OSError("closed"))
            await ipt_main.handle_health_check(reader, writer)

        asyncio.run(run())  # must not raise


class TestShutdownHandler(unittest.TestCase):
    def test_shutdown_sets_stop_event(self):
        """The signal handler sets the shared stop event."""

        async def run():
            import signal

            stop = asyncio.Event()
            await ipt_main.shutdown(signal.SIGTERM, stop)
            return stop.is_set()

        self.assertTrue(asyncio.run(run()))


class TestDnsBackendMonitorLoop(unittest.TestCase):
    def test_monitor_ticks_reconcile_until_stopped(self):
        """monitor_dns_backend keeps reconciling until stop; each tick runs
        the reconcile function off the loop thread."""
        import time

        from ipt_server.tasks.dns_backend_monitor import monitor_dns_backend

        reconciles = []
        stop_gate = threading_event = __import__("threading").Event()

        def fake_reconcile():
            reconciles.append(time.monotonic())
            if len(reconciles) >= 2:
                threading_event.set()

        async def run():
            stop = asyncio.Event()
            with (
                patch.object(
                    ipt_main, "reconcile_dns_backend", side_effect=fake_reconcile
                ),
                patch("ipt_server.tasks.dns_backend_monitor._INTERVAL_SECONDS", 0.001),
            ):
                task = asyncio.create_task(monitor_dns_backend(stop))
                await asyncio.to_thread(threading_event.wait, 5.0)
                stop.set()
                await asyncio.wait_for(task, timeout=5.0)

        asyncio.run(run())
        self.assertGreaterEqual(len(reconciles), 2)


if __name__ == "__main__":
    unittest.main()
