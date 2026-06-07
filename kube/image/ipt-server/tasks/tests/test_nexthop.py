"""Unit tests for kernel nexthop object management.

Validates: nexthop.py subprocess calls produce correct ip-nexthop CLI args.
Code: tasks/nexthop.py
"""

from unittest.mock import patch, MagicMock
import subprocess
import pytest

import nexthop


_PROTO = nexthop.PROTO  # 100


def _mock_run(returncode=0):
    """Return a mock subprocess.CompletedProcess with given returncode."""
    m = MagicMock(spec=subprocess.CompletedProcess)
    m.returncode = returncode
    return m


# ---------------------------------------------------------------------------
# flush_owned
# ---------------------------------------------------------------------------


def test_flush_owned_calls_correct_args():
    """Flush owned nexthop objects issues correct ip-nexthop CLI args.

    Validates: flush_owned() issues `ip nexthop flush proto 199`.
    Code: nexthop.flush_owned
    Assertion: subprocess.run called with ["ip", "nexthop", "flush", "proto", "100"].
    """
    with patch("subprocess.run", return_value=_mock_run()) as mock_run:
        nexthop.flush_owned()
        mock_run.assert_called_once_with(
            ["ip", "nexthop", "flush", "proto", str(_PROTO)],
            capture_output=True,
            check=True,
        )


def test_flush_owned_raises_on_failure():
    """Flush failure raises RuntimeError to prevent stale nexthop state.

    Validates: flush_owned() raises RuntimeError when subprocess raises CalledProcessError.
    Code: nexthop.flush_owned
    Assertion: RuntimeError is raised when subprocess.run raises CalledProcessError.
    """
    with patch("subprocess.run", side_effect=subprocess.CalledProcessError(1, "ip")):
        with pytest.raises(RuntimeError):
            nexthop.flush_owned()


# ---------------------------------------------------------------------------
# create
# ---------------------------------------------------------------------------


def test_create_calls_correct_args():
    """Create nexthop object issues correct ip-nexthop add CLI args.

    Validates: create(nhid, via, dev) issues `ip nexthop add id N via X dev Y proto 199`.
    Code: nexthop.create
    Assertion: subprocess.run called with the full add command.
    """
    with patch("subprocess.run", return_value=_mock_run()) as mock_run:
        nexthop.create(nhid=10, via="192.0.2.1", dev="eth0")
        mock_run.assert_called_once_with(
            [
                "ip",
                "nexthop",
                "add",
                "id",
                "10",
                "via",
                "192.0.2.1",
                "dev",
                "eth0",
                "proto",
                str(_PROTO),
            ],
            capture_output=True,
            check=True,
        )


def test_create_accepts_explicit_proto():
    """Create nexthop object accepts explicit proto override.

    Validates: create(nhid, via, dev, proto=N) uses the supplied proto value.
    Code: nexthop.create
    Assertion: subprocess.run called with overridden proto.
    """
    with patch("subprocess.run", return_value=_mock_run()) as mock_run:
        nexthop.create(nhid=10, via="192.0.2.1", dev="eth0", proto=42)
        mock_run.assert_called_once_with(
            [
                "ip",
                "nexthop",
                "add",
                "id",
                "10",
                "via",
                "192.0.2.1",
                "dev",
                "eth0",
                "proto",
                "42",
            ],
            capture_output=True,
            check=True,
        )


# ---------------------------------------------------------------------------
# create_blackhole
# ---------------------------------------------------------------------------


def test_create_blackhole_calls_correct_args():
    """Create blackhole nexthop issues correct ip-nexthop add blackhole CLI args.

    Validates: create_blackhole(nhid) issues `ip nexthop add id N blackhole proto 199`.
    Code: nexthop.create_blackhole
    Assertion: subprocess.run called with blackhole add command.
    """
    with patch("subprocess.run", return_value=_mock_run()) as mock_run:
        nexthop.create_blackhole(nhid=20)
        mock_run.assert_called_once_with(
            ["ip", "nexthop", "add", "id", "20", "blackhole", "proto", str(_PROTO)],
            capture_output=True,
            check=True,
        )


def test_create_blackhole_accepts_explicit_proto():
    """Create blackhole nexthop accepts explicit proto override.

    Validates: create_blackhole(nhid, proto=N) uses the supplied proto value.
    Code: nexthop.create_blackhole
    Assertion: subprocess.run called with overridden proto.
    """
    with patch("subprocess.run", return_value=_mock_run()) as mock_run:
        nexthop.create_blackhole(nhid=20, proto=55)
        mock_run.assert_called_once_with(
            ["ip", "nexthop", "add", "id", "20", "blackhole", "proto", "55"],
            capture_output=True,
            check=True,
        )


# ---------------------------------------------------------------------------
# create_group
# ---------------------------------------------------------------------------


def test_create_group_calls_correct_args():
    """Create nexthop group issues correct ip-nexthop add group CLI args.

    Validates: create_group(nhid, member_nhid) issues `ip nexthop add id N group M proto 199`.
    Code: nexthop.create_group
    Assertion: subprocess.run called with group add command.
    """
    with patch("subprocess.run", return_value=_mock_run()) as mock_run:
        nexthop.create_group(nhid=30, member_nhid=10)
        mock_run.assert_called_once_with(
            ["ip", "nexthop", "add", "id", "30", "group", "10", "proto", str(_PROTO)],
            capture_output=True,
            check=True,
        )


def test_create_group_accepts_explicit_proto():
    """Create nexthop group accepts explicit proto override.

    Validates: create_group(nhid, member_nhid, proto=N) uses the supplied proto value.
    Code: nexthop.create_group
    Assertion: subprocess.run called with overridden proto.
    """
    with patch("subprocess.run", return_value=_mock_run()) as mock_run:
        nexthop.create_group(nhid=30, member_nhid=10, proto=77)
        mock_run.assert_called_once_with(
            ["ip", "nexthop", "add", "id", "30", "group", "10", "proto", "77"],
            capture_output=True,
            check=True,
        )


# ---------------------------------------------------------------------------
# replace_nexthop
# ---------------------------------------------------------------------------


def test_replace_nexthop_calls_correct_args():
    """Replace nexthop object issues correct ip-nexthop replace CLI args.

    Validates: replace_nexthop(nhid, via, dev) issues `ip nexthop replace id N via X dev Y proto 199`.
    Code: nexthop.replace_nexthop
    Assertion: subprocess.run called with replace command including proto.
    """
    with patch("subprocess.run", return_value=_mock_run()) as mock_run:
        nexthop.replace_nexthop(nhid=10, via="10.0.0.1", dev="wg0")
        mock_run.assert_called_once_with(
            [
                "ip",
                "nexthop",
                "replace",
                "id",
                "10",
                "via",
                "10.0.0.1",
                "dev",
                "wg0",
                "proto",
                str(_PROTO),
            ],
            capture_output=True,
            check=True,
        )


# ---------------------------------------------------------------------------
# replace_nexthop_blackhole
# ---------------------------------------------------------------------------


def test_replace_nexthop_blackhole_calls_correct_args():
    """Replace nexthop with blackhole issues correct ip-nexthop replace blackhole CLI args.

    Validates: replace_nexthop_blackhole(nhid) issues `ip nexthop replace id N blackhole proto 199`.
    Code: nexthop.replace_nexthop_blackhole
    Assertion: subprocess.run called with replace blackhole command.
    """
    with patch("subprocess.run", return_value=_mock_run()) as mock_run:
        nexthop.replace_nexthop_blackhole(nhid=10)
        mock_run.assert_called_once_with(
            ["ip", "nexthop", "replace", "id", "10", "blackhole", "proto", str(_PROTO)],
            capture_output=True,
            check=True,
        )


# ---------------------------------------------------------------------------
# replace_group
# ---------------------------------------------------------------------------


def test_replace_group_calls_correct_args():
    """Replace nexthop group issues correct ip-nexthop replace group CLI args.

    Validates: replace_group(nhid, member_nhid) issues `ip nexthop replace id N group M proto 199`.
    Code: nexthop.replace_group
    Assertion: subprocess.run called with group replace command.
    """
    with patch("subprocess.run", return_value=_mock_run()) as mock_run:
        nexthop.replace_group(nhid=30, member_nhid=10)
        mock_run.assert_called_once_with(
            [
                "ip",
                "nexthop",
                "replace",
                "id",
                "30",
                "group",
                "10",
                "proto",
                str(_PROTO),
            ],
            capture_output=True,
            check=True,
        )


# ---------------------------------------------------------------------------
# create_device
# ---------------------------------------------------------------------------


def test_create_device_calls_correct_args():
    """Create device nexthop issues correct ip-nexthop add dev CLI args.

    Validates: create_device(nhid, dev) issues `ip nexthop add id N dev D proto 199`.
    Code: nexthop.create_device
    Assertion: subprocess.run called with device add command.
    """
    with patch("subprocess.run", return_value=_mock_run()) as mock_run:
        nexthop.create_device(nhid=40, dev="border")
        mock_run.assert_called_once_with(
            ["ip", "nexthop", "add", "id", "40", "dev", "border", "proto", str(_PROTO)],
            capture_output=True,
            check=True,
        )


def test_create_device_accepts_explicit_proto():
    """Create device nexthop accepts explicit proto override.

    Validates: create_device(nhid, dev, proto=N) uses the supplied proto value.
    Code: nexthop.create_device
    Assertion: subprocess.run called with overridden proto.
    """
    with patch("subprocess.run", return_value=_mock_run()) as mock_run:
        nexthop.create_device(nhid=40, dev="border", proto=99)
        mock_run.assert_called_once_with(
            ["ip", "nexthop", "add", "id", "40", "dev", "border", "proto", "99"],
            capture_output=True,
            check=True,
        )


# ---------------------------------------------------------------------------
# replace_device
# ---------------------------------------------------------------------------


def test_replace_device_calls_correct_args():
    """Replace device nexthop issues correct ip-nexthop replace dev CLI args.

    Validates: replace_device(nhid, dev) issues `ip nexthop replace id N dev D proto 199`.
    Code: nexthop.replace_device
    Assertion: subprocess.run called with device replace command.
    """
    with patch("subprocess.run", return_value=_mock_run()) as mock_run:
        nexthop.replace_device(nhid=40, dev="border")
        mock_run.assert_called_once_with(
            [
                "ip",
                "nexthop",
                "replace",
                "id",
                "40",
                "dev",
                "border",
                "proto",
                str(_PROTO),
            ],
            capture_output=True,
            check=True,
        )
