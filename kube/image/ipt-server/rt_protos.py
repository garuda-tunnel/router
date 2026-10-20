"""Install iproute2 route-proto names from code constants (single source of truth).

`ip route` prints raw numbers unless iproute2 knows a name; the proto numbers
live only in code (route.DNS_PROTO, pinning.kernel.PINNING_PROTO), so the name
table is rendered from those same constants at startup. Names are cosmetic:
routing must never depend on this file, hence fail-soft.
"""

import logging
import os
from pathlib import Path

import route
from pinning import kernel as pinning_kernel

RT_PROTOS_CONF_PATH = Path("/etc/iproute2/rt_protos.d/garuda.conf")

log = logging.getLogger(__name__)


def render_rt_protos() -> str:
    """Render the rt_protos.d '<num> <name>' lines from the code constants."""
    entries = (
        (route.DNS_PROTO, route.DNS_PROTO_NAME),
        (pinning_kernel.PINNING_PROTO, pinning_kernel.PINNING_PROTO_NAME),
    )
    return "".join(f"{num} {name}\n" for num, name in entries)


def write_rt_protos() -> None:
    """Atomically write RT_PROTOS_CONF_PATH unless its content is already current.

    Fail-soft: any OSError (read-only fs, permissions) logs one WARNING and
    returns — names are cosmetic.
    """
    content = render_rt_protos()
    path = RT_PROTOS_CONF_PATH
    try:
        if path.is_file() and path.read_text() == content:
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text(content)
        os.replace(tmp, path)
    except OSError as e:
        log.warning("Cannot write %s (route-proto names are cosmetic): %s", path, e)
