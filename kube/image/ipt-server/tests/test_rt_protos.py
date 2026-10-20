"""rt_protos: iproute2 route-proto names rendered from code constants."""

import logging
import os

import rt_protos
import route
from pinning import kernel as pinning_kernel


def test_renders_expected_lines_from_constants():
    assert rt_protos.render_rt_protos() == (
        f"{route.DNS_PROTO} {route.DNS_PROTO_NAME}\n"
        f"{pinning_kernel.PINNING_PROTO} {pinning_kernel.PINNING_PROTO_NAME}\n"
    )
    assert rt_protos.render_rt_protos() == "250 garuda-dns\n201 garuda-pin\n"


def test_write_creates_dir_and_file(tmp_path, monkeypatch):
    conf = tmp_path / "rt_protos.d" / "garuda.conf"
    monkeypatch.setattr(rt_protos, "RT_PROTOS_CONF_PATH", conf)
    rt_protos.write_rt_protos()
    assert conf.read_text() == rt_protos.render_rt_protos()
    assert not conf.with_name(conf.name + ".tmp").exists()


def test_second_call_does_not_rewrite(tmp_path, monkeypatch):
    conf = tmp_path / "rt_protos.d" / "garuda.conf"
    monkeypatch.setattr(rt_protos, "RT_PROTOS_CONF_PATH", conf)
    replaces = []
    real_replace = os.replace
    monkeypatch.setattr(
        rt_protos.os,
        "replace",
        lambda src, dst: (replaces.append(dst), real_replace(src, dst))[1],
    )
    rt_protos.write_rt_protos()
    rt_protos.write_rt_protos()
    assert len(replaces) == 1  # unchanged content -> no atomic rewrite


def test_write_failure_warns_and_does_not_raise(tmp_path, monkeypatch, caplog):
    # Path is a directory: os.replace onto it fails with OSError.
    monkeypatch.setattr(rt_protos, "RT_PROTOS_CONF_PATH", tmp_path)
    with caplog.at_level(logging.WARNING, logger="rt_protos"):
        rt_protos.write_rt_protos()
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1
    assert "cosmetic" in warnings[0].getMessage()


def test_file_numbers_track_constants(tmp_path, monkeypatch):
    conf = tmp_path / "garuda.conf"
    monkeypatch.setattr(rt_protos, "RT_PROTOS_CONF_PATH", conf)
    monkeypatch.setattr(route, "DNS_PROTO", route.DNS_PROTO + 1)
    monkeypatch.setattr(pinning_kernel, "PINNING_PROTO", pinning_kernel.PINNING_PROTO + 1)
    rt_protos.write_rt_protos()
    content = conf.read_text()
    assert f"{route.DNS_PROTO} garuda-dns" in content
    assert f"{pinning_kernel.PINNING_PROTO} garuda-pin" in content
