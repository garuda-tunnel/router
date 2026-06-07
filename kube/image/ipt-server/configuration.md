# Configuration Guide for ipt_server

> **Breaking change (2026-04):** Rule format changed. The old dict entries `{domain: X}`, `{net: X}`, `{country: X}` are no longer accepted. Write bare strings; the type is inferred automatically. See "Rule Type Inference" below.

The `ipt_server` service is configured via a YAML file (default: `settings.yaml`), parsed using the Pydantic `MySettings` model. This file defines routing rules, interfaces, and service settings.

## Configuration Structure

```yaml
routes:
  - route:
      - gw: <gateway IP>          # ordered members (highest priority first)
      - dev: <interface name>     # fallback member (border interface)
    rules:
      - <CIDR | IPv4 | ISO code | regex>
      - <CIDR | IPv4 | ISO code | regex>
  - route:
      - dev: <interface name>
    rules:
      - <CIDR | IPv4 | ISO code | regex>
interfaces:
  - <interface name>
pbr_mark: <PBR mark value>
dns_mark: <DNS intercept mark value>
table: <routing table number>
domain_route_ttl: <default TTL for domain routes>
ws_port: <WebSocket port>
db: <path to IP database>
clean_conntrack: <true/false>
```

## Example Configuration

```yaml
routes:
  - route:
      - gw: "10.9.19.2"      # primary: WireGuard gateway (OSPF-alive check)
      - dev: "border"          # fallback: border interface
    rules:
      - ".*"
      - "0.0.0.0/0"
  - route:
      - dev: "border"
    rules:
      - "RU"
      - '.*\.ru'
interfaces:
  - wg-firezone
  - wg-firezone_1
pbr_mark: 200
dns_mark: 201
table: 200
domain_route_ttl: 100
```

## Field Descriptions

### Top-Level Fields

| Field             | Type            | Description                                                                                  | Default           | Example                     |
|-------------------|-----------------|----------------------------------------------------------------------------------------------|-------------------|-----------------------------|
| `routes`          | `List[RouteActionGroup]` | List of route action groups. Required.                                              | N/A               | See below                   |
| `interfaces`      | `List[str]`     | List of network interfaces available for routing.                                            | `['wg-firezone']` | `['wg-firezone', 'eth0']`   |
| `pbr_mark`        | `int`           | Firewall mark for PBR (used with NFTables).                                                  | `512`             | `200`                       |
| `dns_mark`        | `int`           | Firewall mark for intercepted DNS; keeps DNS packets out of the geo-routing PBR table after DNAT. | `513`             | `201`                       |
| `table`           | `int`           | Routing table number in the Linux kernel.                                                    | `200`             | `200`                       |
| `domain_route_ttl`| `int`           | Default TTL (in seconds) for domain-based routes if not specified.                           | `300`             | `100`                       |
| `ws_port`         | `int`           | WebSocket server port for DNS A-record updates.                                              | `8765`            | `8080`                      |
| `db`              | `str`           | Path to the IP database (used for country routes).                                           | `'ipt.db'`        | `'/tmp/ip_db.duckdb'`       |
| `clean_conntrack` | `bool`          | Enable/disable cleanup of expired `conntrack` entries when removing routes.                  | `False`           | `True`                      |

### Route Entry Fields

Each entry in `routes` has two sub-keys:

| Sub-key | Type        | Description                                                                 |
|---------|-------------|-----------------------------------------------------------------------------|
| `route` | `list[dict]`| Ordered list of member actions. Each member is either `gw: <IP>` (WireGuard/OSPF gateway) or `dev: <iface>` (border interface). Members are tried highest-priority-first; the first alive member becomes the nhg's active member. |
| `rules` | `list[str]` | List of match rules as bare strings. Type is inferred: CIDR/IPv4 -> net, ISO-3166-1 alpha-2 (case-insensitive) -> country, otherwise -> domain regex. See "Rule Type Inference" below. |

### Route Member Types

| Member key | Description                                                                                         |
|------------|-----------------------------------------------------------------------------------------------------|
| `gw`       | Gateway IP. Liveness determined by OSPF: `nexthop_monitor` checks for an external `0.0.0.0/0` route via `vtysh`. |
| `dev`      | Interface name. Liveness determined by kernel interface presence.                                   |

## Rule Type Inference

Each string in a `rules:` list is passed through a chain of resolvers. The first resolver to claim the value produces the typed rule.

| Resolver        | Triggers on                                                                                                      | Produces       | Examples                              |
|-----------------|------------------------------------------------------------------------------------------------------------------|----------------|---------------------------------------|
| NetResolver     | Contains `/` and parses as `ipaddress.ip_network(..., strict=False)`; **or** three dots and parses as IPv4 address. Bare IPv4 is promoted to `/32`. | `NetRule`      | `"1.0.0.0/8"`, `"8.8.8.8"`            |
| CountryResolver | Length 2, all alphabetic, and `pycountry.countries.get(alpha_2=value.upper())` returns a country. Case-insensitive. | `CountryRule`  | `"RU"`, `"am"`, `"Am"`                |
| RegexResolver   | Terminal: value must compile as a Python regex. Raises `ValueError` if it does not.                              | `DomainRule`   | `".*\\.ru"`, `".*"`, `"facebook\\.com"` |

### Match semantics

- **Domain regex matching uses `re.fullmatch`.** A regex must cover the entire queried name, not just a prefix. `.*\\.ru` matches `example.ru` but not `example.ru.com`.
- **Country codes are case-insensitive on input** and normalized to uppercase internally.
- **Bare IPv4 addresses are promoted to `/32`**, so `"8.8.8.8"` and `"8.8.8.8/32"` are equivalent in the config.

### Pitfalls

- A two-letter lowercase string that also happens to be a valid ISO code resolves to a country, not a regex. If you need a two-letter regex literal (e.g. to match exactly the string `"am"`), write it with regex metachars or a pattern longer than 2 chars: `"am\\b"`, `".am."`, `"(am)"`. Avoid bare two-letter values that look like ISO codes unless you mean a country.
- `"XX"` and other well-formed but invalid ISO codes fall through to RegexResolver and become `DomainRule`s that match only the literal string. That is almost never useful; prefer explicit regex metachars if you intend a dead rule.
- IPv6 rules are not supported. An IPv6 string like `"2001:db8::1"` falls through to RegexResolver and becomes a `DomainRule` — probably not what you want.

## Additional Examples

Primary WireGuard gateway, border fallback; RU traffic pinned to border:

```yaml
routes:
  - route:
      - gw: "10.9.19.2"
      - dev: "border"
    rules:
      - ".*"
      - "0.0.0.0/0"
      - "1.0.0.0/8"
  - route:
      - dev: "border"
    rules:
      - "RU"
      - '.*\.ru'
      - '.*\.google\.com'
interfaces:
  - wg-firezone
pbr_mark: 200
dns_mark: 201
table: 200
```

For more on `route_ttl`, NHG failover, and routing behavior, see [FEATURES.md](./featues.md).

## Egress pinning

Optional. Lets a transit-network user pin all of their traffic to a chosen
egress (overriding the default geo-PBR Auto behaviour) via a one-page web
UI and a small GET-based REST API.

### Settings

```yaml
ipt_pinning_egress:
  hub:
    gw: 192.0.2.1
  usa:
    dev: border
ipt_pinning_ttl: 86400        # seconds; default 24h
ipt_pinning_api_port: 80      # inside the container
```

Keys are slug-style identifiers shown to the user (`^[a-z0-9_-]+$`). The
key `auto` is reserved. An empty map disables the subsystem entirely:
no aiohttp listener, no kernel writes, no `ip rule` entries.

### Endpoints

| Verb | Path | Purpose |
|------|------|---------|
| GET  | `/`                          | HTML UI (rendered server-side) |
| GET  | `/api/egresses`              | JSON: list of egress keys |
| GET  | `/api/pin`                   | JSON: current pin for the calling source IP |
| GET  | `/api/pin/set?egress=<key>`  | Set/refresh a pin |
| GET  | `/api/pin/clear`             | Clear the pin (back to Auto) |

Identity is the TCP source address of the request. Client-supplied
`saddr=` query parameters and `X-Forwarded-For` headers are ignored.

Pass `&return=html` to mutating endpoints to receive a 303 redirect to
`/` instead of JSON; the UI uses this to chain navigation without JS.

### Script example

```bash
curl http://<ipt-server>/api/egresses
curl "http://<ipt-server>/api/pin/set?egress=hub"
curl http://<ipt-server>/api/pin
curl http://<ipt-server>/api/pin/clear
```

### Lifecycle

Pinnings live in memory; container restarts wipe them. A pin auto-expires
after `ipt_pinning_ttl` seconds; visiting `/api/pin/set` (or the UI link)
refreshes the timer. Sweeping runs every 60s.

If a pinned egress goes dead (OSPF probe fails, or `dev` interface is
absent), the per-egress routing table installs `blackhole default` and
the pinned traffic is dropped — there is no fallback to Auto. The user
must switch back manually.

### Self-service portal anchor

| Field | Default | Description |
|-------|---------|-------------|
| `pinning_portal_anchor_addr` | `1.1.1.1` | Public IP used as the nft REDIRECT anchor for the self-service portal. Traffic to this address on `pinning_portal_anchor_port` is intercepted in the `prerouting` chain and redirected to the local pinning API listener. The Cloudflare anycast address is used because it is guaranteed to route through the full-tunnel WireGuard interface; tcp/1111 does not collide with any Cloudflare DNS service port. |
| `pinning_portal_anchor_port` | `1111` | TCP port matched by the REDIRECT rule. |
