"""Routing engine for IPT server with TTL-aware route and conntrack management."""

from typing import Optional
from pyroute2 import Conntrack
from intervaltree import IntervalTree, Interval
from dns_records import ARecord
from ipdb.query import IPDatabase

import queue
import re
import subprocess
import logging
import time
import Config
from Config import NhgDescriptor
from route import RouteObject
import nexthop
import threading
import copy
from lib import timeit

import ipaddress

logger = logging.getLogger(__name__)


class Router:
    def _cleanup_expired_routes(self):
        """Periodically remove expired routes that have no active conntrack entries."""
        while not self._shutdown_event.is_set():
            self._shutdown_event.wait(timeout=self._cleanup_interval)
            if self._shutdown_event.is_set():
                break
            with self._routes_lock:
                expired_intervals = [
                    interval for interval in self._route_tree if interval.data.expired
                ]

                if not expired_intervals:
                    continue

                # Track which expired intervals have active connections
                intervals_to_keep = set()

                try:
                    for entry in self._conntrack.dump_entries():
                        try:
                            src_addr = int(ipaddress.ip_address(entry.tuple_orig.saddr))
                            dst_addr = int(ipaddress.ip_address(entry.tuple_orig.daddr))

                            # Check each expired interval to see if it contains this connection
                            for interval in expired_intervals:
                                if (
                                    interval.begin <= src_addr < interval.end
                                    or interval.begin <= dst_addr < interval.end
                                ):
                                    intervals_to_keep.add(interval)
                                    # Once we know we're keeping this interval, no need to check it again
                                    break
                        except Exception as e:
                            logger.debug(f"Error processing conntrack entry: {e}")
                except Exception as e:
                    logger.warning(f"Error accessing conntrack entries: {e}")

                # Process each expired interval
                for interval in expired_intervals:
                    if interval not in intervals_to_keep:
                        # No active connections, remove the route
                        self._route_tree.remove(interval)
                        route_spec = interval.data.route_spec
                        self._route_queue.put(("del", route_spec))
                        logger.info(f"Removed expired route: {interval.data.net}")

    def remove_conntrack_entries_for_destination(self, destination: IntervalTree):
        """
        Remove conntrack entries for a given destination IP or subnet using ct.dump_entries().

        Args:
            destination (str): Destination IP or subnet (e.g., "192.168.1.0/24" or "8.8.8.8")
        """
        #        return

        if not self._cfg.clean_conntrack:
            return
        try:
            # pyroute2 0.9.x ``_generate_with_cleanup`` closes the
            # thread-local event loop when the dump generator exits.
            # Mixing ``dump_entries`` iteration with nested sync
            # ``entry("del", ...)`` calls trips "Event loop is closed":
            # the inner call's cleanup tears down the loop the outer
            # generator is still using.  Materialize candidate tuples
            # first, then delete.
            tuples_to_delete = []
            for entry in self._conntrack.dump_entries():
                try:
                    src_addr = int(ipaddress.ip_address(entry.tuple_orig.saddr))
                    dst_addr = int(ipaddress.ip_address(entry.tuple_orig.daddr))
                    if destination[src_addr] or destination[dst_addr]:
                        tuples_to_delete.append(entry.tuple_orig)
                except Exception as e:
                    logger.warning(
                        f"Error processing conntrack entry: {entry.tuple_orig} {e}"
                    )

            deleted_count = 0
            for tuple_orig in tuples_to_delete:
                try:
                    logger.debug(f"Delete conntrack entry {tuple_orig}")
                    self._conntrack.entry("del", tuple_orig=tuple_orig)
                    deleted_count += 1
                    logger.info(
                        f"Deleted conntrack entry: src={tuple_orig.saddr}, dst={tuple_orig.daddr}"
                    )
                except Exception as e:
                    logger.warning(f"Error deleting conntrack entry: {tuple_orig} {e}")
            logger.info(f"Deleted {deleted_count} conntrack entries.")

        except Exception as e:
            logger.warning(f"Error processing conntrack entries: {e}")
            raise

    def _ip_batch(self, lines: list[str]) -> None:
        """Send route commands to kernel via ip -batch stdin."""
        if not lines:
            return
        batch_input = "\n".join(lines) + "\n"
        try:
            result = subprocess.run(
                ["ip", "-batch", "-"],
                input=batch_input,
                text=True,
                capture_output=True,
            )
            if result.returncode != 0:
                logger.error(
                    "ip -batch failed (rc=%d): %s",
                    result.returncode,
                    result.stderr.strip(),
                )
        except Exception as exc:
            logger.error("ip -batch error: %s", exc)

    def _apply_route_immediately(self, route: Config.RouteObject) -> None:
        spec = route.route_spec.copy()
        spec.update({"table": self._cfg.table})
        dst = spec["dst"]
        dst_len = spec["dst_len"]
        nhid = spec["nhid"]
        table = spec["table"]
        self._ip_batch([f"route replace {dst}/{dst_len} nhid {nhid} table {table}"])
        logger.debug("Applied route immediately: %s nhid=%s", route, nhid)

    def add_route(
        self, route: Config.RouteObject, immediate=False
    ) -> Optional[RouteObject]:
        """Insert or refresh a route, optionally applying it to kernel immediately."""
        route_to_apply = None
        result = None
        outcome = "unknown"

        with self._routes_lock:
            net_start = route.net_start
            net_end = route.net_end

            intervals = self._route_tree[net_start : net_end + 1]
            for interval in intervals:
                if (
                    interval.begin == net_start
                    and interval.end == net_end + 1
                    and interval.data.metric == route.metric
                    and interval.data.weight == route.weight
                    and interval.data.dev == route.dev
                    and interval.data.gw == route.gw
                    and interval.data.nhid == route.nhid
                ):
                    interval.data.reset_expiration(route.ttl)
                    result = copy.deepcopy(interval.data)
                    outcome = "refresh"
                    break

            if result is None:
                for interval in intervals:
                    if (
                        interval.begin <= net_start
                        and net_end < interval.end
                        and interval.end - interval.begin > net_end - net_start + 1
                        and interval.data.weight > route.weight
                    ):
                        logger.info(
                            f"Skipping route for {route.net} due to overlapping route with less specific prefix and higher weight"
                        )
                        result = copy.deepcopy(interval.data)
                        outcome = "suppressed_overlapping"
                        break

            if result is None:
                route.reset_expiration(route.ttl)
                new_interval = Interval(net_start, net_end + 1, route)
                self._route_tree.add(new_interval)
                logger.debug(f"Adding new route: {route}")
                result = copy.deepcopy(new_interval.data)
                outcome = "inserted"
                if immediate:
                    route_to_apply = route  # _apply_route_immediately is read-only
                else:
                    self._route_queue.put(("replace", route.route_spec))

        if route_to_apply is not None:
            self._apply_route_immediately(route_to_apply)

        logger.info(
            "Route add outcome: net=%s outcome=%s immediate=%s dev=%s gw=%s nhid=%s ttl=%s",
            route.net,
            outcome,
            immediate,
            route.dev,
            route.gw,
            route.nhid,
            route.ttl,
        )

        return result

    def replay_routes(self):
        """Replay all routes from _route_tree directly into kernel after table flush.

        Uses ip -batch subprocess to bypass the queue (which may be blocked).
        The interface monitor is the single owner of the ifname->ifindex
        snapshot and updates it within 5s of a kernel change.
        """
        count = 0
        lines = []
        with self._routes_lock:
            for interval in self._route_tree:
                route = interval.data
                if route.nhid is None and route.dev is None:
                    continue
                try:
                    spec = route.route_spec
                except RuntimeError:
                    continue
                dst = spec["dst"]
                dst_len = spec["dst_len"]
                nhid = spec["nhid"]
                lines.append(
                    f"route replace {dst}/{dst_len} nhid {nhid} table {self._cfg.table}"
                )
                count += 1
        self._ip_batch(lines)
        logger.info(f"Replayed {count} routes from route tree to kernel")

    def replay_routes_for_interface(self, iface: str):
        """Replay only routes for *iface* from _route_tree into kernel table.

        Mirrors the logic of replay_routes() but filters by route.dev.
        The interface monitor updates the ifname->ifindex snapshot
        automatically.
        """
        count = 0
        lines = []
        with self._routes_lock:
            for interval in self._route_tree:
                route = interval.data
                if route.dev != iface:
                    continue
                spec = route.route_spec
                dst = spec["dst"]
                dst_len = spec["dst_len"]
                nhid = spec["nhid"]
                lines.append(
                    f"route replace {dst}/{dst_len} nhid {nhid} table {self._cfg.table}"
                )
                count += 1
        self._ip_batch(lines)
        logger.info(
            f"Replayed {count} routes for interface {iface} from route tree to kernel"
        )

    def remove_routes_for_interface(self, iface: str):
        """Remove only routes for *iface* from kernel routing table.

        Routes for other interfaces are not touched.  Errors for individual
        deletions (e.g. route already absent from kernel) are logged and
        swallowed so one missing route does not abort the rest.
        """
        count = 0
        lines = []
        with self._routes_lock:
            for interval in self._route_tree:
                route = interval.data
                if route.dev != iface:
                    continue
                try:
                    spec = route.route_spec
                except RuntimeError:
                    continue
                dst = spec["dst"]
                dst_len = spec["dst_len"]
                lines.append(f"route del {dst}/{dst_len} table {self._cfg.table}")
                count += 1
        self._ip_batch(lines)
        logger.info(f"Removed {count} routes for interface {iface} from kernel")

    def _alloc_nhid(self) -> int:
        """Return the next available nexthop ID and advance the counter."""
        nhid = self._nhid_counter
        self._nhid_counter += 1
        return nhid

    def setup_nexthop_group(self) -> None:
        """Create kernel nexthop objects for every RouteActionGroup in config.

        Must be called before any route installation.  Flushes all owned
        nexthop objects first (fail-hard on error), then allocates member
        and group nhids for each unique NhgDescriptor.
        """
        from ipt_server import state

        nexthop.flush_owned()  # raises RuntimeError on failure — intentional

        # Collect unique descriptors
        descriptors: list[NhgDescriptor] = []
        seen: set[NhgDescriptor] = set()
        for conf_route in self._cfg.routes:
            if not isinstance(conf_route, Config.RouteActionGroup):
                continue
            desc = conf_route.nhg_descriptor
            if desc not in seen:
                seen.add(desc)
                descriptors.append(desc)

        # Allocate member nhids and create member nexthop objects
        member_alive: dict[tuple, bool] = {}
        for desc in descriptors:
            for member in desc.members:
                key = (member.gw, member.dev)
                if key in self._member_nhids:
                    continue
                nhid = self._alloc_nhid()
                self._member_nhids[key] = nhid

                if member.gw is not None:
                    # Gateway members always start as blackhole; the
                    # nexthop_monitor first_tick will reconcile them
                    # against the live OSPF LSDB once available.
                    nexthop.create_blackhole(nhid)
                    member_alive[key] = False
                elif member.dev is not None:
                    with state.INTERFACES_LOCK:
                        iface_present = member.dev in state.INTERFACES
                    if iface_present:
                        nexthop.create_device(nhid, member.dev)
                        member_alive[key] = True
                    else:
                        nexthop.create_blackhole(nhid)
                        member_alive[key] = False

        # Create group nexthop objects
        for desc in descriptors:
            group_nhid = self._alloc_nhid()
            # Find highest-priority alive member (first in list)
            active_nhid = None
            for member in desc.members:
                key = (member.gw, member.dev)
                if member_alive.get(key, False):
                    active_nhid = self._member_nhids[key]
                    break
            if active_nhid is None:
                # All dead — use first member (blackhole)
                first_key = (desc.members[0].gw, desc.members[0].dev)
                active_nhid = self._member_nhids[first_key]
            nexthop.create_group(group_nhid, active_nhid)
            self._nhg_registry[desc] = group_nhid
            logger.info(
                "NHG group nhid=%d active_member_nhid=%d descriptor=%s",
                group_nhid,
                active_nhid,
                desc,
            )

    @timeit
    def _load_routes(self):
        """Load routes from config into runtime route tree using nhg registry."""
        self.setup_nexthop_group()

        for conf_route in self._cfg.routes:
            if not isinstance(conf_route, Config.RouteActionGroup):
                if not isinstance(
                    conf_route,
                    (Config.CountryRoute, Config.DomainRoute, Config.NetRoute),
                ):
                    logger.warning(
                        "Skipping unknown route type: %s", type(conf_route).__name__
                    )
                continue
            nhid = self._nhg_registry.get(conf_route.nhg_descriptor)
            if nhid is None:
                logger.error(
                    "No nhid for descriptor %s, skipping", conf_route.nhg_descriptor
                )
                continue
            for rule in conf_route.rules:
                if isinstance(rule, Config.DomainRule):
                    continue  # handled by on_a_record
                elif isinstance(rule, Config.NetRule):
                    net = ipaddress.IPv4Network(rule.net, strict=False)
                    r = RouteObject(net=net, nhid=nhid, metric=200)
                    self.add_route(r)
                elif isinstance(rule, Config.CountryRule):
                    for net in self._ipdb[rule.country]:
                        r = RouteObject(
                            net=ipaddress.IPv4Network(net, strict=False),
                            nhid=nhid,
                            metric=200,
                        )
                        self.add_route(r)
        logger.info("Routes loaded via nhg")

    @timeit
    def __init__(self, app_config: Config.MySettings, ipdb: IPDatabase):
        """Initialize router workers, queue, and preloaded route state.

        Route loading is deferred to a background thread via
        ``start_route_loading()`` so that the health endpoint can start
        immediately and break the FRR ↔ health ↔ OSPF deadlock.
        """
        self._cfg = app_config
        self._ipdb = ipdb
        self._shutdown_event = threading.Event()
        self._routes_loaded = threading.Event()

        # Nexthop group registry — written once by setup_nexthop_group() in the
        # background loader thread, then read-only from on_a_record() in the
        # event loop. CPython GIL makes dict.get() safe without a lock.
        self._nhg_registry: dict[NhgDescriptor, int] = {}
        self._member_nhids: dict[tuple, int] = {}
        self._nhid_counter: int = 1

        self._routes_lock = threading.Lock()
        # Create a synchronized queue for route commands
        self._route_queue = queue.Queue()
        self._conntrack = Conntrack()
        # Start the route command processing thread
        self._route_thread = threading.Thread(
            target=self._process_route_commands_iproute2, daemon=True
        )
        self._route_thread.start()

        # Start the cleanup thread
        self._cleanup_interval = 10
        self._cleanup_thread = threading.Thread(
            target=self._cleanup_expired_routes, daemon=True
        )
        self._cleanup_thread.start()
        self._route_tree = IntervalTree()
        # NOTE: _load_routes() is NOT called here.  Call start_route_loading()
        # after the health endpoint is up.

    def start_route_loading(self):
        """Kick off route loading in a background thread.

        The health endpoint must be listening *before* this is called so
        that FRR can start (depends_on: service_healthy) and OSPF can
        converge — which route resolution depends on.
        """

        def _bg_load():
            try:
                self._load_routes()
            except Exception as e:
                logger.error(f"Background route loading failed: {e}")
            finally:
                self._routes_loaded.set()

        t = threading.Thread(target=_bg_load, daemon=True, name="route-loader")
        t.start()
        logger.info("Route loading started in background")

    def __repr__(self):
        """Return concise debug representation with configured interfaces."""
        return f"Router({self._cfg.interfaces})"

    def on_a_record(self, record: ARecord) -> dict:
        """Resolve routing decision for DNS A record and return response TTL payload."""
        started = time.monotonic()
        selected_route: Optional[RouteObject] = None
        match_elapsed = 0.0
        add_elapsed = 0.0
        ttls = []
        ttls.append(record.ttl)

        # Collect all matching RouteActionGroup entries with DomainRule matches
        candidates = []
        for index, conf_route in enumerate(self._cfg.routes):
            if not isinstance(conf_route, Config.RouteActionGroup):
                continue
            for rule in conf_route.rules:
                if isinstance(rule, Config.DomainRule):
                    if re.fullmatch(rule.domain, record.name):
                        candidates.append((index, rule, conf_route))
                        break  # one match per group is enough

        match_elapsed = time.monotonic() - started

        if candidates:
            # Prefer longest regex (most specific)
            max_specificity = max(c[1].specificity for c in candidates)
            candidates = [c for c in candidates if c[1].specificity == max_specificity]
            # Tie: use first config order
            _, matched_rule, matched_group = candidates[0]

            nhid = self._nhg_registry.get(matched_group.nhg_descriptor)
            if nhid is not None:
                ip_network = ipaddress.IPv4Network(f"{record.content}/32", strict=False)
                selected_route = RouteObject(
                    net=ip_network,
                    nhid=nhid,
                    metric=200,
                )

        if selected_route:
            valid_ttls = [ttl for ttl in ttls if ttl is not None and ttl > 0]
            final_ttl = min(valid_ttls) if valid_ttls else None
            selected_route.ttl = final_ttl

            logger.info(
                "Domain route selected: query=%s name=%s ip=%s net=%s nhid=%s ttl=%s",
                record.query,
                record.name,
                record.content,
                selected_route.net,
                selected_route.nhid,
                final_ttl,
            )
            add_started = time.monotonic()
            self.add_route(selected_route, immediate=True)
            add_elapsed = time.monotonic() - add_started
        else:
            ttls.append(self._cfg.domain_route_ttl)
            valid_ttls = [ttl for ttl in ttls if ttl is not None and ttl > 0]
            final_ttl = min(valid_ttls) if valid_ttls else None
            logger.info(
                "Domain route skipped: query=%s name=%s ip=%s — no matching rule",
                record.query,
                record.name,
                record.content,
            )

        total_elapsed = time.monotonic() - started
        logger.info(
            "A-record processing phases: query=%s name=%s ip=%s matched=%s match_s=%.3f add_s=%.3f total_s=%.3f ttl=%s",
            record.query,
            record.name,
            record.content,
            selected_route is not None,
            match_elapsed,
            add_elapsed,
            total_elapsed,
            final_ttl,
        )

        rv = {"ttl": final_ttl}
        return rv

    def stop(self):
        """Signal worker threads to stop and wait for graceful shutdown."""
        self._shutdown_event.set()

        if self._route_thread.is_alive():
            # Wait for the route processing thread to finish
            self._route_thread.join()

        # Wait for the cleanup thread to finish
        if self._cleanup_thread.is_alive():
            self._cleanup_thread.join()

        logger.info("Router shutdown complete")

    def __del__(self):
        """Best-effort destructor wrapper around stop()."""
        self.stop()

    def _process_route_commands_iproute2(self):
        """Processes route commands from the queue using ip -batch subprocess."""
        commit_interval = 0.02
        last_commit_time = time.time()

        while not self._shutdown_event.is_set():
            batch_lines = []
            current_batch_tree = IntervalTree()

            while not self._shutdown_event.is_set():
                try:
                    cmd, route_spec = self._route_queue.get(timeout=0.01)
                    route_spec["table"] = self._cfg.table
                    dst = route_spec.get("dst", "")
                    dst_len = route_spec.get("dst_len", 32)
                    nhid = route_spec.get("nhid")
                    table = route_spec["table"]

                    if cmd == "del":
                        batch_lines.append(f"route del {dst}/{dst_len} table {table}")
                    else:
                        batch_lines.append(
                            f"route {cmd} {dst}/{dst_len} nhid {nhid} table {table}"
                        )

                    if dst and dst_len:
                        network = ipaddress.IPv4Network(f"{dst}/{dst_len}")
                        start = int(network.network_address)
                        end = int(network.broadcast_address) + 1
                        current_batch_tree[start:end] = {"network": network}

                    if time.time() - last_commit_time >= commit_interval:
                        break
                except queue.Empty:
                    break

            if batch_lines:
                logger.info("Process batch %d routes", len(batch_lines))
                self._ip_batch(batch_lines)

            if len(current_batch_tree) > 0:
                self.remove_conntrack_entries_for_destination(current_batch_tree)

            last_commit_time = time.time()
