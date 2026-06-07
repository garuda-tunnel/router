"""PinningManager: in-memory pin map with TTL.

Kernel-side TTL is enforced by nftables timeout-flagged sets; this
in-memory map only tracks 'what should the next nft reconcile look
like' and exposes a snapshot() for the kernel reconciler.
"""
from __future__ import annotations

import asyncio
import time

from pinning.manager import PinningManager


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


def test_set_then_get_returns_active_entry():
    mgr = PinningManager(ttl_seconds=600)
    _run(mgr.set("172.30.0.3", "outer-pt"))
    entry = _run(mgr.get("172.30.0.3"))
    assert entry is not None
    assert entry.saddr == "172.30.0.3"
    assert entry.egress == "outer-pt"
    assert entry.expires_at > time.time()


def test_set_replaces_previous_egress_for_same_saddr():
    mgr = PinningManager(ttl_seconds=600)
    _run(mgr.set("172.30.0.3", "outer-pt"))
    _run(mgr.set("172.30.0.3", "outer-de"))
    entry = _run(mgr.get("172.30.0.3"))
    assert entry.egress == "outer-de"


def test_clear_removes_entry():
    mgr = PinningManager(ttl_seconds=600)
    _run(mgr.set("172.30.0.3", "outer-pt"))
    _run(mgr.clear("172.30.0.3"))
    assert _run(mgr.get("172.30.0.3")) is None


def test_snapshot_returns_saddr_to_egress_mapping():
    """snapshot() drives nft reconcile — returns plain dict."""
    mgr = PinningManager(ttl_seconds=600)
    _run(mgr.set("172.30.0.3", "outer-pt"))
    _run(mgr.set("172.30.0.4", "outer-de"))
    snap = _run(mgr.snapshot())
    assert snap == {"172.30.0.3": "outer-pt", "172.30.0.4": "outer-de"}


def test_snapshot_skips_expired_entries():
    """Defence in depth: nft is the source of truth on TTL, but the
    Python map should not export stale rows the kernel must then GC."""
    mgr = PinningManager(ttl_seconds=0)  # immediate expiry
    _run(mgr.set("172.30.0.3", "outer-pt"))
    snap = _run(mgr.snapshot())
    assert snap == {}


def test_get_returns_none_for_unknown_saddr():
    mgr = PinningManager(ttl_seconds=600)
    assert _run(mgr.get("172.30.0.99")) is None


def test_clear_idempotent_on_unknown_saddr():
    mgr = PinningManager(ttl_seconds=600)
    _run(mgr.clear("172.30.0.99"))  # must not raise
