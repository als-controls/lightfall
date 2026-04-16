"""Export configuration dialog for the Data Browser."""

from __future__ import annotations

import platform
import subprocess
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


MAX_PING_RETRIES = 4
PING_TIMEOUT_MS = 1000


def ping_or_spawn_exporter(
    ipc: Any,
    ping_subject: str,
    nats_url: str,
) -> bool:
    """Ping the local exporter; spawn one if not running.

    Callable from any thread (uses IPCService.request which is thread-safe).

    Args:
        ipc: IPCService instance.
        ping_subject: NATS subject for the exporter's ping endpoint.
        nats_url: NATS URL to pass to the spawned exporter.

    Returns:
        True if the exporter is reachable, False if all retries failed.
    """
    # Try initial ping
    reply = ipc.request(ping_subject, {}, timeout_ms=PING_TIMEOUT_MS)
    if reply is not None:
        return True

    # No response — spawn a local exporter
    logger.info("No exporter running, spawning lucid-exporter")
    try:
        proc = subprocess.Popen(
            ["lucid-exporter", "--nats", nats_url],
            stderr=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
        )
    except FileNotFoundError:
        logger.error("lucid-exporter not found on PATH")
        return False

    # Retry pings
    for i in range(MAX_PING_RETRIES):
        reply = ipc.request(ping_subject, {}, timeout_ms=PING_TIMEOUT_MS)
        if reply is not None:
            logger.info("Exporter responded after spawn (attempt %d)", i + 1)
            return True

        # Check if process died
        if proc.poll() is not None:
            stderr_text = proc.stderr.read().decode(errors="replace") if proc.stderr else ""
            logger.error("Exporter process exited (code %d): %s", proc.returncode, stderr_text)
            return False

    logger.error("Exporter did not respond after %d retries", MAX_PING_RETRIES)
    return False


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


class ExportDialog(LucidDialog):
    """Dialog for configuring and launching a data export."""

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
        self.setMinimumWidth(600)
        self.setMinimumHeight(600)
        self._setup_ui()
        self._load_thread = None

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

        # NXsas: ROI widget
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
        self._rect_roi = None
        self._frame_shape = None
        self._image_loaded = False

        return widget

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

    def _load_preview_image(self) -> None:
        """Load a sample frame from the first selected run in a background thread."""
        from lucid.utils.threads import QThreadFuture

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
            "No image data found in selected run — NXsas requires image data"
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

    @Slot()
    def _on_browse(self) -> None:
        """Open directory picker."""
        path = QFileDialog.getExistingDirectory(self, "Select Export Directory")
        if path:
            self._dir_edit.setText(path)

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

        Pings the exporter first. If no response, spawns a local instance.
        The ping/spawn/send flow runs in a background thread.
        """
        from lucid.ipc.service import IPCService
        from lucid.core.services import ServiceRegistry
        from lucid.ui.toast import ToastManager
        from lucid.utils.threads import QThreadFuture

        toast = ToastManager.get_instance()
        registry = ServiceRegistry.get_instance()
        ipc = registry.get(IPCService, None)

        if ipc is None:
            toast.error("Export Error", "IPC service not available")
            return

        hostname = platform.node()
        job_subject = f"lucid.export.{hostname}"
        progress_subject = f"lucid.export.{hostname}.progress"
        ping_subject = f"lucid.export.{hostname}.ping"
        nats_url = ipc._nats_url

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

        def _ping_and_send() -> bool:
            """Background thread: ping/spawn exporter, then send job."""
            if not ping_or_spawn_exporter(ipc, ping_subject, nats_url):
                return False
            ipc.subscribe(progress_subject, _on_progress)
            ipc.publish(job_subject, message)
            return True

        def _on_send_result(success: bool) -> None:
            if success:
                toast.info("Export Queued", f"{len(message['run_uids'])} run(s) queued for export")
                logger.info("Export job {} sent to {}", job_id, job_subject)
            else:
                toast.error("Export Error", "Could not start exporter")

        def _on_send_error(exc: Exception) -> None:
            toast.error("Export Error", str(exc))
            logger.error("Export send failed: {}", exc)

        thread = QThreadFuture(
            _ping_and_send,
            callback_slot=_on_send_result,
            except_slot=_on_send_error,
            name="export_ping_send",
        )
        thread.start()
