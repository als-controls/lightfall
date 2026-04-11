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
        """Create the NXsas parameter widget with ROI selection."""
        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.setContentsMargins(0, 8, 0, 0)

        layout.addWidget(QLabel("ROI Selection (optional):"))

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
