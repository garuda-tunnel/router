### FEATURES

```markdown
# Key Features of ipt_server

This document explains the core mechanisms of `ipt_server`, focusing on the `weight` parameter, `domain_route_ttl`, and routing principles. These features enable flexible and dynamic traffic management.

## `weight` Parameter

**Type**: `Optional[int]`  
**Default**: `0`  
**Purpose**: The `weight` parameter determines the priority of a route when multiple routes overlap (e.g., subnets or domains). Higher values indicate higher priority.

### How It Works
- **Conflict Resolution**: When a new route is added (via `Router.add_route()`), the service checks for overlaps in the `IntervalTree`. If an existing route covers the same IP range or domain and has a higher `weight`, the new route is skipped.
- **Behavior**: 
  - Routes with higher `weight` take precedence, even if they are less specific (e.g., a larger subnet).
  - Equal `weight` values do not guarantee a specific outcome; the existing route may persist.

### Example

> **Note:** `weight` is a field of the legacy flat-route format (`BaseRoute`), not of the current grouped
> `RouteActionGroup` format. In the grouped format, rule priority within a group is determined by list
> order, and groups themselves do not overlap by design. The example below uses the old flat format for
> illustration purposes only; new configurations should use the grouped bare-string format.

```yaml
# Legacy flat-route format (not valid in current grouped format):
routes:
  - net: '1.0.0.0/8'
    interface: docker0
    weight: 98
  - net: '1.1.0.0/16'
    interface: docker0
    weight: 97
```
- Traffic to `1.1.0.1` uses the `1.0.0.0/8` route because `weight: 98` > `weight: 97`, despite `1.1.0.0/16` being more specific.

### Notes
- `weight` is only relevant for overlapping routes in the legacy flat-route format. Non-overlapping routes are unaffected.
- In the current grouped format, use separate `RouteActionGroup` entries to control priority.

## `domain_route_ttl` Parameter

**Type**: `int`  
**Default**: `300` (in `MySettings`)  
**Purpose**: `domain_route_ttl` sets the default time-to-live (TTL) in seconds for routes created from DNS A-records (domain-based routes) when no specific `route_ttl` is defined.

### How It Works
- **Route Creation**: When a WebSocket message (e.g., `{"query": "example.com", "content": "93.184.216.34"}`) triggers a route via `Router.on_a_record()`, the TTL is determined as follows:
  1. If the `domain` route specifies `route_ttl`, it takes precedence.
  2. Otherwise, `domain_route_ttl` is used.
  3. The final TTL is the minimum of `route_ttl`, `domain_route_ttl`, and the A-record's `ttl` (if provided).
- **Expiration**: Routes with expired TTL are removed by `Router._cleanup_expired_routes()` every 10 seconds, unless active `conntrack` entries exist (checked if `clean_conntrack: True`).
- **Updates**: If a route already exists, its TTL is refreshed to the maximum of its current TTL and the new value.

### Example
```yaml
routes:
  - route:
      - gw: "10.9.19.2"
      - dev: "border"
    rules:
      - '.*\.ru'
  - route:
      - dev: "border"
    rules:
      - '.*\.chatgpt\.com'
    route_ttl: 10
domain_route_ttl: 100
```
- `example.ru` route lasts 100 seconds (from `domain_route_ttl`).
- `chatgpt.com` route lasts 10 seconds (from `route_ttl`), or 5 seconds if the A-record has `ttl: 5`.

### WebSocket Example
Request:
```json
{"query": "chatgpt.com", "content": "20.236.44.162", "type": 1, "ttl": 8}
```
Response:
```json
{"ttl": 8}
```
- Route TTL is 8 seconds (minimum of `route_ttl: 10`, `domain_route_ttl: 100`, `record.ttl: 8`).

### Notes
- TTL ensures temporary routes (e.g., from DNS) don’t persist indefinitely.
- `clean_conntrack` affects whether expired routes with active connections are preserved.

## Routing Principles

### Route Management
- **Static Routes**: `country` and `net` routes are loaded at startup from `settings.yaml`.
- **Dynamic Routes**: `domain` routes are added in real-time via WebSocket A-records.
- **Storage**: Routes are stored in an `IntervalTree` for efficient overlap detection.

### Conflict Resolution
- Overlapping routes are resolved by comparing `weight`. Higher `weight` wins.
- Example: A `domain` route for `.*\.com` (`weight: 100`) is overridden by `.*\.google.com` (`weight: 200`) for Google domains.

### PBR Integration
- Traffic is filtered using `nftables` with a `fwmark` (e.g., `200`) and routed via a custom table (e.g., `200`).
- Interfaces like `wg-firezone` or `docker0` are specified in the config.

### Cleanup
- Expired routes are removed every 10 seconds, preserving active connections if `clean_conntrack: False`.

This combination of `weight`, TTL, and PBR enables precise and dynamic control over network traffic.

## Per-Source-IP Egress Pinning

- **Per-source-IP egress pinning** — opt-in feature; users select an
  egress through a one-page UI or GET-based API. See `configuration.md`
  -> "Egress pinning".
- **Self-service portal anchor** — nft REDIRECT intercepts packets destined for `1.1.1.1:1111` on the backbone interface and redirects them to the local pinning API; no DNS or separate service required.

## OSPF-Driven NHG Failover

### Overview

Each route action group is realised as a single kernel nexthop group (nhg,
proto 199). All kernel routes in that group reference the group's nhid, so a
single `ip nexthop replace` call atomically reroutes all matching traffic.

### Kernel Nexthop Groups

- One nhg per action group; one nhid per member (`gw=` or `dev=`).
- The nhg always has exactly one active member — the highest-priority member
  whose liveness check currently passes.
- Member priority is determined by order in the `route:` list (first = highest).

### Automatic Failover

When the outer_pt gateway loses its OSPF adjacency:

1. FRR withdraws the external `0.0.0.0/0` route from the OSPF topology.
2. `nexthop_monitor` queries `vtysh "show ip ospf route json"` every 5 s and
   detects the route's absence.
3. The `gw=` member is treated as dead (fail-closed after 3 consecutive
   failures).
4. `nexthop_monitor` calls `ip nexthop replace` to make the `dev=` border
   member the active member of the nhg.
5. All routes referencing the nhid immediately forward through the border
   interface — no per-route surgery, no restart required.

### Recovery

When the OSPF adjacency recovers:

1. FRR re-advertises the external `0.0.0.0/0` route.
2. `nexthop_monitor` detects the route's reappearance; the failure counter
   resets to 0 on the first successful probe.
3. `nexthop_monitor` calls `ip nexthop replace` to restore the `gw=` member
   as the active member, switching the group back to the primary path.

### Latency

Detection window: up to 5 s (one poll interval). Switchover: one
`ip nexthop replace` call — effectively instantaneous from the kernel's
perspective. Total traffic interruption is bounded by the detection window plus
kernel nexthop propagation time (typically sub-millisecond).
```

---

### Usage instructions
1. Save `README.md` as the main file in the project root.
2. Save `CONFIGURATION.md` and `FEATURES.md` in the root or in a `docs/` folder, adding links in `README.md` (they are already included).
3. Make sure Mermaid is supported in your Markdown rendering system (e.g., GitHub supports it).

If anything needs to be improved (more examples, different style or structure), let us know!