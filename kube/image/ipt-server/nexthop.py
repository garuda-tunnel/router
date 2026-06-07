"""Kernel nexthop object management via ip-nexthop CLI.

All operations use proto 199 as ownership marker. Flush removes only
owned objects. replace_* functions are idempotent (create-or-update).
create_* functions require the nhid to be absent; call flush_owned()
before create sequences to ensure a clean slate.
"""

import subprocess

PROTO = 199


def flush_owned() -> None:
    """Remove all kernel nexthop objects owned by ipt_server (proto 199).

    Raises:
        RuntimeError: if the flush command fails. Stale nexthop state would
            cause silent misrouting, so failure is fatal.
    """
    try:
        subprocess.run(
            ["ip", "nexthop", "flush", "proto", str(PROTO)],
            capture_output=True,
            check=True,
        )
    except subprocess.CalledProcessError as exc:
        raise RuntimeError(
            f"ip nexthop flush proto {PROTO} failed (rc={exc.returncode}): {exc.stderr}"
        ) from exc


def create(nhid: int, via: str, dev: str, proto: int = PROTO) -> None:
    """Add a nexthop object with a gateway and egress device.

    Args:
        nhid: Nexthop ID to assign.
        via: Gateway IP address.
        dev: Egress interface name.
        proto: Routing protocol number (default: PROTO=100).
    """
    subprocess.run(
        [
            "ip",
            "nexthop",
            "add",
            "id",
            str(nhid),
            "via",
            via,
            "dev",
            dev,
            "proto",
            str(proto),
        ],
        capture_output=True,
        check=True,
    )


def create_blackhole(nhid: int, proto: int = PROTO) -> None:
    """Add a blackhole nexthop object.

    Args:
        nhid: Nexthop ID to assign.
        proto: Routing protocol number (default: PROTO=100).
    """
    subprocess.run(
        ["ip", "nexthop", "add", "id", str(nhid), "blackhole", "proto", str(proto)],
        capture_output=True,
        check=True,
    )


def create_group(nhid: int, member_nhid: int, proto: int = PROTO) -> None:
    """Add a nexthop group object with a single active member.

    Args:
        nhid: Nexthop group ID to assign.
        member_nhid: ID of the member nexthop.
        proto: Routing protocol number (default: PROTO=100).
    """
    subprocess.run(
        [
            "ip",
            "nexthop",
            "add",
            "id",
            str(nhid),
            "group",
            str(member_nhid),
            "proto",
            str(proto),
        ],
        capture_output=True,
        check=True,
    )


def create_device(nhid: int, dev: str, proto: int = PROTO) -> None:
    """Add a nexthop object with only an egress device (no gateway).

    Used for directly-connected interface routes.

    Args:
        nhid: Nexthop ID to assign.
        dev: Egress interface name.
        proto: Routing protocol number (default: PROTO=100).
    """
    subprocess.run(
        ["ip", "nexthop", "add", "id", str(nhid), "dev", dev, "proto", str(proto)],
        capture_output=True,
        check=True,
    )


def replace_device(nhid: int, dev: str) -> None:
    """Replace (or create) a nexthop object with only an egress device."""
    subprocess.run(
        ["ip", "nexthop", "replace", "id", str(nhid), "dev", dev, "proto", str(PROTO)],
        capture_output=True,
        check=True,
    )


def replace_nexthop(nhid: int, via: str, dev: str) -> None:
    """Replace (or create) a nexthop object with a gateway and egress device.

    Uses proto 199 as ownership marker.

    Args:
        nhid: Nexthop ID to replace.
        via: Gateway IP address.
        dev: Egress interface name.
    """
    subprocess.run(
        [
            "ip",
            "nexthop",
            "replace",
            "id",
            str(nhid),
            "via",
            via,
            "dev",
            dev,
            "proto",
            str(PROTO),
        ],
        capture_output=True,
        check=True,
    )


def replace_nexthop_blackhole(nhid: int) -> None:
    """Replace (or create) a nexthop object as a blackhole.

    Uses proto 199 as ownership marker.

    Args:
        nhid: Nexthop ID to replace.
    """
    subprocess.run(
        ["ip", "nexthop", "replace", "id", str(nhid), "blackhole", "proto", str(PROTO)],
        capture_output=True,
        check=True,
    )


def replace_group(nhid: int, member_nhid: int) -> None:
    """Replace (or create) a nexthop group with a single active member.

    Uses proto 199 as ownership marker.

    Args:
        nhid: Nexthop group ID to replace.
        member_nhid: ID of the member nexthop.
    """
    subprocess.run(
        [
            "ip",
            "nexthop",
            "replace",
            "id",
            str(nhid),
            "group",
            str(member_nhid),
            "proto",
            str(PROTO),
        ],
        capture_output=True,
        check=True,
    )
