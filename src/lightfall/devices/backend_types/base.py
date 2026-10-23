"""Backend types: how each kind of device-backend row is edited, validated and built."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QLabel, QWidget

if TYPE_CHECKING:
    from lightfall.devices.base import DeviceBackend


@dataclass(frozen=True)
class BuildContext:
    """Global connection settings every backend type may use when building."""

    instantiate_mode: str = "background"
    connection_timeout: float = 5.0


class BackendEditor(QWidget):
    """The settings form for one row. Emits ``changed`` on any edit."""

    changed = Signal()

    def get_config(self) -> dict[str, Any]:
        """Return the row config currently shown in the form."""
        raise NotImplementedError


class BackendType(ABC):
    """Describes one kind of device backend a settings row can hold."""

    type_id: str
    display_name: str
    requires_auth: bool = False
    #: An enabled row of an exclusive type is the session's only device source:
    #: every other row and device-backend plugin stays off, and switching into
    #: or out of it takes a restart (see ``backend_types.session``).
    exclusive: bool = False

    @abstractmethod
    def default_config(self) -> dict[str, Any]:
        """Config for a newly added row."""

    @abstractmethod
    def summary(self, config: dict[str, Any]) -> str:
        """One-line description for the backends table."""

    @abstractmethod
    def create_editor(self, config: dict[str, Any], parent: QWidget | None = None) -> BackendEditor:
        """Build the settings form for a row holding ``config``."""

    def validate(self, config: dict[str, Any]) -> list[str]:
        """Return human-readable problems with ``config`` (empty if valid)."""
        return []

    @abstractmethod
    def build(self, config: dict[str, Any], name: str, ctx: BuildContext) -> DeviceBackend:
        """Construct the device backend for a row, keyed ``name`` in the catalog."""

    def session_env(self, config: dict[str, Any]) -> dict[str, str]:
        """Environment an exclusive row's session needs (e.g. EPICS addresses).

        Applied at startup before any EPICS client exists. Computed from
        ``config`` alone: no I/O.
        """
        return {}

    def session_label(self, config: dict[str, Any]) -> str:
        """How an exclusive row's session is named in the window title."""
        return self.display_name


def hint(text: str) -> QLabel:
    """Gray, word-wrapped help text for an editor form."""
    label = QLabel(text)
    label.setWordWrap(True)
    label.setStyleSheet("color: gray;")
    return label
