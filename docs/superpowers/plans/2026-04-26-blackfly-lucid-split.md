# Blackfly observer → lightfall + endstation split (Spec B)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Move the `blackfly_observer` codebase into its proper homes — generic observer-camera abstraction into lightfall, FLIR-specific transport into the 7.0.1.1 endstation — and add an `AgentPlugin` skill that lets the embedded Claude agent build panels for Blackfly cameras.

**Architecture:** Three repos in play. `~/PycharmProjects/ncs/ncs/` (lightfall, master branch) gets `lightfall.ui.widgets.observers.{camera,image_view}` containing the `CameraBase` ABC and `CameraImageView` widget. `~/PycharmProjects/ncs/lightfall-endstation-7011/` (default branch) gets `lightfall_endstation_7011.observers.blackfly` containing the GVCP/GVSP transport stack, `BlackflyCamera`, the `BlackflyAgent` skill, the `bfly-discover` console script, and a `references/panel_template.py` source file the skill points the agent at. After both repos merge and hardware verification on tsuru passes, `~/PycharmProjects/blackfly_observer/` is archived (tar to `~/Downloads/` + delete tree). The dependency direction is one-way (endstation imports lightfall; lightfall never imports endstation), so phase 1 must complete before phase 2 starts.

**Tech Stack:** Python 3.10+, PySide6 (lightfall GUI), pyqtgraph (live image view), pytest (test framework), hatch (build), `lightfall.plugins.agent_plugin.AgentPlugin` (Spec A's plugin base class), `lightfall.plugins.manifest.PluginEntry` (manifest registration). MCP tool surface uses lightfall's `_mcp_helpers` pattern (see existing `lightfall/plugins/agents/`).

**Spec:** `~/PycharmProjects/ncs/ncs/docs/superpowers/specs/2026-04-26-blackfly-lightfall-split-design.md`

---

## File structure

### lightfall (`~/PycharmProjects/ncs/ncs/`)

Files to **create**:

- `src/lightfall/ui/widgets/observers/__init__.py` — re-exports `CameraBase`, `CameraImageView`.
- `src/lightfall/ui/widgets/observers/camera.py` — `CameraBase` ABC. (Single responsibility: the abstract camera contract. The `Geometry` dataclass with FLIR-specific register-readout semantics ships with `BlackflyCamera` in Task 5, not here.)
- `src/lightfall/ui/widgets/observers/image_view.py` — `CameraImageView` pyqtgraph widget. (Single responsibility: live-view widget over any `CameraBase`.)
- `tests/ui/widgets/observers/__init__.py` — empty.
- `tests/ui/widgets/observers/test_camera_base.py` — ABC contract tests.
- `tests/ui/widgets/observers/test_image_view.py` — widget tests using a fake `CameraBase`.

No files modified.

### lightfall-endstation-7011 (`~/PycharmProjects/ncs/lightfall-endstation-7011/`)

Files to **create**:

- `src/lightfall_endstation_7011/observers/__init__.py` — empty package marker.
- `src/lightfall_endstation_7011/observers/blackfly/__init__.py` — re-exports `BlackflyCamera`, `discover`, `DeviceInfo`.
- `src/lightfall_endstation_7011/observers/blackfly/camera.py` — `BlackflyCamera(CameraBase)` + `Geometry` dataclass.
- `src/lightfall_endstation_7011/observers/blackfly/discovery.py` — direct lift.
- `src/lightfall_endstation_7011/observers/blackfly/gvcp.py` — direct lift.
- `src/lightfall_endstation_7011/observers/blackfly/gvcp_transport.py` — direct lift.
- `src/lightfall_endstation_7011/observers/blackfly/gvsp.py` — direct lift.
- `src/lightfall_endstation_7011/observers/blackfly/pixel_formats.py` — direct lift.
- `src/lightfall_endstation_7011/observers/blackfly/registers.py` — direct lift.
- `src/lightfall_endstation_7011/observers/blackfly/skill.py` — `BlackflyAgent(AgentPlugin)`.
- `src/lightfall_endstation_7011/observers/blackfly/references/__init__.py` — empty.
- `src/lightfall_endstation_7011/observers/blackfly/references/panel_template.py` — canonical PanelPlugin template.
- `src/lightfall_endstation_7011/observers/blackfly/scripts/__init__.py` — empty.
- `src/lightfall_endstation_7011/observers/blackfly/scripts/discover.py` — `bfly-discover` entry point.
- `tests/observers/__init__.py` — empty.
- `tests/observers/blackfly/__init__.py` — empty.
- `tests/observers/blackfly/conftest.py` — pytest config (lifted from `blackfly_observer/tests/conftest.py`).
- `tests/observers/blackfly/test_camera_live.py` — 4 hw tests + transport integration.
- `tests/observers/blackfly/test_discovery.py`, `test_gvcp.py`, `test_gvcp_transport.py`, `test_gvsp.py`, `test_pixel_formats.py`, `test_registers.py` — direct lifts.
- `tests/observers/blackfly/test_skill.py` — NEW; smoke-test for `BlackflyAgent`.

Files to **modify**:

- `src/lightfall_endstation_7011/manifest.py` — add one `PluginEntry(type_name="agent", name="blackfly", ...)`.
- `pyproject.toml` — add `[project.scripts]` table with `bfly-discover` entry.

### blackfly_observer (`~/PycharmProjects/blackfly_observer/`)

Deleted after phase 3 hardware verification passes. Tarballed to `~/Downloads/blackfly_observer-archive-2026-04-26.tar.gz` first.

---

## Phase 1 — lightfall: land `CameraBase` + `CameraImageView`

These three tasks must merge to lightfall `master` before phase 2 can begin (endstation imports `lightfall.ui.widgets.observers`).

### Task 1: Lift `CameraBase` ABC into lightfall

**Files:**
- Create: `~/PycharmProjects/ncs/ncs/src/lightfall/ui/widgets/observers/__init__.py`
- Create: `~/PycharmProjects/ncs/ncs/src/lightfall/ui/widgets/observers/camera.py`
- Create: `~/PycharmProjects/ncs/ncs/tests/ui/widgets/observers/__init__.py`
- Create: `~/PycharmProjects/ncs/ncs/tests/ui/widgets/observers/test_camera_base.py`

- [ ] **Step 1: Create the test directory and write the failing ABC test**

In `tests/ui/widgets/observers/__init__.py`, write empty content (`""`).

In `tests/ui/widgets/observers/test_camera_base.py`:

```python
from __future__ import annotations

import pytest

from lightfall.ui.widgets.observers import CameraBase


def test_camerabase_is_abstract():
    """Can't instantiate CameraBase directly."""
    with pytest.raises(TypeError):
        CameraBase()  # type: ignore[abstract]


def test_camerabase_context_manager_shape():
    """Concrete __enter__ / __exit__ live on the base — subclasses inherit."""
    assert CameraBase.__enter__ is not None
    assert CameraBase.__exit__ is not None
```

- [ ] **Step 2: Run the test to verify it fails**

Run from `~/PycharmProjects/ncs/ncs/`:

```bash
.venv/Scripts/python -m pytest tests/ui/widgets/observers/test_camera_base.py -v
```

Expected: `ImportError` / `ModuleNotFoundError` for `lightfall.ui.widgets.observers`.

- [ ] **Step 3: Create the package and `camera.py` with the ABC**

In `src/lightfall/ui/widgets/observers/camera.py`:

```python
"""Observer-camera abstraction for non-ophyd hardware (e.g., GigE Vision).

For ophyd-backed area detectors, see lightfall.ui.widgets.camera (the ophyd-flavored peer).
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Callable

import numpy as np


class CameraBase(ABC):
    """Abstract observer-camera contract used by CameraImageView and similar consumers.

    Concrete implementations own the transport details (GVCP, USB3 Vision, etc).
    The base only specifies the lifecycle methods the consumer needs.
    """

    @abstractmethod
    def open(self) -> None:
        """Acquire exclusive control of the camera. Idempotent."""

    @abstractmethod
    def close(self) -> None:
        """Release control. Idempotent."""

    @abstractmethod
    def start_stream(
        self,
        on_frame: Callable[[np.ndarray], None] | None = None,
    ) -> None:
        """Begin delivering frames. Callback fires from a background thread."""

    @abstractmethod
    def stop_stream(self) -> None:
        """Stop delivering frames and release stream resources."""

    @abstractmethod
    def get_latest_frame(self) -> np.ndarray | None:
        """Most-recently-decoded frame, or None if no frame yet. Shared, read-only."""

    def __enter__(self) -> "CameraBase":
        self.open()
        return self

    def __exit__(self, *a) -> None:
        self.close()
```

In `src/lightfall/ui/widgets/observers/__init__.py`:

```python
"""Non-ophyd observer-camera abstractions and widgets.

For ophyd-backed area detectors, use lightfall.ui.widgets.camera instead.
"""
from lightfall.ui.widgets.observers.camera import CameraBase

__all__ = ["CameraBase"]
```

- [ ] **Step 4: Re-run the test, verify green**

```bash
.venv/Scripts/python -m pytest tests/ui/widgets/observers/test_camera_base.py -v
```

Expected: 2 passed.

- [ ] **Step 5: Commit**

```bash
git add src/lightfall/ui/widgets/observers/__init__.py \
        src/lightfall/ui/widgets/observers/camera.py \
        tests/ui/widgets/observers/__init__.py \
        tests/ui/widgets/observers/test_camera_base.py
git commit -m "feat(observers): add CameraBase ABC for non-ophyd cameras

First step of Spec B (Blackfly observer split). CameraBase is the
lifecycle contract any non-ophyd observer-camera transport satisfies.
Future BlackflyCamera (in lightfall-endstation-7011) and any other GigE-
Vision / USB3-Vision adapters subclass this.

For ophyd-backed AreaDetector, see lightfall.ui.widgets.camera (existing,
unchanged).

Spec: docs/superpowers/specs/2026-04-26-blackfly-lightfall-split-design.md"
```

---

### Task 2: Lift `CameraImageView` into lightfall (qtpy → PySide6)

**Files:**
- Create: `~/PycharmProjects/ncs/ncs/src/lightfall/ui/widgets/observers/image_view.py`
- Modify: `~/PycharmProjects/ncs/ncs/src/lightfall/ui/widgets/observers/__init__.py`
- Create: `~/PycharmProjects/ncs/ncs/tests/ui/widgets/observers/test_image_view.py`

- [ ] **Step 1: Write the failing widget test**

In `tests/ui/widgets/observers/test_image_view.py`:

```python
from __future__ import annotations

import threading
import time

import numpy as np
import pytest

from lightfall.ui.widgets.observers import CameraBase


@pytest.fixture(scope="module")
def qapp():
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    yield app


class FakeCamera(CameraBase):
    """CameraBase test double. Emits `n_frames` random frames from a background thread."""

    def __init__(self, shape: tuple[int, int] = (64, 96), n_frames: int = 3):
        self._shape = shape
        self._n_frames = n_frames
        self._on_frame = None
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._open_calls = 0
        self._close_calls = 0

    def open(self) -> None:
        self._open_calls += 1

    def close(self) -> None:
        self._close_calls += 1

    def start_stream(self, on_frame=None) -> None:
        self._on_frame = on_frame
        self._stop.clear()
        self._thread = threading.Thread(target=self._emit_loop, daemon=True)
        self._thread.start()

    def stop_stream(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2.0)

    def get_latest_frame(self):
        return None

    def _emit_loop(self) -> None:
        for i in range(self._n_frames):
            if self._stop.is_set():
                return
            img = (np.random.rand(*self._shape) * 255).astype(np.uint8)
            if self._on_frame is not None:
                self._on_frame(img)
            time.sleep(0.05)


def test_cameraimageview_requires_camera_to_start(qapp):
    from lightfall.ui.widgets.observers import CameraImageView
    view = CameraImageView()
    with pytest.raises(RuntimeError, match="no camera"):
        view.start()


def test_cameraimageview_set_camera_late(qapp):
    from lightfall.ui.widgets.observers import CameraImageView
    view = CameraImageView()
    fake = FakeCamera()
    view.set_camera(fake)
    assert "idle" in view._status.text()


def test_cameraimageview_receives_frames(qapp):
    """End-to-end: construct with FakeCamera, start, pump events, verify frames rendered."""
    from lightfall.ui.widgets.observers import CameraImageView
    fake = FakeCamera(shape=(32, 48), n_frames=3)
    view = CameraImageView(camera=fake)

    view.start()
    deadline = time.time() + 3.0
    while view._frames_seen < 3 and time.time() < deadline:
        qapp.processEvents()
        time.sleep(0.01)
    view.stop()
    qapp.processEvents()

    assert view._frames_seen == 3, f"expected 3 frames, got {view._frames_seen}"
    assert fake._open_calls == 1
    assert fake._close_calls == 1


def test_cameraimageview_cannot_change_camera_while_streaming(qapp):
    from lightfall.ui.widgets.observers import CameraImageView
    fake = FakeCamera(n_frames=10)
    view = CameraImageView(camera=fake)
    view.start()
    try:
        with pytest.raises(RuntimeError, match="stop the current stream"):
            view.set_camera(FakeCamera())
    finally:
        view.stop()
```

- [ ] **Step 2: Run the test, verify it fails on the import**

```bash
.venv/Scripts/python -m pytest tests/ui/widgets/observers/test_image_view.py -v
```

Expected: `ImportError: cannot import name 'CameraImageView' from 'lightfall.ui.widgets.observers'`.

- [ ] **Step 3: Create `image_view.py` with PySide6 imports**

In `src/lightfall/ui/widgets/observers/image_view.py`:

```python
"""pyqtgraph-based widget for live camera observation, generic over CameraBase."""
from __future__ import annotations

import numpy as np
import pyqtgraph as pg
from PySide6.QtCore import Signal
from PySide6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

from lightfall.ui.widgets.observers.camera import CameraBase


class CameraImageView(QWidget):
    """Minimal live-image widget over any CameraBase.

    Construct with a camera, or construct empty and call ``set_camera(cam)`` later.
    Click Start to open the camera and begin streaming. The receiver thread's frames
    are marshalled onto the GUI thread via a Qt Signal, so the pipeline is thread-safe.
    """

    frame_received = Signal(np.ndarray)

    def __init__(self, camera: CameraBase | None = None, parent=None):
        super().__init__(parent)
        self._camera: CameraBase | None = camera
        self._streaming = False
        self._frames_seen = 0

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        self._image_view = pg.ImageView()
        self._image_view.ui.histogram.hide()
        self._image_view.ui.roiBtn.hide()
        self._image_view.ui.menuBtn.hide()
        layout.addWidget(self._image_view)

        bar = QHBoxLayout()
        self._start_btn = QPushButton("Start")
        self._stop_btn = QPushButton("Stop")
        self._stop_btn.setEnabled(False)
        self._status = QLabel("no camera" if camera is None else "idle")
        bar.addWidget(self._start_btn)
        bar.addWidget(self._stop_btn)
        bar.addWidget(self._status, 1)
        layout.addLayout(bar)

        self._start_btn.clicked.connect(self.start)
        self._stop_btn.clicked.connect(self.stop)
        self.frame_received.connect(self._on_frame_gui)

    def set_camera(self, camera: CameraBase) -> None:
        """Bind (or replace) the camera. Must not be streaming."""
        if self._streaming:
            raise RuntimeError("stop the current stream before changing cameras")
        self._camera = camera
        self._status.setText("idle")

    def start(self) -> None:
        if self._streaming:
            return
        if self._camera is None:
            raise RuntimeError("no camera set; construct with a camera or call set_camera() first")
        self._camera.open()
        self._camera.start_stream(on_frame=self._on_frame_bg)
        self._streaming = True
        self._start_btn.setEnabled(False)
        self._stop_btn.setEnabled(True)
        self._status.setText("streaming")

    def stop(self) -> None:
        if not self._streaming:
            return
        assert self._camera is not None
        self._camera.stop_stream()
        self._camera.close()
        self._streaming = False
        self._start_btn.setEnabled(True)
        self._stop_btn.setEnabled(False)
        self._status.setText("stopped")

    def closeEvent(self, event) -> None:
        self.stop()
        super().closeEvent(event)

    def _on_frame_bg(self, img: np.ndarray) -> None:
        """Called from the camera's receiver thread. Hand off to GUI via Signal."""
        self.frame_received.emit(img)

    def _on_frame_gui(self, img: np.ndarray) -> None:
        """Called on the GUI thread once the signal is dispatched."""
        self._frames_seen += 1
        if self._frames_seen == 1:
            self._image_view.setImage(img.T, autoLevels=True, autoRange=True)
        else:
            # ImageView.updateImage() only refreshes display state (no image arg).
            # For per-frame data updates that preserve user pan/zoom/levels, push
            # the array through the underlying ImageItem.
            self._image_view.getImageItem().setImage(img.T, autoLevels=False)
        self._status.setText(f"{self._frames_seen} frames · {img.shape[1]}×{img.shape[0]} · {img.dtype}")
```

**Note:** the standalone `blackfly_observer/widgets.py` had a bug here — it called `self._image_view.updateImage(img.T)`, but `pyqtgraph.ImageView.updateImage`'s signature is `(self, autoHistogramRange=True)` and takes no image argument. The image array got bound to `autoHistogramRange`, then `if autoHistogramRange:` raised `ValueError: truth value of an array with more than one element is ambiguous`. Lucid's `pytest-qt` plugin re-raises Qt event-loop exceptions as test failures (the standalone repo's bare pytest silently swallowed them), which surfaced the bug during the Task 2 lift. The fix routes per-frame updates through the underlying `ImageItem` instead.

- [ ] **Step 4: Update `__init__.py` to export `CameraImageView`**

Replace contents of `src/lightfall/ui/widgets/observers/__init__.py`:

```python
"""Non-ophyd observer-camera abstractions and widgets.

For ophyd-backed area detectors, use lightfall.ui.widgets.camera instead.
"""
from lightfall.ui.widgets.observers.camera import CameraBase
from lightfall.ui.widgets.observers.image_view import CameraImageView

__all__ = ["CameraBase", "CameraImageView"]
```

- [ ] **Step 5: Run the widget tests, verify all green**

```bash
.venv/Scripts/python -m pytest tests/ui/widgets/observers/ -v
```

Expected: 6 passed (2 from `test_camera_base.py`, 4 from `test_image_view.py`).

- [ ] **Step 6: Commit**

```bash
git add src/lightfall/ui/widgets/observers/__init__.py \
        src/lightfall/ui/widgets/observers/image_view.py \
        tests/ui/widgets/observers/test_image_view.py
git commit -m "feat(observers): add CameraImageView pyqtgraph widget

Live-view widget generic over any CameraBase. Frames arrive on a
background thread (camera-side) and are marshalled onto the GUI
thread via a Qt Signal. First frame uses setImage (autoLevels +
autoRange); subsequent frames use updateImage so user pan/zoom and
levels survive.

Imports use PySide6 directly (matching neighboring lightfall widgets);
the standalone blackfly_observer used qtpy."
```

---

### Task 3: Run the full lightfall test suite and merge phase 1

**Files:** none modified. This is a verification + merge gate.

- [ ] **Step 1: Run the broader lightfall test suite**

```bash
.venv/Scripts/python -m pytest tests/ -x --ignore=tests/integration -q
```

Expected: same baseline pass count as before this work + 6 new tests under `tests/ui/widgets/observers/`. No regressions.

- [ ] **Step 2: Stop here for review.**

Hand off to the user for an integration review on lightfall `master`. The user merges (or asks for changes) before phase 2 begins. Phase 2 is **blocked** on this merge because `lightfall_endstation_7011.observers.blackfly.camera` will `from lightfall.ui.widgets.observers import CameraBase`, which only resolves once phase 1 is on `master` and the endstation's editable lightfall install picks it up.

---

## Phase 2 — endstation: land transport stack + skill

These six tasks all happen inside `~/PycharmProjects/ncs/lightfall-endstation-7011/`. All work happens on a single feature branch (suggest: `feat/observers-blackfly`) and merges as one PR. Tasks 4–9 are sequential.

**Pre-flight:** From `~/PycharmProjects/ncs/lightfall-endstation-7011/`, run `git checkout -b feat/observers-blackfly` and verify `python -c "from lightfall.ui.widgets.observers import CameraBase, CameraImageView"` succeeds (proves the workspace lightfall install picked up phase 1). If it fails, reinstall lightfall editable into the endstation's venv before proceeding.

### Task 4: Lift the GVCP/GVSP transport modules

**Files (create — all under `src/lightfall_endstation_7011/observers/blackfly/`):**
- `__init__.py`, `registers.py`, `pixel_formats.py`, `gvcp.py`, `gvcp_transport.py`, `gvsp.py`, `discovery.py`
- Plus parent: `src/lightfall_endstation_7011/observers/__init__.py`

The transport modules have inter-module imports (`gvcp_transport` imports `gvcp`; `discovery` imports `gvcp` and `gvcp_transport`). Lift them all in one task, with the imports rewritten in place.

- [ ] **Step 1: Create the `observers/blackfly/` package skeleton**

```bash
mkdir -p src/lightfall_endstation_7011/observers/blackfly
```

In `src/lightfall_endstation_7011/observers/__init__.py`, write `""` (empty).
In `src/lightfall_endstation_7011/observers/blackfly/__init__.py`, write `""` (empty for now — re-exports added in Task 5).

- [ ] **Step 2: Copy the six transport modules from blackfly_observer**

```bash
cp ~/PycharmProjects/blackfly_observer/src/blackfly_observer/registers.py \
   src/lightfall_endstation_7011/observers/blackfly/registers.py
cp ~/PycharmProjects/blackfly_observer/src/blackfly_observer/pixel_formats.py \
   src/lightfall_endstation_7011/observers/blackfly/pixel_formats.py
cp ~/PycharmProjects/blackfly_observer/src/blackfly_observer/gvcp.py \
   src/lightfall_endstation_7011/observers/blackfly/gvcp.py
cp ~/PycharmProjects/blackfly_observer/src/blackfly_observer/gvcp_transport.py \
   src/lightfall_endstation_7011/observers/blackfly/gvcp_transport.py
cp ~/PycharmProjects/blackfly_observer/src/blackfly_observer/gvsp.py \
   src/lightfall_endstation_7011/observers/blackfly/gvsp.py
cp ~/PycharmProjects/blackfly_observer/src/blackfly_observer/discovery.py \
   src/lightfall_endstation_7011/observers/blackfly/discovery.py
```

- [ ] **Step 3: Verify internal imports still resolve**

`registers.py`, `gvcp.py`, `gvsp.py` are leaves — no internal imports.
`pixel_formats.py` imports `from . import registers` — works as-is in the new location.
`gvcp_transport.py` imports `from . import gvcp` — works as-is.
`discovery.py` imports `from . import gvcp` — works as-is. (It opens its own UDP socket directly; doesn't use `GvcpClient`.)

Sanity-check by reading each file's import block:

```bash
head -15 src/lightfall_endstation_7011/observers/blackfly/{discovery,gvcp_transport,gvsp}.py
```

Expected: all imports use the relative `from . import …` style (no absolute `blackfly_observer` references). If any absolute import is found, change it to relative.

- [ ] **Step 4: Smoke-import the package**

```bash
.venv/Scripts/python -c "from lightfall_endstation_7011.observers.blackfly import registers, pixel_formats, gvcp, gvcp_transport, gvsp, discovery; print('ok')"
```

Expected: `ok`. If `ImportError`, re-check step 3.

- [ ] **Step 5: Commit**

```bash
git add src/lightfall_endstation_7011/observers/__init__.py \
        src/lightfall_endstation_7011/observers/blackfly/__init__.py \
        src/lightfall_endstation_7011/observers/blackfly/registers.py \
        src/lightfall_endstation_7011/observers/blackfly/pixel_formats.py \
        src/lightfall_endstation_7011/observers/blackfly/gvcp.py \
        src/lightfall_endstation_7011/observers/blackfly/gvcp_transport.py \
        src/lightfall_endstation_7011/observers/blackfly/gvsp.py \
        src/lightfall_endstation_7011/observers/blackfly/discovery.py
git commit -m "feat(observers): lift GVCP/GVSP transport from blackfly_observer

Direct lift of the six transport modules with no behavioural changes:
registers (FLIR address constants), pixel_formats (Bayer/mono decode),
gvcp (control protocol), gvcp_transport (UDP client), gvsp (stream
protocol + frame assembler), discovery (device scan).

BlackflyCamera lifts in the next commit. Tests follow in Task 6.

Spec: docs/superpowers/specs/2026-04-26-blackfly-lightfall-split-design.md"
```

---

### Task 5: Lift `BlackflyCamera` and wire the package exports

**Files:**
- Create: `src/lightfall_endstation_7011/observers/blackfly/camera.py`
- Modify: `src/lightfall_endstation_7011/observers/blackfly/__init__.py`

`BlackflyCamera` is the only module whose imports change: it must import `CameraBase` from lightfall (not from a sibling). The `Geometry` dataclass moves with it.

- [ ] **Step 1: Create `camera.py` with rewired imports**

In `src/lightfall_endstation_7011/observers/blackfly/camera.py`:

```python
"""High-level Blackfly camera: owns CCP, heartbeat, UDP stream channel."""
from __future__ import annotations

import logging
import socket
import struct
import threading
from dataclasses import dataclass
from typing import Callable

import numpy as np

from lightfall.ui.widgets.observers import CameraBase

from . import gvcp, gvsp, pixel_formats, registers
from .gvcp_transport import GvcpClient

_log = logging.getLogger(__name__)


@dataclass
class Geometry:
    width: int
    height: int
    pixel_format: int


class BlackflyCamera(CameraBase):
    def __init__(self, device_ip: str, bind_ip: str, heartbeat_timeout_ms: int = 3000):
        self._client = GvcpClient(bind_ip=bind_ip, device_ip=device_ip, timeout=1.0)
        self._device_ip = device_ip
        self._bind_ip = bind_ip
        self._heartbeat_timeout = heartbeat_timeout_ms
        self._heartbeat_thread: threading.Thread | None = None
        self._heartbeat_stop = threading.Event()
        self._opened = False
        self._stream_sk: socket.socket | None = None
        self._receiver_thread: threading.Thread | None = None
        self._receiver_stop = threading.Event()
        self._on_frame: Callable[[np.ndarray], None] | None = None
        self._latest_frame: np.ndarray | None = None
        self._latest_lock = threading.Lock()

    def open(self) -> None:
        if self._opened:
            return
        self._client.write_register(registers.REG_CCP, registers.CCP_CONTROL)
        ccp = self._client.read_register(registers.REG_CCP)
        if (ccp & (registers.CCP_CONTROL | registers.CCP_EXCLUSIVE)) == 0:
            raise RuntimeError(f"failed to acquire CCP, got 0x{ccp:08x}")
        self._client.write_register(registers.REG_HEARTBEAT_TIMEOUT, self._heartbeat_timeout)
        self._heartbeat_stop.clear()
        self._heartbeat_thread = threading.Thread(
            target=self._heartbeat_loop, daemon=True, name="blackfly-heartbeat",
        )
        self._heartbeat_thread.start()
        self._opened = True

    def close(self) -> None:
        if not self._opened:
            return
        self._heartbeat_stop.set()
        if self._heartbeat_thread:
            self._heartbeat_thread.join(timeout=4.0)
            if self._heartbeat_thread.is_alive():
                _log.warning("heartbeat thread did not stop within 4s; releasing CCP anyway")
        try:
            self._client.write_register(registers.REG_CCP, registers.CCP_NONE)
        except Exception as e:
            _log.warning("CCP release failed: %r", e)
        self._client.close()
        self._opened = False

    def _heartbeat_loop(self) -> None:
        interval = 1.0
        while not self._heartbeat_stop.wait(interval):
            try:
                ccp = self._client.read_register(registers.REG_CCP)
            except Exception as e:
                _log.warning("heartbeat read failed: %r", e)
                continue
            if (ccp & (registers.CCP_CONTROL | registers.CCP_EXCLUSIVE)) == 0:
                _log.error("control lost: CCP register reads 0x%08x", ccp)
                return

    def read_device_info(self) -> gvcp.DeviceInfo:
        from .discovery import discover
        devs = [d for d in discover(self._bind_ip, [(self._device_ip, gvcp.GVCP_PORT)], timeout=1.0)
                if d.ip == self._device_ip]
        if not devs:
            raise RuntimeError(f"no discovery response from {self._device_ip}")
        return devs[0]

    def read_geometry(self) -> Geometry:
        return Geometry(
            width=self._client.read_register(registers.REG_WIDTH),
            height=self._client.read_register(registers.REG_HEIGHT),
            pixel_format=self._client.read_register(registers.REG_PIXEL_FORMAT),
        )

    def configure_stream(self, host_ip: str, host_port: int, packet_size: int = 1400) -> None:
        host_ipv4 = struct.unpack(">I", socket.inet_aton(host_ip))[0]
        self._client.write_register(registers.REG_SC0_DEST_ADDR, host_ipv4)
        self._client.write_register(registers.REG_SC0_PORT_HOST, host_port & 0xFFFF)
        cur_pkt_reg = self._client.read_register(registers.REG_SC0_PACKET_SIZE)
        new_pkt_reg = (cur_pkt_reg & 0xE0000000) | (packet_size & 0xFFFF)
        self._client.write_register(registers.REG_SC0_PACKET_SIZE, new_pkt_reg)

    def start_acquisition(self) -> None:
        self._client.write_register(registers.REG_ACQUISITION_MODE, registers.ACQUISITION_MODE_CONTINUOUS)
        self._client.write_register(registers.REG_ACQUISITION_START, 1)

    def stop_acquisition(self) -> None:
        self._client.write_register(registers.REG_ACQUISITION_STOP, 1)

    def start_stream(
        self,
        on_frame: Callable[[np.ndarray], None] | None = None,
        packet_size: int = 1400,
    ) -> None:
        """Opens a UDP listener, configures the camera stream, and starts acquisition."""
        self._stream_sk = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._stream_sk.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 16 * 1024 * 1024)
        actual_rcvbuf = self._stream_sk.getsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF)
        requested = 16 * 1024 * 1024
        if actual_rcvbuf // 2 < requested:
            _log.warning(
                "SO_RCVBUF clamped to %d bytes (requested %d); "
                "check /proc/sys/net/core/rmem_max — expect packet loss at high framerate",
                actual_rcvbuf, requested,
            )
        self._stream_sk.bind((self._bind_ip, 0))
        host_port = self._stream_sk.getsockname()[1]

        self.configure_stream(self._bind_ip, host_port, packet_size)
        self._receiver_stop = threading.Event()
        self._on_frame = on_frame
        self._latest_frame = None
        self._receiver_thread = threading.Thread(
            target=self._receiver_loop, daemon=True, name="blackfly-receiver",
        )
        self._receiver_thread.start()
        self.start_acquisition()

    def stop_stream(self) -> None:
        try:
            self.stop_acquisition()
        finally:
            self._receiver_stop.set()
            if self._stream_sk is not None:
                self._stream_sk.close()
            if self._receiver_thread is not None:
                self._receiver_thread.join(timeout=2.0)

    def get_latest_frame(self) -> np.ndarray | None:
        with self._latest_lock:
            return self._latest_frame

    def _receiver_loop(self) -> None:
        asm = gvsp.FrameAssembler()
        self._stream_sk.settimeout(0.5)
        while not self._receiver_stop.is_set():
            try:
                data, _ = self._stream_sk.recvfrom(65536)
            except socket.timeout:
                continue
            except OSError:
                break
            try:
                pkt = gvsp.parse_packet(data)
            except Exception:
                continue
            frame = asm.feed(pkt)
            if frame is None:
                continue
            try:
                img = pixel_formats.decode(
                    frame.data, frame.leader.width, frame.leader.height, frame.leader.pixel_format
                )
            except Exception as e:
                _log.warning("decode error: %r", e)
                continue
            with self._latest_lock:
                self._latest_frame = img
            if self._on_frame is not None:
                try:
                    self._on_frame(img)
                except Exception as e:
                    _log.warning("on_frame callback raised: %r", e)
```

- [ ] **Step 2: Wire the package re-exports**

Replace `src/lightfall_endstation_7011/observers/blackfly/__init__.py`:

```python
"""FLIR Blackfly S support for ALS Beamline 7.0.1.1.

Provides BlackflyCamera (CameraBase implementation over GVCP/GVSP) and
discovery primitives. The CameraBase ABC and CameraImageView widget live
in lightfall.ui.widgets.observers.
"""
from lightfall_endstation_7011.observers.blackfly.camera import BlackflyCamera, Geometry
from lightfall_endstation_7011.observers.blackfly.discovery import discover
from lightfall_endstation_7011.observers.blackfly.gvcp import DeviceInfo

__all__ = ["BlackflyCamera", "DeviceInfo", "Geometry", "discover"]
```

- [ ] **Step 3: Smoke-import the new public surface**

```bash
.venv/Scripts/python -c "from lightfall_endstation_7011.observers.blackfly import BlackflyCamera, DeviceInfo, Geometry, discover; print(BlackflyCamera, DeviceInfo, Geometry, discover)"
```

Expected: prints four objects without error. Verifies that `BlackflyCamera` resolves `CameraBase` from lightfall (which only works because phase 1 is merged).

- [ ] **Step 4: Commit**

```bash
git add src/lightfall_endstation_7011/observers/blackfly/camera.py \
        src/lightfall_endstation_7011/observers/blackfly/__init__.py
git commit -m "feat(observers): lift BlackflyCamera, rewire CameraBase from lightfall

BlackflyCamera (CameraBase, GVCP/GVSP transport) moves into the
endstation. The only behavioural change vs the standalone version is
the import path for CameraBase — now lightfall.ui.widgets.observers.

Public package surface: BlackflyCamera, DeviceInfo, Geometry, discover."
```

---

### Task 6: Lift the transport tests and verify offline

**Files (create — all under `tests/observers/blackfly/`):**
- `__init__.py` (parent: `tests/observers/__init__.py` too)
- `conftest.py`, `test_camera_live.py`, `test_discovery.py`, `test_gvcp.py`, `test_gvcp_transport.py`, `test_gvsp.py`, `test_pixel_formats.py`, `test_registers.py`

- [ ] **Step 1: Create the test-tree skeleton**

```bash
mkdir -p tests/observers/blackfly
```

Write `""` to `tests/observers/__init__.py` and `tests/observers/blackfly/__init__.py`.

- [ ] **Step 2: Copy the seven transport tests + conftest from blackfly_observer**

```bash
cp ~/PycharmProjects/blackfly_observer/tests/conftest.py            tests/observers/blackfly/conftest.py
cp ~/PycharmProjects/blackfly_observer/tests/test_camera_live.py    tests/observers/blackfly/test_camera_live.py
cp ~/PycharmProjects/blackfly_observer/tests/test_discovery.py      tests/observers/blackfly/test_discovery.py
cp ~/PycharmProjects/blackfly_observer/tests/test_gvcp.py           tests/observers/blackfly/test_gvcp.py
cp ~/PycharmProjects/blackfly_observer/tests/test_gvcp_transport.py tests/observers/blackfly/test_gvcp_transport.py
cp ~/PycharmProjects/blackfly_observer/tests/test_gvsp.py           tests/observers/blackfly/test_gvsp.py
cp ~/PycharmProjects/blackfly_observer/tests/test_pixel_formats.py  tests/observers/blackfly/test_pixel_formats.py
cp ~/PycharmProjects/blackfly_observer/tests/test_registers.py      tests/observers/blackfly/test_registers.py
```

- [ ] **Step 3: Rewrite imports in the seven test files**

Every `from blackfly_observer` (or `import blackfly_observer`) reference must become `from lightfall_endstation_7011.observers.blackfly` (or equivalent). Use Grep to find them:

```bash
```

Then in each test file, replace:
- `from blackfly_observer.camera import …` → `from lightfall_endstation_7011.observers.blackfly.camera import …`
- `from blackfly_observer.gvcp import …` → `from lightfall_endstation_7011.observers.blackfly.gvcp import …`
- `from blackfly_observer.gvcp_transport import …` → `from lightfall_endstation_7011.observers.blackfly.gvcp_transport import …`
- `from blackfly_observer.gvsp import …` → `from lightfall_endstation_7011.observers.blackfly.gvsp import …`
- `from blackfly_observer.discovery import …` → `from lightfall_endstation_7011.observers.blackfly.discovery import …`
- `from blackfly_observer.pixel_formats import …` → `from lightfall_endstation_7011.observers.blackfly.pixel_formats import …`
- `from blackfly_observer.registers import …` → `from lightfall_endstation_7011.observers.blackfly.registers import …`
- `from blackfly_observer import …` → `from lightfall_endstation_7011.observers.blackfly import …`

Re-grep to confirm no `blackfly_observer` references remain in `tests/observers/blackfly/`.

- [ ] **Step 4: Update `pyproject.toml` `testpaths` to include the new tests dir**

The endstation's `pyproject.toml` already has `testpaths = ["tests"]` — pytest discovers anything under `tests/` automatically, so no change needed.

Also: the standalone `blackfly_observer` declared a custom `hw` marker. Add it to the endstation's pytest config so the `@pytest.mark.hw` decorator in `test_camera_live.py` resolves cleanly. Modify `pyproject.toml`:

Find:
```toml
[tool.pytest.ini_options]
testpaths = ["tests"]
```

Replace with:
```toml
[tool.pytest.ini_options]
testpaths = ["tests"]
markers = [
    "hw: requires live Blackfly S camera at BLACKFLY_TEST_IP env var",
]
```

- [ ] **Step 5: Run the offline tests, verify all green**

```bash
.venv/Scripts/python -m pytest tests/observers/blackfly/ -v -m "not hw"
```

Expected: all offline tests pass with zero failures. Hw tests are deselected by `-m "not hw"`. The original blackfly_observer suite had 71 tests total (67 offline + 4 hw); the lift preserves that count exactly.

**Note on the research data file.** `test_registers.py::test_blackfly_s_addresses_match_research_file` references `Path(__file__).parent.parent / "research" / "blackfly_s_registers.txt"`. The data file lifts to `tests/observers/research/blackfly_s_registers.txt` so the test's existing path expression resolves without editing the test body.

- [ ] **Step 6: Commit**

```bash
git add tests/observers/__init__.py \
        tests/observers/blackfly/ \
        pyproject.toml
git commit -m "test(observers): lift Blackfly transport tests; add hw marker

70 offline tests + 4 hw-gated tests covering registers, pixel formats,
gvcp, gvcp_transport, gvsp, discovery, and camera_live integration.
Imports rewritten to target lightfall_endstation_7011.observers.blackfly;
the 4 hw tests still gate on BLACKFLY_TEST_IP env var."
```

---

### Task 7: Re-home the `bfly-discover` console script

**Files:**
- Create: `src/lightfall_endstation_7011/observers/blackfly/scripts/__init__.py`
- Create: `src/lightfall_endstation_7011/observers/blackfly/scripts/discover.py`
- Modify: `pyproject.toml` (add `[project.scripts]`)

- [ ] **Step 1: Create the scripts subpackage**

```bash
mkdir -p src/lightfall_endstation_7011/observers/blackfly/scripts
```

Write `""` to `src/lightfall_endstation_7011/observers/blackfly/scripts/__init__.py`.

- [ ] **Step 2: Copy + rewire the script**

Copy from blackfly_observer:

```bash
cp ~/PycharmProjects/blackfly_observer/src/blackfly_observer/scripts/discover.py \
   src/lightfall_endstation_7011/observers/blackfly/scripts/discover.py
```

Edit the imports in the copied file. Replace:

```python
from blackfly_observer import gvcp
from blackfly_observer.discovery import discover
```

with:

```python
from lightfall_endstation_7011.observers.blackfly import gvcp
from lightfall_endstation_7011.observers.blackfly.discovery import discover
```

(The rest of the script — `_default_bind_ip`, `_local_ipv4_addresses`, `main` — stays unchanged.)

- [ ] **Step 3: Add the console-script entry to `pyproject.toml`**

Insert this table after `[project.optional-dependencies]` (or wherever a `[project.scripts]` table conventionally fits):

```toml
[project.scripts]
bfly-discover = "lightfall_endstation_7011.observers.blackfly.scripts.discover:main"
```

- [ ] **Step 4: Reinstall the endstation editable so the script registers**

```bash
.venv/Scripts/pip install -e . --no-deps
```

Expected: install succeeds, no errors.

- [ ] **Step 5: Smoke-run `bfly-discover --help`**

```bash
.venv/Scripts/bfly-discover --help
```

Expected: argparse usage banner. (Don't run a real scan here — that needs a connected camera.)

- [ ] **Step 6: Commit**

```bash
git add src/lightfall_endstation_7011/observers/blackfly/scripts/ \
        pyproject.toml
git commit -m "feat(observers): re-home bfly-discover console script

CLI entry point lives at lightfall_endstation_7011.observers.blackfly.scripts.discover.
Same UX as the standalone blackfly_observer version."
```

---

### Task 8: Add the canonical `panel_template.py` reference

**Files:**
- Create: `src/lightfall_endstation_7011/observers/blackfly/references/__init__.py`
- Create: `src/lightfall_endstation_7011/observers/blackfly/references/panel_template.py`

This file is the **literal source** the BlackflyAgent skill instructs the embedded agent to copy, substitute IPs into, and pass to `panel_builder`'s `ncs_create_user_plugin` MCP tool.

- [ ] **Step 1: Create the references subpackage**

```bash
mkdir -p src/lightfall_endstation_7011/observers/blackfly/references
```

Write `""` to `src/lightfall_endstation_7011/observers/blackfly/references/__init__.py`.

- [ ] **Step 2: Write the panel template**

In `src/lightfall_endstation_7011/observers/blackfly/references/panel_template.py`:

```python
"""Canonical PanelPlugin template for a Blackfly S live-view panel.

The Blackfly skill (lightfall_endstation_7011.observers.blackfly.skill.BlackflyAgent)
instructs the embedded Claude agent to:
  1. read this file's source verbatim,
  2. substitute the placeholders <IP> (camera IPv4) and <HOST> (host NIC IPv4)
     with values gathered from the user (and/or discover_blackfly_cameras),
  3. pass the substituted text to mcp__panel_builder__ncs_create_user_plugin.

Two placeholders only — keep it that way. If a user wants something fancier
(extra controls, multi-camera layout, ROI overlays), they edit the resulting
plugin after creation; this template is only the minimum viable live-view.

The file is also a valid Python module on its own (the placeholder strings are
just string literals at construction time), so the skill's smoke-test can
import it.
"""
from __future__ import annotations

from typing import ClassVar

from lightfall.plugins.panel_plugin import PanelPlugin
from lightfall.ui.panels.base import BasePanel, PanelMetadata
from lightfall.ui.widgets.observers import CameraImageView
from lightfall_endstation_7011.observers.blackfly import BlackflyCamera


class BlackflyLivePanel(BasePanel):
    """Live-view panel hosting a single BlackflyCamera + CameraImageView."""

    panel_metadata: ClassVar[PanelMetadata] = PanelMetadata(
        id="user.panels.blackfly_live",
        name="Blackfly S Live View",
        description="Live image stream from a FLIR Blackfly S over GVCP/GVSP",
        category="Devices",
        keywords=["blackfly", "flir", "camera", "gige", "live view"],
    )

    def _setup_ui(self) -> None:
        super()._setup_ui()
        camera = BlackflyCamera(device_ip="<IP>", bind_ip="<HOST>")
        self._layout.addWidget(CameraImageView(camera=camera))


class BlackflyLivePanelPlugin(PanelPlugin):
    """Panel plugin exposing the Blackfly live-view panel under View > User."""

    @property
    def name(self) -> str:
        return "blackfly_live"

    def get_panel_class(self) -> type[BasePanel]:
        return BlackflyLivePanel
```

**API verification done up-front:** the actual lightfall contract (verified against `~/PycharmProjects/ncs/ncs/src/lightfall/plugins/panel_plugin.py` and `lightfall/ui/panels/base.py`) is:
- `PanelPlugin` is the plugin entry: implements `name` + `get_panel_class() -> type[BasePanel]`. Auto-registered via `__init_subclass__`.
- The actual UI lives in a `BasePanel` subclass with class-level `panel_metadata: PanelMetadata` and a `_setup_ui()` override that calls `super()._setup_ui()` and adds widgets to `self._layout`.
- `panel_builder`'s `ncs_create_user_plugin` MCP tool validates by `compile()` + `exec()` in an isolated namespace and discovers concrete `PluginType` subclasses.

- [ ] **Step 3: Smoke-import the template (it must be valid Python even though `<IP>`/`<HOST>` aren't real IPs — they're inside string literals so import succeeds)**

```bash
.venv/Scripts/python -c "import lightfall_endstation_7011.observers.blackfly.references.panel_template as t; print(t.BlackflyLivePanel)"
```

Expected: prints the class. Confirms all imports in the template resolve.

- [ ] **Step 4: Commit**

```bash
git add src/lightfall_endstation_7011/observers/blackfly/references/
git commit -m "feat(observers): add panel_template reference for Blackfly skill

Two-placeholder template (<IP>, <HOST>). The BlackflyAgent skill (next
commit) instructs the embedded Claude agent to copy this file, substitute
the placeholders, and hand the result to panel_builder."
```

---

### Task 9: Add the `BlackflyAgent` skill (TDD)

**Files:**
- Create: `src/lightfall_endstation_7011/observers/blackfly/skill.py`
- Create: `tests/observers/blackfly/test_skill.py`

This is **new** code (not a lift). Use TDD: test first → fail → implement → green.

Reference: read `~/PycharmProjects/ncs/ncs/src/lightfall/plugins/agents/panel_builder.py` for the `AgentPlugin` shape and `~/PycharmProjects/ncs/ncs/src/lightfall/plugins/agents/_mcp_helpers.py` for the MCP-tool helper convention used by other built-in plugins. Match that convention so the skill plugs into the SDK-native machinery without surprises.

- [ ] **Step 1: Write the failing skill test**

In `tests/observers/blackfly/test_skill.py`:

```python
"""Smoke tests for the BlackflyAgent skill."""
from __future__ import annotations

from lightfall_endstation_7011.observers.blackfly.skill import BlackflyAgent


def test_blackfly_agent_metadata():
    agent = BlackflyAgent()
    assert agent.name == "blackfly"
    assert agent.display_name == "Blackfly Camera"
    assert "Blackfly" in agent.description
    assert agent.category == "devices"


def test_blackfly_agent_system_prompt_non_empty():
    """Skill body must be non-empty and reference the public API entry points."""
    body = BlackflyAgent().get_system_prompt()
    assert body.strip(), "system prompt must not be empty"
    # The prompt must teach the agent the public import paths.
    assert "lightfall.ui.widgets.observers" in body
    assert "lightfall_endstation_7011.observers.blackfly" in body
    # And the workflow must mention the discover tool by name.
    assert "discover_blackfly_cameras" in body


def test_blackfly_agent_exposes_one_mcp_tool():
    """create_tools returns exactly the discover_blackfly_cameras tool."""
    tools = BlackflyAgent().create_tools()
    assert len(tools) == 1, f"expected one tool, got {len(tools)}"
    # Tool registration objects in lightfall carry a .name (matches _mcp_helpers convention).
    tool = tools[0]
    name = getattr(tool, "name", None) or getattr(tool, "__name__", None)
    assert name == "discover_blackfly_cameras", f"unexpected tool name: {name!r}"


def test_blackfly_agent_references_dir_resolves():
    """get_references_dir must point at an existing directory containing panel_template.py."""
    refs = BlackflyAgent().get_references_dir()
    assert refs is not None
    assert refs.is_dir()
    assert (refs / "panel_template.py").is_file()
```

- [ ] **Step 2: Run the test, verify it fails on the import**

```bash
.venv/Scripts/python -m pytest tests/observers/blackfly/test_skill.py -v
```

Expected: `ImportError: No module named 'lightfall_endstation_7011.observers.blackfly.skill'`.

- [ ] **Step 3: Implement `BlackflyAgent`**

In `src/lightfall_endstation_7011/observers/blackfly/skill.py`:

```python
"""BlackflyAgent: discover and wire FLIR Blackfly S cameras into user PanelPlugins."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from lightfall.plugins.agent_plugin import AgentPlugin

from lightfall_endstation_7011.observers.blackfly.discovery import discover


class BlackflyAgent(AgentPlugin):
    """Skill telling the embedded Claude agent how to build a Blackfly live-view panel."""

    @property
    def name(self) -> str:
        return "blackfly"

    @property
    def display_name(self) -> str:
        return "Blackfly Camera"

    @property
    def description(self) -> str:
        return "Discover and wire FLIR Blackfly S cameras into user PanelPlugins"

    @property
    def category(self) -> str:
        return "devices"

    @property
    def priority(self) -> int:
        return 30

    def get_references_dir(self) -> Path | None:
        return Path(__file__).parent / "references"

    def get_system_prompt(self) -> str:
        return """\
## Blackfly Camera Skill

Use this skill when the user asks for a panel that shows a FLIR Blackfly S
(or any GigE Vision Blackfly) camera, or mentions making a viewer for one of
those cameras.

### Public API

```python
from lightfall.ui.widgets.observers import CameraImageView
from lightfall_endstation_7011.observers.blackfly import BlackflyCamera
```

`BlackflyCamera(device_ip, bind_ip)` takes two strings: `device_ip` is the
camera's IPv4 address; `bind_ip` is the host NIC IP the camera should send
GVSP packets to. Both come from the user (or, for `device_ip`, from the
`discover_blackfly_cameras` tool below).

### MCP tool

`discover_blackfly_cameras(timeout_s=1.0)` — scans the local subnet and
returns `list[dict]` of `{ip, mac, model, serial}` entries. Call this when
the user has not given you an explicit camera IP.

### Workflow

1. If the user did not provide a `device_ip`, call `discover_blackfly_cameras()`.
2. If multiple cameras are returned, ask the user to pick one.
3. If the user did not provide a `bind_ip` (the host NIC), ask for it.
4. Read `references/panel_template.py` for the canonical PanelPlugin source.
5. Substitute `<IP>` with the chosen `device_ip` and `<HOST>` with the chosen
   `bind_ip` in the template's text, then call
   `mcp__panel_builder__ncs_create_user_plugin` with the substituted source.
6. Confirm to the user where the panel will appear (View > User > Blackfly S Live View).
"""

    def create_tools(self) -> list[Any]:
        # Match the _mcp_helpers convention used by sibling lightfall agents (see
        # lightfall/plugins/agents/_mcp_helpers.py for the @tool decorator pattern).
        from lightfall.plugins.agents._mcp_helpers import tool

        @tool(name="discover_blackfly_cameras")
        def discover_blackfly_cameras(timeout_s: float = 1.0) -> list[dict]:
            """Scan for FLIR Blackfly S cameras on the local subnet.

            Returns list of dicts with keys: ip, mac, model, serial.
            """
            results = discover(timeout=timeout_s)
            return [
                {
                    "ip": d.ip,
                    "mac": d.mac,
                    "model": d.model_name,
                    "serial": d.serial_number,
                }
                for d in results
            ]

        return [discover_blackfly_cameras]
```

**Implementer note:** the exact `@tool` decorator name and signature in `lightfall.plugins.agents._mcp_helpers` may differ from the placeholder above. Before writing the implementation, read `~/PycharmProjects/ncs/ncs/src/lightfall/plugins/agents/_mcp_helpers.py` and `~/PycharmProjects/ncs/ncs/src/lightfall/plugins/agents/panel_builder.py` to match the actual helper API. If the helper expects a different return shape (e.g., a single registration object rather than a list of decorated callables), adapt accordingly. The test in step 1 only requires that `create_tools()` returns a one-element list whose lone item carries a `name` attribute (or `__name__`) of `"discover_blackfly_cameras"`. Equally, double-check `discovery.discover`'s actual signature — if `timeout` isn't a top-level kwarg or `bind_ip` is required, adjust the wrapper to match (the discover function may need a `bind_ip`, in which case the tool should auto-detect via the same `_default_bind_ip` helper used by the `bfly-discover` script).

- [ ] **Step 4: Run the test, verify all four pass**

```bash
.venv/Scripts/python -m pytest tests/observers/blackfly/test_skill.py -v
```

Expected: 4 passed.

- [ ] **Step 5: Run the full offline endstation suite to confirm no regressions**

```bash
.venv/Scripts/python -m pytest tests/ -m "not hw" -q
```

Expected: all previously passing tests still pass; new skill tests pass.

- [ ] **Step 6: Commit**

```bash
git add src/lightfall_endstation_7011/observers/blackfly/skill.py \
        tests/observers/blackfly/test_skill.py
git commit -m "feat(observers): add BlackflyAgent skill

Single-tool agent plugin (discover_blackfly_cameras) plus a SKILL.md
body that teaches the embedded Claude agent the public Blackfly API and
the canonical workflow:
  discover -> pick -> ask for bind_ip -> read references/panel_template.py
  -> substitute placeholders -> hand off to panel_builder.

Plugin manifest wiring follows in the next commit."
```

---

### Task 10: Wire the manifest entry and integration-smoke the skill

**Files:**
- Modify: `src/lightfall_endstation_7011/manifest.py`

- [ ] **Step 1: Add the manifest entry**

In `src/lightfall_endstation_7011/manifest.py`, the existing manifest is:

```python
manifest = PluginManifest(
    name="lightfall-endstation-7011",
    version="0.1.0",
    description="Lightfall plugins for ALS Beamline 7.0.1.1 endstation",
    plugins=[
        PluginEntry(type_name="controller", name="andor_camera", ...),
        PluginEntry(type_name="controller", name="pimte_camera", ...),
        PluginEntry(type_name="controller", name="detector_diode", ...),
    ],
)
```

Append one entry to the `plugins=[...]` list:

```python
PluginEntry(
    type_name="agent",
    name="blackfly",
    import_path="lightfall_endstation_7011.observers.blackfly.skill:BlackflyAgent",
    metadata={"priority": 30},
),
```

Also update the module docstring at the top of `manifest.py` to mention the new agent plugin (so the file's purpose stays accurate).

- [ ] **Step 2: Verify the manifest loads cleanly**

```bash
.venv/Scripts/python -c "from lightfall_endstation_7011.manifest import manifest; print(len(manifest.plugins), 'plugins'); [print(p.type_name, p.name) for p in manifest.plugins]"
```

Expected:
```
4 plugins
controller andor_camera
controller pimte_camera
controller detector_diode
agent blackfly
```

- [ ] **Step 3: Boot lightfall against this endstation**

In a terminal on Windows (the dev workstation):

```bash
cd ~/PycharmProjects/ncs/ncs
.venv/Scripts/python -m lightfall
```

Expected:
- App boots without error.
- The settings UI under Tools / Settings (or wherever the agent settings are surfaced) lists "Blackfly Camera" alongside the other agent plugins, in the **devices** category, enabled by default.
- The lightfall log shows the `blackfly` plugin loading; no plugin-load errors.

If any error mentions `BlackflyAgent`, capture the traceback and fix at the source (most likely a typo in the import path or a `_mcp_helpers` API mismatch).

- [ ] **Step 4: Functional smoke-test of the discover MCP tool (optional but recommended)**

In the embedded Claude panel, prompt: *"List Blackfly cameras on the network."* Expect the agent to call `mcp__blackfly__discover_blackfly_cameras` and either return an empty list (if no camera is reachable) or a list of `{ip, mac, model, serial}` dicts. Either result is success at this stage; we are not yet on tsuru with a connected camera.

- [ ] **Step 5: Commit**

```bash
git add src/lightfall_endstation_7011/manifest.py
git commit -m "feat(observers): register BlackflyAgent in endstation manifest

PluginEntry(type_name='agent', name='blackfly', priority=30) wires the
skill into the Spec A AgentPlugin/AgentRegistry pipeline. The plugin
auto-loads on app start and surfaces in the agent-settings UI under
the 'devices' category."
```

- [ ] **Step 6: Stop here for review.**

Hand off to the user for an integration review of the endstation feature branch. The user merges (or asks for changes) before phase 3 (hardware verification + archive).

---

## Phase 3 — hardware verification and archive

### Task 11: Hardware verification on tsuru

**Files:** none modified. This is end-to-end verification on the live BFS-PGE-122S6C camera at 192.168.10.81.

- [ ] **Step 1: Sync the endstation feature branch (now master) to tsuru**

Use the established sync mechanism (git-archive → base64 → psmux send-keys, per `project_blackfly_observer.md`'s tsuru access notes), or `scp` if a working channel is available.

- [ ] **Step 2: Run the offline test suite on tsuru**

In the tsuru shell, from `~/PycharmProjects/lightfall-endstation-7011/`:

```bash
PYTHONPATH=src python -m pytest tests/observers/blackfly/ -m "not hw" -q
```

Expected: same 70 passes seen on the dev workstation.

- [ ] **Step 3: Run the 4 hw tests against the live camera**

Confirm the camera is at `192.168.10.81` and the host NIC `enp179s0f0` is configured at `192.168.10.42`. Then:

```bash
BLACKFLY_TEST_IP=192.168.10.81 BLACKFLY_BIND_IP=192.168.10.42 \
  PYTHONPATH=src python -m pytest tests/observers/blackfly/test_camera_live.py -v -m hw
```

Expected: 4 passed.

If a hw test fails with `ACCESS_DENIED` or similar register-write errors (per `feedback_hw_tests_catch_ordering_bugs.md`), the most likely cause is a regression in `BlackflyCamera.open()`'s register-write order. Fix at the source (the Spec A migration order is preserved; deviations are bugs), commit, re-sync, re-run.

- [ ] **Step 4: End-to-end embedded-agent smoke test**

On a workstation that can reach 192.168.10.81 (or via tsuru with X forwarding / VNC), boot lightfall with the endstation installed. In the embedded Claude panel:

> "Make me a panel for my Blackfly S camera at 192.168.10.81. The host NIC is 192.168.10.42."

Expected behaviour:
- Agent calls `mcp__panel_builder__ncs_create_user_plugin` with a substituted version of `panel_template.py`.
- The new "Blackfly S Live View" panel appears under View > User.
- Clicking the panel and pressing **Start** opens the camera, begins streaming, and renders frames in the pyqtgraph image view at the camera's native rate.

If the agent gets stuck (e.g., asks for both IPs again, or writes a malformed plugin), capture the conversation and skill prompt, fix the prompt or template at the source, recommit.

- [ ] **Step 5: No commit needed** unless step 3 or 4 surfaced a fix. Hand off to the user with a short report:
  - Offline tests on tsuru: PASS / FAIL
  - 4 hw tests: PASS / FAIL
  - Embedded-agent end-to-end: PASS / FAIL

If any FAIL, do not proceed to Task 12.

---

### Task 12: Archive `blackfly_observer`

**Files:**
- Delete (after archiving): `~/PycharmProjects/blackfly_observer/` (entire tree)

- [ ] **Step 1: Tarball the working tree to `~/Downloads/`**

```bash
tar czf ~/Downloads/blackfly_observer-archive-2026-04-26.tar.gz \
    -C ~/PycharmProjects blackfly_observer
```

Verify the tarball:

```bash
tar tzf ~/Downloads/blackfly_observer-archive-2026-04-26.tar.gz | head -20
ls -lh ~/Downloads/blackfly_observer-archive-2026-04-26.tar.gz
```

Expected: ~1800 lines of file paths listed; tarball size on the order of 1–5 MB.

- [ ] **Step 2: Delete the working tree**

```bash
rm -rf ~/PycharmProjects/blackfly_observer
```

(Per CLAUDE.md, prefer `trash` over `rm` when an interactive recycle-bin is available. Since `blackfly_observer` is now archived to `~/Downloads/`, plain `rm -rf` is acceptable.)

- [ ] **Step 3: Update the standing memory**

Update `C:\Users\rp\.claude\projects\C--Users-rp-workspace\memory\project_blackfly_lightfall_split_plan.md` to record completion: change the front-matter description to "Spec B merged YYYY-MM-DD; blackfly_observer archived to ~/Downloads/blackfly_observer-archive-2026-04-26.tar.gz" and replace the open-items list with a "Completed" entry pointing at the merged commits in lightfall (phase 1) and lightfall-endstation-7011 (phase 2).

Also update `project_blackfly_observer.md` to note the project is archived; consider removing it from `MEMORY.md` if Ron prefers to keep that index lean.

- [ ] **Step 4: No commit needed (memory updates are outside the repos).** Hand off to the user.

---

## Self-review

**Spec coverage check:**

- §1 Goal → covered by phases 1–3 collectively.
- §2 Vocabulary → enforced by file placement (Tasks 1–2 use `lightfall.ui.widgets.observers`, not `lightfall.devices`).
- §3.1 lightfall layout → Tasks 1, 2.
- §3.2 endstation layout → Tasks 4 (transport), 5 (BlackflyCamera), 6 (tests), 7 (script), 8 (template), 9 (skill), 10 (manifest).
- §3.3 manifest + console-script → Tasks 7, 10.
- §4.1 BlackflyAgent class → Task 9.
- §4.2 system prompt → Task 9 (test asserts the three required substrings).
- §4.3 MCP tool surface → Task 9 (test asserts exactly one tool with the correct name).
- §4.4 panel_template.py → Task 8.
- §5 binding pattern → Task 5 keeps the explicit `device_ip`/`bind_ip` constructor; Task 8 template uses both placeholders.
- §6 test split → Tasks 1, 2 (lightfall), Tasks 6, 9 (endstation).
- §7 migration order → phase ordering plus the Task 3 / Task 10 review gates between phases.
- §8 risks/rollback → mitigations are baked into Task 6 (offline test gate), Task 9 (skill smoke test), Task 10 (lightfall app boot), Task 11 (hardware gate); rollback is enabled by phase ordering (revert phase 2 without touching phase 1, etc.).
- §9 out of scope → no tasks (correct).
- §10 open questions → none remain (correct).

No gaps.

**Placeholder scan:** searched the plan body for "TBD"/"TODO"/"fill in"/"add appropriate"/"similar to Task" — none present. The two implementer notes (in Task 8 step 2 and Task 9 step 3) are deliberate flags telling the implementer to verify a specific upstream API signature before writing — they are **not** placeholders for missing plan content.

**Type consistency check:**
- `CameraBase` referenced in Tasks 1, 2, 5 — same name throughout.
- `CameraImageView` referenced in Tasks 2, 8 — same name throughout.
- `BlackflyCamera`, `Geometry`, `DeviceInfo`, `discover` referenced in Tasks 5, 6, 7, 8, 9 — same names throughout.
- `BlackflyAgent` referenced in Tasks 9, 10 — same name throughout.
- `discover_blackfly_cameras` is the consistent MCP-tool name across the spec, the test (Task 9 step 1), and the implementation (Task 9 step 3).

No inconsistencies.

---

**Plan complete.** Ron has pre-authorized subagent-driven execution. Per the writing-plans skill, the next step is to invoke `superpowers:subagent-driven-development` against this plan.
