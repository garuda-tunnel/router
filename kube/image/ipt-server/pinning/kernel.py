"""Kernel-side reconciliation for the pinning subsystem.

Owns three categories of kernel state, all tagged with PINNING_PROTO
when expressible:

- N ip rules (one per egress): `fwmark PIN_MARK_BASE+i lookup
  TABLE_BASE+i` at priority PINNING_RULE_PRIORITY (100), installed
  once at startup.  No DNS escape goto-rule: DNS_MARK (0x201) does
  not match any pinning fwmark (0xA00+i), so DNS-marked packets fall
  through to geo-PBR or main automatically.
- N per-egress routing tables (TABLE_BASE+i) holding a default route;
  liveness updates these.
- the `ip pinning` nft table (saddr → mark classification);
  reconcile() renders+replaces the whole thing per pin change.

pyroute2 >=0.9 constructs an asyncore event loop in IPRoute.__init__
and rejects dst='default' with EOPNOTSUPP for type=blackhole, so all
IPRoute calls go through asyncio.to_thread and use dst='0.0.0.0/0'
explicitly.  Same to_thread treatment for nftables.Nftables().cmd().
"""
from __future__ import annotations

import asyncio
import logging
from typing import Dict, Mapping, Optional

import nftables
from pyroute2 import Conntrack, IPRoute

from pinning.nft_renderer import PIN_MARK_BASE, NftRenderer


log = logging.getLogger(__name__)

PINNING_PROTO: int = 201
PINNING_TABLE_BASE: int = 300
PINNING_RULE_PRIORITY: int = 100


def _table_for_index(i: int) -> int:
    return PINNING_TABLE_BASE + i


def _mark_for_index(i: int) -> int:
    return PIN_MARK_BASE + i


class KernelReconciler:
    """Translate pinning state into ip rule + ip route + nft objects."""

    # pyroute2 0.9.x rejects dst="default" with EOPNOTSUPP for type=blackhole.
    _DEFAULT_DST = "0.0.0.0/0"

    def __init__(
        self,
        *,
        catalog: Mapping[str, object],
        portal_addr: str,
        portal_port: int,
        api_port: int,
        ttl_seconds: int = 86400,
    ) -> None:
        self._renderer = NftRenderer(
            catalog=catalog,
            portal_addr=portal_addr,
            portal_port=portal_port,
            api_port=api_port,
            ttl_seconds=ttl_seconds,
        )

    def _egress_index(self, egress: str) -> int:
        keys = self._renderer.sorted_keys
        try:
            return keys.index(egress)
        except ValueError as exc:
            raise ValueError(
                f"egress {egress!r} not in catalog "
                f"{keys!r}"
            ) from exc

    # ------------------------------------------------------------------
    # Synchronous helpers — run in worker threads via asyncio.to_thread.
    # ------------------------------------------------------------------

    @classmethod
    def _sync_install_static(cls, n_egresses: int) -> None:
        with IPRoute() as ipr:
            ipr.flush_rules(proto=PINNING_PROTO)
            for i in range(n_egresses):
                ipr.rule(
                    "add",
                    priority=PINNING_RULE_PRIORITY,
                    fwmark=_mark_for_index(i),
                    table=_table_for_index(i),
                    proto=PINNING_PROTO,
                )

    @classmethod
    def _sync_update_liveness(
        cls,
        table: int,
        alive: bool,
        nh_ip: Optional[str],
        nh_dev: Optional[str],
    ) -> None:
        with IPRoute() as ipr:
            if not alive:
                ipr.route(
                    "replace",
                    dst=cls._DEFAULT_DST,
                    table=table,
                    type="blackhole",
                    proto=PINNING_PROTO,
                )
                return
            oif = ipr.link_lookup(ifname=nh_dev)[0] if nh_dev else None
            kwargs: Dict[str, object] = {
                "dst": cls._DEFAULT_DST,
                "table": table,
                "proto": PINNING_PROTO,
            }
            if oif is not None:
                kwargs["oif"] = oif
            if nh_ip is not None:
                kwargs["gateway"] = nh_ip
            ipr.route("replace", **kwargs)

    @staticmethod
    def _sync_apply_nft(ruleset_text: str) -> None:
        nft = nftables.Nftables()
        # Idempotent delete; ignore rc.
        nft.cmd("delete table ip pinning")
        rc, _output, error = nft.cmd(ruleset_text)
        if rc != 0:
            raise RuntimeError(f"nft load failed: {error}")

    # ------------------------------------------------------------------
    # Public async interface.
    # ------------------------------------------------------------------

    async def install_static_rules(self) -> None:
        """Idempotent startup: wipe our ip rules, install fwmark→table.

        Also seeds every per-egress table with a blackhole default so
        unresolved egresses fail closed before liveness reports in.
        Caller invokes once at process start.
        """
        keys = self._renderer.sorted_keys
        await asyncio.to_thread(self._sync_install_static, len(keys))
        for key in keys:
            await self.update_egress_liveness(
                egress=key, alive=False, nh_ip=None, nh_dev=None,
            )
        log.info(
            "pinning: static rules installed for %d egresses, marks=0x%x..0x%x, tables=%d..%d",
            len(keys),
            _mark_for_index(0) if keys else 0,
            _mark_for_index(len(keys) - 1) if keys else 0,
            _table_for_index(0) if keys else 0,
            _table_for_index(len(keys) - 1) if keys else 0,
        )

    async def update_egress_liveness(
        self,
        egress: str,
        alive: bool,
        nh_ip: Optional[str] = None,
        nh_dev: Optional[str] = None,
    ) -> None:
        """Install the per-egress default route (live or blackhole)."""
        i = self._egress_index(egress)
        table = _table_for_index(i)
        await asyncio.to_thread(
            self._sync_update_liveness, table, alive, nh_ip, nh_dev,
        )

    async def reconcile(self, pins: Mapping[str, str]) -> None:
        """Render+apply the pinning nft ruleset for the given saddr→egress map."""
        ruleset = self._renderer.render(pins)
        await asyncio.to_thread(self._sync_apply_nft, ruleset)

    @staticmethod
    def _sync_flush_conntrack(
        saddr: str,
        portal_addr: str,
        portal_port: int,
    ) -> None:
        """Drop all conntrack flows from ``saddr`` except the portal tuple.

        Iterates the kernel conntrack table via pyroute2.Conntrack and
        deletes every entry whose orig-direction source matches ``saddr``,
        with one exception: the portal TCP flow
        (proto=6, daddr=portal_addr, dport=portal_port) is spared so the
        browser tab that issued the pin change receives its HTTP response.

        Two-phase (materialise then delete):
        pyroute2 0.9.x's _generate_with_cleanup closes the thread-local
        event loop when the dump generator is exhausted.  Interleaving
        entry("del") calls within the iteration trips "Event loop is
        closed" because the inner call's cleanup tears down the loop the
        outer generator is still using.  Collect all matching tuples
        first, delete afterwards.

        Portal-tuple exception is TCP-only (proto==6) — consistent with
        the nft portal-bypass guard which is `tcp dport <portal_port>`.
        A UDP flow to the same coordinates is ordinary forwarded traffic
        and is flushed alongside the rest.

        Non-TCP/UDP entries (ICMP, etc.) may lack a `dport` attribute on
        some pyroute2 builds.  The bare except is conservative: entries
        we cannot fully evaluate are SKIPPED (not deleted).  Stale ICMP
        entries are harmless and expire via conntrack TTL sweep.

        Best-effort: any exception opening Conntrack or deleting a tuple
        is logged and swallowed.  Pin state is already correct in nft by
        the time this runs; raising here would mask success behind a
        kernel state we cannot do anything about.
        """
        try:
            ct_ctx = Conntrack()
        except Exception as exc:
            log.warning(
                "pinning: could not open Conntrack for saddr=%s: %s",
                saddr, exc,
            )
            return

        with ct_ctx as ct:
            # Phase 1: materialise matching tuples.
            #
            # The dump iteration has its own try/except so that a
            # mid-iteration failure (netlink reset, kernel-side rate
            # limit, etc.) does NOT skip phase 2.  Whatever was
            # materialised before the failure still gets deleted —
            # this is the contract the spec pins as "whatever was
            # materialized so far still gets deleted".  Wrapping the
            # whole body in one outer try would silently discard
            # partial matches.
            matches = []
            try:
                for entry in ct.dump_entries():
                    try:
                        tup = entry.tuple_orig
                        if tup.saddr != saddr:
                            continue
                        # Portal-tuple exception: TCP flow to the portal
                        # anchor that the browser uses to administer pins.
                        if (
                            tup.proto == 6
                            and tup.daddr == portal_addr
                            and tup.dport == portal_port
                        ):
                            continue
                        matches.append(tup)
                    except Exception:
                        # Non-TCP/UDP entries may raise AttributeError on
                        # .dport; skip conservatively.
                        log.debug(
                            "pinning: skipping conntrack entry with "
                            "unreadable tuple (saddr=%s)", saddr,
                        )
            except Exception as exc:
                log.warning(
                    "pinning: dump_entries failed mid-iteration for "
                    "saddr=%s after %d tuple(s) collected: %s",
                    saddr, len(matches), exc,
                )

            # Phase 2: delete.  Per-tuple try/except absorbs parallel
            # TTL-sweep races (another flush already removed the same
            # tuple) without aborting the loop.
            for tup in matches:
                try:
                    ct.entry("del", tuple_orig=tup)
                except Exception as exc:
                    log.warning(
                        "pinning: could not delete conntrack entry "
                        "saddr=%s dst=%s:%s: %s",
                        saddr,
                        getattr(tup, "daddr", "?"),
                        getattr(tup, "dport", "?"),
                        exc,
                    )

    async def flush_conntrack(self, saddr: str) -> None:
        """Flush all conntrack flows from ``saddr`` except the portal tuple.

        Call this AFTER ``reconcile()`` so the next packet from ``saddr``
        enters the freshly-loaded pinning prerouting chain with no
        inherited routing decision from the previous pin state.  Without
        this flush the kernel conntrack association keeps long-lived TCP
        flows (HTTP/2, persistent connections) on the old route even
        though `meta mark set` re-fires on every new packet.  Browsers
        observe this as "I switched egress but my external IP did not
        change"; curl does not because each invocation is a fresh flow.
        """
        await asyncio.to_thread(
            self._sync_flush_conntrack,
            saddr,
            self._renderer.portal_addr,
            self._renderer.portal_port,
        )
