"""HTTP API tests against the nft-driven reconciler.

The API drives the kernel via reconciler.reconcile(snapshot) on every
set / clear; we mock both the manager and the reconciler to assert
call shapes rather than poke the live kernel.
"""
from __future__ import annotations

import dataclasses
from unittest.mock import AsyncMock

import pytest

from pinning.api import PinningApp, app_key, create_app


@pytest.fixture
def manager():
    return AsyncMock()


@pytest.fixture
def reconciler():
    return AsyncMock()


@pytest.fixture
def catalog():
    return {"outer-de": object(), "outer-pt": object()}


@pytest.fixture
async def cli(aiohttp_client, manager, reconciler, catalog):
    app = create_app(manager=manager, reconciler=reconciler, catalog=catalog)
    return await aiohttp_client(app)


async def test_egresses_endpoint_returns_sorted_keys(cli):
    resp = await cli.get("/api/egresses")
    body = await resp.json()
    assert body == {"egresses": ["outer-de", "outer-pt"]}


async def test_get_pin_unpinned_returns_null_egress(cli, manager):
    manager.get.return_value = None
    resp = await cli.get("/api/pin")
    body = await resp.json()
    assert body["egress"] is None


async def test_set_pin_calls_manager_then_reconciles_snapshot(
    cli, manager, reconciler
):
    """API: set in manager → query manager.snapshot() → reconcile(snapshot).

    Correctness depends on snapshot being queried *after* the set so
    the reconciler sees the new entry in the rendered nft.
    """
    import time
    from pinning.manager import PinEntry
    manager.set.return_value = PinEntry(
        saddr="127.0.0.1", egress="outer-pt",
        expires_at=time.time() + 86400,
    )
    manager.snapshot.return_value = {"127.0.0.1": "outer-pt"}
    resp = await cli.get("/api/pin/set?egress=outer-pt")
    assert resp.status == 200

    manager.set.assert_awaited_once()
    assert manager.set.await_args.args[1] == "outer-pt"

    reconciler.reconcile.assert_awaited_once()
    assert reconciler.reconcile.await_args.args[0] == {"127.0.0.1": "outer-pt"}


async def test_set_pin_unknown_egress_returns_400_no_kernel_call(
    cli, manager, reconciler
):
    resp = await cli.get("/api/pin/set?egress=ghost")
    assert resp.status == 400
    manager.set.assert_not_awaited()
    reconciler.reconcile.assert_not_awaited()


async def test_set_pin_missing_egress_query_returns_400(cli):
    resp = await cli.get("/api/pin/set")
    assert resp.status == 400


async def test_clear_pin_calls_manager_then_reconciles(cli, manager, reconciler):
    manager.snapshot.return_value = {}
    resp = await cli.get("/api/pin/clear")
    assert resp.status == 200
    manager.clear.assert_awaited_once()
    reconciler.reconcile.assert_awaited_once_with({})


async def test_set_pin_flushes_conntrack_for_caller_saddr(
    cli, manager, reconciler
):
    """After a pin change the reconciler must drop conntrack flows for
    the caller's saddr.  Without this, browsers reusing long-lived
    HTTP/2 connections keep egressing through the previous route
    (conntrack-tied) even though `meta mark set` re-fires on every
    packet.  curl sees the new egress (fresh flow) but the user does
    not — exactly the symptom we hit in production.

    Asserted shape: reconcile() runs first (kernel ruleset matches the
    new pin), THEN flush_conntrack(saddr) drops the now-stale flows.
    Reversing the order would race: a packet arriving between the
    flush and the reconcile would re-establish a flow under the OLD
    nft set membership, defeating the flush.
    """
    import time
    from pinning.manager import PinEntry
    # aiohttp test client connects from 127.0.0.1 loopback; PinEntry's
    # saddr field is informational only — the API uses request.remote.
    manager.set.return_value = PinEntry(
        saddr="127.0.0.1", egress="outer-pt",
        expires_at=time.time() + 86400,
    )
    manager.snapshot.return_value = {"127.0.0.1": "outer-pt"}
    resp = await cli.get("/api/pin/set?egress=outer-pt")
    assert resp.status == 200

    reconciler.flush_conntrack.assert_awaited_once_with("127.0.0.1")
    # Order: reconcile runs before flush_conntrack.  Use mock_calls on
    # the parent Mock to verify global ordering — AsyncMock's per-method
    # await_args_list cannot reveal ordering across different methods.
    method_names = [c[0] for c in reconciler.method_calls]
    assert method_names.index("reconcile") < method_names.index("flush_conntrack")


async def test_clear_pin_flushes_conntrack_for_caller_saddr(
    cli, manager, reconciler
):
    """Same contract for `clear`: after the pin is removed, drop the
    conntrack flows the (now-cleared) pin had biased so the caller's
    next request gets routed by the freshly-rendered fall-through
    path (no fwmark match → main table)."""
    manager.snapshot.return_value = {}
    resp = await cli.get("/api/pin/clear")
    assert resp.status == 200
    # The aiohttp test client connects from 127.0.0.1 — assert against
    # whatever request.remote returned, not a hard-coded value.  Pin the
    # exact saddr below for clarity.
    reconciler.flush_conntrack.assert_awaited_once()
    flush_saddr = reconciler.flush_conntrack.await_args.args[0]
    # request.remote on aiohttp_client loopback transport is "127.0.0.1".
    assert flush_saddr == "127.0.0.1"


async def test_set_pin_html_toggle_redirects(cli, manager):
    manager.snapshot.return_value = {}
    resp = await cli.get(
        "/api/pin/set?egress=outer-pt&return=html",
        allow_redirects=False,
    )
    assert resp.status == 303
    assert resp.headers["Location"] == "/"


def test_app_key_exposes_typed_pinning_app(catalog, manager, reconciler):
    """create_app stores deps under a typed AppKey, not string keys.

    Asserts the wiring contract used by handlers: `request.app[app_key]`
    is a frozen PinningApp carrying manager/reconciler/catalog.  We
    don't reach into private state from handlers in production code,
    but the *test* asserts the contract so a regression that drops
    the AppKey or repurposes it to something else fails loudly.
    """
    app = create_app(manager=manager, reconciler=reconciler, catalog=catalog)
    deps = app[app_key]
    assert isinstance(deps, PinningApp)
    assert deps.manager is manager
    assert deps.reconciler is reconciler
    assert deps.catalog is catalog


def test_pinning_app_is_frozen_dataclass():
    """PinningApp is immutable so handlers can't accidentally rewire."""
    assert dataclasses.is_dataclass(PinningApp)
    fields = {f.name: f for f in dataclasses.fields(PinningApp)}
    assert set(fields) == {"manager", "reconciler", "catalog"}
    # frozen=True means assignment raises FrozenInstanceError.
    pa = PinningApp(manager=object(), reconciler=object(), catalog={})
    with pytest.raises(dataclasses.FrozenInstanceError):
        pa.manager = object()  # type: ignore[misc]
