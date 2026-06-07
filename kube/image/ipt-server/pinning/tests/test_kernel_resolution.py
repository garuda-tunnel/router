"""Index-based helpers and constants in pinning.kernel.

Sorted-catalog index maps to (mark, table) deterministically so the
nft renderer (PIN_MARK_BASE) and the ip-rule installer (TABLE_BASE)
agree without sharing state.
"""
import pytest

from pinning import kernel
from pinning.nft_renderer import PIN_BIT, PIN_MARK_BASE


def test_constants_have_expected_values():
    assert kernel.PINNING_PROTO == 201
    assert kernel.PINNING_TABLE_BASE == 300
    assert kernel.PINNING_RULE_PRIORITY == 100
    assert PIN_MARK_BASE == 0xA00
    assert PIN_BIT == 0x800


def test_pin_mark_base_carries_pin_bit():
    assert PIN_MARK_BASE & PIN_BIT == PIN_BIT


def test_table_for_index_starts_at_table_base():
    assert kernel._table_for_index(0) == kernel.PINNING_TABLE_BASE
    assert kernel._table_for_index(3) == kernel.PINNING_TABLE_BASE + 3


def test_mark_for_index_starts_at_pin_mark_base():
    assert kernel._mark_for_index(0) == PIN_MARK_BASE
    assert kernel._mark_for_index(3) == PIN_MARK_BASE + 3


def test_egress_index_resolves_sorted_catalog_position():
    catalog = {"outer-pt": object(), "outer-de": object(), "hub": object()}
    rec = kernel.KernelReconciler(
        catalog=catalog,
        portal_addr="192.0.2.1",
        portal_port=1,
        api_port=80,
    )
    # sorted: hub, outer-de, outer-pt
    assert rec._egress_index("hub") == 0
    assert rec._egress_index("outer-de") == 1
    assert rec._egress_index("outer-pt") == 2


def test_egress_index_unknown_raises():
    rec = kernel.KernelReconciler(
        catalog={"hub": object()},
        portal_addr="192.0.2.1",
        portal_port=1,
        api_port=80,
    )
    with pytest.raises(ValueError):
        rec._egress_index("ghost")
