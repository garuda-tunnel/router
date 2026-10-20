"""PinningManager: in-memory pin map with TTL.

Kernel-side TTL is enforced by nftables timeout-flagged sets; this
in-memory map only tracks 'what should the next nft reconcile look
like' and exposes a snapshot() for the kernel reconciler.

Tests are plain coroutines (pytest asyncio_mode=auto): each runs on the
plugin-managed loop, so no per-call event loop is created or leaked.
"""

from __future__ import annotations

import time

from pinning.manager import PinningManager


async def test_set_then_get_returns_active_entry():
    """set() then get() returns the pinned entry with a future expiry."""
    mgr = PinningManager(ttl_seconds=600)
    await mgr.set("172.30.0.3", "outer-pt")
    entry = await mgr.get("172.30.0.3")
    assert entry is not None
    assert entry.saddr == "172.30.0.3"
    assert entry.egress == "outer-pt"
    assert entry.expires_at > time.time()


async def test_set_replaces_previous_egress_for_same_saddr():
    """A second set() for the same source address replaces the egress."""
    mgr = PinningManager(ttl_seconds=600)
    await mgr.set("172.30.0.3", "outer-pt")
    await mgr.set("172.30.0.3", "outer-de")
    entry = await mgr.get("172.30.0.3")
    assert entry.egress == "outer-de"


async def test_clear_removes_entry():
    """clear() removes the pin for the given source address."""
    mgr = PinningManager(ttl_seconds=600)
    await mgr.set("172.30.0.3", "outer-pt")
    await mgr.clear("172.30.0.3")
    assert await mgr.get("172.30.0.3") is None


async def test_snapshot_returns_saddr_to_egress_mapping():
    """snapshot() drives nft reconcile — returns plain dict."""
    mgr = PinningManager(ttl_seconds=600)
    await mgr.set("172.30.0.3", "outer-pt")
    await mgr.set("172.30.0.4", "outer-de")
    snap = await mgr.snapshot()
    assert snap == {"172.30.0.3": "outer-pt", "172.30.0.4": "outer-de"}


async def test_snapshot_skips_expired_entries():
    """Defence in depth: nft is the source of truth on TTL, but the
    Python map should not export stale rows the kernel must then GC."""
    mgr = PinningManager(ttl_seconds=0)  # immediate expiry
    await mgr.set("172.30.0.3", "outer-pt")
    snap = await mgr.snapshot()
    assert snap == {}


async def test_get_returns_none_for_unknown_saddr():
    """get() of a never-pinned address returns None (unpinned path)."""
    mgr = PinningManager(ttl_seconds=600)
    assert await mgr.get("172.30.0.99") is None


async def test_clear_idempotent_on_unknown_saddr():
    """clear() of a never-pinned address must not raise."""
    mgr = PinningManager(ttl_seconds=600)
    await mgr.clear("172.30.0.99")  # must not raise
