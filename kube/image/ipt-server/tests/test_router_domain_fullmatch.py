"""Ensure Router uses re.fullmatch for DomainRule matching, not prefix re.match."""

from __future__ import annotations

import inspect
import re


def test_router_uses_fullmatch_not_match_for_domain_rules():
    """Router._collect_domain_candidates uses re.fullmatch, not re.match, for DomainRule.

    This verifies that routing domain patterns require full-string match, not prefix match.
    .*\\.ru must NOT match 'example.ru.com' (extra suffix), only 'example.ru'.
    """
    import Router

    src = inspect.getsource(Router)
    # Verify fullmatch is used with rule.domain somewhere in Router source
    assert "fullmatch" in src and "rule.domain" in src, (
        "Router must use fullmatch with rule.domain for DomainRule matching"
    )
    # Verify the old prefix-only re.match is gone
    # Count occurrences: re.match(rule.domain must be 0
    old_pattern_count = len(re.findall(r're\.match\(rule\.domain', src))
    assert old_pattern_count == 0, (
        f"Router still has {old_pattern_count} use(s) of re.match(rule.domain, ...) — "
        "switch to re.fullmatch"
    )


def test_fullmatch_semantics_on_dot_star_ru():
    """Demonstrate that .*\\.ru with fullmatch does not match example.ru.com."""
    pattern = r".*\.ru"
    assert re.fullmatch(pattern, "example.ru") is not None
    assert re.fullmatch(pattern, "example.ru.com") is None
