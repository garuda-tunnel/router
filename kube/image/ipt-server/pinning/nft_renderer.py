"""Render the pinning nft ruleset.

NftRenderer binds catalog + ttl_seconds at construction time; the
per-reconcile saddr→egress map is the only thing render() takes.

I/O: jinja FileSystemLoader runs once at module import time. After
that, render() is a pure function of self.* + pins.

Mark layout (no overlap with pbr_mark=0x200, dns_mark=0x201):
    PIN_BIT       = 0x800         # discriminator bit
    PIN_MARK_BASE = 0xA00         # PIN_BIT | 0x200 family
    pin mark[i]   = PIN_MARK_BASE + i (sorted catalog index)

The pin bit is the contract dns_dnat_ipt_server checks via
`meta mark & PIN_BIT != 0 return` to skip the DNS hijack for pinned
saddrs.  KernelReconciler.install_static_rules uses the same per-index
mapping to install `ip rule fwmark <pin mark[i]> lookup TABLE_BASE+i`.
"""
from __future__ import annotations

import os
from typing import Mapping

import jinja2


PIN_BIT: int = 0x800
PIN_MARK_BASE: int = 0xA00

_TEMPLATE_DIR = os.path.join(os.path.dirname(__file__), "templates")
_jinja_env = jinja2.Environment(
    loader=jinja2.FileSystemLoader(_TEMPLATE_DIR),
    keep_trailing_newline=True,
)


class NftRenderer:
    """Render the pinning nft ruleset.

    Static configuration (catalog, ttl_seconds) is bound at
    construction time. Per-reconcile state (the saddr→egress map) is
    passed to render(). Instance fields are not mutated after
    construction.
    """

    def __init__(
        self,
        *,
        catalog: Mapping[str, object],
        portal_addr: str,
        portal_port: int,
        api_port: int,
        ttl_seconds: int = 86400,
    ) -> None:
        self._catalog = dict(catalog)
        self._sorted_keys = sorted(self._catalog.keys())
        self._ttl_seconds = ttl_seconds
        self._portal_addr = portal_addr
        self._portal_port = portal_port
        self._api_port = api_port

    @property
    def sorted_keys(self) -> list:
        return list(self._sorted_keys)

    @property
    def portal_addr(self) -> str:
        """The portal anchor address used for the prerouting bypass rule."""
        return self._portal_addr

    @property
    def portal_port(self) -> int:
        """The portal anchor TCP port used for the prerouting bypass rule."""
        return self._portal_port

    @staticmethod
    def _set_name(egress: str) -> str:
        # nft set names allow [_a-zA-Z0-9]; egress keys may carry dashes
        # (env_slug rename), so substitute to underscores for nft.
        return "pinned_" + egress.replace("-", "_")

    def render(self, pins: Mapping[str, str]) -> str:
        """Render the nft ruleset for the given saddr→egress map."""
        egresses = []
        for i, k in enumerate(self._sorted_keys):
            raw_members = sorted(s for s, e in pins.items() if e == k)
            elements = [
                f"{saddr} timeout {self._ttl_seconds}s"
                for saddr in raw_members
            ]
            egresses.append({
                "key": k,
                "set_name": self._set_name(k),
                "mark": PIN_MARK_BASE + i,
                "elements": elements,
            })
        return _jinja_env.get_template("pinning.nft.j2").render(
            egresses=egresses,
            ttl_seconds=self._ttl_seconds,
            portal_addr=self._portal_addr,
            portal_port=self._portal_port,
            api_port=self._api_port,
        )
