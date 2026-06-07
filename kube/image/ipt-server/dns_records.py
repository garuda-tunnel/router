"""DNS record helper objects used by IPT server routing logic."""

import ipaddress
from typing import Optional


class ARecord:
    name: str
    query: str
    content: str
    ttl: Optional[int]

    def __init__(self, request: dict):
        """Build an A record wrapper from PowerDNS/websocket message payload."""
        if request["type"] != 1:
            raise ValueError("Invalid record type")
        self.name = request["name"].rstrip(".")
        self.query = request["query"].rstrip(".")
        self.content = request["content"]
        self.ttl = request.get("ttl", None)

    @property
    def ip(self) -> ipaddress.IPv4Network:
        """Return record content parsed as an IPv4 network object."""
        return ipaddress.IPv4Network(self.content)
