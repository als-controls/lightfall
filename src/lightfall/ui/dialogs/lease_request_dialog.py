"""Request Unlock dialog for caproxy attested leases.

Lets the user request a lease for one or more PV patterns via
``CaproxyLeaseService``. See docs/plans/2026-07-23-caproxy-lease-ux.md.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDoubleSpinBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
)

from lightfall.services.caproxy_lease_service import CaproxyLeaseService
from lightfall.ui.dialogs.base import LFDialog
from lightfall.ui.theme import scaled_px
from lightfall.utils.logging import logger

if TYPE_CHECKING:
    from PySide6.QtWidgets import QWidget

_MIN_DURATION_MIN = 1.0
_MAX_DURATION_MIN = 480.0
_DEFAULT_DURATION_MIN = 60.0


class LeaseRequestDialog(LFDialog):
    """Modal dialog for requesting a caproxy attested lease.

    Submits via ``CaproxyLeaseService.request_lease`` and shows the
    resulting pending/error state inline. The user approves the request
    out-of-band (from their phone, via the ``/approve`` page) — this
    dialog never polls for approval itself, it only reports whether the
    request was accepted.
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._service = CaproxyLeaseService.get_instance()
        self._request_pending = False
        self._signals_disconnected = False

        self.setWindowTitle("Request Unlock")
        self.setModal(True)
        self.setMinimumWidth(420)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)

        self._setup_ui()
        self._connect_signals()
        self.finished.connect(self._disconnect_signals)

    # ------------------------------------------------------------------
    # UI
    # ------------------------------------------------------------------

    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setSpacing(12)
        layout.setContentsMargins(24, 24, 24, 24)

        header = QLabel("Request Unlock")
        header.setStyleSheet(f"font-size: {scaled_px(16)}px; font-weight: bold;")
        layout.addWidget(header)

        form = QFormLayout()
        form.setSpacing(8)

        self._patterns_edit = QPlainTextEdit()
        self._patterns_edit.setPlaceholderText("es:motor:z*")
        self._patterns_edit.setFixedHeight(scaled_px(80))
        form.addRow("PV patterns:", self._patterns_edit)

        self._duration_spin = QDoubleSpinBox()
        self._duration_spin.setRange(_MIN_DURATION_MIN, _MAX_DURATION_MIN)
        self._duration_spin.setValue(_DEFAULT_DURATION_MIN)
        self._duration_spin.setSuffix(" min")
        form.addRow("Duration:", self._duration_spin)

        bounds_row = QHBoxLayout()
        self._bounds_min_edit = QLineEdit()
        self._bounds_min_edit.setPlaceholderText("min (optional)")
        self._bounds_max_edit = QLineEdit()
        self._bounds_max_edit.setPlaceholderText("max (optional)")
        bounds_row.addWidget(self._bounds_min_edit)
        bounds_row.addWidget(self._bounds_max_edit)
        form.addRow("Bounds:", bounds_row)

        self._note_edit = QLineEdit()
        self._note_edit.setPlaceholderText("Note (optional)")
        form.addRow("Note:", self._note_edit)

        layout.addLayout(form)

        self._error_label = QLabel()
        self._error_label.setStyleSheet(f"color: #cc0000; font-size: {scaled_px(12)}px;")
        self._error_label.setWordWrap(True)
        self._error_label.setVisible(False)
        layout.addWidget(self._error_label)

        self._status_label = QLabel()
        self._status_label.setStyleSheet(f"color: #2e7d32; font-size: {scaled_px(12)}px;")
        self._status_label.setWordWrap(True)
        self._status_label.setVisible(False)
        layout.addWidget(self._status_label)

        button_row = QHBoxLayout()
        button_row.addStretch()
        self._cancel_btn = QPushButton("Cancel")
        self._cancel_btn.clicked.connect(self.close)
        button_row.addWidget(self._cancel_btn)
        self._submit_btn = QPushButton("Request")
        self._submit_btn.setDefault(True)
        self._submit_btn.clicked.connect(self._on_submit)
        button_row.addWidget(self._submit_btn)
        layout.addLayout(button_row)

    def _connect_signals(self) -> None:
        self._service.request_finished.connect(self._on_request_finished)
        self._service.request_failed.connect(self._on_request_failed)

    # ------------------------------------------------------------------
    # Validation + submit
    # ------------------------------------------------------------------

    def _parsed_patterns(self) -> list[str]:
        text = self._patterns_edit.toPlainText()
        return [line.strip() for line in text.splitlines() if line.strip()]

    def _validate(self) -> tuple[bool, str, dict[str, Any]]:
        """Client-side validation. Returns (ok, error_text, parsed_fields)."""
        patterns = self._parsed_patterns()
        if not patterns:
            return False, "Enter at least one PV pattern.", {}

        bounds_min_text = self._bounds_min_edit.text().strip()
        bounds_max_text = self._bounds_max_edit.text().strip()
        bounds_min: float | None = None
        bounds_max: float | None = None

        if bounds_min_text:
            try:
                bounds_min = float(bounds_min_text)
            except ValueError:
                return False, "Bounds min must be a number.", {}
        if bounds_max_text:
            try:
                bounds_max = float(bounds_max_text)
            except ValueError:
                return False, "Bounds max must be a number.", {}
        if bounds_min is not None and bounds_max is not None and bounds_min > bounds_max:
            return False, "Bounds min must be <= bounds max.", {}

        fields = {
            "pv_patterns": patterns,
            "duration_s": self._duration_spin.value() * 60.0,
            "bounds_min": bounds_min,
            "bounds_max": bounds_max,
            "note": self._note_edit.text().strip(),
        }
        return True, "", fields

    def _on_submit(self) -> None:
        ok, error_text, fields = self._validate()
        if not ok:
            self._show_error(error_text)
            return

        self._set_pending(True)
        self._error_label.setVisible(False)
        logger.info("Requesting caproxy lease for patterns: {}", fields["pv_patterns"])
        self._service.request_lease(
            fields["pv_patterns"],
            fields["duration_s"],
            bounds_min=fields["bounds_min"],
            bounds_max=fields["bounds_max"],
            note=fields["note"],
        )

    def _set_pending(self, pending: bool) -> None:
        self._request_pending = pending
        self._submit_btn.setEnabled(not pending)
        self._patterns_edit.setEnabled(not pending)
        self._duration_spin.setEnabled(not pending)
        self._bounds_min_edit.setEnabled(not pending)
        self._bounds_max_edit.setEnabled(not pending)
        self._note_edit.setEnabled(not pending)

    def _show_error(self, message: str) -> None:
        self._error_label.setText(message)
        self._error_label.setVisible(True)

    # ------------------------------------------------------------------
    # Service signal handlers
    # ------------------------------------------------------------------

    def _on_request_finished(self, data: dict[str, Any]) -> None:
        self._set_pending(False)
        lease_id = data.get("lease_id") or data.get("id") or "unknown"
        self._status_label.setText(
            "Request pending — approve from your phone (the /approve page). "
            f"Lease id: {lease_id}"
        )
        self._status_label.setVisible(True)
        self._error_label.setVisible(False)
        # Form no longer needed; switch to a single "Close" affordance.
        self._submit_btn.setVisible(False)
        self._cancel_btn.setText("Close")

    def _on_request_failed(self, error_text: str) -> None:
        self._set_pending(False)
        self._show_error(error_text)

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def closeEvent(self, event) -> None:
        """Disconnect service signals before close.

        Avoids delivering signals into a deleted widget (known segfault
        class in this repo — the service singleton outlives the dialog).
        """
        self._disconnect_signals()
        super().closeEvent(event)

    def _disconnect_signals(self) -> None:
        if self._signals_disconnected:
            return
        self._signals_disconnected = True
        for signal, slot in (
            (self._service.request_finished, self._on_request_finished),
            (self._service.request_failed, self._on_request_failed),
        ):
            try:
                signal.disconnect(slot)
            except (RuntimeError, TypeError):
                pass
