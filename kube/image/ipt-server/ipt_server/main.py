"""IPT server runtime: websocket DNS processing and policy-based routing orchestration."""

from pyroute2 import IPRoute
import json
import logging
import os
import socket
import time
from pathlib import Path
import dns_records
import signal

import ipt_server as _ipt_server_pkg

logging_level = os.environ.get("LOGLEVEL", "INFO").upper()
_root_logger = logging.getLogger()
if not _root_logger.handlers:
    logging.basicConfig(
        level=getattr(logging, logging_level, logging.INFO),
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )
else:
    # Handlers already configured (e.g., by test harness), but ensure level is not
    # more restrictive than the requested level.
    _requested_level = getattr(logging, logging_level, logging.INFO)
    if _root_logger.level == logging.NOTSET or _root_logger.level > _requested_level:
        _root_logger.setLevel(_requested_level)

import nftables

from Config import MySettings
from Router import Router
from route_health import FrrVtyshOspfHealthSource
from ipdb.query import IPDatabase

IPDB = IPDatabase("/data/ipt.db")

_PACKAGE_ROOT = str(Path(__file__).resolve().parent.parent)
if _PACKAGE_ROOT not in _ipt_server_pkg.__path__:
    _ipt_server_pkg.__path__.append(_PACKAGE_ROOT)

from ipt_server.tasks.dns_backend_monitor import monitor_dns_backend
from ipt_server.tasks.nexthop_monitor import monitor_nexthops
from ipt_server.tasks.interface_monitor import monitor_interfaces
from ipt_server.tasks.route_health_monitor import monitor_route_health
import jinja2
import websockets
import asyncio
from ipt_server import state

logger = logging.getLogger(__name__)

# Strong-reference container for fire-and-forget background tasks.
#
# asyncio.create_task() docs: "Save a reference to the result of this
# function, to avoid a task disappearing mid-execution. The event loop only
# keeps weak references to tasks. A task that isn't referenced elsewhere may
# get garbage collected at any time, even before it's done."
#
# Proven live on vpn2: the monitor loops (and the pinning http_task) were
# spawned via bare asyncio.create_task(...) with the return value discarded.
# Each died silently after its first tick (no exception) once the cyclic GC
# reclaimed the unreferenced Task, leaving pinning tables 301/302 blackhole.
# Every background task in this module MUST go through _spawn_background /
# _retain_task so it stays alive for the process lifetime.
_BACKGROUND_TASKS: set[asyncio.Task] = set()


def _log_task_result(task: asyncio.Task) -> None:
    """Done-callback: make an unexpected background-task death LOUD.

    A retained task that finishes with an exception produces NO asyncio
    warning (asyncio only warns "Task exception was never retrieved" when a
    task with an unretrieved exception is *garbage-collected*; the retention
    container deliberately keeps a strong reference, which suppresses that
    warning). That is exactly how the live vpn2 loop deaths went silent.

    This callback retrieves and logs the outcome so any future death/stall is
    immediately visible in the pod logs:
      * cancellation -> INFO (expected during shutdown),
      * exception    -> ERROR with full traceback,
      * clean return -> DEBUG (background loops are not expected to return on
        their own; if one does, note it so it can be investigated).
    """
    if task.cancelled():
        logger.info("background task %r was cancelled", task.get_name())
        return
    exc = task.exception()
    if exc is not None:
        logger.error(
            "background task %r died with an exception (loop no longer running)",
            task.get_name(),
            exc_info=exc,
        )
    else:
        logger.debug(
            "background task %r finished cleanly (unexpected for a monitor loop)",
            task.get_name(),
        )


def _retain_task(task: asyncio.Task) -> asyncio.Task:
    """Add a strong reference to `task` so it can't be silently GC'd.

    The done-callbacks remove it from the container once it completes (so the
    container doesn't grow unbounded over the process lifetime) and log the
    task's outcome loudly (so a future silent death is never invisible again).
    While pending, it is always reachable via _BACKGROUND_TASKS.
    """
    _BACKGROUND_TASKS.add(task)
    task.add_done_callback(_BACKGROUND_TASKS.discard)
    task.add_done_callback(_log_task_result)
    return task


def _spawn_background(coro) -> asyncio.Task:
    """Create a background task and retain a strong reference to it."""
    return _retain_task(asyncio.create_task(coro))


def _template_env() -> jinja2.Environment:
    env = jinja2.Environment(loader=jinja2.FileSystemLoader(_PACKAGE_ROOT))
    env.filters["hex"] = lambda x: format(x, "x")
    return env


# Module-level state for DNS DNAT reconciliation
_LAST_DNS_BACKEND_IP: str = ""


def load_settings_from_env(env: dict) -> MySettings:
    return MySettings.model_validate(env)


def resolve_backend_hostname(hostname: str | None = None) -> str:
    """Resolve the DNS backend hostname to an IPv4 address.

    Raises OSError if the hostname cannot be resolved.
    """
    if hostname is None:
        hostname = os.environ.get("IPT_DNS_BACKEND_HOST", "garuda_pdns")
    return socket.gethostbyname(hostname)


def dns_backend_accepts_queries(backend_ip: str, timeout: float = 1.0) -> bool:
    """Probe whether the DNS backend is accepting TCP connections on port 1053.

    Uses a narrow TCP connect probe rather than sending a real DNS query.
    Returns True if the connection succeeds, False on any OSError.

    This probe is the readiness gate for NAT rule installation: the backend
    address may resolve via Docker DNS before PowerDNS has finished starting.
    Installing DNAT rules before the backend is ready would silently drop
    client DNS traffic.
    """
    try:
        with socket.create_connection((backend_ip, 1053), timeout=timeout):
            return True
    except OSError as exc:
        logger.warning(
            "DNS backend resolved but is not accepting queries yet: %s (%s)",
            backend_ip,
            exc,
        )
        return False


def _render_dns_dnat_ruleset(backend_ip: str) -> str:
    """Render the nftables ruleset that intercepts client DNS and SNATs reply traffic.

    The ruleset owns two chains:
    - prerouting: DNAT all non-loopback UDP/TCP port 53 traffic to the backend IP.
    - postrouting: masquerade DNS traffic destined for the backend IP so that
      garuda_pdns sees garuda_ipt as the source and replies back through it.

    The postrouting masquerade is scoped to the backend IP only (not a broad
    daddr != 127.0.0.0/8 rule). This ensures recursive upstream traffic from
    garuda_pdns is not affected and does not re-enter the interception path.

    The postrouting chain uses priority 100, same as border_ipt_server. Both
    chains match disjoint traffic (ip daddr <backend> vs meta mark), so
    processing order across the two tables at the same priority has no
    correctness impact.
    """
    template = _template_env().get_template("templates/dns_dnat.nft.j2")
    return template.render(backend_ip=backend_ip)


def render_dns_dnat_rules() -> str:
    return _render_dns_dnat_ruleset(resolve_backend_hostname())


def apply_dns_dnat_rules(backend_ip: str) -> None:
    nft = nftables.Nftables()
    nft.cmd("delete table inet dns_dnat_ipt_server")
    rc, output, error = nft.cmd(_render_dns_dnat_ruleset(backend_ip))
    if rc != 0:
        raise RuntimeError(f"Failed to apply DNS DNAT rules: {error}")


def reconcile_dns_backend() -> None:
    """Reconcile DNS DNAT rules against the current backend address and readiness.

    Skips rule installation if the backend hostname is not yet resolvable or if
    the backend is not yet accepting TCP connections on port 53.  Only installs
    or updates rules when the backend IP changes relative to the last applied IP.
    """
    global _LAST_DNS_BACKEND_IP
    try:
        backend_ip = resolve_backend_hostname()
    except OSError as exc:
        logger.warning(
            "DNS backend not yet resolvable, skipping DNAT reconcile: %s", exc
        )
        return
    if not dns_backend_accepts_queries(backend_ip):
        return
    if backend_ip != _LAST_DNS_BACKEND_IP:
        apply_dns_dnat_rules(backend_ip)
        _LAST_DNS_BACKEND_IP = backend_ip


class _PdnsRuntimeConfig:
    websocket_host: str = "127.0.0.1"
    websocket_port: int = 8765

    def __init__(self):
        self.websocket_host = "127.0.0.1"
        self.websocket_port = 8765


def build_pdns_runtime_config(env: dict) -> _PdnsRuntimeConfig:
    return _PdnsRuntimeConfig()


def render_border_rules() -> str:
    """Render nft masquerade table for border egress; empty if no border."""
    if not state.CONFIG.has_border:
        return ""
    private_bypass = [
        "10.0.0.0/8",
        "172.16.0.0/12",
        "192.168.0.0/16",
        "100.64.0.0/10",
    ]
    template = _template_env().get_template("templates/border.nft.j2")
    return template.render(private_bypass=private_bypass)


def apply_border_rules() -> None:
    nft = nftables.Nftables()
    nft.cmd("delete table inet border_ipt_server")
    ruleset = render_border_rules()
    if not ruleset:
        return
    rc, _output, error = nft.cmd(ruleset)
    if rc != 0:
        raise RuntimeError(f"Failed to apply border rules: {error}")


def render_mss_clamp_rules() -> str:
    """Render the separate inet ipt_server_mss table for forward-direction MSS clamping.

    Returns an empty string when mss_clamp_enabled is False (disabled).
    ipt-server is the central forward-path transit for both chains; the return path
    is asymmetric and bypasses ipt-server (Task 0 DQ2), so this clamps forward SYN
    only. Fixed MSS because ipt-server owns only the 1500 backbone iface where
    clamp-to-pmtu would be a no-op (spec §1.5).
    """
    if not state.CONFIG.mss_clamp_enabled:
        return ""
    template = _template_env().get_template("templates/mss.nft.j2")
    return template.render(config=state.CONFIG)


def apply_mss_clamp_rules() -> None:
    """Apply (or skip) the MSS clamp table.  Idempotent: delete before re-add."""
    nft = nftables.Nftables()
    nft.cmd("delete table inet ipt_server_mss")
    ruleset = render_mss_clamp_rules()
    if not ruleset.strip():
        return
    rc, _output, error = nft.cmd(ruleset)
    if rc != 0:
        raise RuntimeError(f"Failed to apply MSS clamp rules: {error}")
    logger.info(
        "Applied MSS clamp table inet ipt_server_mss (fixed_mss=%d)", state.CONFIG.fixed_mss
    )


def startup_apply_network_state() -> None:
    apply_pbr()
    apply_border_rules()
    apply_mss_clamp_rules()
    reconcile_dns_backend()


def apply_pbr():
    """
    Applies PBR (Policy-Based Routing) rules:
      1. Flushes the specified routing table.
      2. Removes existing rules for the specified table.
      3. Adds an IP rules (using pyroute2.IPRoute).
    """
    logger.debug("Applying PBR")
    clean_pbr()
    with IPRoute() as ipr:
        try:
            ipr.rule("add", fwmark=state.CONFIG.pbr_mark, table=state.CONFIG.table)
            logger.info(
                f"Added ip rule: fwmark=0x{state.CONFIG.pbr_mark:x}, table={state.CONFIG.table}"
            )
        except Exception as e:
            logger.error(f"Error adding ip rule: {e}")
            raise
    # Load the template from the shared environment
    template = _template_env().get_template("templates/pbr.nft.j2")

    # For each interface, render the rules and apply them through the nftables API
    # Add table

    nft = nftables.Nftables()
    logger.debug(f"Rendering PBR for all interfaces: {state.CONFIG.interfaces}")

    rendered_ruleset = template.render(config=state.CONFIG)
    rc, output, error = nft.cmd(rendered_ruleset)
    if rc != 0:
        logger.error(f"Error applying NFT rules: {error} {rendered_ruleset}")
        raise Exception(f"Error applying NFT rules: {error} {rendered_ruleset}")
    logger.info("Applied NFT rules successfully")

    # Create a Jinja2 environment with the custom filter


def clean_pbr():
    """Remove nftables PBR table and flush policy routes from configured table."""
    nft = nftables.Nftables()
    nft.set_json_output(True)
    nft.set_handle_output(False)
    nft.set_terse_output(True)
    rc, output, error = nft.cmd("list tables")
    pbr_table = [
        x
        for x in output["data"]
        if "table" in x.keys() and x["table"]["name"] == "ipt_server_pbr"
    ]
    if len(pbr_table) > 0:
        nft.cmd("delete table ipt_server_pbr")
    with IPRoute() as ipr:
        try:
            # Get existing rules for the specified table
            for x in range(0, len(ipr.get_rules(table=state.CONFIG.table))):
                # Remove the rule
                ipr.rule("del", table=state.CONFIG.table)
            logger.info(f"Removed existing rules for table {state.CONFIG.table}")
            if len(ipr.get_rules(table=state.CONFIG.table)) > 0:
                logger.warning(
                    f"Unable to remove all existing rules for table {state.CONFIG.table}"
                )
            # Flush the routing table
            ipr.flush_routes(table=state.CONFIG.table)
            logger.info(f"Flushed routing table {state.CONFIG.table}")
        except Exception as e:
            logger.error(f"Error flushing table or removing existing rules: {e}")


def build_route_health_source(config: MySettings):
    """Factory: return a health source if gated interfaces are configured, else None."""
    if not config.route_health.interfaces:
        return None
    return FrrVtyshOspfHealthSource()


def main():
    state.CONFIG = MySettings()
    logger.debug("Loading config")
    startup_apply_network_state()
    state.ROUTER = Router(state.CONFIG, ipdb=IPDB)

    try:
        asyncio.run(async_main())
    except KeyboardInterrupt:
        logger.info("Received keyboard interrupt, shutting down...")
    finally:
        logger.info("Exiting application")


def process_a_record(record) -> dict:
    """
    record format: {'query': 'microsoft.com.', 'content': '20.236.44.162', 'name': 'microsoft.com.', 'type': 1}
    """
    return state.ROUTER.on_a_record(dns_records.ARecord(record))


async def process_a_record_with_budget(record) -> dict:
    query = record.get("query", "?")
    name = record.get("name", "?")
    ip = record.get("content", "?")
    budget = state.CONFIG.ws_route_apply_budget_seconds
    started = time.monotonic()
    try:
        rv = await asyncio.wait_for(
            asyncio.to_thread(process_a_record, record),
            timeout=budget,
        )
        elapsed = time.monotonic() - started
        logger.info(
            "A-record route decision completed in %.3fs: query=%s name=%s ip=%s ttl=%s degraded=%s",
            elapsed,
            query,
            name,
            ip,
            rv.get("ttl"),
            rv.get("degraded", False),
        )
        return rv
    except asyncio.TimeoutError:
        # The background thread continues running after cancellation; pyroute2
        # netlink calls are short-lived so this is acceptable.
        elapsed = time.monotonic() - started
        logger.warning(
            "A-record processing exceeded %.3fs budget after %.3fs; returning degraded TTL: query=%s name=%s ip=%s",
            budget,
            elapsed,
            query,
            name,
            ip,
        )
        return {"ttl": 1, "degraded": True}


async def echo(websocket: websockets.ServerConnection) -> None:
    """
    Handle WebSocket connections and process incoming messages.

    Message format: {'query': 'microsoft.com.', 'content': '20.236.44.162', 'name': 'microsoft.com.', 'type': 1}
    """
    remote = getattr(websocket, "remote_address", "unknown")
    logger.info(f"WebSocket connection established from {remote}")
    try:
        async for message in websocket:
            try:
                msg = json.loads(message)
                logger.debug(f"Got message {msg}")
                rv = {}
                if msg["type"] == 1:
                    logger.info(
                        "A-record received: query=%s name=%s ip=%s ttl=%s",
                        msg.get("query", "?"),
                        msg.get("name", "?"),
                        msg.get("content", "?"),
                        msg.get("ttl"),
                    )
                    rv = await process_a_record_with_budget(msg)
                await websocket.send(json.dumps(rv))
            except json.JSONDecodeError:
                logger.error(f"Invalid JSON received: {message}")
                await websocket.send("Error: Invalid JSON")
            except Exception as e:
                logger.error(f"Error processing message: {e}")
                await websocket.send("Error: Message processing failed")
    finally:
        logger.info(f"WebSocket connection closed from {remote}")


async def handle_health_check(reader, writer):
    """Serve lightweight HTTP health-check response for container probes."""
    try:
        data = await reader.read(100)
        message = data.decode()
        addr = writer.get_extra_info("peername")
        logger.debug(f"Health check request from {addr}")

        response = "HTTP/1.1 200 OK\r\nContent-Type: text/plain\r\n\r\nOK"
        writer.write(response.encode())
        await writer.drain()
    except Exception as e:
        logger.error(f"Error handling health check: {e}")
    finally:
        writer.close()
        try:
            await writer.wait_closed()
        except Exception as exc:
            logger.debug("health-check writer close failed: %s", exc)


async def shutdown(sig, stop_event):
    """Cleanup tasks tied to the service's shutdown."""
    logger.info(f"Received exit signal {sig.name}")
    stop_event.set()


async def async_main():
    """Run websocket server, health server, and interface monitor until shutdown.

    The health endpoint starts *first* so that FRR (depends_on: service_healthy)
    can come up and OSPF can converge.  Route loading is kicked off in a
    background thread only after the health endpoint is listening.
    """
    # Create a task that can be cancelled
    stop_event = asyncio.Event()
    loop = asyncio.get_running_loop()

    # Use asyncio's signal handler
    for sig in [signal.SIGINT, signal.SIGTERM]:
        loop.add_signal_handler(
            sig, lambda s=sig: asyncio.create_task(shutdown(s, stop_event)), None
        )

    # --- Health endpoint FIRST (breaks FRR ↔ OSPF ↔ routes deadlock) ---
    health_server = await asyncio.start_server(handle_health_check, "0.0.0.0", 8888)
    logger.info("Health check server listening on 0.0.0.0:8888")

    # Populate state.INTERFACES from kernel BEFORE route loading kicks off
    # and BEFORE the WebSocket echo server starts accepting DNS A-records.
    # RouteObject.interfaces reads this snapshot, so it must be non-empty
    # before the first route_spec call inside _load_routes() or on_a_record().
    from ipt_server.tasks.interface_monitor import refresh_interfaces_snapshot

    await refresh_interfaces_snapshot()
    logger.info("Initial interfaces snapshot populated")

    # Now that health endpoint is up and interfaces snapshot is populated,
    # start route loading in background. FRR will see garuda_ipt as healthy,
    # start OSPF, and gateway resolution inside _load_routes will eventually
    # succeed.
    state.ROUTER.start_route_loading()

    # Start the websocket server
    server = await websockets.serve(
        echo, "0.0.0.0", state.CONFIG.ws_port, ping_timeout=30, ping_interval=30
    )

    # Start Interface Monitor
    _spawn_background(monitor_interfaces(stop_event))

    # Start Nexthop Monitor: OSPF-aware single-active-member selection
    _spawn_background(
        monitor_nexthops(
            state.ROUTER._nhg_registry,
            state.ROUTER._member_nhids,
            stop_event,
        )
    )

    # Start DNS backend monitor so DNAT is reconciled when garuda_pdns becomes
    # available (it starts after garuda_ipt due to depends_on ordering).
    _spawn_background(monitor_dns_backend(stop_event))

    # Start Route Health Monitor (no-op if no gated interfaces configured)
    health_source = build_route_health_source(state.CONFIG)
    _spawn_background(monitor_route_health(health_source))

    # Pinning subsystem (opt-in: empty catalog -> no kernel writes, no HTTP).
    if getattr(state.CONFIG, "pinning_egress", None):
        from pinning.bootstrap import setup_pinning  # local import: feature optional

        def _interfaces_view():
            with state.INTERFACES_LOCK:
                return list(state.INTERFACES.keys())

        manager, reconciler, http_task = await setup_pinning(
            cfg=state.CONFIG,
            interfaces_view=_interfaces_view,
            stop_event=stop_event,
        )
        # Retain manager/reconciler/http_task for the process lifetime.
        # state.PINNING_* anchors manager/reconciler via the always-imported
        # ipt_server.state module; http_task additionally goes through the
        # same retention container as the monitor tasks (belt-and-suspenders
        # alongside reconciler._liveness_task's own retention in
        # pinning/bootstrap.py).
        state.PINNING_MANAGER = manager
        state.PINNING_RECONCILER = reconciler
        _retain_task(http_task)
        logger.info(
            "pinning enabled: %d egresses, ttl=%ds, port=%d",
            len(state.CONFIG.pinning_egress),
            state.CONFIG.pinning_ttl,
            state.CONFIG.pinning_api_port,
        )
    else:
        logger.info("pinning disabled (empty pinning_egress catalog)")

    # Initial health check: only replay routes for healthy gated interfaces.
    if health_source is not None:
        initial_health = health_source.get_interface_health(
            state.CONFIG.route_health.interfaces
        )
        state.INTERFACE_HEALTH.update(initial_health)
        for iface, healthy in initial_health.items():
            if not healthy:
                logger.info(
                    f"Startup: gated interface {iface} is unhealthy, skipping route replay"
                )
                state.ROUTER.remove_routes_for_interface(iface)

    try:
        # Instead of just waiting on the event, create a periodic task that checks the event
        # This ensures the event loop keeps running and can process signals
        while not stop_event.is_set():
            # Short sleep to allow other tasks and signal handlers to run
            await asyncio.sleep(0.1)
    finally:
        # Clean up resources
        logger.info("Cleaning up resources...")
        server.close()
        health_server.close()
        await server.wait_closed()
        await health_server.wait_closed()
        # pyroute2 0.9.x sync netlink calls can't run on the main
        # asyncio loop — hop into a worker thread for shutdown cleanup.
        await asyncio.to_thread(clean_pbr)
        if state.ROUTER:
            state.ROUTER.stop()  # Ensure Router's __del__ method is called for cleanup

        logger.info("Server shutdown complete")


if __name__ == "__main__":
    import os

    if srv := os.environ.get("PYDEV"):
        try:
            import pydevd_pycharm

            pydevd_pycharm.settrace(
                srv.split(":")[0],
                port=int(srv.split(":")[1]),
                stdoutToServer=True,
                stderrToServer=True,
                suspend=False,
            )
        except Exception as e:
            logger.exception("Failed to connect to pydevd", exc_info=e)
    main()

# todo: add involved interfaces change monitoring and restart
# todo: add health checks
