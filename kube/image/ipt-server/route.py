"""Route object model used by IPT server for route lifecycle management."""

from typing import Dict, Optional, Any
import ipaddress
from dataclasses import dataclass

import logging
from datetime import datetime, timedelta


logger = logging.getLogger(__name__)


@dataclass
class RouteObject:
    net: ipaddress.IPv4Network
    family: int = 2  # AF_INET
    proto: int = 3  # RTPROT_BOOT
    type: int = 1  # RTN_UNICAST
    weight: int = 0
    metric: int = 0
    gw: Optional[str] = None
    dev: Optional[str] = None
    nhid: Optional[int] = None
    ttl: Optional[int] = None
    net_start: int = 0
    net_end: int = 0
    expiration: Optional[datetime] = None

    @property
    def route_spec(self) -> Dict[str, Any]:
        """Build pyroute2-compatible route specification for this route."""
        if self.nhid is None:
            raise RuntimeError(
                f"Route {self.net} has no nhid assigned — "
                "call setup_nexthop_group() before installing routes"
            )
        return {
            "dst": str(self.net.network_address),
            "dst_len": self.net.prefixlen,
            "family": self.family,
            "proto": self.proto,
            "type": self.type,
            "priority": self.metric,
            "nhid": self.nhid,
        }

    @property
    def expired(self) -> bool:
        """Return whether route TTL-based expiration time has passed."""
        if self.expiration is None:
            return False
        return datetime.now() > self.expiration

    def __post_init__(self):
        """Normalize network value and cache integer address range."""
        if not isinstance(self.net, ipaddress.IPv4Network):
            self.net = ipaddress.IPv4Network(self.net, strict=False)
        self.net_start = int(self.net.network_address)
        self.net_end = int(self.net.broadcast_address)

    def reset_expiration(self, new_ttl: Optional[int] = None):
        """Update expiration timestamp using current or provided TTL value."""
        if new_ttl is not None:
            if self.ttl is None:
                self.ttl = new_ttl
            else:
                self.ttl = max(self.ttl, new_ttl)
            self.expiration = datetime.now() + timedelta(seconds=self.ttl)
        elif self.ttl is not None:
            self.expiration = datetime.now() + timedelta(seconds=self.ttl)
        else:
            self.expiration = None

    @classmethod
    @property
    def interfaces(cls) -> Dict[str, Any]:
        """Read-only snapshot of ``ifname -> [(ifindex, None)]``.

        The underlying snapshot is owned by
        ``ipt_server.tasks.interface_monitor`` and stored in
        ``ipt_server.state.INTERFACES``. This property only reads that
        snapshot under ``state.INTERFACES_LOCK`` and returns a fresh dict.

        The returned shape mirrors the previous ``lru_cache``-backed
        implementation so that existing ``PropertyMock`` patches in
        tests keep working without modification.
        """
        from ipt_server import state

        with state.INTERFACES_LOCK:
            return {name: [(idx, None)] for name, idx in state.INTERFACES.items()}
