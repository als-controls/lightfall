"""Mock backend type: simulated ophyd.sim devices."""

from __future__ import annotations

from typing import Any

from PySide6.QtWidgets import QCheckBox, QFormLayout, QWidget

from lightfall.devices.backend_types.base import BackendEditor, BackendType, BuildContext, hint
from lightfall.devices.backends.mock import MockBackend


class _MockEditor(BackendEditor):
    def __init__(self, config: dict[str, Any], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        form = QFormLayout(self)
        self._noisy = QCheckBox("Include noisy devices")
        self._noisy.setChecked(bool(config.get("include_noisy", True)))
        self._noisy.toggled.connect(lambda *_: self.changed.emit())
        form.addRow(self._noisy)
        form.addRow(hint("Simulated ophyd.sim devices for development and testing."))

    def get_config(self) -> dict[str, Any]:
        return {"include_noisy": self._noisy.isChecked()}


class MockBackendType(BackendType):
    type_id = "mock"
    display_name = "Mock"

    def default_config(self) -> dict[str, Any]:
        return {"include_noisy": True}

    def summary(self, config: dict[str, Any]) -> str:
        return "with noisy devices" if config.get("include_noisy", True) else "quiet devices only"

    def create_editor(self, config: dict[str, Any], parent: QWidget | None = None) -> BackendEditor:
        return _MockEditor(config, parent)

    def build(self, config: dict[str, Any], name: str, ctx: BuildContext) -> MockBackend:
        return MockBackend(include_noisy=bool(config.get("include_noisy", True)), name=name)
