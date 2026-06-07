import asyncio
import json
import time

import pytest

from conftest import REPO_ROOT


def test_process_a_record_timeout_log_includes_record_identity(caplog, monkeypatch):
    """Timeout warning must include query/name/ip so operators can correlate drops."""
    from types import SimpleNamespace

    import ipt_server.main as main
    from ipt_server import state

    monkeypatch.setattr(
        state, "CONFIG", SimpleNamespace(ws_route_apply_budget_seconds=0.01)
    )

    def _slow_process(_record):
        time.sleep(0.05)
        return {"ttl": 300}

    monkeypatch.setattr(main, "process_a_record", _slow_process)

    record = {
        "query": "2ip.ru.",
        "name": "2ip.ru.",
        "content": "188.40.167.82",
        "type": 1,
        "ttl": 300,
    }

    with caplog.at_level("WARNING"):
        rv = asyncio.run(main.process_a_record_with_budget(record))

    assert rv == {"ttl": 1, "degraded": True}
    timeout_logs = [
        r for r in caplog.records if "A-record processing exceeded" in r.getMessage()
    ]
    assert timeout_logs, "expected timeout warning log"
    msg = timeout_logs[-1].getMessage()
    assert "query=2ip.ru." in msg
    assert "name=2ip.ru." in msg
    assert "ip=188.40.167.82" in msg
    assert timeout_logs[-1].name == "ipt_server.main"


def test_echo_logs_incoming_a_record(caplog, monkeypatch):
    """WebSocket handler should log that an A-record arrived."""
    import ipt_server.main as main

    class FakeWebSocket:
        remote_address = ("127.0.0.1", 12345)

        def __init__(self, messages):
            self._messages = list(messages)
            self.sent = []

        def __aiter__(self):
            return self

        async def __anext__(self):
            if not self._messages:
                raise StopAsyncIteration
            return self._messages.pop(0)

        async def send(self, msg):
            self.sent.append(msg)

    async def _ok(_record):
        return {"ttl": 60}

    monkeypatch.setattr(main, "process_a_record_with_budget", _ok)

    ws = FakeWebSocket(
        [
            json.dumps(
                {
                    "query": "2ip.ru.",
                    "name": "2ip.ru.",
                    "content": "188.40.167.82",
                    "type": 1,
                    "ttl": 300,
                }
            )
        ]
    )

    with caplog.at_level("INFO"):
        asyncio.run(main.echo(ws))

    assert ws.sent == ['{"ttl": 60}']
    received = [r for r in caplog.records if "A-record received" in r.getMessage()]
    assert received, "expected A-record receive log"
    assert received[-1].name == "ipt_server.main"
    assert "query=2ip.ru." in received[-1].getMessage()


@pytest.mark.parametrize(
    "rel_path",
    [
        "kube/image/ipt-server/tasks/interface_monitor.py",
        "kube/image/ipt-server/tasks/route_health_monitor.py",
        "kube/image/ipt-server/lib.py",
    ],
)
def test_runtime_modules_do_not_emit_runtime_logs_via_root_logger(rel_path):
    """Operational runtime modules must log under module names, not root."""
    text = (REPO_ROOT / rel_path).read_text()
    assert "logging.info(" not in text
    assert "logging.warning(" not in text
    assert "logging.error(" not in text
