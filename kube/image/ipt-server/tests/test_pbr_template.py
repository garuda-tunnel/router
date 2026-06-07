"""Contract tests for pbr.nft.j2: DNS mark branch and geo-routing guard."""

import unittest
from types import SimpleNamespace


class TestPbrTemplateDnsBranch(unittest.TestCase):
    """Contract tests for pbr.nft.j2: DNS mark branch and geo-routing guard."""

    def _render(self, pbr_mark=0x200, dns_mark=0x201, interfaces=("backbone",)):
        import jinja2
        from pathlib import Path

        template_dir = Path(__file__).parent.parent / "templates"
        env = jinja2.Environment(loader=jinja2.FileSystemLoader(str(template_dir)))
        env.filters["hex"] = lambda x: format(x, "x")
        template = env.get_template("pbr.nft.j2")

        config = SimpleNamespace(
            pbr_mark=pbr_mark,
            dns_mark=dns_mark,
            interfaces=list(interfaces),
            domain_route_ttl=300,
        )
        return template.render(config=config)

    def test_dns_udp_branch_sets_dns_mark_before_geo_routing(self):
        """UDP dport 53 rule sets dns_mark and appears before the geo-routing mark rule."""
        rendered = self._render()
        dns_udp_line = next(
            (
                ln
                for ln in rendered.splitlines()
                if "udp dport 53" in ln and "meta mark set" in ln
            ),
            None,
        )
        geo_mark_line = next(
            (
                ln
                for ln in rendered.splitlines()
                if "daddr != @private_subnets" in ln and "meta mark set 0x200" in ln
            ),
            None,
        )
        self.assertIsNotNone(
            dns_udp_line, f"DNS UDP classification rule missing in:\n{rendered}"
        )
        self.assertIsNotNone(
            geo_mark_line, f"Geo-routing mark rule missing in:\n{rendered}"
        )
        self.assertIn("0x201", dns_udp_line)
        self.assertLess(
            rendered.index(dns_udp_line),
            rendered.index(geo_mark_line),
            "DNS classification must render before geo-routing classification",
        )

    def test_dns_tcp_branch_sets_dns_mark(self):
        """TCP dport 53 rule sets dns_mark."""
        rendered = self._render()
        tcp_line = next(
            (
                ln
                for ln in rendered.splitlines()
                if "tcp dport 53" in ln and "meta mark set" in ln
            ),
            None,
        )
        self.assertIsNotNone(
            tcp_line, f"DNS TCP classification rule missing in:\n{rendered}"
        )
        self.assertIn("0x201", tcp_line)

    def test_geo_routing_mark_rule_is_guarded_by_meta_mark_zero(self):
        """The generic daddr-based mark-set rule must only fire when meta mark 0x0."""
        rendered = self._render()
        line = next(
            (
                ln
                for ln in rendered.splitlines()
                if "daddr != @private_subnets" in ln and "meta mark set 0x200" in ln
            ),
            None,
        )
        self.assertIsNotNone(
            line, f"Geo-routing meta mark set rule missing in:\n{rendered}"
        )
        self.assertIn("meta mark 0x0", line)

    def test_geo_routing_ct_mark_rule_is_guarded_by_meta_mark_zero(self):
        """The ct mark set rule (persistence) must also only fire when meta mark 0x0."""
        rendered = self._render()
        line = next(
            (
                ln
                for ln in rendered.splitlines()
                if "daddr != @private_subnets" in ln and "ct mark set 0x200" in ln
            ),
            None,
        )
        self.assertIsNotNone(line, f"Geo-routing ct mark rule missing in:\n{rendered}")
        self.assertIn("meta mark 0x0", line)

    def test_ct_timeout_rules_still_match_on_pbr_mark(self):
        """ct timeout rules continue to match on pbr_mark (0x200), not dns_mark."""
        rendered = self._render()
        udp_timeout_line = next(
            (
                ln
                for ln in rendered.splitlines()
                if "udp_marked_timeout" in ln and "ct timeout set" in ln
            ),
            None,
        )
        tcp_timeout_line = next(
            (
                ln
                for ln in rendered.splitlines()
                if "tcp_marked_timeout" in ln and "ct timeout set" in ln
            ),
            None,
        )
        self.assertIsNotNone(udp_timeout_line)
        self.assertIsNotNone(tcp_timeout_line)
        self.assertIn("meta mark 0x200", udp_timeout_line)
        self.assertIn("meta mark 0x200", tcp_timeout_line)

    def test_dns_branch_uses_configured_interfaces(self):
        """DNS classification rules are scoped to config.interfaces."""
        rendered = self._render(interfaces=("backbone", "wg-firezone"))
        dns_udp_line = next(
            (
                ln
                for ln in rendered.splitlines()
                if "udp dport 53" in ln and "meta mark set" in ln
            ),
            None,
        )
        self.assertIsNotNone(dns_udp_line)
        self.assertIn('"backbone"', dns_udp_line)
        self.assertIn('"wg-firezone"', dns_udp_line)


if __name__ == "__main__":
    unittest.main()
