"""Suite-wide pytest fixtures for the ipt-server component tests.

Every test in this component must be hermetic:

* no real ``ip``/``nft``/``vtysh`` binary may be executed,
* constructing pyroute2 ``IPRoute``/``NDB``/``Conntrack`` or binding a
  netlink socket fails fast unless the test patched that boundary,
* no network connection beyond loopback may be attempted,
* the shared runtime state in ``ipt_server.state`` (and the module globals
  of ``ipt_server.main``) is saved before and restored after every test —
  restoring the original container *objects*, not just their contents —
  so no test can poison another by mutating or rebinding them.

The autouse fixtures below enforce all of this mechanically, so an
individual test cannot forget them. Router worker-thread hygiene is
provided by ``tests.support.hermetic_router`` — see tests/README.md.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

_COMPONENT_ROOT = Path(__file__).resolve().parent
if str(_COMPONENT_ROOT) not in sys.path:
    sys.path.insert(0, str(_COMPONENT_ROOT))

# Importing ipt_server.main extends the namespace package path so that
# ipt_server.tasks.* is importable in any test, regardless of import order.
import ipt_server.main  # noqa: E402,F401

# Binaries that would touch the host kernel/network stack. Tests fake the
# kernel boundary instead; a real execution of one of these is a test bug.
_FORBIDDEN_BINARIES = {"ip", "nft", "vtysh", "conntrack"}

# Hostnames tests may resolve without patching: IP literals never hit a
# resolver, and only the documentation domains are ever expected.
_ALLOWED_RESOLVE_SUFFIXES = ("example.net", "localhost")


def _argv_violation(argv) -> str | None:
    if not argv:
        return None
    program = str(argv[0])
    name = Path(program).name
    if name in _FORBIDDEN_BINARIES:
        return (
            f"test attempted to execute host-touching binary {program!r}; "
            "patch the kernel boundary instead (see tests/README.md)"
        )
    return None


@pytest.fixture(autouse=True)
def _forbid_host_access(monkeypatch):
    """Fail fast on host access: forbidden binaries, pyroute2 constructors,
    netlink binds, non-loopback connects and non-local name resolution.

    Wraps ``subprocess.run``/``subprocess.Popen``, the ``socket`` module's
    connect/resolve entry points, and the pyroute2 constructor classes for
    the duration of each test. Tests that patch these callables (or the
    module-level boundary names that use them) replace the wrapper
    wholesale — they have faked the boundary, so there is nothing left to
    guard.
    """
    real_run = subprocess.run
    real_popen = subprocess.Popen

    def guarded_run(args, *a, **kw):
        violation = _argv_violation(args if isinstance(args, (list, tuple)) else [args])
        if violation:
            raise AssertionError(violation)
        return real_run(args, *a, **kw)

    def guarded_popen(args, *a, **kw):
        violation = _argv_violation(args if isinstance(args, (list, tuple)) else [args])
        if violation:
            raise AssertionError(violation)
        return real_popen(args, *a, **kw)

    monkeypatch.setattr(subprocess, "run", guarded_run)
    monkeypatch.setattr(subprocess, "Popen", guarded_popen)

    import socket

    real_socket_cls = socket.socket

    class _FencedSocket(real_socket_cls):
        """socket subclass that refuses to bind AF_NETLINK sockets."""

        def bind(self, address):
            if self.family == socket.AF_NETLINK:
                raise AssertionError(
                    "test attempted to bind a netlink socket; patch the "
                    "netlink boundary (IPRoute/Conntrack) instead "
                    "(see tests/README.md)"
                )
            return super().bind(address)

    monkeypatch.setattr(socket, "socket", _FencedSocket)

    real_connect = real_socket_cls.connect
    real_gethostbyname = socket.gethostbyname

    def guarded_connect(self, address):
        try:
            host = address[0]
        except (TypeError, IndexError):
            host = None  # AF_UNIX / non-inet address shapes are local by nature
        if isinstance(host, str):
            import ipaddress

            try:
                addr = ipaddress.ip_address(host)
                loopback = addr.is_loopback
            except ValueError:
                loopback = False
            if not loopback:
                raise AssertionError(
                    f"test attempted to connect to non-loopback {address!r}; "
                    "patch the socket boundary instead (see tests/README.md)"
                )
        return real_connect(self, address)

    def guarded_gethostbyname(hostname):
        if isinstance(hostname, str) and not hostname.endswith(
            _ALLOWED_RESOLVE_SUFFIXES
        ):
            import ipaddress

            try:
                ipaddress.ip_address(hostname)
            except ValueError:
                raise AssertionError(
                    f"test attempted to resolve {hostname!r}; patch "
                    "socket.gethostbyname at the boundary instead "
                    "(see tests/README.md)"
                ) from None
        return real_gethostbyname(hostname)

    monkeypatch.setattr(real_socket_cls, "connect", guarded_connect)
    monkeypatch.setattr(socket, "gethostbyname", guarded_gethostbyname)

    # pyroute2 constructors: constructing the real objects opens netlink
    # sockets. Guard the pyroute2 classes AND every in-repo usage-site
    # binding of them (modules do `from pyroute2 import IPRoute`, so
    # patching pyroute2 alone would not stop an unpatched construction).
    # Tests must patch the boundary name in the using module; an
    # unpatched construction is a hermeticity bug.
    import pyroute2

    def make_guard(owner, name):
        def guarded_ctor(*args, **kwargs):
            raise AssertionError(
                f"test constructed pyroute2 {name} via {owner} without "
                "patching the netlink boundary; patch it in the using "
                "module (see tests/README.md)"
            )

        return guarded_ctor

    for cls_name in ("IPRoute", "NDB", "Conntrack"):
        monkeypatch.setattr(pyroute2, cls_name, make_guard("pyroute2", cls_name))

    import Router as router_module
    import ipt_server.main as ipt_main
    from ipt_server.tasks import interface_monitor, nexthop_monitor

    monkeypatch.setattr(
        router_module, "Conntrack", make_guard("Router", "Conntrack")
    )
    monkeypatch.setattr(
        ipt_main, "IPRoute", make_guard("ipt_server.main", "IPRoute")
    )
    monkeypatch.setattr(
        interface_monitor, "IPRoute", make_guard("interface_monitor", "IPRoute")
    )
    monkeypatch.setattr(
        nexthop_monitor, "IPRoute", make_guard("nexthop_monitor", "IPRoute")
    )

    yield


@pytest.fixture(autouse=True)
def _restore_shared_runtime_state():
    """Snapshot and restore shared mutable state around every test.

    Saves the *container objects themselves* (dicts/sets bound to
    ``ipt_server.state`` and ``ipt_server.main``) and, on teardown,
    restores both their identity (rebinding the original object, so tests
    that reassigned the attribute lose the reassignment) and their
    contents (so tests that mutated the original object in place lose the
    mutation).
    """
    from ipt_server import main as ipt_main
    from ipt_server import state

    saved_bindings = {
        "CONFIG": state.CONFIG,
        "ROUTER": state.ROUTER,
        "PINNING_MANAGER": state.PINNING_MANAGER,
        "PINNING_RECONCILER": state.PINNING_RECONCILER,
        "INTERFACES": state.INTERFACES,
        "INTERFACE_HEALTH": state.INTERFACE_HEALTH,
    }
    saved_contents = {
        "INTERFACES": dict(state.INTERFACES),
        "INTERFACE_HEALTH": dict(state.INTERFACE_HEALTH),
    }
    saved_main_bindings = {
        "_LAST_DNS_BACKEND_IP": ipt_main._LAST_DNS_BACKEND_IP,
        "_BACKGROUND_TASKS": ipt_main._BACKGROUND_TASKS,
    }
    saved_task_contents = set(ipt_main._BACKGROUND_TASKS)

    yield

    # Restore identity first, then the original contents into the
    # original containers.
    for name, obj in saved_bindings.items():
        setattr(state, name, obj)
    state.INTERFACES.clear()
    state.INTERFACES.update(saved_contents["INTERFACES"])
    state.INTERFACE_HEALTH.clear()
    state.INTERFACE_HEALTH.update(saved_contents["INTERFACE_HEALTH"])

    ipt_main._LAST_DNS_BACKEND_IP = saved_main_bindings["_LAST_DNS_BACKEND_IP"]
    tasks = saved_main_bindings["_BACKGROUND_TASKS"]
    ipt_main._BACKGROUND_TASKS = tasks
    tasks.clear()
    tasks.update(saved_task_contents)
