# Interactive ROI Widget Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the text-input ROI fields in the NXsas export dialog with an interactive PyQtGraph ImageView + RectROI widget that loads a sample frame from Tiled.

**Architecture:** A pure function `load_sample_frame` fetches a 2D image from the first selected run via Tiled (runs in QThreadFuture). The NXsas parameter widget is rebuilt as an ImageView + RectROI + status label + coordinate readout. Export button state is managed based on image loading success/failure.

**Tech Stack:** PySide6, pyqtgraph (ImageView, RectROI), tiled[client], numpy

**Spec:** `docs/superpowers/specs/2026-04-10-roi-widget-design.md`

---

## File Structure

### Modified Files

| File | Changes |
|------|---------|
| `src/lightfall/ui/dialogs/export_dialog.py` | Add `load_sample_frame()`, replace `_create_nxsas_params`, rewrite `_on_type_changed`, rewrite `_get_roi_params`, add image load callbacks, manage export button state |
| `tests/test_export_dialog.py` | Add tests for `load_sample_frame` and ROI param extraction |

---

## Task 1: Sample Frame Loading Function

**Files:**
- Modify: `src/lightfall/ui/dialogs/export_dialog.py` (add `load_sample_frame` function)
- Modify: `tests/test_export_dialog.py` (add tests)

- [ ] **Step 1: Write failing tests for load_sample_frame**

Append to `tests/test_export_dialog.py`:

```python
import numpy as np

from lightfall.ui.dialogs.export_dialog import load_sample_frame


class TestLoadSampleFrame:
    def _make_mock_client(self, image_data: np.ndarray | None = None, scalar_only: bool = False):
        """Create a mock Tiled client with a run containing image or scalar data."""
        client = MagicMock()
        run = MagicMock()
        stream = MagicMock()

        if scalar_only:
            data_keys = {"motor": {"shape": []}, "detector_stats": {"shape": [1]}}
            stream.metadata = {"data_keys": data_keys}
            stream.keys.return_value = ["motor", "detector_stats"]
        else:
            shape = list(image_data.shape) if image_data is not None else [10, 100, 100]
            data_keys = {"detector": {"shape": shape}}
            stream.metadata = {"data_keys": data_keys}
            stream.keys.return_value = ["detector"]
            col = MagicMock()
            if image_data is not None:
                col.read.return_value = image_data
            else:
                col.read.return_value = np.zeros((10, 100, 100))
            stream.__getitem__ = lambda _self, key: col

        run.keys.return_value = ["primary"]
        run.__getitem__ = lambda _self, key: stream
        run.metadata = {"start": {"uid": "test-uid"}}
        client.__getitem__ = lambda _self, key: run

        return client

    def test_loads_middle_frame_from_3d(self):
        image_data = np.arange(30).reshape(3, 2, 5).astype(np.float32)
        client = self._make_mock_client(image_data)

        frame = load_sample_frame(client, "run-key")
        assert frame.ndim == 2
        assert frame.shape == (2, 5)
        # Middle frame of 3 is index 1
        np.testing.assert_array_equal(frame, image_data[1])

    def test_loads_2d_directly(self):
        image_data = np.ones((50, 60), dtype=np.float32)
        client = self._make_mock_client(image_data)

        frame = load_sample_frame(client, "run-key")
        assert frame.shape == (50, 60)

    def test_raises_on_scalar_only(self):
        client = self._make_mock_client(scalar_only=True)

        with pytest.raises(ValueError, match="No image field"):
            load_sample_frame(client, "run-key")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd ~/PycharmProjects/ncs/ncs && .venv/Scripts/python -m pytest tests/test_export_dialog.py::TestLoadSampleFrame -v`
Expected: FAIL — `ImportError: cannot import name 'load_sample_frame'`

- [ ] **Step 3: Implement load_sample_frame**

In `src/lightfall/ui/dialogs/export_dialog.py`, add this function after `ping_or_spawn_exporter` and before the `ExportDialog` class:

```python
def load_sample_frame(client: Any, run_key: str) -> Any:
    """Load a single 2D sample frame from a Tiled run.

    Finds the first image field (ndim >= 2) in the primary stream and
    returns frame 0 (2D data) or the middle frame (3D data).

    Args:
        client: Tiled catalog client.
        run_key: Key for the run in the catalog.

    Returns:
        2D numpy array (single frame).

    Raises:
        ValueError: If no image field found in primary stream.
    """
    import numpy as np

    run = client[run_key]
    stream = run["primary"]
    data_keys = stream.metadata.get("data_keys", {})

    # Find first field with ndim >= 2
    image_field = None
    for key, info in data_keys.items():
        if len(info.get("shape", [])) >= 2:
            image_field = key
            break

    if image_field is None:
        raise ValueError("No image field found in primary stream")

    data = np.asarray(stream[image_field].read())

    if data.ndim == 2:
        return data
    elif data.ndim >= 3:
        mid = data.shape[0] // 2
        return data[mid]
    else:
        raise ValueError(f"Unexpected data dimensions: {data.ndim}")
```

Also add `import numpy as np` to the file's top-level imports (it's not there yet — add after `import uuid`). Actually, since numpy is only used in this function, the lazy import inside the function is fine. But the tests import numpy at the top. Leave the function as-is with the local import.

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd ~/PycharmProjects/ncs/ncs && .venv/Scripts/python -m pytest tests/test_export_dialog.py::TestLoadSampleFrame -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
cd ~/PycharmProjects/ncs/ncs
git add src/lightfall/ui/dialogs/export_dialog.py tests/test_export_dialog.py
git commit -m "feat(export): add load_sample_frame for ROI preview"
```

---

## Task 2: Replace NXsas Widget with ImageView + RectROI

**Files:**
- Modify: `src/lightfall/ui/dialogs/export_dialog.py` (replace `_create_nxsas_params`, `_on_type_changed`, `_get_roi_params`, add load callbacks)

This task modifies the Qt widget code. It replaces the 4 text fields with an interactive image viewer.

- [ ] **Step 1: Replace `_create_nxsas_params`**

In `src/lightfall/ui/dialogs/export_dialog.py`, replace the entire `_create_nxsas_params` method (lines 184-206) with:

```python
    def _create_nxsas_params(self) -> QWidget:
        """Create the NXsas parameter widget with ImageView + RectROI."""
        import pyqtgraph as pg

        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.setContentsMargins(0, 8, 0, 0)

        # Status label
        self._roi_status = QLabel("")
        layout.addWidget(self._roi_status)

        # ImageView for sample frame
        self._image_view = pg.ImageView()
        self._image_view.setMinimumSize(400, 400)
        self._image_view.ui.roiBtn.hide()
        self._image_view.ui.menuBtn.hide()
        layout.addWidget(self._image_view, stretch=1)

        # ROI coordinate readout
        self._roi_label = QLabel("ROI: full frame")
        layout.addWidget(self._roi_label)

        # RectROI (created but not added until image loads)
        self._rect_roi: pg.RectROI | None = None
        self._frame_shape: tuple[int, int] | None = None
        self._image_loaded = False

        return widget
```

- [ ] **Step 2: Update `__init__` for new dialog size**

Change line 131 from:
```python
        self.setMinimumWidth(500)
```
to:
```python
        self.setMinimumWidth(600)
        self.setMinimumHeight(600)
```

Also add a tracking attribute for the load thread after `self._setup_ui()`:

```python
        self._load_thread = None
```

- [ ] **Step 3: Replace `_on_type_changed`**

Replace the `_on_type_changed` method with:

```python
    @Slot(int)
    def _on_type_changed(self, index: int) -> None:
        """Switch the parameter widget when export type changes."""
        type_id = self._type_combo.currentData()
        if type_id == "nxsas":
            self._params_stack.setCurrentIndex(1)
            self._load_preview_image()
        else:
            self._params_stack.setCurrentIndex(0)
            self._clear_preview()
            self._ok_btn.setEnabled(True)
```

- [ ] **Step 4: Add image loading methods**

Add these methods to the `ExportDialog` class:

```python
    def _load_preview_image(self) -> None:
        """Load a sample frame from the first selected run in a background thread."""
        from lightfall.utils.threads import QThreadFuture

        self._ok_btn.setEnabled(False)
        self._roi_status.setText("Loading preview...")
        self._image_loaded = False

        client = self._tiled_service._client
        if client is None:
            self._roi_status.setText("Not connected to Tiled")
            return

        run_key = self._records[0]._client_key

        self._load_thread = QThreadFuture(
            load_sample_frame,
            client,
            run_key,
            callback_slot=self._on_preview_loaded,
            except_slot=self._on_preview_error,
            name="export_load_preview",
        )
        self._load_thread.start()

    @Slot(object)
    def _on_preview_loaded(self, frame) -> None:
        """Handle successful image load — display and add ROI."""
        import numpy as np
        import pyqtgraph as pg

        self._image_view.setImage(frame.T)  # transpose for pyqtgraph (col-major)
        self._frame_shape = frame.shape  # (rows, cols) = (h, w)

        # Add RectROI covering full frame
        h, w = frame.shape
        if self._rect_roi is not None:
            self._image_view.getView().removeItem(self._rect_roi)

        self._rect_roi = pg.RectROI(
            [0, 0], [w, h],
            pen=pg.mkPen("r", width=2),
            hoverPen=pg.mkPen("y", width=2),
            scaleSnap=True,
            translateSnap=True,
        )
        self._rect_roi.setZValue(10)
        self._image_view.getView().addItem(self._rect_roi)
        self._rect_roi.sigRegionChanged.connect(self._on_roi_changed)

        self._image_loaded = True
        self._ok_btn.setEnabled(True)
        self._roi_status.setText("Drag to select ROI")
        self._update_roi_label()

    @Slot(Exception)
    def _on_preview_error(self, error: Exception) -> None:
        """Handle image load failure."""
        self._roi_status.setText(
            f"No image data found in selected run — NXsas requires image data"
            if "No image field" in str(error)
            else f"Failed to load preview: {error}"
        )
        self._ok_btn.setEnabled(False)
        self._image_loaded = False
        logger.warning("Preview load failed: {}", error)

    def _clear_preview(self) -> None:
        """Clear the image view and ROI."""
        self._image_view.clear()
        if self._rect_roi is not None:
            self._image_view.getView().removeItem(self._rect_roi)
            self._rect_roi = None
        self._frame_shape = None
        self._image_loaded = False
        self._roi_status.setText("")
        self._roi_label.setText("ROI: full frame")

    @Slot()
    def _on_roi_changed(self) -> None:
        """Update the ROI coordinate readout when the user drags the ROI."""
        self._update_roi_label()

    def _update_roi_label(self) -> None:
        """Update the ROI coordinate label from current RectROI state."""
        if self._rect_roi is None or self._frame_shape is None:
            self._roi_label.setText("ROI: full frame")
            return

        pos = self._rect_roi.pos()
        size = self._rect_roi.size()
        x, y = int(pos[0]), int(pos[1])
        w, h = int(size[0]), int(size[1])
        fh, fw = self._frame_shape

        if x == 0 and y == 0 and w == fw and h == fh:
            self._roi_label.setText("ROI: full frame")
        else:
            self._roi_label.setText(f"ROI: X={x}, Y={y}, W={w}, H={h}")
```

- [ ] **Step 5: Replace `_get_roi_params`**

Replace the existing `_get_roi_params` method with:

```python
    def _get_roi_params(self) -> dict[str, Any] | None:
        """Extract ROI parameters from the RectROI widget.

        Returns None if ROI covers the full frame (no cropping needed).
        """
        if self._rect_roi is None or self._frame_shape is None:
            return None

        pos = self._rect_roi.pos()
        size = self._rect_roi.size()
        x, y = int(pos[0]), int(pos[1])
        w, h = int(size[0]), int(size[1])
        fh, fw = self._frame_shape

        # Full frame — no cropping
        if x == 0 and y == 0 and w == fw and h == fh:
            return None

        return {"x": x, "y": y, "width": w, "height": h}
```

- [ ] **Step 6: Remove old text field attributes**

Delete the old `_roi_x`, `_roi_y`, `_roi_w`, `_roi_h` QLineEdit references. They no longer exist since `_create_nxsas_params` was replaced. No code references them anymore (the old `_get_roi_params` that read them was just replaced).

Verify no references remain:

Run: `cd ~/PycharmProjects/ncs/ncs && grep -n "_roi_x\|_roi_y\|_roi_w\|_roi_h" src/lightfall/ui/dialogs/export_dialog.py`
Expected: no output

- [ ] **Step 7: Verify the dialog module imports cleanly**

Run: `cd ~/PycharmProjects/ncs/ncs && .venv/Scripts/python -c "from lightfall.ui.dialogs.export_dialog import ExportDialog; print('OK')"`
Expected: `OK`

- [ ] **Step 8: Run all export dialog tests**

Run: `cd ~/PycharmProjects/ncs/ncs && .venv/Scripts/python -m pytest tests/test_export_dialog.py -v`
Expected: All 8 tests pass (2 build_job_message + 3 ping/spawn + 3 load_sample_frame)

- [ ] **Step 9: Run all tests**

Run: `cd ~/PycharmProjects/ncs/ncs && .venv/Scripts/python -m pytest tests/exporter/ tests/test_export_dialog.py tests/ipc/test_service.py -v`
Expected: All pass, no regressions

- [ ] **Step 10: Commit**

```bash
cd ~/PycharmProjects/ncs/ncs
git add src/lightfall/ui/dialogs/export_dialog.py
git commit -m "feat(export): replace text ROI fields with interactive ImageView + RectROI"
```

---

## Summary

| Task | Description | Files Modified | Tests |
|------|-------------|---------------|-------|
| 1 | load_sample_frame function | `export_dialog.py`, `test_export_dialog.py` | 3 new |
| 2 | ImageView + RectROI widget | `export_dialog.py` | manual (Qt widget) |

**Total:** 1 file modified + tests, 3 new tests
