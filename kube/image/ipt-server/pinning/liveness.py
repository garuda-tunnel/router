"""Liveness probing for pinning egresses.

Reuses the OSPF-based gateway prober from tasks.nexthop_monitor for
gw-typed egresses. For dev-only egresses, alive iff the interface is
currently registered in state.INTERFACES.
"""
from __future__ import annotations

from typing import Optional, Set, Tuple

from tasks.nexthop_monitor import _probe_gw_alive


def probe_egress(
    target,
    interfaces: Set[str],
) -> Tuple[bool, Optional[str], Optional[str]]:
    """Return (alive, nh_ip, nh_dev) for a single egress catalog entry."""
    if target.gw is not None:
        return _probe_gw_alive(target.gw)
    dev = target.dev
    return (dev in interfaces, None, dev)
