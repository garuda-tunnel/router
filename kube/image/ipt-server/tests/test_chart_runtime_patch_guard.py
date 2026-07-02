"""Regression guard against the stale runtime-patch shadow.

Background: `kube/charts/ipt-server/files/sitecustomize.py` was a legacy in-cluster
hotfix carrier. It was rendered into a ConfigMap (`ipt-server-runtime-patches`),
mounted at `/runtime-patches`, and loaded via `PYTHONPATH=/runtime-patches`. At
import time it monkey-patched `_probe_gw_alive` / `_find_router_owning_address`
(backed by its own private, *pre-fix* `_resolve_direct_router_nexthop`) onto the
nexthop-monitor modules. Once the correct resolver shipped in-image (1.2.1+),
this shadow silently overrode the fix on every pod start, blackholing de/pt
egress.

The whole mechanism was removed. These tests guard against it (or any equivalent
resolver/probe monkey-patch) coming back and re-shadowing the in-image code.
"""

import subprocess
from pathlib import Path

import pytest

# tests/ -> ipt-server (image root) -> image -> kube
_KUBE_DIR = Path(__file__).resolve().parents[3]
_CHART_DIR = _KUBE_DIR / "charts" / "ipt-server"
_RUNTIME_PATCH_FILE = _CHART_DIR / "files" / "sitecustomize.py"
_RUNTIME_PATCH_TEMPLATE = _CHART_DIR / "templates" / "runtime-patches.yaml"

# Functions whose behaviour must come from the in-image code, never from a
# chart-baked runtime patch that shadows them.
_SHADOWED_SYMBOLS = (
    "_resolve_direct_router_nexthop",
    "_probe_gw_alive",
    "_find_router_owning_address",
)


def test_runtime_patch_mechanism_removed():
    """The legacy runtime-patch ConfigMap and its sitecustomize payload are gone.

    The in-image resolver (LPM + skip-default + on-backbone fallback) must run
    unshadowed. If the file/template come back, this fails loudly.
    """
    assert not _RUNTIME_PATCH_FILE.exists(), (
        f"{_RUNTIME_PATCH_FILE} reintroduces a chart-baked runtime patch that can "
        "shadow the in-image nexthop resolver. Do not re-add it."
    )
    assert not _RUNTIME_PATCH_TEMPLATE.exists(), (
        f"{_RUNTIME_PATCH_TEMPLATE} reintroduces the runtime-patches ConfigMap."
    )


def test_sitecustomize_does_not_shadow_resolver_if_present():
    """Defence-in-depth: if any runtime-patch sitecustomize file ever returns,
    it must not redefine/monkey-patch the resolver or probe functions."""
    if not _RUNTIME_PATCH_FILE.exists():
        pytest.skip("runtime-patch file fully removed (preferred end state)")
    source = _RUNTIME_PATCH_FILE.read_text()
    for symbol in _SHADOWED_SYMBOLS:
        assert symbol not in source, (
            f"{_RUNTIME_PATCH_FILE} references {symbol!r}; a chart-baked patch "
            "must never redefine or reassign the in-image nexthop resolver/probe."
        )


def test_rendered_chart_has_no_runtime_patch_wiring():
    """`helm template` output must not mount runtime patches onto PYTHONPATH.

    Guards the deployment wiring (volume/mount/env) as well as the ConfigMap, so
    the pod's Python never auto-imports a shadowing `sitecustomize` module.
    """
    if not _which("helm"):
        pytest.skip("helm not available")
    values = _CHART_DIR.parent.parent / "tests" / "helm" / "values-default.yaml"
    rendered = subprocess.run(
        [
            "helm",
            "template",
            "ipt-server",
            str(_CHART_DIR),
            "--namespace",
            "garuda",
            "-f",
            str(values),
        ],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    for needle in (
        "runtime-patches",
        "sitecustomize",
        "/runtime-patches",
    ):
        assert needle not in rendered, (
            f"rendered chart still contains {needle!r}: the runtime-patch shadow "
            "mechanism has not been fully removed."
        )


def _which(name: str) -> bool:
    from shutil import which

    return which(name) is not None
