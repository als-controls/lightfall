"""Tests for the local NATS server manager (resolver, version, probe)."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from lightfall.ipc import local_server
from lightfall.ipc.local_server import (
    NATS_BINARY_NAME,
    nats_binary_version,
    probe_nats,
    resolve_nats_binary,
)


class TestResolveBinary:
    def test_bundled_wins(self, tmp_path, monkeypatch):
        # Fake a venv layout: <tmp>/python with the bundled binary beside it.
        fake_python = tmp_path / "python"
        fake_python.write_text("")
        (tmp_path / NATS_BINARY_NAME).write_text("")
        monkeypatch.setattr(local_server.sys, "executable", str(fake_python))
        # which should be ignored when the bundled binary exists
        monkeypatch.setattr(local_server.shutil, "which", lambda _: "/system/nats-server")
        assert resolve_nats_binary() == str(tmp_path / NATS_BINARY_NAME)

    def test_falls_back_to_which(self, tmp_path, monkeypatch):
        fake_python = tmp_path / "python"
        fake_python.write_text("")
        # No bundled binary beside python.
        monkeypatch.setattr(local_server.sys, "executable", str(fake_python))
        monkeypatch.setattr(local_server.shutil, "which", lambda _: "/system/nats-server")
        assert resolve_nats_binary() == "/system/nats-server"

    def test_none_when_unresolved(self, tmp_path, monkeypatch):
        fake_python = tmp_path / "python"
        fake_python.write_text("")
        monkeypatch.setattr(local_server.sys, "executable", str(fake_python))
        monkeypatch.setattr(local_server.shutil, "which", lambda _: None)
        assert resolve_nats_binary() is None


class TestVersion:
    def test_parses_version(self, monkeypatch):
        def fake_run(*args, **kwargs):
            return subprocess.CompletedProcess(args, 0, stdout="nats-server: v2.14.2\n", stderr="")
        monkeypatch.setattr(local_server.subprocess, "run", fake_run)
        assert nats_binary_version("/x/nats-server") == "2.14.2"

    def test_none_on_failure(self, monkeypatch):
        def fake_run(*args, **kwargs):
            raise OSError("boom")
        monkeypatch.setattr(local_server.subprocess, "run", fake_run)
        assert nats_binary_version("/x/nats-server") is None


class TestProbe:
    def test_returns_none_on_refused(self):
        # Nothing is listening on this port.
        assert probe_nats("127.0.0.1", 1, timeout=0.2) is None


class _FakeProc:
    """Minimal subprocess.Popen stand-in."""

    def __init__(self, exits_with=None):
        self._exits_with = exits_with  # None = stays alive; int = exited code
        self.terminated = False
        self.killed = False

    def poll(self):
        return self._exits_with

    def terminate(self):
        self.terminated = True
        self._exits_with = -15

    def kill(self):
        self.killed = True
        self._exits_with = -9

    def wait(self, timeout=None):
        return self._exits_with


class TestLocalNatsServer:
    def test_start_raises_when_binary_unresolved(self, monkeypatch):
        monkeypatch.setattr(local_server, "resolve_nats_binary", lambda: None)
        srv = local_server.LocalNatsServer(port=4299)
        with pytest.raises(local_server.NatsBinaryNotFoundError):
            srv.start()

    def test_start_builds_correct_args_and_becomes_ready(self, monkeypatch):
        captured = {}

        def fake_popen(args, **kwargs):
            captured["args"] = args
            return _FakeProc()

        monkeypatch.setattr(local_server, "resolve_nats_binary", lambda: "/x/nats-server")
        monkeypatch.setattr(local_server.subprocess, "Popen", fake_popen)
        monkeypatch.setattr(local_server, "probe_nats", lambda h, p, timeout=5.0: {"version": "2.14.2"})

        srv = local_server.LocalNatsServer(port=4299, host="127.0.0.1")
        srv.start(timeout_s=1.0)
        assert captured["args"] == ["/x/nats-server", "-a", "127.0.0.1", "-p", "4299"]
        assert srv.is_running()

    def test_start_port_in_use(self, monkeypatch):
        monkeypatch.setattr(local_server, "resolve_nats_binary", lambda: "/x/nats-server")
        monkeypatch.setattr(local_server.subprocess, "Popen", lambda a, **k: _FakeProc(exits_with=1))
        srv = local_server.LocalNatsServer(port=4299)
        with pytest.raises(local_server.NatsPortInUseError):
            srv.start(timeout_s=1.0)

    def test_start_readiness_timeout_kills(self, monkeypatch):
        proc = _FakeProc()  # alive but never ready
        monkeypatch.setattr(local_server, "resolve_nats_binary", lambda: "/x/nats-server")
        monkeypatch.setattr(local_server.subprocess, "Popen", lambda a, **k: proc)
        monkeypatch.setattr(local_server, "probe_nats", lambda h, p, timeout=5.0: None)
        srv = local_server.LocalNatsServer(port=4299, poll_interval=0.01)
        with pytest.raises(local_server.NatsReadinessTimeoutError):
            srv.start(timeout_s=0.1)
        assert proc.terminated or proc.killed

    def test_stop_is_idempotent(self, monkeypatch):
        proc = _FakeProc()
        monkeypatch.setattr(local_server, "resolve_nats_binary", lambda: "/x/nats-server")
        monkeypatch.setattr(local_server.subprocess, "Popen", lambda a, **k: proc)
        monkeypatch.setattr(local_server, "probe_nats", lambda h, p, timeout=5.0: {"version": "2.14.2"})
        srv = local_server.LocalNatsServer(port=4299)
        srv.start(timeout_s=1.0)
        srv.stop()
        srv.stop()  # no error second time
        assert proc.terminated
        assert not srv.is_running()
