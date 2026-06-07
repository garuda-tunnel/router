import ipt_server.main  # noqa: F401  # side effect: extends ipt_server.__path__


def test_state_has_interfaces_snapshot_and_lock():
    """state module must expose INTERFACES dict and INTERFACES_LOCK."""
    from ipt_server import state

    assert hasattr(state, "INTERFACES")
    assert isinstance(state.INTERFACES, dict)
    assert hasattr(state, "INTERFACES_LOCK")
    # threading.Lock() returns a _thread.lock, not a class instance; test
    # the behavioural contract instead of isinstance.
    lock = state.INTERFACES_LOCK
    assert hasattr(lock, "acquire")
    assert hasattr(lock, "release")
    with lock:
        pass


import asyncio
from unittest.mock import MagicMock


def _fake_link(name: str, index: int, operstate: str = "UP"):
    link = MagicMock()
    link.get_attr.side_effect = lambda attr: {
        "IFLA_IFNAME": name,
        "IFLA_OPERSTATE": operstate,
    }[attr]
    link.__getitem__.side_effect = lambda key: {"index": index}[key]
    return link


def test_refresh_interfaces_snapshot_writes_state(monkeypatch):
    """refresh_interfaces_snapshot must populate state.INTERFACES from netlink."""
    from ipt_server import state
    from ipt_server.tasks import interface_monitor

    # Clean slate
    with state.INTERFACES_LOCK:
        state.INTERFACES.clear()

    fake_ipr_instance = MagicMock()
    fake_ipr_instance.__enter__.return_value = fake_ipr_instance
    fake_ipr_instance.__exit__.return_value = False
    fake_ipr_instance.get_links.return_value = [
        _fake_link("backbone", 3),
        _fake_link("border", 4),
        _fake_link("lo", 1),
    ]
    monkeypatch.setattr(interface_monitor, "IPRoute", lambda: fake_ipr_instance)

    asyncio.run(interface_monitor.refresh_interfaces_snapshot())

    with state.INTERFACES_LOCK:
        snapshot = dict(state.INTERFACES)
    assert snapshot == {"backbone": 3, "border": 4, "lo": 1}


def test_refresh_interfaces_snapshot_removes_disappeared_interface(monkeypatch):
    """If an interface is gone in the new snapshot, state drops it."""
    from ipt_server import state
    from ipt_server.tasks import interface_monitor

    with state.INTERFACES_LOCK:
        state.INTERFACES.clear()
        state.INTERFACES.update({"backbone": 3, "border": 4})

    fake_ipr_instance = MagicMock()
    fake_ipr_instance.__enter__.return_value = fake_ipr_instance
    fake_ipr_instance.__exit__.return_value = False
    fake_ipr_instance.get_links.return_value = [
        _fake_link("backbone", 3),
        # border is absent
    ]
    monkeypatch.setattr(interface_monitor, "IPRoute", lambda: fake_ipr_instance)

    asyncio.run(interface_monitor.refresh_interfaces_snapshot())

    with state.INTERFACES_LOCK:
        snapshot = dict(state.INTERFACES)
    assert snapshot == {"backbone": 3}
    assert "border" not in snapshot


def test_interfaces_descriptor_reads_state_snapshot():
    """The interfaces property getter reads from state.INTERFACES.

    Invokes the classmethod-wrapped property getter directly through
    ``__dict__['interfaces'].__func__.fget`` because Python 3.13 does
    not run the ``@classmethod @property`` chain via plain attribute
    access. Production runs Python 3.12 where the chain works; the
    direct-fget invocation is semantically equivalent and covers both.
    """
    from ipt_server import state
    from route import RouteObject

    with state.INTERFACES_LOCK:
        state.INTERFACES.clear()
        state.INTERFACES.update({"foo": 7, "bar": 8})

    descriptor = RouteObject.__dict__["interfaces"]
    if hasattr(descriptor, "__func__"):
        # Python 3.13: classmethod wraps the property
        getter = descriptor.__func__.fget
    else:
        # Python 3.12: classmethod(property(...)) collapses, we get the property
        getter = descriptor.fget
    result = getter(RouteObject)
    assert result == {"foo": [(7, None)], "bar": [(8, None)]}


