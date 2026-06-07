# ipt_server Monitor Tasks

This directory contains long-running monitor tasks that reconcile runtime drift
around `ipt_server` without restarting the service.

These tasks are peers, not layers over each other. Each one owns one specific
drift class and updates shared runtime state through `ipt_server.state` and
`state.ROUTER`.

## Component Map

### `route_health_monitor.py`

`route_health_monitor.py` owns gated interface health transitions.

Problem it solves:

- some route groups are only valid while a specific FRR/OSPF adjacency is in a
  healthy state
- interface presence alone is not enough; the interface can exist while the
  routing adjacency behind it is degraded
- without a dedicated health monitor, `ipt_server` would keep replaying or
  retaining routes for an interface that should currently be considered unsafe

What it does:

1. polls a `HealthSource` implementation for the configured gated interfaces
2. compares the new health snapshot with the previous one
3. updates `state.INTERFACE_HEALTH`
4. replays routes when an interface recovers
5. removes routes when an interface degrades

How it is used:

- `ipt_server.main` builds a health source through
  `build_route_health_source()`
- when `config.route_health.interfaces` is empty, the task is a no-op by design
- when route health is configured, the task runs forever as an `asyncio` task
  next to the other monitors

Operational boundary:

- this monitor does not discover interfaces
- this monitor does not resolve gateway drift
- this monitor only answers the question: should routes tied to this gated
  interface be present right now?

Current health source:

- `route_health.py::FrrVtyshOspfHealthSource` reads FRR OSPF neighbor state via
  `vtysh` and reports per-interface health

### `nexthop_monitor.py`

`nexthop_monitor.py` owns OSPF-aware single-active-member selection for kernel
nexthop groups (nhg).

Problem it solves:

- each route action group maps to a kernel nhg with an ordered list of members
  (`gw=` gateway members and `dev=` border-interface members)
- the nhg must keep exactly one active member — the highest-priority member
  whose liveness check currently passes
- without a dedicated monitor, a `gw=` member that loses its OSPF-backed
  default route would silently blackhole traffic instead of falling back to the
  `dev=` border member

What it does:

1. waits until `state.ROUTER._routes_loaded` is set
2. for each nhg in `nhg_registry`, evaluates member liveness in priority order:
   - `gw=` member: queries `vtysh "show ip ospf route json"` for the presence
     of an external `0.0.0.0/0` route; absent → member considered dead
   - `dev=` member: confirms the named interface is present in the kernel
3. determines the highest-priority alive member
4. if the active member has changed, calls `ip nexthop replace` to atomically
   switch the nhg to the new active member
5. repeats every 5 seconds

Key functions:

- `_probe_gw_alive(gw)` — runs the vtysh OSPF query and parses the JSON output
- `_probe_dev_alive(dev)` — checks kernel interface list for the named device
- `_tick(nhg_registry, member_nhids)` — single evaluation pass over all nhgs;
  calls `ip nexthop replace` where the active member changed
- `monitor_nexthops(nhg_registry, member_nhids, stop_event)` — main loop,
  runs `_tick` every 5 s until `stop_event` is set

Failure model:

- 3 consecutive probe failures → fail-closed: the member is treated as dead
  regardless of the raw probe result
- the consecutive-failure counter resets to 0 on the first successful probe

How it is used:

- `ipt_server.main` starts it as a sibling background task after the nhg
  objects have been created at startup
- it runs forever as an `asyncio` task next to the other monitors

Operational boundary:

- this monitor does not manage route installation or removal
- this monitor does not own interface health gating (see `route_health_monitor`)
- this monitor only switches which nhg member is active based on liveness

## Which Monitor To Look At

Use this rule of thumb when debugging:

- route disappeared or reappeared when OSPF neighbor health changed:
  `route_health_monitor.py`
- nhg active member switched unexpectedly or did not switch after OSPF/default
  route churn: `nexthop_monitor.py`
- interface attach/detach or link-state drift: `interface_monitor.py`
- DNS backend readiness and DNAT drift: `dns_backend_monitor.py`

## Relationship To Other Docs

- [Gateway drift design](../../../../../docs/superpowers/specs/2026-04-10-ipt-server-gateway-watcher-design.md)
- [Interface snapshot ownership design](../../../../../docs/superpowers/specs/2026-04-11-ipt-server-interfaces-cache-ownership-design.md)
- [ipt_server service overview](../readme.md)
- [ipt_server configuration guide](../configuration.md)

## Key Code Entry Points

- [Route health monitor](route_health_monitor.py)
- [Nexthop monitor](nexthop_monitor.py)
- [Route health source](../route_health.py)
- [ipt_server entrypoint](../ipt_server/main.py)

These docs explain why the monitors exist. This README explains which runtime
responsibility each monitor owns today.
