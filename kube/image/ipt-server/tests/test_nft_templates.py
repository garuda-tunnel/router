from unittest.mock import patch
from ipt_server import main as ipt_main


def test_dns_dnat_template_renders_with_backend_ip():
    rendered = ipt_main._render_dns_dnat_ruleset("10.0.0.5")
    assert "dnat to 10.0.0.5:1053" in rendered
    assert "ip daddr 10.0.0.5 udp dport 1053 masquerade" in rendered
    assert rendered.strip().startswith("table inet dns_dnat_ipt_server")


def test_dns_dnat_short_circuits_on_pin_bit():
    """Packets carrying the pin bit (0x800) bypass the DNS hijack DNAT.

    The pinning chain (priority -150) stamps marks 0xA00+i on pinned
    saddrs.  This rule, at the top of the dns_dnat prerouting chain
    (priority -100), keeps the pinned packet's original destination
    so the per-egress routing table can forward DNS through the
    chosen egress instead of redirecting to the local pdns recursor
    (which lives on a docker network unreachable from per-egress
    tables).
    """
    ruleset = ipt_main._render_dns_dnat_ruleset("172.31.0.3")
    # Match-and-return on pin bit; placed before the DNAT rules.
    assert "meta mark and 0x800 != 0 return" in ruleset
    pos_return = ruleset.find("meta mark and 0x800 != 0 return")
    pos_dnat = ruleset.find("dnat to")
    assert pos_return != -1 and pos_dnat != -1
    assert pos_return < pos_dnat, (
        "pin-bit short-circuit must come before the DNAT rules"
    )


def test_border_template_empty_when_no_border():
    with patch.object(ipt_main.state, "CONFIG") as cfg:
        cfg.has_border = False
        assert ipt_main.render_border_rules() == ""


def test_border_template_includes_private_returns_when_has_border():
    with patch.object(ipt_main.state, "CONFIG") as cfg:
        cfg.has_border = True
        rendered = ipt_main.render_border_rules()
    assert "table inet border_ipt_server" in rendered
    for net in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "100.64.0.0/10"):
        assert f"ip daddr {net} return" in rendered
    assert 'oifname "border" masquerade' in rendered
