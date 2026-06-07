"""In-memory per-source-IP egress pinning state.

Source of truth for 'what the next nft reconcile should look like'.
TTL enforcement on the kernel side is handled by nftables `flags
timeout`; this module simply skips expired entries when handing a
snapshot to the kernel reconciler.
"""
from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from typing import Dict, Optional


@dataclass
class PinEntry:
    """One active pin: which egress this saddr is pinned to and when it expires."""

    saddr: str
    egress: str
    expires_at: float  # unix timestamp


class PinningManager:
    """Owns the in-memory pin map. Single instance per process."""

    def __init__(self, ttl_seconds: int) -> None:
        self._ttl = ttl_seconds
        self._data: Dict[str, PinEntry] = {}
        self._lock = asyncio.Lock()

    async def get(self, saddr: str) -> Optional[PinEntry]:
        async with self._lock:
            entry = self._data.get(saddr)
            if entry is None or entry.expires_at <= time.time():
                self._data.pop(saddr, None)
                return None
            return entry

    async def set(self, saddr: str, egress: str) -> PinEntry:
        async with self._lock:
            entry = PinEntry(
                saddr=saddr, egress=egress,
                expires_at=time.time() + self._ttl,
            )
            self._data[saddr] = entry
            return entry

    async def clear(self, saddr: str) -> None:
        async with self._lock:
            self._data.pop(saddr, None)

    async def snapshot(self) -> Dict[str, str]:
        """Return saddr→egress for non-expired entries (drives nft reconcile)."""
        async with self._lock:
            now = time.time()
            return {
                saddr: e.egress
                for saddr, e in list(self._data.items())
                if e.expires_at > now
            }
