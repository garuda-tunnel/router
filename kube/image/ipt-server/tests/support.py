"""Shared hermetic helpers for ipt-server Router tests.

The Router constructor opens a real netlink ``Conntrack`` socket and starts
two daemon worker threads. Any test that constructs a Router MUST go through
:func:`hermetic_router` (or replicate its contract):

1. ``Router.Conntrack`` is patched *before* construction, so no real netlink
   socket is ever opened (the constructor binding is the unit boundary).
2. On exit both worker threads are stopped and joined **in the owning test
   thread**. This matters: ``Router.__del__`` calls ``stop()`` which joins
   the workers, and a join of the current thread raises
   ``RuntimeError: cannot join current thread``. If garbage collection runs
   on a worker thread while the Router is unreferenced-but-unstopped, the
   deallocator explodes with an unraisable exception. Deterministic stop in
   the owning thread leaves ``__del__`` nothing to join.
"""

from __future__ import annotations

import ipaddress
import threading
from contextlib import contextmanager
from datetime import datetime
from unittest.mock import MagicMock, patch

import Router as router_module
from Router import Router


class FrozenClock(datetime):
    """Deterministic stand-in for ``route.datetime``.

    Route TTL logic reads ``datetime.now()``; the clock is a fixed epoch
    plus an explicit offset that tests advance (``FrozenClock.advance`` or
    setting ``offset``). It never reads the real clock, so expiry is a
    controlled input, never elapsed wall time.
    """

    EPOCH = 1760000000.0  # constant instant; unrelated to the host clock
    offset = 0.0

    @classmethod
    def now(cls, tz=None):
        return datetime.fromtimestamp(cls.EPOCH + cls.offset, tz)

    @classmethod
    def advance(cls, seconds):
        cls.offset += seconds

    @classmethod
    def reset(cls):
        cls.offset = 0.0


def ct_entry(saddr: str, daddr: str):
    """Build a minimal conntrack-entry fake with the fields Router reads."""
    return MagicMock(
        tuple_orig=MagicMock(saddr=saddr, daddr=daddr),
    )


class FakeConntrack:
    """In-memory conntrack boundary fake.

    ``entries``: iterable returned by ``dump_entries`` (default empty).
    ``dump_error``: exception to raise from ``dump_entries`` when set.
    ``deleted``: tuple_orig values passed to ``entry("del", ...)``.
    ``delete_errors``: mapping index -> exception raised on the Nth delete.
    """

    def __init__(self, entries=(), dump_error=None, delete_errors=None):
        self.entries = list(entries)
        self.dump_error = dump_error
        self.delete_errors = delete_errors or {}
        self.deleted = []
        self.dump_calls = 0
        self.delete_attempts = 0

    def dump_entries(self):
        self.dump_calls += 1
        if self.dump_error is not None:
            raise self.dump_error
        return iter(self.entries)

    def entry(self, cmd, tuple_orig=None):
        if cmd == "del":
            error = self.delete_errors.get(self.delete_attempts)
            self.delete_attempts += 1
            if error is not None:
                raise error
            self.deleted.append(tuple_orig)


def ip_int(address: str) -> int:
    """Convert a dotted-quad string to an integer for interval math."""
    return int(ipaddress.ip_address(address))


@contextmanager
def hermetic_router(cfg, ipdb=None, conntrack=None):
    """Yield a Router whose kernel boundaries are fakes, stopped on exit.

    See the module docstring for why the Conntrack patch must happen before
    construction and why the workers are joined here.
    """
    boundary = conntrack if conntrack is not None else FakeConntrack()
    with patch.object(router_module, "Conntrack", return_value=boundary):
        router = Router(cfg, ipdb=ipdb if ipdb is not None else MagicMock())
    try:
        yield router
    finally:
        stop_workers(router)


def stop_workers(router: Router, timeout: float = 5.0) -> None:
    """Signal shutdown and join both worker threads from the calling thread.

    Raises if either worker is still alive after the join: a lingering
    worker would keep running inside an expiring patch context."""
    current = threading.current_thread()
    router._shutdown_event.set()
    for worker in (router._route_thread, router._cleanup_thread):
        if worker.is_alive() and worker is not current:
            worker.join(timeout=timeout)
            if worker.is_alive():
                raise AssertionError(
                    f"Router worker {worker.name!r} did not stop within "
                    f"{timeout}s; test patches may have expired"
                )


def drain_queue(router: Router) -> list:
    """Return and clear everything currently pending on the route queue."""
    items = []
    while True:
        try:
            items.append(router._route_queue.get_nowait())
        except Exception:
            return items
