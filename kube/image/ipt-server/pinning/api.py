"""HTTP API and UI for per-source-IP egress pinning.

Wiring shape:
  * `PinningApp` (frozen dataclass) — bundles the three runtime deps
    handlers need (manager, reconciler, catalog).
  * `app_key` — a single typed `web.AppKey[PinningApp]` so handler
    code accesses deps via `request.app[app_key].manager` (mypy /
    pyright resolve the type without cast).
  * `routes` — module-level `web.RouteTableDef`; handlers register
    declaratively with `@routes.get(path)`.  `create_app(...)` does
    `app[app_key] = PinningApp(...)` then `app.add_routes(routes)`.

Replacing the previous 3-string-key god-dict (`app["manager"]` etc.)
removes a class of typo-induced runtime KeyError bugs and makes the
handler dependency surface explicit at every call site.
"""
from __future__ import annotations

import os
import time
from dataclasses import dataclass
from typing import Mapping

import jinja2
from aiohttp import web

from pinning.kernel import KernelReconciler
from pinning.manager import PinningManager


_TEMPLATE_DIR = os.path.join(os.path.dirname(__file__), "templates")
_jinja_env = jinja2.Environment(
    loader=jinja2.FileSystemLoader(_TEMPLATE_DIR),
    autoescape=jinja2.select_autoescape(["html"]),
)


@dataclass(frozen=True)
class PinningApp:
    """All state the pinning HTTP handlers need.

    Frozen so handlers cannot accidentally rewire the manager or the
    reconciler at runtime; the only legitimate mutation point is
    `create_app`, which builds a fresh `PinningApp` per
    `web.Application`.
    """

    manager: PinningManager
    reconciler: KernelReconciler
    catalog: Mapping[str, object]


# Module-level typed key.  `request.app[app_key]` is statically typed
# as PinningApp by mypy/pyright (aiohttp >=3.9 contract).
app_key: web.AppKey[PinningApp] = web.AppKey("pinning", PinningApp)


# Module-level RouteTableDef.  The same `routes` object is reused
# across multiple apps in the test suite — supported by aiohttp:
# `add_routes(routes)` registers each declared route into the target
# Application without sharing per-route state.
routes = web.RouteTableDef()


def _client_saddr(request: web.Request) -> str:
    """Source IP comes strictly from the TCP transport. X-Forwarded-For is ignored."""
    if request.remote is None:
        raise web.HTTPBadRequest(reason="cannot determine client saddr")
    return request.remote


@routes.get("/api/egresses")
async def list_egresses(request: web.Request) -> web.Response:
    deps = request.app[app_key]
    return web.json_response({"egresses": sorted(deps.catalog.keys())})


@routes.get("/api/pin")
async def get_pin(request: web.Request) -> web.Response:
    deps = request.app[app_key]
    saddr = _client_saddr(request)
    entry = await deps.manager.get(saddr)
    if entry is None:
        return web.json_response(
            {
                "saddr": saddr,
                "egress": None,
                "expires_in": None,
                "expires_at": None,
            }
        )
    now = time.time()
    return web.json_response(
        {
            "saddr": saddr,
            "egress": entry.egress,
            "expires_in": max(0, int(entry.expires_at - now)),
            "expires_at": entry.expires_at,
        }
    )


@routes.get("/api/pin/set")
async def set_pin(request: web.Request) -> web.Response:
    deps = request.app[app_key]
    egress = request.query.get("egress")
    if not egress:
        raise web.HTTPBadRequest(
            reason="missing required query parameter: egress"
        )
    if egress not in deps.catalog:
        return web.json_response(
            {"error": f"unknown egress {egress!r}"},
            status=400,
        )
    saddr = _client_saddr(request)
    entry = await deps.manager.set(saddr, egress)
    # nft reconcile sees the up-to-date snapshot.
    await deps.reconciler.reconcile(await deps.manager.snapshot())
    # Drop existing conntrack flows for this saddr so long-lived
    # browser connections (HTTP/2, persistent) re-enter the freshly-
    # loaded pinning prerouting chain on their next packet instead
    # of riding the conntrack-tied routing decision the previous
    # pin had committed.
    await deps.reconciler.flush_conntrack(saddr)
    if request.query.get("return") == "html":
        raise web.HTTPSeeOther(location="/")
    now = time.time()
    return web.json_response(
        {
            "saddr": saddr,
            "egress": entry.egress,
            "expires_in": max(0, int(entry.expires_at - now)),
            "expires_at": entry.expires_at,
        }
    )


@routes.get("/api/pin/clear")
async def clear_pin(request: web.Request) -> web.Response:
    deps = request.app[app_key]
    saddr = _client_saddr(request)
    await deps.manager.clear(saddr)
    await deps.reconciler.reconcile(await deps.manager.snapshot())
    # See set_pin: drop conntrack so the caller's existing flows
    # don't keep riding the cleared pin's prior routing decision.
    await deps.reconciler.flush_conntrack(saddr)
    if request.query.get("return") == "html":
        raise web.HTTPSeeOther(location="/")
    return web.json_response(
        {
            "saddr": saddr,
            "egress": None,
            "expires_in": None,
            "expires_at": None,
        }
    )


@routes.get("/")
async def index(request: web.Request) -> web.Response:
    deps = request.app[app_key]
    saddr = _client_saddr(request)
    entry = await deps.manager.get(saddr)
    template = _jinja_env.get_template("index.html")
    rendered = template.render(
        saddr=saddr,
        current=(entry.egress if entry else None),
        expires_in=(
            max(0, int(entry.expires_at - time.time())) if entry else None
        ),
        egresses=sorted(deps.catalog.keys()),
    )
    return web.Response(body=rendered, content_type="text/html")


def create_app(*, manager, reconciler, catalog) -> web.Application:
    """Build an aiohttp application bound to the given manager/reconciler.

    Public signature unchanged from the previous string-keyed version
    so `pinning.bootstrap.setup_pinning` and the test suite need no
    edits.  Internally we package the three deps into a `PinningApp`
    and expose them via the typed `app_key`.
    """
    app = web.Application()
    app[app_key] = PinningApp(
        manager=manager, reconciler=reconciler, catalog=catalog,
    )
    app.add_routes(routes)
    return app
