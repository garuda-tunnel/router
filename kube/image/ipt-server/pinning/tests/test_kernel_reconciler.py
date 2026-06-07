"""KernelReconciler tests against the nft + ip rule architecture.

The reconciler owns three things:
  * static `ip rule fwmark <pin mark[i]> lookup TABLE_BASE+i` entries
    (one per egress, installed once),
  * per-egress routing tables (the default route in each is owned by
    the liveness loop via update_egress_liveness),
  * a render-and-replace cycle of the `pinning` nft table on every
    pin/unpin.

There is NO DNS escape goto-rule in RPDB: DNS_MARK (0x201) does not
match any pinning fwmark (0xA00+i), so DNS-marked packets fall
through to the geo-PBR rule (priority 32000) or main automatically.
"""
from __future__ import annotations

import asyncio
from unittest.mock import MagicMock, patch

import pytest

from pinning import kernel
from pinning.kernel import KernelReconciler


def _catalog(*keys):
    return {k: object() for k in keys}


def _make_iproute_ctx():
    patcher = patch("pinning.kernel.IPRoute")
    cls = patcher.start()
    instance = MagicMock()
    cls.return_value.__enter__.return_value = instance
    cls.return_value.__exit__.return_value = False
    return patcher, instance


def _make_nft_ctx():
    patcher = patch("pinning.kernel.nftables.Nftables")
    cls = patcher.start()
    instance = MagicMock()
    instance.cmd.return_value = (0, "", "")
    cls.return_value = instance
    return patcher, instance


def test_install_static_rules_flushes_proto_rules_first():
    """install_static_rules removes any leftover proto=PINNING_PROTO
    state before installing the new fwmark→table mapping (idempotent
    re-run after restart with a different catalog must not leak rules)."""
    pr_patcher, mock_iproute = _make_iproute_ctx()
    nft_patcher, _ = _make_nft_ctx()
    try:
        rec = KernelReconciler(
            catalog=_catalog("outer-de", "outer-pt"),
            portal_addr="192.0.2.1",
            portal_port=1,
            api_port=80,
        )
        asyncio.run(rec.install_static_rules())
        mock_iproute.flush_rules.assert_called_with(proto=kernel.PINNING_PROTO)
    finally:
        pr_patcher.stop()
        nft_patcher.stop()


def test_install_static_rules_emits_one_fwmark_lookup_per_egress():
    """One ip rule per egress: fwmark=PIN_MARK_BASE+i lookup TABLE_BASE+i."""
    pr_patcher, mock_iproute = _make_iproute_ctx()
    nft_patcher, _ = _make_nft_ctx()
    try:
        from pinning.nft_renderer import PIN_MARK_BASE
        rec = KernelReconciler(
            catalog=_catalog("a", "b", "c"),
            portal_addr="192.0.2.1",
            portal_port=1,
            api_port=80,
        )
        asyncio.run(rec.install_static_rules())

        per_egress = [
            c for c in mock_iproute.rule.call_args_list
            if c.kwargs.get("priority") == kernel.PINNING_RULE_PRIORITY
        ]
        assert len(per_egress) == 3
        seen = {(c.kwargs["fwmark"], c.kwargs["table"]) for c in per_egress}
        assert seen == {
            (PIN_MARK_BASE + 0, kernel.PINNING_TABLE_BASE + 0),
            (PIN_MARK_BASE + 1, kernel.PINNING_TABLE_BASE + 1),
            (PIN_MARK_BASE + 2, kernel.PINNING_TABLE_BASE + 2),
        }
        for c in per_egress:
            assert c.kwargs["proto"] == kernel.PINNING_PROTO
    finally:
        pr_patcher.stop()
        nft_patcher.stop()


def test_install_static_rules_does_not_emit_dns_escape_goto():
    """No goto-rule for DNS_MARK in RPDB.

    Pinning marks (0xA00+i) do not overlap DNS_MARK (0x201), so the
    fwmark→table rules cannot accidentally divert DNS traffic; the
    DNS hijack opt-out lives in dns_dnat_ipt_server (pin bit match),
    not in RPDB.  Asserting absence pins the contract.
    """
    pr_patcher, mock_iproute = _make_iproute_ctx()
    nft_patcher, _ = _make_nft_ctx()
    try:
        rec = KernelReconciler(
            catalog=_catalog("hub"),
            portal_addr="192.0.2.1",
            portal_port=1,
            api_port=80,
        )
        asyncio.run(rec.install_static_rules())
        for c in mock_iproute.rule.call_args_list:
            assert c.kwargs.get("action") != "goto", (
                f"unexpected goto rule installed by pinning: {c.kwargs}"
            )
    finally:
        pr_patcher.stop()
        nft_patcher.stop()


def test_install_static_rules_initialises_egress_tables_with_blackhole():
    """Each egress table gets a blackhole default until liveness reports in."""
    pr_patcher, mock_iproute = _make_iproute_ctx()
    nft_patcher, _ = _make_nft_ctx()
    try:
        rec = KernelReconciler(
            catalog=_catalog("outer-de", "outer-pt"),
            portal_addr="192.0.2.1",
            portal_port=1,
            api_port=80,
        )
        asyncio.run(rec.install_static_rules())
        replace_calls = [
            c for c in mock_iproute.route.call_args_list
            if c.args == ("replace",) and c.kwargs.get("type") == "blackhole"
        ]
        assert len(replace_calls) == 2
    finally:
        pr_patcher.stop()
        nft_patcher.stop()


def test_reconcile_renders_and_loads_nft_ruleset():
    """reconcile(pins) deletes the old table then loads the rendered text."""
    nft_patcher, mock_nft = _make_nft_ctx()
    try:
        rec = KernelReconciler(
            catalog=_catalog("hub"),
            portal_addr="192.0.2.1",
            portal_port=1,
            api_port=80,
        )
        asyncio.run(rec.reconcile({"172.30.0.3": "hub"}))

        cmds = [c.args[0] for c in mock_nft.cmd.call_args_list]
        assert any("delete table ip pinning" in c for c in cmds)
        loaded = next(
            c for c in cmds
            if "table ip pinning" in c and "set pinned_hub" in c
        )
        assert "172.30.0.3 timeout" in loaded
        assert "ip saddr @pinned_hub meta mark set 0xa00" in loaded
    finally:
        nft_patcher.stop()


def test_reconcile_propagates_nft_failure_with_error_text():
    """A non-zero rc on load raises with the nft stderr embedded."""
    nft_patcher, mock_nft = _make_nft_ctx()
    try:
        # Deletion call rc=0 (idempotent); load call rc=1.
        mock_nft.cmd.side_effect = [(0, "", ""), (1, "", "syntax error near :-150")]
        rec = KernelReconciler(
            catalog=_catalog("hub"),
            portal_addr="192.0.2.1",
            portal_port=1,
            api_port=80,
        )
        with pytest.raises(RuntimeError, match="syntax error near"):
            asyncio.run(rec.reconcile({}))
    finally:
        nft_patcher.stop()


def test_update_egress_liveness_replaces_per_table_default():
    """Liveness path unchanged: replace default route in TABLE_BASE+i."""
    pr_patcher, mock_iproute = _make_iproute_ctx()
    try:
        rec = KernelReconciler(
            catalog=_catalog("outer-de", "outer-pt"),
            portal_addr="192.0.2.1",
            portal_port=1,
            api_port=80,
        )
        asyncio.run(rec.update_egress_liveness(
            egress="outer-pt", alive=True, nh_ip="10.9.19.2", nh_dev=None,
        ))
        replace = next(
            c for c in mock_iproute.route.call_args_list
            if c.args == ("replace",)
        )
        assert replace.kwargs["table"] == kernel.PINNING_TABLE_BASE + 1
        assert replace.kwargs["gateway"] == "10.9.19.2"
        assert replace.kwargs["dst"] == "0.0.0.0/0"  # not 'default' — pyroute2 0.9 quirk
    finally:
        pr_patcher.stop()


def test_update_egress_liveness_dead_writes_blackhole():
    """alive=False writes a blackhole default into the egress table."""
    pr_patcher, mock_iproute = _make_iproute_ctx()
    try:
        rec = KernelReconciler(
            catalog=_catalog("hub"),
            portal_addr="192.0.2.1",
            portal_port=1,
            api_port=80,
        )
        asyncio.run(rec.update_egress_liveness(
            egress="hub", alive=False,
        ))
        replace = next(
            c for c in mock_iproute.route.call_args_list
            if c.args == ("replace",)
        )
        assert replace.kwargs["type"] == "blackhole"
    finally:
        pr_patcher.stop()


def _make_conntrack_entry(saddr, daddr, dport, proto=6):
    """Build a mock ConntrackEntry whose tuple_orig has the given fields."""
    tup = MagicMock()
    tup.saddr = saddr
    tup.daddr = daddr
    tup.dport = dport
    tup.proto = proto
    entry = MagicMock()
    entry.tuple_orig = tup
    return entry


def _make_conntrack_ctx(entries=None, dump_raises=None):
    """Patch pyroute2.Conntrack used by KernelReconciler.

    Returns (patcher, ct_instance_mock).
    The ct_instance_mock is the object returned by `Conntrack().__enter__`.
    """
    patcher = patch("pinning.kernel.Conntrack")
    cls = patcher.start()
    instance = MagicMock()
    cls.return_value.__enter__.return_value = instance
    cls.return_value.__exit__.return_value = False
    if dump_raises is not None:
        instance.dump_entries.side_effect = dump_raises
    else:
        instance.dump_entries.return_value = entries or []
    return patcher, instance


def test_flush_conntrack_deletes_matching_saddr_flows():
    """flush_conntrack(saddr) calls ct.entry('del') for every flow
    whose tuple_orig.saddr matches and is not the portal tuple.

    Uses pyroute2.Conntrack directly; no subprocess.
    """
    pr_patcher, _ = _make_iproute_ctx()
    nft_patcher, _ = _make_nft_ctx()
    entries = [
        _make_conntrack_entry("192.0.2.42", "203.0.113.5", 443),   # should delete
        _make_conntrack_entry("192.0.2.99", "203.0.113.5", 443),   # wrong saddr — skip
        _make_conntrack_entry("192.0.2.42", "203.0.113.5", 80),    # should delete
    ]
    ct_patcher, ct_mock = _make_conntrack_ctx(entries=entries)
    try:
        rec = KernelReconciler(
            catalog=_catalog("hub"),
            portal_addr="192.0.2.1",
            portal_port=1111,
            api_port=80,
        )
        asyncio.run(rec.flush_conntrack("192.0.2.42"))

        # Two matching entries: the "192.0.2.42" non-portal flows.
        assert ct_mock.entry.call_count == 2
        for call in ct_mock.entry.call_args_list:
            assert call.args[0] == "del"
    finally:
        ct_patcher.stop()
        pr_patcher.stop()
        nft_patcher.stop()


def test_flush_conntrack_skips_portal_tuple_tcp():
    """flush_conntrack must NOT delete the portal TCP flow.

    The portal connection (browser → portal_addr:portal_port, proto=TCP)
    must survive the flush so the in-flight HTTP request that issued the
    pin change gets its response.  Killing it causes the browser tab to
    time out waiting for a response whose conntrack DNAT mapping has
    vanished.
    """
    pr_patcher, _ = _make_iproute_ctx()
    nft_patcher, _ = _make_nft_ctx()
    entries = [
        # portal TCP flow — must NOT be deleted
        _make_conntrack_entry("192.0.2.42", "192.0.2.1", 1111, proto=6),
        # regular forwarded flow — should be deleted
        _make_conntrack_entry("192.0.2.42", "203.0.113.5", 443, proto=6),
    ]
    ct_patcher, ct_mock = _make_conntrack_ctx(entries=entries)
    try:
        rec = KernelReconciler(
            catalog=_catalog("hub"),
            portal_addr="192.0.2.1",
            portal_port=1111,
            api_port=80,
        )
        asyncio.run(rec.flush_conntrack("192.0.2.42"))

        # Exactly one delete call — the non-portal forwarded flow.
        assert ct_mock.entry.call_count == 1
        # The deleted tuple must be the forwarded flow, NOT the portal one.
        deleted_tup = ct_mock.entry.call_args.kwargs["tuple_orig"]
        assert deleted_tup.daddr == "203.0.113.5", (
            f"expected forwarded-flow daddr 203.0.113.5 to be deleted; "
            f"got daddr={deleted_tup.daddr!r} (portal tuple was "
            f"daddr=192.0.2.1 — that flow must survive)"
        )
        assert deleted_tup.dport == 443
    finally:
        ct_patcher.stop()
        pr_patcher.stop()
        nft_patcher.stop()


def test_flush_conntrack_does_not_skip_udp_to_portal_coordinates():
    """A UDP flow to the same daddr+dport as the portal is NOT spared.

    The nft portal-bypass guard is `tcp dport <port>` — it only
    intercepts TCP.  A UDP datagram to portal_addr+portal_port is
    ordinary forwarded traffic and must be flushed alongside the rest.
    """
    pr_patcher, _ = _make_iproute_ctx()
    nft_patcher, _ = _make_nft_ctx()
    entries = [
        # UDP to portal coordinates — must be deleted (not spared)
        _make_conntrack_entry("192.0.2.42", "192.0.2.1", 1111, proto=17),
    ]
    ct_patcher, ct_mock = _make_conntrack_ctx(entries=entries)
    try:
        rec = KernelReconciler(
            catalog=_catalog("hub"),
            portal_addr="192.0.2.1",
            portal_port=1111,
            api_port=80,
        )
        asyncio.run(rec.flush_conntrack("192.0.2.42"))

        assert ct_mock.entry.call_count == 1, (
            "UDP flow to portal coordinates must be flushed; "
            "portal exception is TCP-only"
        )
    finally:
        ct_patcher.stop()
        pr_patcher.stop()
        nft_patcher.stop()


def test_flush_conntrack_skips_entry_with_unreadable_tuple():
    """Entries that raise on tuple_orig attribute access are skipped.

    The bare except in the filter loop is conservatively-correct:
    an entry we cannot fully evaluate (e.g. a malformed entry where
    even saddr access raises) is NOT deleted.  The flush must not
    crash; processing continues for remaining entries.
    """
    pr_patcher, _ = _make_iproute_ctx()
    nft_patcher, _ = _make_nft_ctx()

    # Build a bad entry whose .saddr raises (truly unreadable)
    bad_tup = MagicMock()
    type(bad_tup).saddr = property(
        lambda self: (_ for _ in ()).throw(AttributeError("saddr"))
    )
    bad_entry = MagicMock()
    bad_entry.tuple_orig = bad_tup

    # Good entry that should still be processed after the bad one
    good_entry = _make_conntrack_entry("192.0.2.42", "203.0.113.5", 443)

    ct_patcher, ct_mock = _make_conntrack_ctx(entries=[bad_entry, good_entry])
    try:
        rec = KernelReconciler(
            catalog=_catalog("hub"),
            portal_addr="192.0.2.1",
            portal_port=1111,
            api_port=80,
        )
        # Must not raise; bad entry is skipped, good entry is deleted.
        asyncio.run(rec.flush_conntrack("192.0.2.42"))
        # The good entry (matching saddr, non-portal) must still be deleted.
        assert ct_mock.entry.call_count == 1
    finally:
        ct_patcher.stop()
        pr_patcher.stop()
        nft_patcher.stop()


def test_flush_conntrack_continues_after_per_tuple_delete_failure():
    """If entry('del') raises for one tuple, remaining matches are
    still deleted.

    Common cause: a parallel TTL sweep or another flush already
    removed the same flow.  The exception must not abort the loop.
    """
    pr_patcher, _ = _make_iproute_ctx()
    nft_patcher, _ = _make_nft_ctx()
    entries = [
        _make_conntrack_entry("192.0.2.42", "203.0.113.1", 443),
        _make_conntrack_entry("192.0.2.42", "203.0.113.2", 443),
        _make_conntrack_entry("192.0.2.42", "203.0.113.3", 443),
    ]
    ct_patcher, ct_mock = _make_conntrack_ctx(entries=entries)
    # First delete raises; second and third should still be attempted.
    ct_mock.entry.side_effect = [RuntimeError("race"), None, None]
    try:
        rec = KernelReconciler(
            catalog=_catalog("hub"),
            portal_addr="192.0.2.1",
            portal_port=1111,
            api_port=80,
        )
        # Must not raise.
        asyncio.run(rec.flush_conntrack("192.0.2.42"))
        assert ct_mock.entry.call_count == 3
    finally:
        ct_patcher.stop()
        pr_patcher.stop()
        nft_patcher.stop()


def test_flush_conntrack_materializes_before_delete():
    """All dump_entries() entries are consumed before the first delete.

    pyroute2 0.9.x's _generate_with_cleanup closes the thread-local
    event loop when the dump generator exits.  Interleaving delete
    calls within the iteration trips 'Event loop is closed'.  The
    implementation must collect all matches first, then delete.
    """
    pr_patcher, _ = _make_iproute_ctx()
    nft_patcher, _ = _make_nft_ctx()

    call_order = []

    def track_dump():
        call_order.append("dump_start")
        yield _make_conntrack_entry("192.0.2.42", "203.0.113.5", 443)
        yield _make_conntrack_entry("192.0.2.42", "203.0.113.5", 80)
        call_order.append("dump_end")

    ct_patcher = patch("pinning.kernel.Conntrack")
    cls = ct_patcher.start()
    instance = MagicMock()
    cls.return_value.__enter__.return_value = instance
    cls.return_value.__exit__.return_value = False
    instance.dump_entries.side_effect = track_dump

    def track_entry(op, **kwargs):
        call_order.append("delete")

    instance.entry.side_effect = track_entry
    try:
        rec = KernelReconciler(
            catalog=_catalog("hub"),
            portal_addr="192.0.2.1",
            portal_port=1111,
            api_port=80,
        )
        asyncio.run(rec.flush_conntrack("192.0.2.42"))

        dump_end_idx = call_order.index("dump_end")
        first_delete_idx = next(
            i for i, v in enumerate(call_order) if v == "delete"
        )
        assert dump_end_idx < first_delete_idx, (
            f"dump must complete before first delete; order={call_order}"
        )
    finally:
        ct_patcher.stop()
        pr_patcher.stop()
        nft_patcher.stop()


def test_flush_conntrack_deletes_collected_tuples_when_dump_raises_mid_iteration():
    """Partial dump must not abort the flush.

    pyroute2's dump_entries() can raise mid-iteration (netlink error,
    socket reset, kernel-side rate limit).  The two-phase shape was
    chosen specifically so anything materialised before the exception
    still gets deleted in phase 2 — the spec requires "whatever was
    materialized so far still gets deleted".

    Wrapping the entire body in one outer try would discard the
    partial matches and leave stale forwarded flows in the kernel —
    exactly the failure mode this fix is meant to prevent.
    """
    pr_patcher, _ = _make_iproute_ctx()
    nft_patcher, _ = _make_nft_ctx()

    def half_then_raise():
        # Yield two matching entries, then blow up.
        yield _make_conntrack_entry("192.0.2.42", "203.0.113.5", 443)
        yield _make_conntrack_entry("192.0.2.42", "203.0.113.6", 443)
        raise OSError("netlink reset mid-dump")

    ct_patcher = patch("pinning.kernel.Conntrack")
    cls = ct_patcher.start()
    instance = MagicMock()
    cls.return_value.__enter__.return_value = instance
    cls.return_value.__exit__.return_value = False
    instance.dump_entries.side_effect = half_then_raise
    try:
        rec = KernelReconciler(
            catalog=_catalog("hub"),
            portal_addr="192.0.2.1",
            portal_port=1111,
            api_port=80,
        )
        # Must not raise.
        asyncio.run(rec.flush_conntrack("192.0.2.42"))

        # Both materialised tuples must still be deleted.
        assert instance.entry.call_count == 2, (
            f"expected both partial-dump tuples to be deleted; "
            f"got {instance.entry.call_count}"
        )
    finally:
        ct_patcher.stop()
        pr_patcher.stop()
        nft_patcher.stop()


def test_flush_conntrack_does_not_raise_when_conntrack_open_fails():
    """If Conntrack() raises (e.g. netlink unavailable), flush must
    swallow the exception — pin state is already correct in nft.
    """
    pr_patcher, _ = _make_iproute_ctx()
    nft_patcher, _ = _make_nft_ctx()
    ct_patcher = patch("pinning.kernel.Conntrack", side_effect=OSError("no netlink"))
    ct_patcher.start()
    try:
        rec = KernelReconciler(
            catalog=_catalog("hub"),
            portal_addr="192.0.2.1",
            portal_port=1111,
            api_port=80,
        )
        # Must not raise.
        asyncio.run(rec.flush_conntrack("192.0.2.42"))
    finally:
        ct_patcher.stop()
        pr_patcher.stop()
        nft_patcher.stop()


def test_reconcile_renders_includes_portal_redirect():
    """reconcile() loads nft text containing the portal redirect rule.

    Verifies the reconciler threads its portal kwargs into the
    renderer correctly and the resulting ruleset has the line we
    expect.
    """
    nft_patcher, mock_nft = _make_nft_ctx()
    try:
        rec = KernelReconciler(
            catalog=_catalog("hub"),
            portal_addr="192.0.2.7",
            portal_port=1234,
            api_port=8080,
        )
        asyncio.run(rec.reconcile({}))
        cmds = [c.args[0] for c in mock_nft.cmd.call_args_list]
        loaded = next(
            c for c in cmds
            if "table ip pinning" in c and "redirect to :8080" in c
        )
        assert "ip daddr 192.0.2.7" in loaded
        assert "tcp dport 1234" in loaded
    finally:
        nft_patcher.stop()
