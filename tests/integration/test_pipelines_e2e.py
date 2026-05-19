"""End-to-end test for the notebook-pipelines feature.

Exercises the full chain against live services:

  device-flow Keycloak login  -> bcgtiled /auth/apikey  -> JobMessage on
  local NATS  -> lucid-pipelines executor subprocess   -> papermill on
  the passthrough fixture notebook -> Tiled write with merged access
  tags  -> client reads back the derived run.

Skips collection entirely when ``LUCID_INTEGRATION`` is not set or any
required tooling (nats-server, the lucid-pipelines venv, ipykernel)
is missing. The first successful run will print a Keycloak device-flow
URL to the test output; the minted 7-day key is then cached at
``~/.cache/lucid-pipelines/integration-key.json`` for subsequent runs.

Output runs are tagged ``beamline:test`` so they can be cleaned up out-
of-band; the test deliberately does not delete them itself to keep the
fixture code thin.
"""
from __future__ import annotations

import asyncio
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import Path
from typing import Any

import pytest

# Guard: opt-in only.
if not os.environ.get("LUCID_INTEGRATION"):
    pytest.skip(
        "set LUCID_INTEGRATION=1 to run pipelines e2e tests",
        allow_module_level=True,
    )

try:
    import nats as nats_lib
    from tiled.client import from_uri
except ImportError as _exc:  # pragma: no cover
    pytest.skip(
        f"integration deps missing ({_exc})", allow_module_level=True,
    )

from tests.integration._device_flow import cached_or_login


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

NATS_SERVER_BIN = (
    r"C:\Users\rp\AppData\Local\Microsoft\WinGet\Packages"
    r"\NATSAuthors.NATSServer_Microsoft.Winget.Source_8wekyb3d8bbwe"
    r"\nats-server-v2.10.25-windows-amd64\nats-server.exe"
).replace("\\", "/")

TILED_URL = os.environ.get(
    "LUCID_INTEGRATION_TILED_URL", "http://bcgtiled.dhcp.lbl.gov:8000",
)
KEYCLOAK_URL = os.environ.get(
    "LUCID_INTEGRATION_KEYCLOAK_URL", "https://keycloak.als.lbl.gov",
)
KEYCLOAK_REALM = os.environ.get("LUCID_INTEGRATION_KEYCLOAK_REALM", "als")
KEYCLOAK_CLIENT_ID = os.environ.get(
    "LUCID_INTEGRATION_DEVICE_CLIENT_ID", "als-tiled-device",
)

# Lucid-pipelines venv with executor + passthrough plugin installed.
LP_PYTHON = Path(os.environ.get(
    "LUCID_PIPELINES_PYTHON",
    str(Path.home() / "PycharmProjects" / "lucid-pipelines"
        / ".venv" / "Scripts" / "python.exe"),
))

PASSTHROUGH_PKG_DIR = (
    Path(__file__).parent / "_fixtures" / "passthrough_plugin"
)
PASSTHROUGH_VERSION = "0.0.1"
PASSTHROUGH_KERNEL = f"lucid-pipelines:passthrough_pipeline@{PASSTHROUGH_VERSION}"

INPUT_BEAMLINE_TAG = "beamline:test"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _wait_port(host: str, port: int, timeout: float) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with socket.create_connection((host, port), timeout=1):
                return
        except OSError:
            time.sleep(0.1)
    raise TimeoutError(f"{host}:{port} did not come up in {timeout}s")


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session")
def lp_python() -> Path:
    if not LP_PYTHON.exists():
        pytest.skip(
            f"lucid-pipelines venv python not at {LP_PYTHON}; set "
            "LUCID_PIPELINES_PYTHON to override",
        )
    # Sanity-check the venv has everything we need.
    rc = subprocess.run(
        [str(LP_PYTHON), "-c", "import lucid_pipelines, ipykernel, nats"],
        capture_output=True,
    )
    if rc.returncode != 0:
        pytest.skip(
            f"lucid-pipelines venv missing deps: {rc.stderr.decode()}",
        )
    return LP_PYTHON


@pytest.fixture(scope="session")
def passthrough_installed(lp_python):
    """Install the passthrough fixture into the lucid-pipelines venv and
    register a Jupyter kernel under the executor's expected name."""
    # Editable install so iteration on the fixture is cheap.
    subprocess.check_call(
        [str(lp_python), "-m", "pip", "install", "--quiet", "-e",
         str(PASSTHROUGH_PKG_DIR)],
    )

    # Register the kernel papermill will look up.
    subprocess.check_call(
        [str(lp_python), "-m", "ipykernel", "install",
         "--user", "--name", PASSTHROUGH_KERNEL,
         "--display-name", PASSTHROUGH_KERNEL],
    )
    yield


@pytest.fixture(scope="session")
def nats_server():
    if not os.path.isfile(NATS_SERVER_BIN):
        pytest.skip(f"nats-server not found at {NATS_SERVER_BIN}")
    port = _free_port()
    proc = subprocess.Popen(
        [NATS_SERVER_BIN, "-p", str(port)],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    try:
        _wait_port("127.0.0.1", port, timeout=10)
    except TimeoutError:
        proc.kill()
        pytest.fail("nats-server did not start within 10s")

    yield f"nats://127.0.0.1:{port}"

    proc.terminate()
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()


@pytest.fixture(scope="session")
def tiled_api_key():
    try:
        return cached_or_login(
            tiled_url=TILED_URL,
            server_url=KEYCLOAK_URL,
            realm=KEYCLOAK_REALM,
            client_id=KEYCLOAK_CLIENT_ID,
        )
    except Exception as exc:
        pytest.skip(f"device-flow login failed: {exc}")


@pytest.fixture(scope="session")
def tiled_client(tiled_api_key):
    proxy = (
        os.environ.get("LUCID_INTEGRATION_PROXY") or "socks5h://localhost:1080"
    ) if "lbl.gov" in TILED_URL else None
    import httpx

    return from_uri(
        TILED_URL,
        api_key=tiled_api_key["secret"],
        # Tiled's httpx defaults don't honor proxies on Windows; pass an
        # explicit transport when needed.
        httpx_client=httpx.Client(proxy=proxy) if proxy else None,
    )


@pytest.fixture(scope="session")
def executor_proc(nats_server, lp_python, passthrough_installed):
    """Spawn the lucid-pipelines executor subprocess."""
    tmp = Path(tempfile.mkdtemp(prefix="lucid_e2e_"))
    notebook_store = tmp / "notebooks"
    env_cache_root = tmp / "envs"
    notebook_store.mkdir()
    env_cache_root.mkdir()

    # Pre-seed env_cache so the executor's build step is a no-op: we
    # already installed passthrough into lp_python's venv, and the kernel
    # was registered separately. has_env() only checks the directory
    # shape, so creating it suffices.
    seeded = env_cache_root / f"passthrough_pipeline@{PASSTHROUGH_VERSION}"
    (seeded / "Scripts").mkdir(parents=True)

    hostname = f"e2e-{uuid.uuid4().hex[:8]}"
    proc = subprocess.Popen(
        [
            str(lp_python), "-m", "lucid_pipelines.cli",
            "--nats", nats_server,
            "--hostname", hostname,
            "--notebook-store", str(notebook_store),
            "--env-cache", str(env_cache_root),
            "--log-level", "INFO",
        ],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )

    # Wait for the executor to subscribe by pinging the list subject.
    deadline = time.monotonic() + 10
    last_err: Any = None
    while time.monotonic() < deadline:
        try:
            asyncio.run(_ping_executor(nats_server, hostname))
            break
        except Exception as exc:  # noqa: BLE001
            last_err = exc
            time.sleep(0.2)
    else:
        proc.terminate()
        out, err = proc.communicate(timeout=5)
        pytest.fail(
            f"executor did not respond on NATS after 10s: {last_err}\n"
            f"stdout: {out.decode()[:1000]}\nstderr: {err.decode()[:1000]}"
        )

    yield {"proc": proc, "hostname": hostname, "tmpdir": tmp}

    proc.terminate()
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()
    shutil.rmtree(tmp, ignore_errors=True)


async def _ping_executor(nats_url: str, hostname: str) -> None:
    nc = await nats_lib.connect(nats_url)
    try:
        await nc.request(
            f"lucid.pipeline.{hostname}.list", b"{}", timeout=1,
        )
    finally:
        await nc.drain()


# ---------------------------------------------------------------------------
# Input-run seeding
# ---------------------------------------------------------------------------


@pytest.fixture()
def input_run(tiled_client):
    """Write a tiny 3-D float array to bcgtiled and yield its UID.

    The run is tagged ``beamline:test`` so any output it produces inherits
    the same tag, marking it as non-real data.
    """
    import numpy as np

    uid = f"e2e-input-{uuid.uuid4()}"
    tags = [INPUT_BEAMLINE_TAG]
    container = tiled_client.create_container(
        key=uid,
        metadata={
            "start": {
                "uid": uid,
                "plan_name": "e2e-fixture",
                "access_blob": {
                    "tags": tags,
                    "beamline": "test",
                },
                "tiled_access_tags": tags,
            },
        },
        access_tags=tags,
    )
    primary = container.create_container(key="primary")
    primary.write_array(
        np.arange(60, dtype=np.float32).reshape(5, 3, 4), key="signal",
    )
    return uid


# ---------------------------------------------------------------------------
# The test
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_passthrough_pipeline_completes_with_access_blob_inherited(
    executor_proc, nats_server, tiled_client, tiled_api_key, input_run,
):
    """Submit a passthrough job and verify the derived run carries the
    input's access tags so the operator who owned the input data can
    also read the output."""
    asyncio.run(_run_happy_path(
        nats_url=nats_server,
        hostname=executor_proc["hostname"],
        tiled_url=TILED_URL,
        tiled_api_key=tiled_api_key["secret"],
        input_run_uid=input_run,
        tiled_client=tiled_client,
    ))


async def _run_happy_path(
    *,
    nats_url: str,
    hostname: str,
    tiled_url: str,
    tiled_api_key: str,
    input_run_uid: str,
    tiled_client: Any,
) -> None:
    nc = await nats_lib.connect(nats_url)
    progress_subject = f"lucid.pipeline.{hostname}.progress"
    job_subject = f"lucid.pipeline.{hostname}"

    completed: asyncio.Future = asyncio.get_event_loop().create_future()

    async def _on_progress(msg):
        try:
            ev = json.loads(msg.data)
        except json.JSONDecodeError:
            return
        if ev.get("status") in ("completed", "failed"):
            if not completed.done():
                completed.set_result(ev)

    sub = await nc.subscribe(progress_subject, cb=_on_progress)

    job_id = f"e2e-{uuid.uuid4()}"
    payload = json.dumps({
        "job_id": job_id,
        "tiled_url": tiled_url.rstrip("/") + "/api/v1",
        "api_key": tiled_api_key,
        "api_key_expires_at": None,
        "input_run_uid": input_run_uid,
        "input_access_blob": {
            "tags": [INPUT_BEAMLINE_TAG], "beamline": "test",
        },
        "pipeline": "passthrough",
        "parameters": {"stream": "primary", "field": "signal"},
        "user_id": "e2e-test",
        "requested_by": "pytest",
        "submitted_at": "1970-01-01T00:00:00Z",
    }).encode()

    reply = await nc.request(job_subject, payload, timeout=10)
    accepted = json.loads(reply.data)
    assert accepted.get("status") == "queued", accepted

    try:
        ev = await asyncio.wait_for(completed, timeout=180)
    finally:
        await sub.unsubscribe()
        await nc.drain()

    assert ev["status"] == "completed", f"executor failed: {ev}"
    output_uids = ev.get("output_run_uids") or []
    assert len(output_uids) == 1, ev

    # Read back the derived run and confirm the input's tag survived.
    output = tiled_client[output_uids[0]]
    tags = (output.metadata.get("start") or {}).get("tiled_access_tags") or []
    assert INPUT_BEAMLINE_TAG in tags, (
        f"output run lost the input access tag: {tags}"
    )
