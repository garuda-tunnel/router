"""IPT server configuration models and route mapping helpers."""

from pydantic import (
    AliasChoices,
    BaseModel,
    ConfigDict,
    Field,
    PrivateAttr,
    ValidationInfo,
    field_validator,
    model_validator,
)
from pydantic_settings import BaseSettings, SettingsConfigDict
from typing import Any, Dict, List, Literal, Optional, Union
import ipaddress
import json
import re
import yaml
from route import RouteObject
from route_config import normalize_route_entry


def _parse_bool(v: str) -> bool:
    if v.lower() == "true":
        return True
    if v.lower() == "false":
        return False
    raise ValueError(f"expected 'true' or 'false', got {v!r}")


class RouteHealthInterface(BaseModel):
    """Health configuration for one target interface."""

    kind: str
    required_state: str = "Full"
    neighbor_interface: Optional[str] = None


class RouteHealthSettings(BaseModel):
    """Container for route health configuration, keyed by target interface name."""

    interfaces: Dict[str, RouteHealthInterface] = {}


class _LegacyRouteAction(BaseModel):
    """Gateway v1 route action: either a next-hop IP (gw) or device name (dev)."""

    gw: Optional[str] = None
    dev: Optional[str] = None

    @model_validator(mode="after")
    def validate_exactly_one(self):
        if self.gw and self.dev:
            raise ValueError("route cannot have both gw and dev")
        if not self.gw and not self.dev:
            raise ValueError("route must have gw or dev")
        return self


class RouteMember(BaseModel):
    """One member of a nexthop group: either a gateway IP or a device name."""

    model_config = ConfigDict(frozen=True)

    gw: Optional[str] = None
    dev: Optional[str] = None

    @model_validator(mode="after")
    def validate_exactly_one(self):
        if self.gw and self.dev:
            raise ValueError("route member cannot have both gw and dev")
        if not self.gw and not self.dev:
            raise ValueError("route member must have gw or dev")
        if self.dev == "_DEFAULT":
            raise ValueError("_DEFAULT sentinel removed; use explicit dev name or gw")
        return self


class NhgDescriptor(BaseModel):
    """Ordered list of nexthop group members. Used as nhg registry key."""

    model_config = ConfigDict(frozen=True)

    members: List[RouteMember]

    def __hash__(self):
        return hash(tuple((m.gw, m.dev) for m in self.members))

    def __eq__(self, other):
        if not isinstance(other, NhgDescriptor):
            return NotImplemented
        return [(m.gw, m.dev) for m in self.members] == [
            (m.gw, m.dev) for m in other.members
        ]


class DomainRule(BaseModel):
    type: Literal["domain"] = "domain"
    domain: str

    @property
    def specificity(self) -> int:
        """Longer regex = more specific = higher priority in tie-breaking."""
        return len(self.domain)


class NetRule(BaseModel):
    type: Literal["net"] = "net"
    net: str  # CIDR string, validated at use time


class CountryRule(BaseModel):
    type: Literal["country"] = "country"
    country: str


Rule = Union[DomainRule, NetRule, CountryRule]


class RouteActionGroup(BaseModel):
    """One route config entry: ordered member list + match rules."""

    route: List[RouteMember] = Field(min_length=1)
    rules: List[Rule] = Field(min_length=1)

    @field_validator("rules", mode="before")
    @classmethod
    def _normalize_rules(cls, v: Any) -> list:
        """Accept a list of bare-string rules; infer type via rule_resolvers.

        Returns a list of Rule instances ready for pydantic to finish validating.
        Rejects the legacy dict-tagged shape with a migration-friendly error.
        """
        # Local import breaks a potential circular import between Config and
        # rule_resolvers (which imports Config for the Rule union).
        from rule_resolvers import resolve_rule  # noqa: PLC0415

        if not isinstance(v, list):
            raise ValueError("rules must be a list")
        _rule_types = {"domain", "net", "country"}
        resolved: list = []
        for i, item in enumerate(v):
            if isinstance(item, (DomainRule, NetRule, CountryRule)):
                resolved.append(item)
                continue
            # Pass through serialized Rule dicts (e.g. from model_dump round-trip).
            # Pydantic discriminates them via the 'type' field after this validator.
            if isinstance(item, dict) and item.get("type") in _rule_types:
                resolved.append(item)
                continue
            if not isinstance(item, str):
                raise ValueError(
                    f"rules[{i}] must be a string; dict format was removed, "
                    f"write bare values (got {type(item).__name__})"
                )
            try:
                resolved.append(resolve_rule(item))
            except ValueError as exc:
                raise ValueError(f"rules[{i}]={item!r}: {exc}") from exc
        return resolved

    @property
    def nhg_descriptor(self) -> NhgDescriptor:
        return NhgDescriptor(members=self.route)


class BaseRoute(BaseSettings):
    route: _LegacyRouteAction
    metric: Optional[int] = 200
    weight: Optional[int] = 0
    model_config = ConfigDict(extra="allow")
    route_ttl: Optional[int] = None
    _routes: List[RouteObject] = PrivateAttr(default_factory=list)

    def add_subnet(self, ip_net: Union[str, ipaddress.IPv4Network]) -> None:
        """Append a subnet-derived RouteObject to this route definition."""
        subnet = ipaddress.IPv4Network(ip_net, strict=False)

        route_spec = RouteObject(
            net=subnet,
            weight=self.weight,
            gw=self.route.gw,
            dev=self.route.dev,
            ttl=self.route_ttl,
            metric=self.metric,
        )

        self._routes.append(route_spec)

    @property
    def routes(self) -> List[RouteObject]:
        """Return generated RouteObject entries for this route config."""
        return self._routes

    def match(self, value: Any) -> bool:
        """
        Match the given value against this route's criteria.
        This method should be implemented by subclasses.
        """
        raise NotImplementedError("Subclasses must implement the match() method.")


class CountryRoute(BaseRoute):
    type: Literal["country"] = "country"
    country: str

    @classmethod
    def from_country(cls, ipdb, country: str, **kwargs) -> "CountryRoute":
        """Construct a CountryRoute and populate subnets via the given IPDB."""
        route = cls(country=country, **kwargs)
        for subnet in ipdb[country]:
            route.add_subnet(subnet)
        return route


class DomainRoute(BaseRoute):
    type: Literal["domain"] = "domain"
    domain: str
    _domain_re: Optional[re.Pattern] = None

    @property
    def domain_re(self) -> re.Pattern:
        """
        Returns a compiled regular expression for the domain pattern.
        """
        if self._domain_re is None:
            self._domain_re = re.compile(self.domain)
        return self._domain_re

    def match(self, value: Any) -> bool:
        """
        Returns True if the input (assumed to be a domain string)
        matches the compiled regular expression.
        """
        if isinstance(value, str):
            return bool(self.domain_re.fullmatch(value))
        return False

    def build_route(self, ip: Union[str, ipaddress.IPv4Address]) -> RouteObject:
        """
        Returns a RouteObject based on the DomainRoute's properties and the given IP address.
        """
        ip_network = ipaddress.IPv4Network(f"{ip}/32", strict=False)
        return RouteObject(
            net=ip_network,
            weight=self.weight,
            metric=self.metric,
            gw=self.route.gw,
            dev=self.route.dev,
            ttl=self.route_ttl,
        )


class NetRoute(BaseRoute):
    type: Literal["net"] = "net"
    net: Union[str, ipaddress.IPv4Network]
    ttl: Optional[int] = None

    @field_validator("net", mode="before")
    def set_net(cls, v) -> ipaddress.IPv4Network:
        """
        Accepts a string and converts it into an IPv4Network object.
        """
        return ipaddress.IPv4Network(v)

    def model_post_init(self, __context: Any) -> None:
        """Populate route objects from explicit net value after model initialization."""
        self.add_subnet(self.net)

    def add_subnet(self, ip_net: Union[str, ipaddress.IPv4Network]) -> None:
        """Append a subnet RouteObject using NetRoute-specific TTL handling."""
        subnet = ipaddress.IPv4Network(ip_net, strict=False)

        route_spec = RouteObject(
            net=subnet,
            weight=self.weight,
            gw=self.route.gw,
            dev=self.route.dev,
            ttl=self.ttl,
            metric=self.metric,
        )

        self._routes.append(route_spec)


class MySettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="IPT_", extra="ignore")

    table: int = Field(200)
    ws_port: int = Field(8765)
    pbr_mark: int = Field(512)
    dns_mark: int = Field(513)
    # Bound for synchronous PBR install latency before the websocket DNS
    # handler returns a degraded TTL=1 response. Keep below the Lua hook's
    # ws:receive timeout (currently 0.25s in powerdns/etc/hook.lua) so
    # PowerDNS receives the JSON ack before giving up.
    # See docs/artifacts/dns-pbr-race-*.
    ws_route_apply_budget_seconds: float = Field(0.2, gt=0)
    interfaces: List[str] = Field(
        validation_alias=AliasChoices("IPT_INTERFACES_JSON", "interfaces"),
    )
    clean_conntrack: bool = Field(
        validation_alias=AliasChoices("IPT_CLEAN_CONNTRACK", "clean_conntrack"),
    )
    # state_file: str = Field('/tmp/state.pkl')
    domain_route_ttl: int = Field(
        validation_alias=AliasChoices("IPT_DOMAIN_ROUTE_TTL", "domain_route_ttl"),
    )
    route_health: RouteHealthSettings = Field(default_factory=RouteHealthSettings)
    nic_attach: List[str] = Field(
        default_factory=list,
        validation_alias=AliasChoices("IPT_NIC_ATTACH", "nic_attach"),
    )
    # The routes field accepts both legacy per-rule entries and new grouped format.
    routes: List[Union[CountryRoute, DomainRoute, NetRoute, RouteActionGroup]] = Field(
        validation_alias=AliasChoices("IPT_ROUTES_JSON", "routes"),
    )
    pinning_egress: Dict[str, RouteMember] = Field(
        default_factory=dict,
        validation_alias=AliasChoices("IPT_PINNING_EGRESS_JSON", "pinning_egress"),
    )
    pinning_ttl: int = Field(
        default=86400,
        validation_alias=AliasChoices("IPT_PINNING_TTL", "pinning_ttl"),
    )
    pinning_api_port: int = Field(
        default=80,
        validation_alias=AliasChoices("IPT_PINNING_API_PORT", "pinning_api_port"),
    )
    pinning_portal_anchor_addr: str = "1.1.1.1"
    pinning_portal_anchor_port: int = 1111
    mss_clamp_value: int = Field(
        default=1240,
        validation_alias=AliasChoices("IPT_MSS_CLAMP_VALUE", "mss_clamp_value"),
        ge=0,
        le=1460,
    )

    @staticmethod
    def _parse_json_env(value: Any, env_key: str, expected_type: type):
        if isinstance(value, expected_type):
            return value
        if isinstance(value, bytes):
            value = value.decode()
        if not isinstance(value, str):
            raise ValueError(f"{env_key} must be a JSON string")
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError as exc:
            raise ValueError(f"{env_key} must contain valid JSON") from exc
        if not isinstance(parsed, expected_type):
            raise ValueError(
                f"{env_key} must decode to {expected_type.__name__}, got {type(parsed).__name__}"
            )
        return parsed

    @staticmethod
    def _validate_non_empty_string_list(
        values: List[Any], field_name: str
    ) -> List[str]:
        normalized: List[str] = []
        for i, item in enumerate(values):
            if not isinstance(item, str):
                raise ValueError(
                    f"{field_name}[{i}] must be a string, got {type(item).__name__}"
                )
            value = item.strip()
            if not value:
                raise ValueError(f"{field_name}[{i}] must be a non-empty string")
            normalized.append(value)
        return normalized

    @field_validator("pinning_egress", mode="before")
    @classmethod
    def parse_pinning_egress(cls, v: Any) -> Dict[str, Any]:
        if v is None or v == "":
            return {}
        if isinstance(v, dict):
            return v
        return cls._parse_json_env(v, "IPT_PINNING_EGRESS_JSON", dict)

    @field_validator("pinning_egress")
    @classmethod
    def validate_pinning_egress_keys(
        cls, v: Dict[str, "RouteMember"]
    ) -> Dict[str, "RouteMember"]:
        slug_re = re.compile(r"^[a-z0-9_-]+$")
        for key in v.keys():
            if key.lower() == "auto":
                raise ValueError(
                    f"egress key {key!r} is reserved (case-insensitive 'auto')"
                )
            if not slug_re.fullmatch(key):
                raise ValueError(
                    f"egress key {key!r} must match slug pattern ^[a-z0-9_-]+$"
                )
        return v

    @field_validator("interfaces", mode="before")
    def parse_interfaces(cls, v: Any) -> List[str]:
        parsed = cls._parse_json_env(v, "IPT_INTERFACES_JSON", list)
        return cls._validate_non_empty_string_list(parsed, "interfaces")


    @field_validator("nic_attach", mode="before")
    def parse_nic_attach(cls, v: Any) -> List[str]:
        parsed = cls._parse_json_env(v, "IPT_NIC_ATTACH", list)
        allowed = {"backbone", "border"}
        for item in parsed:
            if item not in allowed:
                raise ValueError(
                    f"IPT_NIC_ATTACH contains unknown network {item!r}; "
                    f"allowed: {sorted(allowed)}"
                )
        return parsed

    @property
    def has_border(self) -> bool:
        """Return True when border interface is declared in nic_attach."""
        return "border" in self.nic_attach

    @field_validator("clean_conntrack", mode="before")
    def parse_clean_conntrack(cls, v: Any) -> bool:
        if isinstance(v, bool):
            return v
        if isinstance(v, str):
            return _parse_bool(v)
        raise ValueError("IPT_CLEAN_CONNTRACK must be a boolean or true/false string")

    @field_validator("routes", mode="before")
    def parse_routes(cls, v: Any, model_values: ValidationInfo) -> list:
        v = cls._parse_json_env(v, "IPT_ROUTES_JSON", list)

        route_classes = {
            "country": CountryRoute,
            "domain": DomainRoute,
            "net": NetRoute,
        }

        new_routes = []
        for item in v:
            if not isinstance(item, dict):
                new_routes.append(item)
                continue

            # New grouped format: entry has `route` (list) + `rules` keys.
            if "rules" in item:
                new_routes.append(RouteActionGroup(**item))
                continue

            # Delegate route entry validation to route_config — single source of truth.
            normalize_route_entry(item)

            base_route = {
                k: v for k, v in item.items() if k not in route_classes.keys()
            }
            matching_types = [k for k in route_classes.keys() if k in item]

            if len(matching_types) != 1:
                if len(matching_types) == 0:
                    raise ValueError("Invalid route configuration: no valid key found")
                raise ValueError(
                    f"route entry must have exactly one selector "
                    f"(net, domain, or country), got: {matching_types}"
                )

            route_type = matching_types[0]

            values = item[route_type]
            values = [values] if not isinstance(values, list) else values

            for value in values:
                new_route = base_route.copy()
                new_route["type"] = route_type
                new_route[route_type] = value

                # Set default domain_route_ttl for domain routes if not specified
                if route_type == "domain" and "route_ttl" not in new_route:
                    new_route["route_ttl"] = model_values.data["domain_route_ttl"]

                new_routes.append(route_classes[route_type](**new_route))
        return new_routes

    @model_validator(mode="after")
    def populate_country_routes(self) -> "MySettings":
        """Populate subnet data for country routes after config is parsed."""
        country_routes = [r for r in self.routes if isinstance(r, CountryRoute)]
        if country_routes:
            from ipdb.query import IPDatabase  # noqa: PLC0415

            ipdb = IPDatabase("/data/ipt.db")
            for route_entry in country_routes:
                for subnet in ipdb[route_entry.country]:
                    route_entry.add_subnet(subnet)
        return self

    @classmethod
    def load(cls, filename_or_b64: str):
        """Load settings from YAML file path and return parsed MySettings instance."""
        with open(filename_or_b64, "r") as f:
            config = yaml.load(f, Loader=yaml.FullLoader)
        return cls(**config)
