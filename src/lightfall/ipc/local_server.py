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
