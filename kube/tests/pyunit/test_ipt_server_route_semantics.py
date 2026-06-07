"""Contract tests for IPT route_config.py normalize_route_entry.

Reference:
- `modules/ipt_server/kube/image/ipt-server/route_config.py`

Code:
- `normalize_route_entry()`

Assertion (new delivery contract):
- normalizer accepts only `route: {gw: <ip>}` or `route: {dev: <name>}` shape
- legacy `gw`, `next_hop`, `interface` flat keys are rejected with ValueError
"""

import importlib.util
from pathlib import Path
import pytest


IPT_SERVER_DIR = Path(__file__).resolve().parents[2] / "image" / "ipt-server"
ROUTE_CONFIG_PATH = IPT_SERVER_DIR / "route_config.py"

spec = importlib.util.spec_from_file_location("ipt_route_config", ROUTE_CONFIG_PATH)
module = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(module)
normalize_route_entry = module.normalize_route_entry


# ---------------------------------------------------------------------------
# New contract: route key required; legacy flat keys rejected
# ---------------------------------------------------------------------------


def test_next_hop_flat_key_is_rejected():
    with pytest.raises(ValueError, match="legacy route key"):
        normalize_route_entry({"net": "0.0.0.0/0", "next_hop": "10.9.19.2"})


def test_interface_flat_key_is_rejected():
    with pytest.raises(ValueError, match="legacy route key"):
        normalize_route_entry({"country": "RU", "interface": "_DEFAULT"})


def test_gw_flat_key_is_rejected():
    with pytest.raises(ValueError, match="legacy route key"):
        normalize_route_entry({"net": "1.1.1.1/32", "gw": "wg_uk", "weight": 1})


def test_entry_without_route_key_is_rejected():
    with pytest.raises(ValueError):
        normalize_route_entry({"net": "0.0.0.0/0", "metric": 10})


# ---------------------------------------------------------------------------
# Validation: route object constraints still enforced
# ---------------------------------------------------------------------------


def test_normalize_rejects_route_with_both_gw_and_dev():
    with pytest.raises(ValueError, match="route.*gw.*dev"):
        normalize_route_entry(
            {"net": "1.1.1.1/32", "route": {"gw": "10.9.19.2", "dev": "eth0"}}
        )


def test_normalize_rejects_string_route_syntax_in_v1():
    with pytest.raises(ValueError, match="string.*route|object-form"):
        normalize_route_entry({"net": "1.1.1.1/32", "route": "via 172.30.0.1 dev eth0"})


def test_normalize_rejects_mixed_interface_and_next_hop():
    with pytest.raises(ValueError, match="legacy route key"):
        normalize_route_entry(
            {"net": "1.1.1.1/32", "gw": "wg_uk", "next_hop": "10.0.0.5"}
        )


def test_normalize_rejects_interface_name_in_next_hop():
    with pytest.raises(ValueError, match="legacy route key"):
        normalize_route_entry({"net": "0.0.0.0/0", "next_hop": "wg_uk", "metric": 10})


# ---------------------------------------------------------------------------
# Valid new contract: passthrough for route: {gw: ...} or route: {dev: ...}
# ---------------------------------------------------------------------------


def test_passthrough_of_already_v1_route_gw():
    route = normalize_route_entry({"net": "0.0.0.0/0", "route": {"gw": "10.9.19.2"}})
    assert route["route"]["gw"] == "10.9.19.2"
    assert "dev" not in route["route"]


def test_passthrough_of_already_v1_route_dev():
    route = normalize_route_entry({"country": "RU", "route": {"dev": "_DEFAULT"}})
    assert route["route"]["dev"] == "_DEFAULT"
    assert "gw" not in route["route"]
