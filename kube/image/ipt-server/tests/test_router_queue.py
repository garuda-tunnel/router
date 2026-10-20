"""Route queue worker contract: ('replace', spec) becomes an ip -batch line.

The ip -batch command text is the kernel boundary contract (the exact line
the kernel executes), so asserting on it here is legitimate: see
tests/README.md, "Kernel-boundary assertions".
"""

import threading
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from route import DNS_METRIC, DNS_PROTO
from tests.support import hermetic_router


class TestRouteQueueWorkerReplace(unittest.TestCase):
    def test_route_queue_worker_supports_replace(self):
        """A ('replace', spec) on the queue becomes an ip -batch subprocess
        call carrying the route class proto and metric (#68)."""
        processed = threading.Event()
        seen = {}

        def fake_run(argv, **kwargs):
            if argv[:2] == ["ip", "-batch"]:
                seen["argv"] = argv
                seen["input"] = kwargs.get("input")
                processed.set()
            return MagicMock(returncode=0, stderr="", stdout="")

        cfg = SimpleNamespace(
            table=200,
            clean_conntrack=False,
            domain_route_ttl=300,
            routes=[],
            interfaces=[],
        )

        with patch("subprocess.run", side_effect=fake_run):
            with hermetic_router(cfg) as router:
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
                # The fake subprocess boundary signals synchronously from
                # the worker thread once the batch is processed.
                self.assertTrue(
                    processed.wait(timeout=5.0),
                    "worker must process the queued replace",
                )

        self.assertEqual(seen["argv"], ["ip", "-batch", "-"])
        self.assertIn(
            "route replace 203.0.113.8/32 nhid 3 table 200 proto 250 metric 200",
            seen["input"],
        )


if __name__ == "__main__":
    unittest.main()
