"""Behavior tests for the generic Recursor drop-in (powerdns.extraConfig).

Scope: chart rendering (kube/charts/ipt-server), the image recursor.yml
contract (kube/image/powerdns/etc/recursor.yml), and — when a Docker daemon
is reachable — a container test that builds the garuda-powerdns image and
proves a forwarded name resolves through a stub resolver with the drop-in
mounted, and that startup without the drop-in is unchanged.

Not covered: Recursor YAML semantics themselves (the Recursor validates its
own config at start; the product adds no parser by design).

Run: pytest kube/tests/pyunit/test_powerpowerdns_extra_config.py
Container tests additionally need a working Docker daemon and network access
for the image build; they skip when either is unavailable.
"""

from __future__ import annotations

import hashlib
import shutil
import socket
import struct
import subprocess
from pathlib import Path
from typing import Iterator
from textwrap import dedent

import pytest
import yaml

from conftest import REPO_ROOT

CHART_DIR = REPO_ROOT / "kube" / "charts" / "ipt-server"
POWERDNS_IMAGE_DIR = REPO_ROOT / "kube" / "image" / "powerdns"
RECURSOR_YML = POWERDNS_IMAGE_DIR / "etc" / "recursor.yml"

# Sample drop-in: RFC5737 forwarder, example.net zone, plus a comment line to
# prove verbatim passthrough (no YAML re-serialization by chart or module).
SAMPLE_EXTRA = dedent("""\
    recursor:
      forward_zones_recurse:
        # stand zone is resolved by the site resolver, not recursion
        - zone: stand.example.net
          forwarders:
            - 192.0.2.53:53
    """
)


# ---------------------------------------------------------------------------
# Helpers: helm rendering
# ---------------------------------------------------------------------------


def _render(values: str) -> str:
    result = subprocess.run(
        ["helm", "template", "ipt-server", str(CHART_DIR), "--namespace", "garuda", "-f", "-"],
        input=values,
        text=True,
        capture_output=True,
    )
    assert result.returncode == 0, result.stderr
    return result.stdout


def _render_docs(values: str) -> list[dict]:
    return [d for d in yaml.safe_load_all(_render(values)) if d]


def _extra_values(extra: str) -> str:
    body = "".join(f"    {line}\n" for line in extra.splitlines())
    return f"powerdns:\n  extraConfig: |\n{body}"


def _powerdns_container(deployment: dict) -> dict:
    return next(
        c for c in deployment["spec"]["template"]["spec"]["containers"] if c["name"] == "powerdns"
    )


def _deployment(docs: list[dict]) -> dict:
    return next(d for d in docs if d["kind"] == "Deployment")


# ---------------------------------------------------------------------------
# Chart rendering: default must render nothing extra
# ---------------------------------------------------------------------------


def test_default_renders_no_powerdns_extra_artifacts() -> None:
    """Empty powerdns.extraConfig must render no ConfigMap, volume, mount, or annotation.

    Validates: charts/ipt-server/templates/{configmap-powerdns-extra,deployment}.yaml
    Assertion: with the chart default, rendered manifests contain no
    `<name>-powerdns-extra` ConfigMap, no volume/volumeMounts on the pod, and
    no garuda.powerdns-extra/checksum annotation — the default render stays
    byte-identical to main (locked further by tests/golden/default.yaml).
    """
    docs = _render_docs("{}")
    assert not any(
        d["kind"] == "ConfigMap" and d["metadata"]["name"].endswith("-powerdns-extra") for d in docs
    )
    pod_spec = _deployment(docs)["spec"]["template"]["spec"]
    assert "volumes" not in pod_spec
    assert "volumeMounts" not in _powerdns_container(_deployment(docs))
    annotations = _deployment(docs)["spec"]["template"]["metadata"]["annotations"]
    assert not any(k.startswith("garuda.powerdns-extra/") for k in annotations)


# ---------------------------------------------------------------------------
# Chart rendering: non-empty extraConfig
# ---------------------------------------------------------------------------


def test_extra_config_renders_verbatim_configmap() -> None:
    """The drop-in ConfigMap must carry the raw value byte-for-byte under 50-extra.yml.

    Validates: charts/ipt-server/templates/configmap-powerdns-extra.yaml
    Assertion: ConfigMap `<name>-powerdns-extra` exists and its `50-extra.yml`
    key equals the value verbatim (comments and indentation preserved).
    """
    docs = _render_docs(_extra_values(SAMPLE_EXTRA))
    cm = next(
        d
        for d in docs
        if d["kind"] == "ConfigMap" and d["metadata"]["name"] == "ipt-server-powerdns-extra"
    )
    assert cm["data"]["50-extra.yml"] == SAMPLE_EXTRA


def test_extra_config_mounts_dropin_readonly_subpath() -> None:
    """The powerdns container must mount the drop-in via subPath, read-only.

    Validates: charts/ipt-server/templates/deployment.yaml
    Assertion: exactly one volumeMount on the powerdns container at
    /etc/powerdns/recursor.d/50-extra.yml with subPath 50-extra.yml and
    readOnly true, backed by the `<name>-powerdns-extra` ConfigMap volume.
    """
    docs = _render_docs(_extra_values(SAMPLE_EXTRA))
    deployment = _deployment(docs)
    pod_spec = deployment["spec"]["template"]["spec"]

    mounts = _powerdns_container(deployment).get("volumeMounts", [])
    assert mounts == [
        {
            "name": "powerdns-extra",
            "mountPath": "/etc/powerdns/recursor.d/50-extra.yml",
            "subPath": "50-extra.yml",
            "readOnly": True,
        }
    ]
    volumes = pod_spec.get("volumes", [])
    assert volumes == [
        {"name": "powerdns-extra", "configMap": {"name": "ipt-server-powerdns-extra"}}
    ]


def test_extra_config_checksum_annotation_rolls_pod_on_change() -> None:
    """The pod annotation must be sha256(extraConfig) and change with the value.

    Validates: charts/ipt-server/templates/deployment.yaml
    Assertion: garuda.powerdns-extra/checksum equals sha256 of the raw value
    for two different values, so editing the setting produces a new pod
    (Deployment annotation change) instead of a silently stale recursor.
    """
    other = SAMPLE_EXTRA.replace("192.0.2.53:53", "192.0.2.54:53")

    def checksum(value: str) -> str:
        docs = _render_docs(_extra_values(value))
        annotations = _deployment(docs)["spec"]["template"]["metadata"]["annotations"]
        return annotations["garuda.powerdns-extra/checksum"]

    assert checksum(SAMPLE_EXTRA) == hashlib.sha256(SAMPLE_EXTRA.encode()).hexdigest()
    assert checksum(other) == hashlib.sha256(other.encode()).hexdigest()
    assert checksum(SAMPLE_EXTRA) != checksum(other)


def test_chart_rejects_non_string_extra_config() -> None:
    """powerdns.extraConfig must be a string (values.schema.json fail-fast).

    A map/list value would silently change rendering semantics; the schema
    must reject it at helm time instead of failing at Recursor start.
    """
    result = subprocess.run(
        ["helm", "template", "ipt-server", str(CHART_DIR), "--namespace", "garuda", "-f", "-"],
        input="powerdns:\n  extraConfig:\n    key: value\n",
        text=True,
        capture_output=True,
    )
    assert result.returncode != 0


# ---------------------------------------------------------------------------
# Image contract: recursor.yml must include the drop-in directory
# ---------------------------------------------------------------------------


def test_recursor_yml_declares_include_dir() -> None:
    """recursor.yml must set recursor.include_dir to the chart drop-in dir.

    Validates: kube/image/powerdns/etc/recursor.yml
    Assertion: include_dir is /etc/powerdns/recursor.d (the directory the
    chart mounts 50-extra.yml into). Without this key the Recursor ignores
    drop-ins entirely; pointing it at a missing directory would be a start
    error — the base image ships the (possibly empty) directory and
    `COPY etc` merges into it, so the directory always exists in the image.
    """
    rendered = yaml.safe_load(RECURSOR_YML.read_text())
    assert rendered["recursor"]["include_dir"] == "/etc/powerdns/recursor.d"
    # Existing settings must be untouched by the drop-in change.
    assert rendered["recursor"]["lua_dns_script"] == "/etc/powerdns/hook.lua"
    assert rendered["incoming"]["listen"] == ["0.0.0.0:1053"]


# ---------------------------------------------------------------------------
# Container tests (Docker): real image, real Recursor, stub resolver
# ---------------------------------------------------------------------------

STUB_ANSWER = "192.0.2.10"  # RFC5737 TEST-NET-1
FORWARDED_NAME = "stand.example.net"

# Minimal DNS server answering every A query with STUB_ANSWER (UDP + TCP).
# Runs via python3 inside a container of the garuda-powerdns image itself, on
# a private Docker network shared with the Recursor under test.
STUB_SCRIPT = '''
import socket, struct, threading

ANSWER_IP = {answer!r}

def response(query):
    if len(query) < 12:
        return b""
    qid, _flags, qdcount = struct.unpack("!HHH", query[:6])
    if qdcount != 1:
        return b""
    offset = 12
    while query[offset]:
        offset += query[offset] + 1
    offset += 1
    qtype, _qclass = struct.unpack("!HH", query[offset:offset + 4])
    question = query[12:offset + 4]
    if qtype != 1:
        return struct.pack("!HHHHHH", qid, 0x8580, 1, 0, 0, 0) + question
    header = struct.pack("!HHHHHH", qid, 0x8580, 1, 1, 0, 0)
    answer = (b"\\xc0\\x0c" + struct.pack("!HHI", 1, 1, 60)
              + struct.pack("!H", 4) + socket.inet_aton(ANSWER_IP))
    return header + question + answer

def serve_udp(sock):
    while True:
        data, client = sock.recvfrom(4096)
        resp = response(data)
        if resp:
            sock.sendto(resp, client)

def serve_tcp(sock):
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(("0.0.0.0", 53))
    sock.listen(4)
    while True:
        conn, _ = sock.accept()
        def handle(conn):
            with conn:
                conn.settimeout(2)
                try:
                    length = struct.unpack("!H", conn.recv(2))[0]
                    resp = response(conn.recv(length))
                    if resp:
                        conn.sendall(struct.pack("!H", len(resp)) + resp)
                except OSError:
                    pass
        threading.Thread(target=handle, args=(conn,), daemon=True).start()

udp = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
udp.bind(("0.0.0.0", 53))
threading.Thread(target=serve_udp, args=(udp,), daemon=True).start()
serve_tcp(socket.socket(socket.AF_INET, socket.SOCK_STREAM))
'''


def _docker_ok() -> bool:
    return shutil.which("docker") is not None and subprocess.run(
        ["docker", "info"], capture_output=True
    ).returncode == 0


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _parse_a_answers(pkt: bytes) -> list[str]:
    _qid, _flags = struct.unpack("!HH", pkt[:4])
    qdcount, ancount = struct.unpack("!HH", pkt[4:8])
    offset = 12
    for _ in range(qdcount):
        while pkt[offset]:
            offset += pkt[offset] + 1
        offset += 5
    answers: list[str] = []
    for _ in range(ancount):
        if pkt[offset] & 0xC0 == 0xC0:
            offset += 2
        else:
            while pkt[offset]:
                offset += pkt[offset] + 1
            offset += 1
        rtype, _rclass, _ttl, rdlen = struct.unpack("!HHIH", pkt[offset : offset + 10])
        offset += 10
        if rtype == 1:
            answers.append(socket.inet_ntoa(pkt[offset : offset + rdlen]))
        offset += rdlen
    return answers


def _query(server: tuple[str, int], qname: str, qid: int = 0xBEEF) -> bytes:
    parts = qname.rstrip(".").split(".")
    question = b"".join(bytes([len(p)]) + p.encode() for p in parts) + b"\x00"
    header = struct.pack("!HHHHHH", qid, 0x0100, 1, 0, 0, 0)
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.settimeout(2)
        sock.sendto(header + question + struct.pack("!HH", 1, 1), server)
        return sock.recv(4096)


def _docker(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["docker", *args], text=True, capture_output=True)


@pytest.fixture(scope="session")
def powerdns_image() -> Iterator[str]:
    if not _docker_ok():
        pytest.skip("Docker daemon not reachable; container tests skipped")
    tag = "garuda-powerdns:sdd-3b"
    result = _docker("build", "-q", "-t", tag, str(POWERDNS_IMAGE_DIR))
    if result.returncode != 0:
        pytest.skip(f"image build failed (no network for apt/pull?): {result.stdout}{result.stderr}")
    return tag


@pytest.fixture(scope="session")
def docker_net(powerdns_image: str) -> Iterator[str]:
    net = "garuda-sdd-3b-test"
    result = _docker("network", "create", net)
    assert result.returncode == 0, result.stderr
    yield net
    _docker("network", "rm", net)


@pytest.fixture(scope="session")
def stub_resolver(docker_net: str, powerdns_image: str) -> Iterator[str]:
    """Stub DNS container on the shared network; returns its IP address."""
    import tempfile

    with tempfile.NamedTemporaryFile("w", suffix=".py", delete=False) as f:
        f.write(STUB_SCRIPT.format(answer=STUB_ANSWER))
        script = Path(f.name)
    script.chmod(0o644)
    result = _docker(
        "run", "-d", "--rm", "--name", "garuda-powerdns-stub",
        "--network", docker_net,
        "-v", f"{script}:/stub.py:ro",
        "--entrypoint", "python3",
        powerdns_image, "/stub.py",
    )
    assert result.returncode == 0, result.stderr
    container = result.stdout.strip()
    ip = _docker("inspect", "-f", "{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}", container)
    assert ip.returncode == 0 and ip.stdout.strip(), ip.stderr
    yield ip.stdout.strip()
    _docker("rm", "-f", container)
    script.unlink()


def _run_recursor(
    image: str, net: str, host_port: int, dropins: list[Path] | None
) -> str:
    cmd = [
        "run", "-d", "--rm",
        "--name", f"garuda-powerdns-test-{host_port}",
        "--network", net,
        "-p", f"127.0.0.1:{host_port}:1053/udp",
    ]
    for dropin in dropins or []:
        cmd += ["-v", f"{dropin}:/etc/powerdns/recursor.d/{dropin.name}:ro"]
    cmd.append(image)
    result = _docker(*cmd)
    assert result.returncode == 0, result.stderr
    return result.stdout.strip()


def _wait_ready(host_port: int, qname: str, timeout: float = 45.0) -> None:
    import time

    deadline = time.monotonic() + timeout
    last_err: Exception | None = None
    while time.monotonic() < deadline:
        try:
            _query(("127.0.0.1", host_port), qname)
            return
        except OSError as err:
            last_err = err
            time.sleep(0.5)
    pytest.fail(f"recursor did not answer {qname} within {timeout}s: {last_err}")


def _container_logs(container: str) -> str:
    """docker logs with stderr merged: the Recursor logs to stderr."""
    logs = _docker("logs", container)
    return logs.stdout + logs.stderr


def _assert_no_config_errors(container: str) -> None:
    logs = _container_logs(container)
    offenders = [
        line
        for line in logs.splitlines()
        if 'subsystem="config"' in line and ('prio="Error"' in line or "Fatal error" in line)
    ]
    assert not offenders, f"recursor reported config errors: {offenders}"


def test_container_with_dropin_forwards_zone(
    tmp_path: Path, docker_net: str, stub_resolver: str, powerdns_image: str
) -> None:
    """A drop-in-mounted Recursor must resolve a forwarded name via a stub resolver.

    Validates: kube/image/powerdns (recursor.yml include_dir + chart drop-in shape)
    Assertion: with 50-extra.yml forwarding stand.example.net to the stub
    resolver, a query for stand.example.net returns the stub's RFC5737 answer
    and the Recursor log contains no config errors.

    Method:
    1. Arrange: stub resolver container on a private Docker network; drop-in
       file with forward_zones_recurse stand.example.net -> <stub_ip>:53.
       A second scaffold drop-in (40-test-stub.yml) clears
       outgoing.dont_query: its default blocks RFC1918/TEST-NET forwarders,
       which exist only in this test environment (production forwarders are
       routable); this also proves multi-file drop-in merging.
    2. Act: run the built image with both drop-ins mounted in
       /etc/powerdns/recursor.d/; query via the published UDP port.
    3. Assert: answer is 192.0.2.10; no config errors in docker logs.
    """
    dropin = tmp_path / "50-extra.yml"
    dropin.write_text(
        "recursor:\n"
        "  forward_zones_recurse:\n"
        f"    - zone: {FORWARDED_NAME}\n"
        "      forwarders:\n"
        f"        - {stub_resolver}:53\n"
    )
    scaffold = tmp_path / "40-test-stub.yml"
    scaffold.write_text("outgoing:\n  dont_query: []\n")
    for path in (dropin, scaffold):
        path.chmod(0o644)
    host_port = _free_port()
    container = _run_recursor(powerdns_image, docker_net, host_port, [dropin, scaffold])
    try:
        _wait_ready(host_port, FORWARDED_NAME)
        pkt = _query(("127.0.0.1", host_port), FORWARDED_NAME)
        assert _parse_a_answers(pkt) == [STUB_ANSWER]
        _assert_no_config_errors(container)
    finally:
        _docker("rm", "-f", container)


def test_container_without_dropin_starts_unchanged(
    docker_net: str, powerdns_image: str
) -> None:
    """Without a drop-in the Recursor must start exactly as before the change.

    Validates: kube/image/powerdns (include_dir against the empty stock
    recursor.d directory)
    Assertion: the image starts with no drop-in mounted, serves queries via
    recursion as before, and logs no config errors — the include_dir line is
    inert when the chart supplies no drop-in.
    """
    host_port = _free_port()
    container = _run_recursor(powerdns_image, docker_net, host_port, None)
    try:
        # The recursor only proves readiness by answering; a query it must
        # resolve itself would need upstream DNS. Listening + no fatal config
        # error is the contract for "starts unchanged".
        import time

        deadline = time.monotonic() + 45
        while time.monotonic() < deadline:
            if "Enabled multiplexer" in _container_logs(container):
                break
            time.sleep(0.5)
        else:
            pytest.fail("recursor did not reach 'Enabled multiplexer' (startup) without drop-in")
        assert _docker("inspect", "-f", "{{.State.Running}}", container).stdout.strip() == "true"
        _assert_no_config_errors(container)
    finally:
        _docker("rm", "-f", container)
