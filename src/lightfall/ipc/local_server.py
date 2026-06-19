"""Local NATS server management.

Provides binary resolution (preferring the bundled ``nats-server-bin``), a
version probe for the settings UI, a TCP readiness probe shared with the
settings panel, and a :class:`LocalNatsServer` subprocess manager.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import socket
import subprocess
import sys
import time

from loguru import logger

__all__ = [
    "NATS_BINARY_NAME",
    "resolve_nats_binary",
    "nats_binary_version",
    "probe_nats",
    "LocalNatsServer",
    "LocalNatsServerError",
    "NatsBinaryNotFoundError",
    "NatsPortInUseError",
    "NatsReadinessTimeoutError",
]

NATS_BINARY_NAME = "nats-server.exe" if os.name == "nt" else "nats-server"

_VERSION_RE = re.compile(r"v?(\d+\.\d+\.\d+)")


class LocalNatsServerError(Exception):
    """Base error for local NATS server management."""


class NatsBinaryNotFoundError(LocalNatsServerError):
    """The nats-server binary could not be resolved."""


class NatsPortInUseError(LocalNatsServerError):
    """The server process exited immediately (typically a port bind failure)."""


class NatsReadinessTimeoutError(LocalNatsServerError):
    """The server never began answering on its port within the timeout."""


def resolve_nats_binary() -> str | None:
    """Return a path to nats-server, or None if unresolved.

    Bundled first (next to ``sys.executable`` — where ``nats-server-bin``
    installs it), then ``shutil.which`` as a fallback. Bundled-first is
    deliberate: ``which`` can shadow the bundled binary with an older system
    build.
    """
    from pathlib import Path

    bundled = Path(sys.executable).parent / NATS_BINARY_NAME
    if bundled.exists():
        return str(bundled)
    return shutil.which("nats-server")


def nats_binary_version(path: str) -> str | None:
    """Return the X.Y.Z version string of the binary at *path*, or None."""
    try:
        result = subprocess.run(
            [path, "--version"],
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    match = _VERSION_RE.search(result.stdout or "")
    return match.group(1) if match else None


def probe_nats(host: str, port: int, timeout: float = 5.0) -> dict | None:
    """TCP-connect and read the NATS ``INFO`` banner.

    Returns the parsed INFO dict on success, or None if the host is
    unreachable or does not speak NATS.
    """
    try:
        with socket.create_connection((host, port), timeout=timeout) as sock:
            sock.settimeout(timeout)
            data = sock.recv(4096).decode("utf-8", errors="replace")
    except OSError:
        return None
    if data.startswith("INFO "):
        try:
            return json.loads(data[5:].strip())
        except json.JSONDecodeError:
            return None
    return None


class LocalNatsServer:
    """Owns a local ``nats-server`` subprocess (core NATS, no JetStream)."""

    def __init__(
        self,
        port: int = 4222,
        host: str = "127.0.0.1",
        poll_interval: float = 0.05,
    ) -> None:
        self._port = port
        self._host = host
        self._poll_interval = poll_interval
        self._proc: subprocess.Popen | None = None

    def is_running(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    def start(self, timeout_s: float = 5.0) -> None:
        """Launch nats-server and block until it answers, or raise."""
        if self.is_running():
            return

        binary = resolve_nats_binary()
        if binary is None:
            raise NatsBinaryNotFoundError(
                "nats-server not found (expected the nats-server-bin package)"
            )

        args = [binary, "-a", self._host, "-p", str(self._port)]
        creationflags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
        logger.info("Starting local nats-server: {}", " ".join(args))
        self._proc = subprocess.Popen(
            args,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            creationflags=creationflags,
        )

        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            code = self._proc.poll()
            if code is not None:
                self._proc = None
                raise NatsPortInUseError(
                    f"nats-server exited immediately (code {code}); "
                    f"port {self._port} may be in use"
                )
            if probe_nats(self._host, self._port, timeout=self._poll_interval) is not None:
                logger.info("Local nats-server ready on {}:{}", self._host, self._port)
                return
            time.sleep(self._poll_interval)

        self.stop()
        raise NatsReadinessTimeoutError(
            f"nats-server did not become ready within {timeout_s}s"
        )

    def stop(self) -> None:
        """Terminate the subprocess if running. Idempotent."""
        proc = self._proc
        if proc is None:
            return
        self._proc = None
        if proc.poll() is not None:
            return
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            logger.warning("nats-server did not terminate; killing")
            proc.kill()
