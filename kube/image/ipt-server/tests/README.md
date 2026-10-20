# ipt-server test suite

Pytest suite for the ipt-server component. Tests live next to the code they
own: `tests/` (component core), `ipt_server/tests/` (runtime wiring, CLI,
echo), `tasks/tests/` (monitor loops), `ipdb/tests/`, `pinning/tests/`.

## How to run

All commands run from the component root `kube/image/ipt-server` (the
directory containing `pyproject.toml`); from a repository clone:

```bash
cd kube/image/ipt-server
python -m venv /tmp/ipt-venv
/tmp/ipt-venv/bin/python -m pip install -r requirements.txt -r requirements-dev.txt
/tmp/ipt-venv/bin/python -m pytest                # fast local run
/tmp/ipt-venv/bin/python -m pytest --cov --cov-branch --cov-fail-under=90
```

CI runs the coverage command from the same directory on every pull request
and push to `main` (`.github/workflows/test-ipt-server.yml`), with line and
branch coverage each gated at 90% separately. Duplicate-test detection and the
bounded mutation check are documented in `../readme.md` ("Tests").

## Hermetic by construction

No test touches the host:

* **Kernel boundaries are faked, never executed.** `ip`, `nft`, `vtysh` and
  `conntrack` binaries, non-loopback `connect()`s, non-local name
  resolution, netlink socket binds and unpatched pyroute2
  constructors (`IPRoute`/`NDB`/`Conntrack`, including every in-repo
  usage-site binding) are rejected by an autouse fence in the
  component-root `conftest.py`. A test that forgets to patch a boundary
  fails immediately.
* **Router tests use `tests/support.hermetic_router`.** It patches
  `Router.Conntrack` *before* construction (no real netlink socket) and
  joins both worker threads in the owning test thread on exit, so
  `Router.__del__` never joins a thread from itself (the historical source
  of `PytestUnraisableExceptionWarning`). `FakeConntrack` provides
  scriptable conntrack dumps; `FrozenClock` makes TTL expiry an input, not
  elapsed wall time.
* **Shared state is isolated.** An autouse fixture snapshots and restores
  `ipt_server.state` globals and `ipt_server.main` module globals around
  every test; no test can poison another through `state.CONFIG`,
  `state.ROUTER`, pinning handles, or the interface snapshot.
* **The one intentional real network use:** `pinning/tests/test_api.py`
  serves the pinning HTTP API on loopback through `pytest-aiohttp`'s test
  client. Everything else fakes sockets.

## Deterministic synchronization

* No test sleeps to prove another thread reached a state. Worker/loop
  completion is observed through `threading.Event`/`asyncio.Event` set
  inside the fake boundary that produced the effect (see
  `tests/test_router_route_classes.py::TestQueuedDeleteRace` for the gated
  critical-section pattern, and `ipt_server/tests/test_async_main.py` for
  monitor-driven clean shutdown instead of timeout cancellation).
* The two latency-measurement tests (`echo` budget/responsiveness,
  `pinning/tests/test_bootstrap.py::test_liveness_loop_does_not_block_event_loop`)
  deliberately use short wall-clock intervals with generous margins; they
  measure responsiveness, they do not order races.

## Kernel-boundary assertions

Tests assert behavior through public interfaces. The exception is where the
command *is* the contract: the exact `ip -batch` / nft ruleset text the
kernel executes, and `ip nexthop` CLI argv in `tasks/tests/test_nexthop.py`.
There the boundary is faked and the emitted text asserted, with the reason
stated in the test docstring.

## Test data

Only `example.net`-style domains and RFC 5737 (192.0.2.0/24, 198.51.100.0/24,
203.0.113.0/24) / RFC 1918 / CGNAT addresses appear in tests and fixtures
(`tasks/tests/fixtures/` holds sanitized synthetic OSPF topologies). The
production default `pinning_portal_anchor_addr` is asserted via the model
field default, not by copying its public IP literal into a test.

## Coverage scope

`pyproject.toml` measures the component root (`source = ["."`]) with branch
coverage; tests and the vendored `nftables/` binding are omitted with the
justification written there. The gate is 90%.

## Conventions

* Every test module and test function carries a docstring stating the
  behavior it protects and why it matters (concise one-liners are fine).
* Regression tests name the protected behavior, not a ticket.
* Disposable verification tests are never committed.
