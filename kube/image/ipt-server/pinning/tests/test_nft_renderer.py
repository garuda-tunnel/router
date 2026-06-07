"""Renderer tests for the pinning nft ruleset.

The renderer is a pure function: catalog + pin snapshot → nft text.
Validation is text-level so it stays robust against pyroute2 / kernel
drift; an end-to-end smoke phase exercises the kernel apply path.
"""
from pinning.nft_renderer import NftRenderer


def test_renders_named_table_with_prerouting_chain_at_priority_minus_150():
    """Table name and chain priority are part of the public interface.

    Priority -150 places the chain *between* the PBR mark hook (-200,
    where DNS_MARK is set) and the DNAT hook (-100, where the DNS
    hijack DNAT runs).  The pinning chain rewrites the mark on
    pinned saddrs *before* dns_dnat sees the packet, so dns_dnat's
    pin-bit check decides whether to hijack.
    """
    ruleset = NftRenderer(
        catalog={"hub": object()},
        portal_addr="192.0.2.1",
        portal_port=1,
        api_port=80,
    ).render(pins={})
    assert "table ip pinning {" in ruleset
    assert "type filter hook prerouting priority -150" in ruleset
    assert "policy accept" in ruleset


def test_renders_iifname_backbone_guard():
    """Pinning only applies to packets entering on the backbone interface."""
    ruleset = NftRenderer(
        catalog={"hub": object()},
        portal_addr="192.0.2.1",
        portal_port=1,
        api_port=80,
    ).render(pins={})
    assert 'iifname != "backbone" return' in ruleset


def test_renders_private_subnet_bypass():
    """LAN / management / RFC1918 destinations skip pinning."""
    ruleset = NftRenderer(
        catalog={"hub": object()},
        portal_addr="192.0.2.1",
        portal_port=1,
        api_port=80,
    ).render(pins={})
    assert "private_subnets" in ruleset
    assert "10.0.0.0/8" in ruleset
    assert "192.168.0.0/16" in ruleset
    assert "ip daddr @private_subnets return" in ruleset


def test_does_not_emit_dns_escape_inside_pinning_chain():
    """Pinning intentionally overrides DNS classification.

    A pinned client's DNS must flow through the chosen egress (so the
    egress's resolver answers).  The DNS hijack opt-out is owned by
    dns_dnat_ipt_server (matches `meta mark & 0x800 != 0 return`),
    not by the pinning chain.

    Asserting absence here pins the contract: anyone re-adding
    `meta mark 0x201 return` would silently bring the hijack back on
    pinned clients.
    """
    ruleset = NftRenderer(
        catalog={"hub": object()},
        portal_addr="192.0.2.1",
        portal_port=1,
        api_port=80,
    ).render(pins={})
    assert "meta mark 0x201 return" not in ruleset
    assert "meta mark 0x201" not in ruleset, (
        "pinning chain must not reference DNS_MARK; the dns_dnat "
        "table owns the pin-bit bypass instead"
    )


def test_emits_one_set_per_egress_in_sorted_order():
    """Sets are named pinned_<egress_slug>; sort matches catalog ordering.

    KernelReconciler.install_static_rules uses the same sorted order
    to assign marks and tables (mark = PIN_MARK_BASE + i, table =
    PINNING_TABLE_BASE + i), so renderer and reconciler agree on the
    mapping without sharing state.
    """
    ruleset = NftRenderer(
        catalog={"outer-pt": object(), "outer-de": object()},
        portal_addr="192.0.2.1",
        portal_port=1,
        api_port=80,
    ).render(pins={})
    pos_de = ruleset.find("set pinned_outer_de")
    pos_pt = ruleset.find("set pinned_outer_pt")
    assert pos_de != -1 and pos_pt != -1
    assert pos_de < pos_pt, "sets must be emitted in sorted catalog order"


def test_each_set_carries_timeout_flag():
    """Kernel auto-expires set elements (replaces the Python sweep loop)."""
    ruleset = NftRenderer(
        catalog={"hub": object()},
        portal_addr="192.0.2.1",
        portal_port=1,
        api_port=80,
    ).render(pins={})
    set_block = ruleset.split("set pinned_hub")[1].split("}")[0]
    assert "type ipv4_addr" in set_block
    assert "flags timeout" in set_block


def test_classification_rules_use_pin_marks_starting_at_0xA00():
    """Each egress gets `ip saddr @pinned_<egress> meta mark set 0xA00+i`.

    PIN_MARK_BASE = 0xA00 = 0x800 (pin bit) | 0x200 (PBR family).  The
    pin bit (0x800) is the discriminator dns_dnat checks via
    `meta mark & 0x800 != 0`.  Index-based incrementing matches
    KernelReconciler.install_static_rules so the static fwmark→table
    rules map correctly.
    """
    ruleset = NftRenderer(
        catalog={"outer-de": object(), "outer-pt": object()},
        portal_addr="192.0.2.1",
        portal_port=1,
        api_port=80,
    ).render(pins={})
    assert "ip saddr @pinned_outer_de meta mark set 0xa00" in ruleset
    assert "ip saddr @pinned_outer_pt meta mark set 0xa01" in ruleset


def test_classification_rules_do_not_stamp_ct_mark():
    """Classification rules must set ONLY `meta mark`, NOT `ct mark`.

    The Python conntrack flush (pyroute2.Conntrack) filters flows by
    saddr and portal-tuple exclusion directly — it no longer needs a
    ct mark discriminator stamped in the kernel flow table.  Stamping
    ct mark adds unused kernel state and was the source of a previous
    correctness bug (portal connections were killed by the ct mark
    filter despite not being forwarded traffic).

    If this test fails, someone re-added `ct mark set` to the
    classification rules.  Remove it and update the flush logic.
    """
    ruleset = NftRenderer(
        catalog={"outer-de": object(), "outer-pt": object()},
        portal_addr="192.0.2.1",
        portal_port=1,
        api_port=80,
    ).render(pins={})
    # classification lines must NOT contain ct mark
    for line in ruleset.splitlines():
        if "ip saddr @pinned_" in line and "meta mark set" in line:
            assert "ct mark set" not in line, (
                f"classification rule must not stamp ct mark: {line!r}"
            )


def test_nft_renderer_exposes_portal_addr_property():
    """NftRenderer.portal_addr is part of the public API.

    KernelReconciler.flush_conntrack reads portal_addr/portal_port from
    the renderer to spare the portal TCP tuple during conntrack flush
    (see test_flush_conntrack_skips_portal_tuple_tcp for the behavioural
    side).  This test pins the API contract so that renaming the
    underlying attribute to private (`_portal_addr`) or removing the
    property fails fast — independent of any flush_conntrack mock setup.
    """
    renderer = NftRenderer(
        catalog={"hub": object()},
        portal_addr="192.0.2.7",
        portal_port=1234,
        api_port=8080,
    )
    assert renderer.portal_addr == "192.0.2.7"


def test_nft_renderer_exposes_portal_port_property():
    """NftRenderer.portal_port is part of the public API.

    See test_nft_renderer_exposes_portal_addr_property for rationale.
    Behavioural coverage is in test_flush_conntrack_skips_portal_tuple_tcp.
    """
    renderer = NftRenderer(
        catalog={"hub": object()},
        portal_addr="192.0.2.7",
        portal_port=1234,
        api_port=8080,
    )
    assert renderer.portal_port == 1234


def test_filter_prerouting_bypasses_portal_destination_before_classification():
    """The pinning filter prerouting chain must `return` on portal-bound
    traffic before any saddr classification fires.

    Portal-bound packets (iifname=backbone, daddr=portal_addr,
    tcp dport=portal_port) traverse BOTH the nat chain (which does
    REDIRECT) AND this filter chain — they share the prerouting hook.
    Without an early-return guard the filter chain would stamp the
    per-egress `meta mark` on the portal flow, sending its response
    packets through the wrong routing table.  The guard keeps portal
    traffic on the host's main routing table so the local API listener
    receives and replies to the redirected connection cleanly.

    Semantic alignment: the Python conntrack flush
    (KernelReconciler.flush_conntrack) spares the same TCP tuple
    (proto=6 ∧ daddr=portal_addr ∧ dport=portal_port).  Both the nft
    guard and the flush exception use the exact same coordinates so a
    portal connection can never be marked nor torn down by a pin
    change.

    The guard MUST come before the saddr-set lookups so the early
    return wins regardless of pinning state.  We anchor it after the
    iifname/private-subnets gates because both still apply (portal
    IS public, but classification is the wrong place to act on it).
    """
    ruleset = NftRenderer(
        catalog={"hub": object()},
        portal_addr="192.0.2.7",
        portal_port=1234,
        api_port=8080,
    ).render(pins={})
    # Slice the filter chain body out of the rendered text.
    filter_block = (
        ruleset.split("type filter hook prerouting", 1)[1]
        .split("}", 1)[0]
    )
    iifname_pos = filter_block.find('iifname != "backbone" return')
    portal_pos = filter_block.find("ip daddr 192.0.2.7 tcp dport 1234 return")
    classify_pos = filter_block.find("meta mark set")
    assert iifname_pos != -1
    assert portal_pos != -1, (
        "filter prerouting must contain a portal-bypass rule "
        "(`ip daddr <portal_addr> tcp dport <portal_port> return`) "
        "to keep portal flows out of the pinning ct mark family"
    )
    assert classify_pos != -1
    assert iifname_pos < portal_pos < classify_pos, (
        "portal bypass must sit between the iifname guard and the "
        "saddr classification so it short-circuits the chain "
        "regardless of pinning-set membership"
    )


def test_portal_redirect_does_not_set_ct_mark():
    """The portal NAT redirect rule must NOT stamp ct mark.

    The current pinning ruleset does not stamp `ct mark` anywhere —
    the conntrack flush is now done in Python (pyroute2.Conntrack)
    against the orig-direction tuple, with no ct-mark discriminator.
    This test pins that the portal redirect line in particular stays
    free of `ct mark` so that re-introducing a ct-mark scheme later
    (e.g. for a different subsystem) cannot accidentally tag portal
    connections without explicit consideration of the flush logic.
    """
    ruleset = NftRenderer(
        catalog={"hub": object()},
        portal_addr="192.0.2.1",
        portal_port=1111,
        api_port=80,
    ).render(pins={})
    redirect_line = next(
        line for line in ruleset.splitlines() if "redirect to :80" in line
    )
    assert "ct mark" not in redirect_line, (
        f"portal redirect line must not stamp ct mark; got: {redirect_line!r}"
    )


def test_pin_mark_base_carries_the_pin_bit():
    """PIN_MARK_BASE must include the discriminator bit 0x800."""
    from pinning.nft_renderer import PIN_BIT, PIN_MARK_BASE
    assert PIN_BIT == 0x800
    assert PIN_MARK_BASE & PIN_BIT == PIN_BIT, (
        f"PIN_MARK_BASE ({PIN_MARK_BASE:#x}) must have pin bit "
        f"{PIN_BIT:#x} set so dns_dnat can short-circuit on "
        f"`meta mark & {PIN_BIT:#x} != 0`"
    )


def test_pin_snapshot_renders_set_elements_with_ttl():
    """Pins land in the right per-egress set as element-with-timeout."""
    ruleset = NftRenderer(
        catalog={"outer-pt": object(), "outer-de": object()},
        portal_addr="192.0.2.1",
        portal_port=1,
        api_port=80,
        ttl_seconds=86400,
    ).render(pins={
        "172.30.0.3": "outer-pt", "172.30.0.4": "outer-de",
    })
    pt_set = ruleset.split("set pinned_outer_pt")[1].split("}")[0]
    de_set = ruleset.split("set pinned_outer_de")[1].split("}")[0]
    assert "172.30.0.3 timeout 86400s" in pt_set
    assert "172.30.0.4 timeout 86400s" in de_set
    assert "172.30.0.3" not in de_set
    assert "172.30.0.4" not in pt_set


def test_renders_portal_redirect_in_nat_chain():
    """Portal redirect rule lives in a dedicated nat prerouting chain.

    REDIRECT is a NAT verdict; it is rejected by the kernel in a
    filter chain ('Operation not supported').  The renderer must
    emit a separate chain with `type nat hook prerouting` that
    contains only the redirect rule, leaving the existing filter
    chain unchanged.  The nat hook fires at the same priority as
    the filter hook (-150) so portal traffic is intercepted before
    any egress classification.
    """
    ruleset = NftRenderer(
        catalog={"hub": object()},
        portal_addr="192.0.2.1",
        portal_port=1111,
        api_port=80,
    ).render(pins={})
    # nat chain must exist and contain the redirect
    nat_chain_pos = ruleset.find("type nat hook prerouting")
    redirect_pos = ruleset.find("redirect to :80")
    filter_chain_pos = ruleset.find("type filter hook prerouting")
    guard_pos = ruleset.find('iifname != "backbone" return')
    assert nat_chain_pos != -1, "expected nat prerouting chain in output"
    assert redirect_pos != -1, "expected portal redirect line in output"
    assert filter_chain_pos != -1, "expected filter prerouting chain in output"
    assert guard_pos != -1, "expected iifname guard in filter chain"
    # redirect must appear inside the nat chain (before the filter chain)
    assert redirect_pos > nat_chain_pos, (
        "redirect must appear after the nat chain declaration"
    )
    assert redirect_pos < filter_chain_pos, (
        "redirect must appear in the nat chain, not the filter chain"
    )


def test_portal_redirect_uses_configured_addr_port_api_port():
    """Renderer threads portal kwargs into the redirect rule verbatim."""
    ruleset = NftRenderer(
        catalog={"hub": object()},
        portal_addr="192.0.2.7",
        portal_port=1234,
        api_port=8080,
    ).render(pins={})
    assert 'iifname "backbone"' in ruleset
    assert "ip daddr 192.0.2.7" in ruleset
    assert "tcp dport 1234" in ruleset
    assert "redirect to :8080" in ruleset


def test_constructor_rejects_missing_portal_kwargs():
    """Required portal kwargs have no defaults — typo at the call
    site fails loudly instead of silently emitting a placeholder
    redirect.

    Note: this test technically passes against the pre-feature ctor
    (because the ctor doesn't accept those kwargs at all, so popping
    one and calling raises TypeError). Once Task 2.3 adds portal_addr/
    port/api_port as required kwargs, this test pins the
    required-ness contract: it would FAIL if a future refactor gave
    any of the three a default value.
    """
    import pytest
    for missing in ("portal_addr", "portal_port", "api_port"):
        kwargs = dict(
            catalog={"hub": object()},
            portal_addr="192.0.2.1",
            portal_port=1111,
            api_port=80,
        )
        kwargs.pop(missing)
        with pytest.raises(TypeError):
            NftRenderer(**kwargs)
