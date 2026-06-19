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
