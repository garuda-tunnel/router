"""Helpers for normalizing IPT route configuration entries."""


def normalize_route_entry(route_data):
    normalized = dict(route_data)

    # If input already has a `route` key, validate and return early
    if "route" in normalized:
        route_val = normalized["route"]
        if isinstance(route_val, str):
            raise ValueError(
                "string route syntax is not supported; "
                "use object-form route: {gw: <ip>} or route: {dev: <name>}"
            )
        if isinstance(route_val, dict):
            if route_val.get("gw") and route_val.get("dev"):
                raise ValueError("route cannot have both gw and dev")
            return normalized
        if isinstance(route_val, list):
            for i, member in enumerate(route_val):
                if not isinstance(member, dict):
                    raise ValueError(
                        f"route[{i}] must be a dict with 'gw' or 'dev', got {type(member).__name__}"
                    )
                has_gw = bool(member.get("gw"))
                has_dev = bool(member.get("dev"))
                if has_gw and has_dev:
                    raise ValueError(f"route[{i}] cannot have both gw and dev")
                if not has_gw and not has_dev:
                    raise ValueError(f"route[{i}] must have gw or dev")
            return normalized

    # No `route` key — reject legacy flat keys
    for legacy_key in ("next_hop", "interface", "gw"):
        if legacy_key in normalized:
            raise ValueError(
                f"legacy route key '{legacy_key}' is not allowed in the delivery contract; "
                f"use route: {{gw: <ip>}} or route: {{dev: <name>}}"
            )

    raise ValueError(
        "route entry must contain a 'route' key with {gw: <ip>} or {dev: <name>}"
    )
