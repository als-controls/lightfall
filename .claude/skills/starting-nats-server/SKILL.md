---
name: starting-nats-server
description: Use when LUCID code (demos, integration tests, scripts) needs a running NATS broker and one isn't already up — covers binary lookup, subprocess spawn, readiness probe, and teardown.
---

# Starting a NATS server for LUCID

LUCID does not embed NATS. In production it's an external service; in tests/demos
you spawn `nats-server` as a subprocess. This skill captures the recipe.

## When to use

- Writing an integration test that needs a real broker (mocks won't catch wire bugs).
- Writing a demo or smoke script that exercises the IPC layer end-to-end.
- A new dev environment can't connect to `nats://localhost:4222` and you need a
  local one for development.

## When NOT to use

- Unit tests — use the `NatsLink` abstraction / fake transport instead.
- Production / staging — NATS is an external service. Ask beamline controls for
  the URL; don't bundle a broker into the LUCID process.

## Binary location

Order of resolution (use this exact order — matches the existing scripts):

1. `$NATS_SERVER_BIN` env var, if set.
2. `shutil.which("nats-server")` — picks it up if on `$PATH`.
3. Hardcoded Windows winget install path (last resort, used by integration tests):
   ```
   C:\Users\rp\AppData\Local\Microsoft\WinGet\Packages\NATSAuthors.NATSServer_Microsoft.Winget.Source_8wekyb3d8bbwe\nats-server-v2.10.25-windows-amd64\nats-server.exe
   ```

Install on Windows: `winget install NATSAuthors.NATSServer`. The binary lands at
the path above. After install, either add it to `$PATH` or set `$NATS_SERVER_BIN`.

## Canonical pytest fixture

This is the pattern used in `tests/integration/test_tsuchinoko_e2e.py` and
`tests/integration/test_pipelines_e2e.py`. Copy it; don't reinvent.

```python
import os
import shutil
import socket
import subprocess
import time
import pytest


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _resolve_nats_bin() -> str | None:
    return os.environ.get("NATS_SERVER_BIN") or shutil.which("nats-server")


@pytest.fixture(scope="session")
def nats_server():
    """Start a nats-server on a random port. Session-scoped. Skips if no binary."""
    bin_path = _resolve_nats_bin()
    if not bin_path or not os.path.isfile(bin_path):
        pytest.skip("nats-server not found (set NATS_SERVER_BIN or install on PATH)")

    port = _free_port()
    proc = subprocess.Popen(
        [bin_path, "-p", str(port)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )

    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        try:
            socket.create_connection(("127.0.0.1", port), timeout=1).close()
            break
        except OSError:
            time.sleep(0.1)
    else:
        proc.kill()
        pytest.fail("nats-server did not start within 10s")

    yield f"nats://127.0.0.1:{port}"

    proc.terminate()
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()
```

## One-shot script form

For demos / smoke scripts (see `scripts/demo_autonomous_experiment.py`):

```python
import os, shutil, subprocess, sys, time

if os.environ.get("NATS_TEST_AUTOSTART") == "1":
    bin_path = os.environ.get("NATS_SERVER_BIN") or shutil.which("nats-server")
    if not bin_path:
        print("ERROR: NATS_SERVER_BIN not set and nats-server not on PATH.")
        sys.exit(2)
    nats_proc = subprocess.Popen([bin_path, "-p", "4222"])
    time.sleep(1.0)  # cheap readiness; prefer the socket probe above for tests
```

Default port `4222` is fine for single-broker scripts. Use a random free port
(see `_free_port` above) when tests run in parallel or alongside a real broker.

## Gotchas

- **Naive `time.sleep(1.0)` is flaky under load.** Use the socket-connect probe
  in the canonical fixture for anything that has to be reliable.
- **Always tear down.** A leaked `nats-server` on port 4222 will make every
  subsequent test fixture *appear* to work but connect to a stale broker with
  the wrong subjects. Use the `yield` + `terminate` pattern, not bare `Popen`.
- **Don't redirect stdout/stderr to PIPE without draining** for long-running
  spawns — the OS pipe buffer (~64 KB on Windows) fills up and nats-server
  blocks. For session-scoped fixtures that finish in seconds this is fine; for
  anything longer, redirect to `subprocess.DEVNULL` or a file.
- **JetStream is off by default.** If you need JS, add `-js` and a
  `-sd <storage-dir>` flag. None of the current LUCID tests need it.

## Reference implementations in this repo

- `tests/integration/test_tsuchinoko_e2e.py` — canonical fixture (copied above).
- `tests/integration/test_pipelines_e2e.py` — same pattern, slightly different
  skip condition.
- `scripts/demo_autonomous_experiment.py` — minimal one-shot form gated by
  `NATS_TEST_AUTOSTART=1`.

If you're writing a new test that needs NATS, copy from the first one.
