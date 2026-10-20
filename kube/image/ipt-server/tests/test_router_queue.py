"""Regression guard: the route queue worker accepts ('replace', spec)."""

import unittest
from types import SimpleNamespace
from unittest.mock import patch, MagicMock, PropertyMock

from Router import Router
from route import DNS_METRIC, DNS_PROTO


class TestRouteQueueWorkerReplace(unittest.TestCase):
    """Regression guard: the route queue worker accepts ('replace', spec)."""

    @patch("route.RouteObject.interfaces", new_callable=PropertyMock)
    @patch("subprocess.run")
    def test_route_queue_worker_supports_replace(
        self, mock_subprocess_run, mock_interfaces
    ):
        """A ('replace', spec) on the queue becomes an ip -batch subprocess call
        carrying the route class proto and metric (#68)."""
        import time

        mock_interfaces.return_value = {"backbone": [(1, 0)]}
        mock_subprocess_run.return_value = MagicMock(returncode=0, stderr="")

        cfg = SimpleNamespace(
            table=200,
            clean_conntrack=False,
            domain_route_ttl=300,
            routes=[],
            interfaces=[],
        )

        router = Router(cfg, ipdb=MagicMock())
        try:
            router._route_queue.put(
                (
                    "replace",
                    {
                        "dst": "203.0.113.8",
                        "dst_len": 32,
                        "nhid": 3,
                        "proto": DNS_PROTO,
                        "priority": DNS_METRIC,
                    },
                )
            )
            # Let the worker thread drain
            deadline = time.time() + 2.0
            while time.time() < deadline:
                if mock_subprocess_run.called:
                    break
                time.sleep(0.02)
        finally:
            router._shutdown_event.set()
            router._route_thread.join(timeout=2.0)

        self.assertTrue(
            mock_subprocess_run.called,
            "Expected subprocess.run to be called for ip -batch",
        )
        call_args = mock_subprocess_run.call_args
        self.assertEqual(call_args[0][0], ["ip", "-batch", "-"])
        # input is a keyword arg
        batch_input = call_args[1]["input"]
        self.assertIn(
            "route replace 203.0.113.8/32 nhid 3 table 200 proto 250 metric 200",
            batch_input,
        )


if __name__ == "__main__":
    unittest.main()
