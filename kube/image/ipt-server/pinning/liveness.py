"""Liveness probing for pinning egresses.

Reuses the OSPF-based gateway prober from ipt_server.tasks.nexthop_monitor
for gw-typed egresses. For dev-only egresses, alive iff the interface is
currently registered in state.INTERFACES.

The import must use the running app's module path (ipt_server.tasks.*, the
namespace main.py extends onto the image root): importing the top-level
tasks.nexthop_monitor would load a second module object whose
_BACKBONE_SUBNETS is never refreshed, and every on-backbone gateway
(border) would be judged dead — blackholing pinned clients (#83).
"""
from __future__ import annotations

from typing import Optional, Set, Tuple

from ipt_server.tasks.nexthop_monitor import _probe_gw_alive


def probe_egress(
    target,
    interfaces: Set[str],
) -> Tuple[bool, Optional[str], Optional[str]]:
    """Return (alive, nh_ip, nh_dev) for a single egress catalog entry."""
    if target.gw is not None:
        return _probe_gw_alive(target.gw)
    dev = target.dev
    return (dev in interfaces, None, dev)
