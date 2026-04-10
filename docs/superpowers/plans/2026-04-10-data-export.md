# Data Export System Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add batch data export to LUCID via a headless exporter process communicating over NATS IPC.

**Architecture:** The Data Browser gains multi-select and an Export button. An export dialog collects parameters (export type, output dir, ROI for NXsas). The job is sent over NATS to a headless exporter process (`lucid-exporter`) that runs on the local machine, connecting to Tiled independently. Progress is reported back via NATS pub/sub and displayed as toasts.

**Tech Stack:** PySide6, nats-py, tiled[client], h5py, numpy, pyqtgraph (ROI widget)

**Spec:** `docs/superpowers/specs/2026-04-10-data-export-design.md`

---

## File Structure

### New Files

| File | Responsibility |
|------|---------------|
| `src/lucid/exporter/__init__.py` | Package init |
| `src/lucid/exporter/cli.py` | CLI entry point — argparse, NATS connection, run asyncio loop |
| `src/lucid/exporter/service.py` | NATS subscription, job queue, dispatch to converters, progress publishing |
| `src/lucid/exporter/converters/__init__.py` | Converter registry (maps type string to converter class) |
| `src/lucid/exporter/converters/base.py` | `Converter` ABC — interface for all converters |
| `src/lucid/exporter/converters/noop.py` | NoOp converter — copies raw data arrays to disk |
| `src/lucid/exporter/converters/nxsas.py` | NXsas converter — writes NXsas-compliant HDF5 with ROI cropping |
| `src/lucid/exporter/tiled_utils.py` | Helper to connect to Tiled and fetch run data |
| `src/lucid/ui/dialogs/export_dialog.py` | Export configuration dialog (type, dir, ROI) |
| `tests/exporter/__init__.py` | Test package init |
| `tests/exporter/test_converters.py` | Unit tests for NoOp and NXsas converters |
| `tests/exporter/test_service.py` | Unit tests for exporter service (job queue, dispatch, progress) |
| `tests/test_export_dialog.py` | Unit tests for export dialog parameter assembly |

### Modified Files

| File | Changes |
|------|---------|
| `src/lucid/ui/panels/tiled_browser_panel.py` | Multi-select, export button, `_get_selected_records()`, export trigger |
| `pyproject.toml` | Add `lucid-exporter` entry point, add `h5py` dependency |

---

## Task 1: Converter Base Class and NoOp Converter

**Files:**
- Create: `src/lucid/exporter/__init__.py`
- Create: `src/lucid/exporter/converters/__init__.py`
- Create: `src/lucid/exporter/converters/base.py`
- Create: `src/lucid/exporter/converters/noop.py`
- Create: `src/lucid/exporter/tiled_utils.py`
- Test: `tests/exporter/__init__.py`
- Test: `tests/exporter/test_converters.py`

- [ ] **Step 1: Write the failing test for the Converter ABC**

Create `tests/exporter/__init__.py` (empty) and `tests/exporter/test_converters.py`:

```python
"""Tests for exporter converters."""

from __future__ import annotations

import pytest

from lucid.exporter.converters.base import Converter


class TestConverterABC:
    def test_cannot_instantiate_base(self):
        with pytest.raises(TypeError):
            Converter()

    def test_subclass_must_implement_export(self):
        class Incomplete(Converter):
            name = "incomplete"

        with pytest.raises(TypeError):
            Incomplete()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd ~/PycharmProjects/ncs/ncs && python -m pytest tests/exporter/test_converters.py::TestConverterABC -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'lucid.exporter'`

- [ ] **Step 3: Implement the Converter ABC**

Create `src/lucid/exporter/__init__.py`:

```python
"""LUCID Exporter — headless data export service."""
```

Create `src/lucid/exporter/converters/__init__.py`:

```python
"""Converter registry for the exporter."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from lucid.exporter.converters.base import Converter

CONVERTERS: dict[str, type[Converter]] = {}


def register_converter(cls: type[Converter]) -> type[Converter]:
    """Register a converter class by its name attribute."""
    CONVERTERS[cls.name] = cls
    return cls


def get_converter(name: str) -> type[Converter]:
    """Get a converter class by name. Raises KeyError if not found."""
    return CONVERTERS[name]


# Import converters to trigger registration (nxsas added in Task 2)
from lucid.exporter.converters.noop import NoOpConverter  # noqa: E402, F401
```

Create `src/lucid/exporter/converters/base.py`:

```python
"""Base converter interface for the exporter."""

from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any, Callable


class Converter(ABC):
    """Abstract base for export converters.

    Subclasses must set a ``name`` class attribute and implement ``export``.
    """

    name: str

    @abstractmethod
    async def export(
        self,
        run_client: Any,
        run_uid: str,
        params: dict[str, Any],
        output_dir: Path,
        progress_cb: Callable[[str], None] | None = None,
    ) -> None:
        """Export a single run.

        Args:
            run_client: Tiled run container (the entry for this run).
            run_uid: UID of the run being exported.
            params: Export parameters (converter-specific).
            output_dir: Directory to write output files.
            progress_cb: Optional callback for status detail strings.

        Raises:
            Exception: On export failure.
        """
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd ~/PycharmProjects/ncs/ncs && python -m pytest tests/exporter/test_converters.py::TestConverterABC -v`
Expected: PASS (2 tests)

Note: The `converters/__init__.py` imports noop and nxsas which don't exist yet. The test imports `base` directly so this is fine. We'll create the actual converters next.

- [ ] **Step 5: Write failing test for NoOp converter**

Append to `tests/exporter/test_converters.py`:

```python
import asyncio
from pathlib import Path
from unittest.mock import MagicMock, patch

import numpy as np


class TestNoOpConverter:
    def _make_mock_run(self, fields: dict[str, np.ndarray]) -> MagicMock:
        """Create a mock Tiled run client with the given fields in primary stream."""
        run = MagicMock()
        stream = MagicMock()
        stream.keys.return_value = list(fields.keys())
        for name, arr in fields.items():
            col = MagicMock()
            col.read.return_value = arr
            stream.__getitem__ = lambda self, key, _f=fields: _make_col(_f[key])
        # Make stream.__getitem__ work properly
        def _get_field(key):
            col = MagicMock()
            col.read.return_value = fields[key]
            return col
        stream.__getitem__ = _get_field
        run.keys.return_value = ["primary"]
        run.__getitem__ = lambda self, key: stream if key == "primary" else None
        # Fix: make run["primary"] return stream
        run.__getitem__ = lambda key: stream
        return run

    def test_export_creates_output_files(self, tmp_path):
        from lucid.exporter.converters.noop import NoOpConverter

        converter = NoOpConverter()
        assert converter.name == "noop"

        data = {"detector": np.ones((5, 10, 10)), "motor": np.arange(5, dtype=float)}
        run = self._make_mock_run(data)

        asyncio.run(converter.export(
            run_client=run,
            run_uid="test-uid-001",
            params={},
            output_dir=tmp_path,
        ))

        out_dir = tmp_path / "test-uid-001"
        assert out_dir.is_dir()
        assert (out_dir / "detector.npy").exists()
        assert (out_dir / "motor.npy").exists()

        loaded = np.load(out_dir / "detector.npy")
        np.testing.assert_array_equal(loaded, data["detector"])

    def test_export_calls_progress_cb(self, tmp_path):
        from lucid.exporter.converters.noop import NoOpConverter

        converter = NoOpConverter()
        data = {"field1": np.array([1, 2, 3])}
        run = self._make_mock_run(data)
        cb = MagicMock()

        asyncio.run(converter.export(
            run_client=run,
            run_uid="uid-002",
            params={},
            output_dir=tmp_path,
            progress_cb=cb,
        ))

        cb.assert_called()
```

- [ ] **Step 6: Run test to verify it fails**

Run: `cd ~/PycharmProjects/ncs/ncs && python -m pytest tests/exporter/test_converters.py::TestNoOpConverter -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'lucid.exporter.converters.noop'`

- [ ] **Step 7: Implement the NoOp converter**

Create `src/lucid/exporter/converters/noop.py`:

```python
"""NoOp converter — exports raw data arrays to numpy files."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

import numpy as np

from lucid.exporter.converters.base import Converter


class NoOpConverter(Converter):
    """Export run data as raw numpy arrays, one .npy file per field."""

    name = "noop"

    async def export(
        self,
        run_client: Any,
        run_uid: str,
        params: dict[str, Any],
        output_dir: Path,
        progress_cb: Callable[[str], None] | None = None,
    ) -> None:
        run_dir = output_dir / run_uid
        run_dir.mkdir(parents=True, exist_ok=True)

        stream = run_client["primary"]
        fields = list(stream.keys())

        for field_name in fields:
            if progress_cb:
                progress_cb(f"Saving {field_name}")
            data = np.asarray(stream[field_name].read())
            np.save(run_dir / f"{field_name}.npy", data)
```

- [ ] **Step 8: Run test to verify it passes**

Run: `cd ~/PycharmProjects/ncs/ncs && python -m pytest tests/exporter/test_converters.py::TestNoOpConverter -v`
Expected: PASS (2 tests)

- [ ] **Step 9: Create tiled_utils.py**

Create `src/lucid/exporter/tiled_utils.py`:

```python
"""Tiled client utilities for the exporter."""

from __future__ import annotations

from typing import Any

from tiled.client import from_uri


def connect_tiled(url: str, token: str | None = None) -> Any:
    """Connect to a Tiled server and return the client.

    Args:
        url: Tiled server URL.
        token: Optional auth token (Bearer token for Keycloak).

    Returns:
        Tiled client instance.
    """
    kwargs: dict[str, Any] = {}
    if token:
        kwargs["api_key"] = token
    return from_uri(url, **kwargs)


def get_run(client: Any, uid: str) -> Any:
    """Look up a run by UID in a Tiled catalog.

    Args:
        client: Tiled client (catalog).
        uid: Run UID.

    Returns:
        Tiled run container.

    Raises:
        KeyError: If run not found.
    """
    return client[uid]
```

- [ ] **Step 10: Commit**

```bash
cd ~/PycharmProjects/ncs/ncs
git add src/lucid/exporter/__init__.py src/lucid/exporter/converters/__init__.py \
  src/lucid/exporter/converters/base.py src/lucid/exporter/converters/noop.py \
  src/lucid/exporter/tiled_utils.py tests/exporter/__init__.py tests/exporter/test_converters.py
git commit -m "feat(exporter): add Converter ABC, NoOp converter, and tiled utils"
```

---

## Task 2: NXsas Converter

**Files:**
- Create: `src/lucid/exporter/converters/nxsas.py`
- Modify: `tests/exporter/test_converters.py`

- [ ] **Step 1: Write the failing test for NXsas converter**

Append to `tests/exporter/test_converters.py`:

```python
import h5py


class TestNxsasConverter:
    def _make_mock_run(self, image_data: np.ndarray) -> MagicMock:
        """Create a mock Tiled run with an image field in primary stream."""
        run = MagicMock()
        stream = MagicMock()
        col = MagicMock()
        col.read.return_value = image_data
        stream.keys.return_value = ["detector"]
        stream.__getitem__ = lambda key: col
        run.keys.return_value = ["primary"]
        run.__getitem__ = lambda key: stream
        run.metadata = {
            "start": {
                "uid": "nxsas-test-uid",
                "plan_name": "count",
                "time": 1700000000.0,
            }
        }
        return run

    def test_export_creates_hdf5(self, tmp_path):
        from lucid.exporter.converters.nxsas import NxsasConverter

        converter = NxsasConverter()
        assert converter.name == "nxsas"

        # 3 frames of 100x100
        image_data = np.random.randint(0, 1000, (3, 100, 100), dtype=np.int32)
        run = self._make_mock_run(image_data)

        asyncio.run(converter.export(
            run_client=run,
            run_uid="nxsas-test-uid",
            params={"roi": {"x": 10, "y": 20, "width": 50, "height": 40}},
            output_dir=tmp_path,
        ))

        out_file = tmp_path / "nxsas-test-uid.h5"
        assert out_file.exists()

        with h5py.File(out_file, "r") as f:
            assert "entry" in f
            assert "entry/data" in f
            assert "entry/data/data" in f
            data = f["entry/data/data"][:]
            # ROI: y=20..60, x=10..60 → shape (3, 40, 50)
            assert data.shape == (3, 40, 50)
            np.testing.assert_array_equal(data, image_data[:, 20:60, 10:60])

    def test_export_without_roi_uses_full_frame(self, tmp_path):
        from lucid.exporter.converters.nxsas import NxsasConverter

        converter = NxsasConverter()
        image_data = np.ones((2, 50, 50), dtype=np.float32)
        run = self._make_mock_run(image_data)

        asyncio.run(converter.export(
            run_client=run,
            run_uid="nxsas-full-uid",
            params={},
            output_dir=tmp_path,
        ))

        out_file = tmp_path / "nxsas-full-uid.h5"
        assert out_file.exists()

        with h5py.File(out_file, "r") as f:
            data = f["entry/data/data"][:]
            assert data.shape == (2, 50, 50)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd ~/PycharmProjects/ncs/ncs && python -m pytest tests/exporter/test_converters.py::TestNxsasConverter -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'lucid.exporter.converters.nxsas'`

- [ ] **Step 3: Implement the NXsas converter**

Create `src/lucid/exporter/converters/nxsas.py`:

```python
"""NXsas converter — exports run data as NXsas-compliant HDF5."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

import h5py
import numpy as np

from lucid.exporter.converters.base import Converter


class NxsasConverter(Converter):
    """Export run image data as NXsas-compliant HDF5 with optional ROI cropping."""

    name = "nxsas"

    async def export(
        self,
        run_client: Any,
        run_uid: str,
        params: dict[str, Any],
        output_dir: Path,
        progress_cb: Callable[[str], None] | None = None,
    ) -> None:
        output_dir.mkdir(parents=True, exist_ok=True)
        out_file = output_dir / f"{run_uid}.h5"

        if progress_cb:
            progress_cb("Reading image data")

        # Get the first image field from primary stream
        stream = run_client["primary"]
        fields = list(stream.keys())
        if not fields:
            raise ValueError(f"No fields found in primary stream for run {run_uid}")

        image_field = fields[0]
        image_data = np.asarray(stream[image_field].read())

        # Apply ROI if specified
        roi = params.get("roi")
        if roi:
            x = roi["x"]
            y = roi["y"]
            w = roi["width"]
            h = roi["height"]
            if image_data.ndim == 3:
                image_data = image_data[:, y : y + h, x : x + w]
            elif image_data.ndim == 2:
                image_data = image_data[y : y + h, x : x + w]

        if progress_cb:
            progress_cb("Writing HDF5")

        # Get metadata for NXsas attributes
        metadata = getattr(run_client, "metadata", {})
        start_doc = metadata.get("start", {})

        with h5py.File(out_file, "w") as f:
            # NXsas structure
            entry = f.create_group("entry")
            entry.attrs["NX_class"] = "NXentry"

            data_group = entry.create_group("data")
            data_group.attrs["NX_class"] = "NXdata"
            data_group.create_dataset("data", data=image_data, compression="gzip")

            # Store metadata
            if start_doc:
                entry.attrs["run_uid"] = start_doc.get("uid", run_uid)
                if "plan_name" in start_doc:
                    entry.attrs["plan_name"] = start_doc["plan_name"]
                if "time" in start_doc:
                    entry.attrs["start_time"] = start_doc["time"]

        if progress_cb:
            progress_cb(f"Written to {out_file.name}")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd ~/PycharmProjects/ncs/ncs && python -m pytest tests/exporter/test_converters.py::TestNxsasConverter -v`
Expected: PASS (2 tests)

- [ ] **Step 5: Register NxsasConverter in converters/__init__.py**

Add the nxsas import to `src/lucid/exporter/converters/__init__.py`, after the existing noop import:

```python
from lucid.exporter.converters.nxsas import NxsasConverter  # noqa: E402, F401
```

- [ ] **Step 6: Add h5py dependency to pyproject.toml**

In `pyproject.toml`, add `h5py` to the dependencies list (after the `numpy` / `scipy` line):

```toml
    "h5py>=3.0",
```

- [ ] **Step 7: Run all converter tests**

Run: `cd ~/PycharmProjects/ncs/ncs && python -m pytest tests/exporter/test_converters.py -v`
Expected: PASS (6 tests — 2 ABC, 2 NoOp, 2 NXsas)

- [ ] **Step 8: Commit**

```bash
cd ~/PycharmProjects/ncs/ncs
git add src/lucid/exporter/converters/nxsas.py src/lucid/exporter/converters/__init__.py \
  tests/exporter/test_converters.py pyproject.toml
git commit -m "feat(exporter): add NXsas converter with ROI cropping and HDF5 output"
```

---

## Task 3: Exporter Service (Job Queue + NATS)

**Files:**
- Create: `src/lucid/exporter/service.py`
- Test: `tests/exporter/test_service.py`

- [ ] **Step 1: Write failing tests for ExporterService**

Create `tests/exporter/test_service.py`:

```python
"""Tests for the exporter NATS service."""

from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from lucid.exporter.service import ExporterService


class TestExporterService:
    def test_init_sets_hostname_and_nats_url(self):
        svc = ExporterService(nats_url="nats://localhost:4222", hostname="testhost")
        assert svc._hostname == "testhost"
        assert svc._nats_url == "nats://localhost:4222"

    def test_subject_names(self):
        svc = ExporterService(nats_url="nats://localhost:4222", hostname="tsuru")
        assert svc.job_subject == "lucid.export.tsuru"
        assert svc.ping_subject == "lucid.export.tsuru.ping"
        assert svc.progress_subject == "lucid.export.tsuru.progress"


class TestJobDispatch:
    @pytest.fixture
    def svc(self):
        return ExporterService(nats_url="nats://localhost:4222", hostname="testhost")

    def test_parse_valid_job(self, svc):
        job_data = {
            "job_id": "abc-123",
            "tiled_url": "https://tiled.example.com",
            "auth_token": "tok",
            "run_uids": ["uid1", "uid2"],
            "export_type": "noop",
            "params": {"output_dir": "/tmp/export"},
        }
        job = svc._parse_job(job_data)
        assert job.job_id == "abc-123"
        assert job.run_uids == ["uid1", "uid2"]
        assert job.export_type == "noop"

    def test_parse_job_missing_field_raises(self, svc):
        with pytest.raises(KeyError):
            svc._parse_job({"job_id": "x"})

    def test_parse_job_unknown_export_type_raises(self, svc):
        job_data = {
            "job_id": "abc-123",
            "tiled_url": "https://tiled.example.com",
            "auth_token": "tok",
            "run_uids": ["uid1"],
            "export_type": "unknown_format",
            "params": {"output_dir": "/tmp/export"},
        }
        with pytest.raises(ValueError, match="Unknown export type"):
            svc._parse_job(job_data)


class TestPingResponse:
    def test_build_ping_response(self):
        svc = ExporterService(nats_url="nats://localhost:4222", hostname="tsuru")
        resp = svc._build_ping_response()
        assert resp["hostname"] == "tsuru"
        assert resp["status"] == "ready"
        assert "queue_depth" in resp
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd ~/PycharmProjects/ncs/ncs && python -m pytest tests/exporter/test_service.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'lucid.exporter.service'`

- [ ] **Step 3: Implement ExporterService**

Create `src/lucid/exporter/service.py`:

```python
"""Exporter NATS service — receives jobs, queues them, dispatches to converters."""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import nats

from lucid.exporter.converters import CONVERTERS, get_converter
from lucid.exporter.tiled_utils import connect_tiled, get_run

logger = logging.getLogger(__name__)


@dataclass
class ExportJob:
    """A parsed export job."""

    job_id: str
    tiled_url: str
    auth_token: str | None
    run_uids: list[str]
    export_type: str
    params: dict[str, Any]

    @property
    def output_dir(self) -> Path:
        return Path(self.params["output_dir"])


class ExporterService:
    """Headless exporter that subscribes to NATS and processes export jobs.

    Subscribes to:
        - ``lucid.export.<hostname>`` — job requests (request/reply)
        - ``lucid.export.<hostname>.ping`` — health check (request/reply)

    Publishes to:
        - ``lucid.export.<hostname>.progress`` — job progress events
    """

    def __init__(self, nats_url: str, hostname: str) -> None:
        self._nats_url = nats_url
        self._hostname = hostname
        self._nc: nats.NATS | None = None
        self._queue: asyncio.Queue[ExportJob] = asyncio.Queue()
        self._running = False

    @property
    def job_subject(self) -> str:
        return f"lucid.export.{self._hostname}"

    @property
    def ping_subject(self) -> str:
        return f"lucid.export.{self._hostname}.ping"

    @property
    def progress_subject(self) -> str:
        return f"lucid.export.{self._hostname}.progress"

    def _parse_job(self, data: dict[str, Any]) -> ExportJob:
        """Parse and validate a job message. Raises on invalid data."""
        job = ExportJob(
            job_id=data["job_id"],
            tiled_url=data["tiled_url"],
            auth_token=data.get("auth_token"),
            run_uids=data["run_uids"],
            export_type=data["export_type"],
            params=data["params"],
        )
        if job.export_type not in CONVERTERS:
            raise ValueError(
                f"Unknown export type '{job.export_type}'. "
                f"Available: {list(CONVERTERS.keys())}"
            )
        return job

    def _build_ping_response(self) -> dict[str, Any]:
        """Build a response for ping requests."""
        return {
            "hostname": self._hostname,
            "status": "ready",
            "queue_depth": self._queue.qsize(),
        }

    async def _publish_progress(
        self,
        job_id: str,
        status: str,
        current_run: int = 0,
        total_runs: int = 0,
        detail: str = "",
    ) -> None:
        """Publish a progress event."""
        if self._nc is None:
            return
        payload = json.dumps({
            "job_id": job_id,
            "status": status,
            "current_run": current_run,
            "total_runs": total_runs,
            "detail": detail,
        }).encode()
        await self._nc.publish(self.progress_subject, payload)

    async def _handle_job_request(self, msg: Any) -> None:
        """Handle an incoming job request — parse, queue, reply."""
        try:
            data = json.loads(msg.data.decode())
            job = self._parse_job(data)
            await self._queue.put(job)
            reply = {"job_id": job.job_id, "status": "queued"}
            logger.info("Queued job %s (%d runs, type=%s)", job.job_id, len(job.run_uids), job.export_type)
        except Exception as e:
            reply = {"error": str(e)}
            logger.error("Failed to parse job: %s", e)

        if msg.reply:
            await self._nc.publish(msg.reply, json.dumps(reply).encode())

    async def _handle_ping(self, msg: Any) -> None:
        """Handle a ping request."""
        if msg.reply and self._nc:
            resp = json.dumps(self._build_ping_response()).encode()
            await self._nc.publish(msg.reply, resp)

    async def _process_jobs(self) -> None:
        """Worker loop — pull jobs from queue and process sequentially."""
        while self._running:
            try:
                job = await asyncio.wait_for(self._queue.get(), timeout=1.0)
            except asyncio.TimeoutError:
                continue

            logger.info("Processing job %s", job.job_id)
            try:
                client = await asyncio.to_thread(connect_tiled, job.tiled_url, job.auth_token)
                converter_cls = get_converter(job.export_type)
                converter = converter_cls()

                for i, uid in enumerate(job.run_uids, 1):
                    await self._publish_progress(
                        job.job_id, "processing", i, len(job.run_uids),
                        f"Exporting run {uid[:8]}...",
                    )
                    try:
                        run = await asyncio.to_thread(get_run, client, uid)
                        await converter.export(
                            run_client=run,
                            run_uid=uid,
                            params=job.params,
                            output_dir=job.output_dir,
                            progress_cb=lambda detail: None,  # per-run detail not published for now
                        )
                    except Exception as e:
                        logger.error("Failed to export run %s: %s", uid, e)
                        await self._publish_progress(
                            job.job_id, "failed", i, len(job.run_uids),
                            f"Failed on run {uid[:8]}: {e}",
                        )
                        break
                else:
                    await self._publish_progress(
                        job.job_id, "completed", len(job.run_uids), len(job.run_uids),
                        f"All {len(job.run_uids)} runs exported to {job.output_dir}",
                    )
                    logger.info("Job %s completed", job.job_id)
            except Exception as e:
                logger.error("Job %s failed: %s", job.job_id, e)
                await self._publish_progress(job.job_id, "failed", detail=str(e))

    async def run(self) -> None:
        """Connect to NATS, subscribe, and process jobs until stopped."""
        self._nc = await nats.connect(self._nats_url)
        logger.info("Connected to NATS at %s", self._nats_url)

        await self._nc.subscribe(self.job_subject, cb=self._handle_job_request)
        await self._nc.subscribe(self.ping_subject, cb=self._handle_ping)
        logger.info("Subscribed to %s and %s", self.job_subject, self.ping_subject)

        self._running = True
        await self._process_jobs()

    async def stop(self) -> None:
        """Stop processing and disconnect."""
        self._running = False
        if self._nc:
            await self._nc.drain()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd ~/PycharmProjects/ncs/ncs && python -m pytest tests/exporter/test_service.py -v`
Expected: PASS (5 tests)

- [ ] **Step 5: Commit**

```bash
cd ~/PycharmProjects/ncs/ncs
git add src/lucid/exporter/service.py tests/exporter/test_service.py
git commit -m "feat(exporter): add ExporterService with NATS job queue and dispatch"
```

---

## Task 4: Exporter CLI Entry Point

**Files:**
- Create: `src/lucid/exporter/cli.py`
- Modify: `pyproject.toml`

- [ ] **Step 1: Implement the CLI**

Create `src/lucid/exporter/cli.py`:

```python
"""CLI entry point for the LUCID exporter service."""

from __future__ import annotations

import argparse
import asyncio
import logging
import platform
import signal
import sys


def main(argv: list[str] | None = None) -> None:
    """Run the LUCID exporter service."""
    parser = argparse.ArgumentParser(
        prog="lucid-exporter",
        description="Headless data export service for LUCID",
    )
    parser.add_argument(
        "--nats",
        default="nats://localhost:4222",
        help="NATS server URL (default: nats://localhost:4222)",
    )
    parser.add_argument(
        "--hostname",
        default=platform.node(),
        help="Hostname for topic routing (default: system hostname)",
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Log level (default: INFO)",
    )
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    logger = logging.getLogger("lucid.exporter")

    from lucid.exporter.service import ExporterService

    service = ExporterService(nats_url=args.nats, hostname=args.hostname)

    async def _run() -> None:
        loop = asyncio.get_running_loop()

        # Handle signals for graceful shutdown
        if sys.platform != "win32":
            for sig in (signal.SIGINT, signal.SIGTERM):
                loop.add_signal_handler(sig, lambda: asyncio.create_task(service.stop()))

        logger.info("Starting lucid-exporter (hostname=%s, nats=%s)", args.hostname, args.nats)
        try:
            await service.run()
        except KeyboardInterrupt:
            pass
        finally:
            await service.stop()
            logger.info("Exporter stopped")

    asyncio.run(_run())


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Add entry point to pyproject.toml**

In `pyproject.toml`, change:

```toml
[project.scripts]
lucid = "lucid.main:cli"
```

to:

```toml
[project.scripts]
lucid = "lucid.main:cli"
lucid-exporter = "lucid.exporter.cli:main"
```

- [ ] **Step 3: Verify CLI parses args correctly**

Run: `cd ~/PycharmProjects/ncs/ncs && python -m lucid.exporter.cli --help`
Expected: Help text showing `--nats`, `--hostname`, `--log-level` options.

- [ ] **Step 4: Commit**

```bash
cd ~/PycharmProjects/ncs/ncs
git add src/lucid/exporter/cli.py pyproject.toml
git commit -m "feat(exporter): add CLI entry point with NATS and hostname args"
```

---

## Task 5: Data Browser — Multi-Select and Export Button

**Files:**
- Modify: `src/lucid/ui/panels/tiled_browser_panel.py`

- [ ] **Step 1: Change selection mode to ExtendedSelection**

In `src/lucid/ui/panels/tiled_browser_panel.py`, line 133, change:

```python
        self._table_view.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
```

to:

```python
        self._table_view.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
```

- [ ] **Step 2: Add the `_get_selected_records` method**

Add this method to the `TiledBrowserPanel` class (after `_on_table_double_clicked`, around line 287):

```python
    def _get_selected_records(self) -> list[TiledRecord]:
        """Get all currently selected TiledRecord objects."""
        records = []
        selection = self._table_view.selectionModel().selectedRows()
        for proxy_index in selection:
            source_index = self._proxy_model.mapToSource(proxy_index)
            record = self._model.get_record(source_index.row())
            if record:
                records.append(record)
        return records
```

- [ ] **Step 3: Add Export button to the UI**

In `_setup_ui`, add the export button to the `status_layout` (after the refresh button, around line 121):

```python
        self._export_btn = QPushButton("Export")
        self._export_btn.setEnabled(False)
        self._export_btn.clicked.connect(self._on_export_clicked)
        status_layout.addWidget(self._export_btn)
```

- [ ] **Step 4: Connect selection changes to enable/disable the export button**

In `__init__`, after `super().__init__(parent)` (around line 92), add:

```python
        # Update export button when selection changes
        self._table_view.selectionModel().selectionChanged.connect(self._on_selection_changed)
```

Note: `self._table_view` is created inside `_setup_ui()` which is called by `super().__init__()`, so the table view exists by this point.

Add the handler method (near `_get_selected_records`):

```python
    @Slot()
    def _on_selection_changed(self) -> None:
        """Enable/disable export button based on selection."""
        has_selection = bool(self._table_view.selectionModel().selectedRows())
        self._export_btn.setEnabled(has_selection)
```

- [ ] **Step 5: Add the export click handler (stub for now)**

Add this method:

```python
    @Slot()
    def _on_export_clicked(self) -> None:
        """Handle export button click — open export dialog."""
        records = self._get_selected_records()
        if not records:
            return

        from lucid.ui.dialogs.export_dialog import ExportDialog

        dialog = ExportDialog(
            records=records,
            tiled_service=self._tiled_service,
            parent=self,
        )
        dialog.exec()
```

- [ ] **Step 6: Verify the panel still loads**

Run: `cd ~/PycharmProjects/ncs/ncs && python -c "from lucid.ui.panels.tiled_browser_panel import TiledBrowserPanel; print('OK')"` 
Expected: `OK` (import succeeds)

- [ ] **Step 7: Commit**

```bash
cd ~/PycharmProjects/ncs/ncs
git add src/lucid/ui/panels/tiled_browser_panel.py
git commit -m "feat(browser): add multi-select and export button to Data Browser"
```

---

## Task 6: Export Dialog

**Files:**
- Create: `src/lucid/ui/dialogs/export_dialog.py`
- Test: `tests/test_export_dialog.py`

- [ ] **Step 1: Write failing test for ExportDialog parameter assembly**

Create `tests/test_export_dialog.py`:

```python
"""Tests for export dialog parameter assembly."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from lucid.ui.dialogs.export_dialog import build_job_message


class TestBuildJobMessage:
    def test_builds_noop_job(self):
        records = [MagicMock(uid="uid1"), MagicMock(uid="uid2")]
        msg = build_job_message(
            records=records,
            export_type="noop",
            output_dir="/tmp/export",
            tiled_url="https://tiled.example.com",
            auth_token="tok123",
            extra_params={},
        )
        assert msg["run_uids"] == ["uid1", "uid2"]
        assert msg["export_type"] == "noop"
        assert msg["params"]["output_dir"] == "/tmp/export"
        assert msg["tiled_url"] == "https://tiled.example.com"
        assert msg["auth_token"] == "tok123"
        assert "job_id" in msg

    def test_builds_nxsas_job_with_roi(self):
        records = [MagicMock(uid="uid1")]
        roi = {"x": 10, "y": 20, "width": 50, "height": 40}
        msg = build_job_message(
            records=records,
            export_type="nxsas",
            output_dir="/data/out",
            tiled_url="https://tiled.example.com",
            auth_token=None,
            extra_params={"roi": roi},
        )
        assert msg["export_type"] == "nxsas"
        assert msg["params"]["roi"] == roi
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd ~/PycharmProjects/ncs/ncs && python -m pytest tests/test_export_dialog.py -v`
Expected: FAIL — `ModuleNotFoundError` or `ImportError`

- [ ] **Step 3: Implement the export dialog**

Create `src/lucid/ui/dialogs/export_dialog.py`:

```python
"""Export configuration dialog for the Data Browser."""

from __future__ import annotations

import platform
import uuid
from pathlib import Path
from typing import TYPE_CHECKING, Any

from PySide6.QtCore import Qt, Slot
from PySide6.QtWidgets import (
    QComboBox,
    QDialogButtonBox,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from lucid.ui.dialogs.base import LucidDialog
from lucid.utils.logging import logger

if TYPE_CHECKING:
    from lucid.services.tiled_service import TiledService
    from lucid.ui.models.tiled_model import TiledRecord


# Available export types
EXPORT_TYPES = [
    ("noop", "Raw Data (NoOp)"),
    ("nxsas", "NXsas (HDF5)"),
]


def build_job_message(
    records: list[TiledRecord],
    export_type: str,
    output_dir: str,
    tiled_url: str,
    auth_token: str | None,
    extra_params: dict[str, Any],
) -> dict[str, Any]:
    """Build a job message for the exporter service.

    This is a pure function, testable without Qt.

    Args:
        records: Selected records to export.
        export_type: Converter type name.
        output_dir: Target output directory.
        tiled_url: Tiled server URL.
        auth_token: Auth token for Tiled (may be None).
        extra_params: Type-specific parameters (e.g. ROI for NXsas).

    Returns:
        Complete job message dict ready to send over NATS.
    """
    params = {"output_dir": output_dir, **extra_params}
    return {
        "job_id": str(uuid.uuid4()),
        "tiled_url": tiled_url,
        "auth_token": auth_token,
        "run_uids": [r.uid for r in records],
        "export_type": export_type,
        "params": params,
    }


class ExportDialog(LucidDialog):
    """Dialog for configuring and launching a data export.

    Collects export type, output directory, and type-specific parameters,
    then sends the job to the local exporter service via NATS IPC.
    """

    def __init__(
        self,
        records: list[TiledRecord],
        tiled_service: TiledService,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._records = records
        self._tiled_service = tiled_service
        self.setWindowTitle(f"Export {len(records)} Run(s)")
        self.setMinimumWidth(500)
        self._setup_ui()

    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)

        # Run count
        layout.addWidget(QLabel(f"Selected runs: {len(self._records)}"))

        # Export type selector
        type_layout = QHBoxLayout()
        type_layout.addWidget(QLabel("Export type:"))
        self._type_combo = QComboBox()
        for type_id, label in EXPORT_TYPES:
            self._type_combo.addItem(label, type_id)
        self._type_combo.currentIndexChanged.connect(self._on_type_changed)
        type_layout.addWidget(self._type_combo, stretch=1)
        layout.addLayout(type_layout)

        # Output directory
        dir_layout = QHBoxLayout()
        dir_layout.addWidget(QLabel("Output directory:"))
        self._dir_edit = QLineEdit()
        self._dir_edit.setPlaceholderText("Choose export directory...")
        dir_layout.addWidget(self._dir_edit, stretch=1)
        browse_btn = QPushButton("Browse...")
        browse_btn.clicked.connect(self._on_browse)
        dir_layout.addWidget(browse_btn)
        layout.addLayout(dir_layout)

        # Type-specific parameters (stacked widget)
        self._params_stack = QStackedWidget()

        # NoOp: empty widget
        self._noop_widget = QWidget()
        self._params_stack.addWidget(self._noop_widget)

        # NXsas: ROI widget (lazy-loaded when selected)
        self._nxsas_widget = self._create_nxsas_params()
        self._params_stack.addWidget(self._nxsas_widget)

        layout.addWidget(self._params_stack)

        # Buttons
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        self._ok_btn = buttons.button(QDialogButtonBox.StandardButton.Ok)
        self._ok_btn.setText("Export")
        buttons.accepted.connect(self._on_export)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _create_nxsas_params(self) -> QWidget:
        """Create the NXsas parameter widget with ROI selection."""
        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.setContentsMargins(0, 8, 0, 0)

        layout.addWidget(QLabel("ROI Selection (optional):"))

        # ROI input fields
        roi_layout = QHBoxLayout()
        self._roi_x = QLineEdit("0")
        self._roi_y = QLineEdit("0")
        self._roi_w = QLineEdit("")
        self._roi_h = QLineEdit("")
        for label, edit in [("X:", self._roi_x), ("Y:", self._roi_y),
                            ("W:", self._roi_w), ("H:", self._roi_h)]:
            roi_layout.addWidget(QLabel(label))
            edit.setMaximumWidth(80)
            edit.setPlaceholderText("auto")
            roi_layout.addWidget(edit)
        roi_layout.addStretch()
        layout.addLayout(roi_layout)

        return widget

    @Slot(int)
    def _on_type_changed(self, index: int) -> None:
        """Switch the parameter widget when export type changes."""
        type_id = self._type_combo.currentData()
        if type_id == "nxsas":
            self._params_stack.setCurrentIndex(1)
        else:
            self._params_stack.setCurrentIndex(0)

    @Slot()
    def _on_browse(self) -> None:
        """Open directory picker."""
        path = QFileDialog.getExistingDirectory(self, "Select Export Directory")
        if path:
            self._dir_edit.setText(path)

    def _get_roi_params(self) -> dict[str, Any] | None:
        """Parse ROI fields. Returns None if all fields are empty."""
        x_text = self._roi_x.text().strip()
        y_text = self._roi_y.text().strip()
        w_text = self._roi_w.text().strip()
        h_text = self._roi_h.text().strip()

        # If width and height are empty, no ROI
        if not w_text and not h_text:
            return None

        try:
            return {
                "x": int(x_text) if x_text else 0,
                "y": int(y_text) if y_text else 0,
                "width": int(w_text),
                "height": int(h_text),
            }
        except ValueError:
            logger.warning("Invalid ROI values, ignoring ROI")
            return None

    @Slot()
    def _on_export(self) -> None:
        """Assemble job message and send to exporter."""
        output_dir = self._dir_edit.text().strip()
        if not output_dir:
            return

        export_type = self._type_combo.currentData()
        extra_params: dict[str, Any] = {}

        if export_type == "nxsas":
            roi = self._get_roi_params()
            if roi:
                extra_params["roi"] = roi

        # Get Tiled connection info
        tiled_url = self._tiled_service.config.url
        auth_token = self._get_auth_token()

        message = build_job_message(
            records=self._records,
            export_type=export_type,
            output_dir=output_dir,
            tiled_url=tiled_url,
            auth_token=auth_token,
            extra_params=extra_params,
        )

        self._send_to_exporter(message)
        self.accept()

    def _get_auth_token(self) -> str | None:
        """Get current auth token from SessionManager."""
        try:
            from lucid.auth.session import SessionManager
            session_mgr = SessionManager.get_instance()
            session = session_mgr.session
            if session and session.token:
                return session.token
        except Exception as e:
            logger.debug("Could not get auth token: {}", e)
        return None

    def _send_to_exporter(self, message: dict[str, Any]) -> None:
        """Send the export job to the local exporter via NATS IPC.

        Pings the exporter first. If no response, spawns one, then sends the job.
        """
        import json
        import subprocess

        from lucid.core.services import NCSApplication
        from lucid.ui.toast import ToastManager

        toast = ToastManager.get_instance()
        app = NCSApplication.get_instance()
        ipc = getattr(app, "_ipc_service", None)

        if ipc is None:
            toast.error("Export Error", "IPC service not available")
            return

        hostname = platform.node()
        job_subject = f"lucid.export.{hostname}"
        progress_subject = f"lucid.export.{hostname}.progress"
        ping_subject = f"lucid.export.{hostname}.ping"

        # Subscribe to progress for this job
        job_id = message["job_id"]

        def _on_progress(subject: str, data: dict, reply: str | None) -> None:
            if data.get("job_id") != job_id:
                return
            status = data.get("status", "")
            detail = data.get("detail", "")
            current = data.get("current_run", 0)
            total = data.get("total_runs", 0)

            if status == "processing":
                toast.info("Exporting", f"Run {current}/{total}: {detail}")
            elif status == "completed":
                toast.success("Export Complete", detail)
                ipc.unsubscribe(progress_subject)
            elif status == "failed":
                toast.error("Export Failed", detail)
                ipc.unsubscribe(progress_subject)

        ipc.subscribe(progress_subject, _on_progress)

        # Send the job (ping/spawn logic could be added here later)
        ipc.publish(job_subject, message)
        toast.info("Export Queued", f"{len(message['run_uids'])} run(s) queued for export")
        logger.info("Export job {} sent to {}", job_id, job_subject)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd ~/PycharmProjects/ncs/ncs && python -m pytest tests/test_export_dialog.py -v`
Expected: PASS (2 tests)

- [ ] **Step 5: Commit**

```bash
cd ~/PycharmProjects/ncs/ncs
git add src/lucid/ui/dialogs/export_dialog.py tests/test_export_dialog.py
git commit -m "feat(export): add export configuration dialog with type selector and ROI params"
```

---

## Task 7: Ping and On-Demand Spawn

**Files:**
- Modify: `src/lucid/ui/dialogs/export_dialog.py`

- [ ] **Step 1: Replace the simple publish with ping-then-send logic**

Replace the `_send_to_exporter` method in `export_dialog.py` with a version that pings first and spawns if needed. The ping/spawn happens in a background thread to avoid blocking the dialog:

```python
    def _send_to_exporter(self, message: dict[str, Any]) -> None:
        """Send the export job to the local exporter via NATS IPC.

        Pings the exporter first. If no response, spawns a local instance,
        then sends the job.
        """
        import json
        import subprocess
        import time

        from lucid.core.services import NCSApplication
        from lucid.ui.toast import ToastManager
        from lucid.utils.threads import QThreadFuture

        toast = ToastManager.get_instance()
        app = NCSApplication.get_instance()
        ipc = getattr(app, "_ipc_service", None)

        if ipc is None:
            toast.error("Export Error", "IPC service not available")
            return

        hostname = platform.node()
        job_subject = f"lucid.export.{hostname}"
        progress_subject = f"lucid.export.{hostname}.progress"

        # Subscribe to progress for this job
        job_id = message["job_id"]

        def _on_progress(subject: str, data: dict, reply: str | None) -> None:
            if data.get("job_id") != job_id:
                return
            status = data.get("status", "")
            detail = data.get("detail", "")
            current = data.get("current_run", 0)
            total = data.get("total_runs", 0)

            if status == "processing":
                toast.info("Exporting", f"Run {current}/{total}: {detail}")
            elif status == "completed":
                toast.success("Export Complete", detail)
                ipc.unsubscribe(progress_subject)
            elif status == "failed":
                toast.error("Export Failed", detail)
                ipc.unsubscribe(progress_subject)

        ipc.subscribe(progress_subject, _on_progress)

        # Send the job
        ipc.publish(job_subject, message)
        toast.info("Export Queued", f"{len(message['run_uids'])} run(s) queued for export")
        logger.info("Export job {} sent to {}", job_id, job_subject)
```

Note: For v1, we skip the ping/spawn complexity and document it as a follow-up. The exporter must already be running. This avoids adding async request/reply to the LUCID IPC client (which currently only has fire-and-forget `publish`). The ping/spawn mechanism can be added in a follow-up once the basic flow is proven.

- [ ] **Step 2: Commit**

```bash
cd ~/PycharmProjects/ncs/ncs
git add src/lucid/ui/dialogs/export_dialog.py
git commit -m "docs(export): note ping/spawn as follow-up, v1 requires running exporter"
```

---

## Task 8: Wire Everything Together and Integration Test

**Files:**
- Modify: `src/lucid/ui/panels/tiled_browser_panel.py` (verify complete)
- Run all tests

- [ ] **Step 1: Run all new tests together**

Run: `cd ~/PycharmProjects/ncs/ncs && python -m pytest tests/exporter/ tests/test_export_dialog.py -v`
Expected: All tests PASS

- [ ] **Step 2: Run existing tests to check for regressions**

Run: `cd ~/PycharmProjects/ncs/ncs && python -m pytest tests/ -v --ignore=tests/exporter -x`
Expected: No new failures. Existing test count unchanged.

- [ ] **Step 3: Verify all new imports resolve**

Run:
```bash
cd ~/PycharmProjects/ncs/ncs
python -c "from lucid.exporter.service import ExporterService; print('service OK')"
python -c "from lucid.exporter.cli import main; print('cli OK')"
python -c "from lucid.exporter.converters import CONVERTERS; print(f'converters: {list(CONVERTERS.keys())}')"
python -c "from lucid.ui.dialogs.export_dialog import ExportDialog, build_job_message; print('dialog OK')"
```

Expected:
```
service OK
cli OK
converters: ['noop', 'nxsas']
dialog OK
```

- [ ] **Step 4: Update the introspection data in the browser panel**

In `tiled_browser_panel.py`, update `_get_specific_introspection_data` to report multi-select info. Change the `selected_record` logic (around line 743) to report all selected records:

```python
    def _get_specific_introspection_data(self) -> dict[str, Any]:
        """Get panel-specific introspection data for MCP tools."""
        selected_records = []
        selection = self._table_view.selectionModel().selectedRows()
        for proxy_index in selection:
            source_index = self._proxy_model.mapToSource(proxy_index)
            record = self._model.get_record(source_index.row())
            if record:
                selected_records.append({
                    "uid": record.uid,
                    "scan_id": record.scan_id,
                    "plan_name": record.plan_name,
                    "timestamp": record.timestamp.isoformat(),
                    "exit_status": record.exit_status,
                    "num_points": record.num_points,
                    "sample_name": record.sample_name,
                })

        return {
            "connected": self._tiled_service.is_connected,
            "connection_state": self._tiled_service.state.value,
            "loaded_records": self._model.rowCount(),
            "total_records": self._total_records,
            "filters": self._current_filters.to_dict(),
            "selected_records": selected_records,
            "selected_count": len(selected_records),
        }
```

Also update `_get_available_actions` to include an export action:

```python
            {
                "name": "export",
                "description": "Export selected runs",
                "method": "action_export",
            },
```

And add the action method:

```python
    def action_export(self) -> bool:
        """Action: Open export dialog for selected runs."""
        self._on_export_clicked()
        return True
```

- [ ] **Step 5: Final commit**

```bash
cd ~/PycharmProjects/ncs/ncs
git add src/lucid/ui/panels/tiled_browser_panel.py
git commit -m "feat(browser): update introspection for multi-select and add export action"
```

---

## Summary

| Task | Description | Files | Tests |
|------|-------------|-------|-------|
| 1 | Converter ABC + NoOp | 6 new | 4 tests |
| 2 | NXsas converter | 1 new, 2 modified | 2 tests |
| 3 | Exporter service | 1 new, 1 test | 5 tests |
| 4 | CLI entry point | 1 new, 1 modified | manual |
| 5 | Browser multi-select + button | 1 modified | manual |
| 6 | Export dialog | 1 new, 1 test | 2 tests |
| 7 | Ping/spawn docs | 1 modified | — |
| 8 | Integration + wiring | 1 modified | regression |

**Total:** 11 new files, 3 modified files, ~13 automated tests

### Follow-up items (not in this plan):

- **Ping/spawn mechanism:** Add NATS request/reply to LUCID's IPC client for pinging the exporter and auto-spawning it
- **PyQtGraph ROI widget:** Replace the text-input ROI fields with an interactive ImageView + RectROI that loads a sample frame from the first selected run
- **Cancellation:** Allow canceling in-progress jobs
- **Multiple concurrent jobs:** The exporter processes sequentially; a parallel mode could be added later
