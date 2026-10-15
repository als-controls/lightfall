"""BCS backend type: devices discovered from a BCS server over ZMQ."""

from __future__ import annotations

from typing import Any

from PySide6.QtWidgets import QFormLayout, QLineEdit, QSpinBox, QWidget

from lightfall.devices.backend_types.base import BackendEditor, BackendType, BuildContext, hint
from lightfall.devices.backends.bcs import BCSBackend


class _BCSEditor(BackendEditor):
    def __init__(self, config: dict[str, Any], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        form = QFormLayout(self)
        self._host = QLineEdit(str(config.get("host", "localhost")))
        self._host.setPlaceholderText("localhost")
        self._port = QSpinBox()
        self._port.setRange(1, 65535)
        self._port.setValue(int(config.get("port", 5577)))
        self._beamline = QLineEdit(str(config.get("beamline", "")))
        self._beamline.setPlaceholderText("(optional)")
        self._timeout = QSpinBox()
        self._timeout.setRange(100, 60000)
        self._timeout.setSingleStep(500)
        self._timeout.setSuffix(" ms")
        self._timeout.setValue(int(config.get("timeout_ms", 5000)))
        for edit in (self._host, self._beamline):
            edit.textChanged.connect(lambda *_: self.changed.emit())
        for spin in (self._port, self._timeout):
            spin.valueChanged.connect(lambda *_: self.changed.emit())
        form.addRow("Server host:", self._host)
        form.addRow("Server port:", self._port)
        form.addRow("Beamline:", self._beamline)
        form.addRow("Timeout:", self._timeout)
        form.addRow(hint(
            "Connects to a BCS server via ZMQ and auto-discovers devices "
            "using bcsophyd. Requires bcsophyd package."
        ))

    def get_config(self) -> dict[str, Any]:
        return {
            "host": self._host.text().strip(),
            "port": self._port.value(),
            "beamline": self._beamline.text().strip(),
            "timeout_ms": self._timeout.value(),
        }


class BCSBackendType(BackendType):
    type_id = "bcs"
    display_name = "BCS (ZMQ)"

    def default_config(self) -> dict[str, Any]:
        return {"host": "localhost", "port": 5577, "beamline": "", "timeout_ms": 5000}

    def summary(self, config: dict[str, Any]) -> str:
        return f"{config.get('host', 'localhost')}:{config.get('port', 5577)}"

    def create_editor(self, config: dict[str, Any], parent: QWidget | None = None) -> BackendEditor:
        return _BCSEditor(config, parent)

    def validate(self, config: dict[str, Any]) -> list[str]:
        if not str(config.get("host", "")).strip():
            return ["Server host is required"]
        return []

    def build(self, config: dict[str, Any], name: str, ctx: BuildContext) -> BCSBackend:
        return BCSBackend(
            host=str(config.get("host") or "localhost"),
            port=int(config.get("port", 5577)),
            timeout_ms=int(config.get("timeout_ms", 5000)),
            beamline=config.get("beamline") or None,
            name=name,
        )
