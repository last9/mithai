"""Unit tests for the embedded-API transport selection (TCP port vs UDS)."""

import os
import socket as socketmod
import stat

from mithai.cli.run_cmd import (
    _harden_bound_socket,
    _prepare_socket_path,
    _resolve_ui_transport,
)


def test_no_env_returns_none():
    assert _resolve_ui_transport({}) is None


def test_port_only():
    use_uds, socket, port = _resolve_ui_transport({"MITHAI_UI_PORT": "8421"})
    assert use_uds is False
    assert socket == ""
    assert port == 8421


def test_socket_only():
    use_uds, socket, port = _resolve_ui_transport({"MITHAI_UI_SOCKET": "/run/mithai/a.sock"})
    assert use_uds is True
    assert socket == "/run/mithai/a.sock"
    assert port == 0


def test_socket_takes_precedence_over_port():
    use_uds, socket, port = _resolve_ui_transport(
        {"MITHAI_UI_SOCKET": "/run/mithai/a.sock", "MITHAI_UI_PORT": "8421"}
    )
    assert use_uds is True
    assert socket == "/run/mithai/a.sock"
    # Port is still parsed (harmless) but the socket is what gets used.
    assert port == 8421


def test_empty_values_treated_as_unset():
    assert _resolve_ui_transport({"MITHAI_UI_SOCKET": "", "MITHAI_UI_PORT": ""}) is None


def test_uds_explicitly_supported():
    use_uds, socket, port = _resolve_ui_transport(
        {"MITHAI_UI_SOCKET": "/run/mithai/a.sock"}, uds_supported=True
    )
    assert use_uds is True
    assert socket == "/run/mithai/a.sock"


# --- Windows / no-AF_UNIX behavior (uds_supported=False) ---

def test_windows_socket_with_port_falls_back_to_tcp():
    # Socket requested but the host can't do AF_UNIX, and a port is available:
    # fall back to TCP rather than crash.
    use_uds, socket, port = _resolve_ui_transport(
        {"MITHAI_UI_SOCKET": "/run/mithai/a.sock", "MITHAI_UI_PORT": "8421"},
        uds_supported=False,
    )
    assert use_uds is False
    assert socket == ""
    assert port == 8421


def test_windows_socket_without_port_disables_api():
    # Socket requested, no AF_UNIX, no port fallback: disable rather than crash.
    assert (
        _resolve_ui_transport(
            {"MITHAI_UI_SOCKET": "/run/mithai/a.sock"}, uds_supported=False
        )
        is None
    )


def test_windows_port_only_unaffected():
    # No socket requested: AF_UNIX support is irrelevant, TCP works as always.
    use_uds, socket, port = _resolve_ui_transport(
        {"MITHAI_UI_PORT": "8421"}, uds_supported=False
    )
    assert use_uds is False
    assert port == 8421


def test_windows_socket_with_non_numeric_port_disables_api():
    # Socket requested, no AF_UNIX, and the port fallback is garbage (parses to
    # 0). Falling back would bind an ephemeral port the platform never assigned
    # (locally "healthy" but unreachable), so this must disable the API instead
    # — same outcome as having no port at all.
    assert (
        _resolve_ui_transport(
            {"MITHAI_UI_SOCKET": "/run/mithai/a.sock", "MITHAI_UI_PORT": "garbage"},
            uds_supported=False,
        )
        is None
    )


# --- Non-numeric MITHAI_UI_PORT is tolerated, not fatal ---

def test_non_numeric_port_with_socket_keeps_uds():
    # A garbage port must not crash the pure helper; the socket still wins.
    use_uds, socket, port = _resolve_ui_transport(
        {"MITHAI_UI_SOCKET": "/run/mithai/a.sock", "MITHAI_UI_PORT": "not-a-number"}
    )
    assert use_uds is True
    assert socket == "/run/mithai/a.sock"
    assert port == 0


def test_non_numeric_port_without_socket_disables_api():
    # No socket and an unparseable port: nothing to listen on.
    assert _resolve_ui_transport({"MITHAI_UI_PORT": "8a4b"}) is None


# --- _prepare_socket_path: stale clearing + parent dir + path-type handling ---

def test_prepare_socket_creates_parent_dir_0700(tmp_path, monkeypatch):
    # Relative short path: the macOS sun_path limit (104) rejects the long
    # absolute tmp_path, which _prepare_socket_path now validates up front.
    monkeypatch.chdir(tmp_path)
    assert _prepare_socket_path("sub/a.sock") is True
    parent = tmp_path / "sub"
    assert parent.is_dir()
    assert stat.S_IMODE(parent.stat().st_mode) == 0o700


def test_prepare_socket_removes_stale_socket(tmp_path, monkeypatch):
    # Bind from inside tmp_path with a short relative name: the macOS AF_UNIX
    # sun_path limit (104) rejects the long absolute tmp_path otherwise.
    monkeypatch.chdir(tmp_path)
    s = socketmod.socket(socketmod.AF_UNIX, socketmod.SOCK_STREAM)
    s.bind("a.sock")
    try:
        assert os.path.exists("a.sock")
        assert stat.S_ISSOCK(os.lstat("a.sock").st_mode)
        assert _prepare_socket_path("a.sock") is True
        assert not os.path.exists("a.sock")
    finally:
        s.close()


def test_prepare_socket_removes_stray_regular_file(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "a.sock").write_text("leftover")
    assert _prepare_socket_path("a.sock") is True
    assert not (tmp_path / "a.sock").exists()


def test_prepare_socket_refuses_directory(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "a.sock").mkdir()
    # A directory at the path is a misconfiguration: refuse clearly, do not
    # remove the dir and do not let bind() hang the readiness probe.
    assert _prepare_socket_path("a.sock") is False
    assert (tmp_path / "a.sock").is_dir()


def test_prepare_socket_absent_path_is_ready(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert _prepare_socket_path("a.sock") is True


# --- MITHAI_UI_PORT="0"/negative is not a usable assigned port ---

def test_port_zero_without_socket_disables_api():
    # port 0 would make uvicorn bind a random ephemeral port the platform never
    # assigned (locally "healthy" but unreachable): treat it as no usable port.
    assert _resolve_ui_transport({"MITHAI_UI_PORT": "0"}) is None


def test_negative_port_without_socket_disables_api():
    assert _resolve_ui_transport({"MITHAI_UI_PORT": "-1"}) is None


def test_port_zero_with_socket_keeps_uds():
    use_uds, sock, port = _resolve_ui_transport(
        {"MITHAI_UI_SOCKET": "/run/mithai/a.sock", "MITHAI_UI_PORT": "0"}
    )
    assert use_uds is True
    assert sock == "/run/mithai/a.sock"
    assert port == 0


def test_windows_socket_with_zero_port_disables_api():
    # No AF_UNIX and the only "fallback" is the unusable port 0: disable, do not
    # bind an ephemeral port the platform never assigned.
    assert (
        _resolve_ui_transport(
            {"MITHAI_UI_SOCKET": "/run/mithai/a.sock", "MITHAI_UI_PORT": "0"},
            uds_supported=False,
        )
        is None
    )


# --- _prepare_socket_path: AF_UNIX sun_path length limit ---

def test_prepare_socket_rejects_overlong_path(tmp_path):
    # A path past the AF_UNIX sun_path limit must be refused up front with a
    # clear error, not left to fail bind() in the serve thread (opaque timeout).
    long_name = "x" * 200
    sock = tmp_path / long_name
    assert _prepare_socket_path(str(sock)) is False
    # Refused before creating anything.
    assert not sock.exists()


# --- _harden_bound_socket: owner-only perms + inode-pinned exit cleanup ---

def test_harden_bound_socket_chmods_0600_and_registers_cleanup(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    s = socketmod.socket(socketmod.AF_UNIX, socketmod.SOCK_STREAM)
    s.bind("a.sock")
    try:
        # World-connectable perms before hardening (depends on umask, but force
        # a known-loose mode so the chmod is observable).
        os.chmod("a.sock", 0o777)
        registered = []
        monkeypatch.setattr(
            "mithai.cli.run_cmd.atexit.register",
            lambda fn, *args: registered.append((fn, args)),
        )
        _harden_bound_socket("a.sock")
        assert stat.S_IMODE(os.stat("a.sock").st_mode) == 0o600
        # Cleanup registered, pinned to the bound inode.
        assert len(registered) == 1
        _, args = registered[0]
        assert args[0] == "a.sock"
        assert args[1] == os.stat("a.sock").st_ino
    finally:
        s.close()
