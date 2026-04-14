"""Abstract base class for tiled-only visualization widgets.

BaseVisualization defines the interface for all visualization widgets
that read from a tiled BlueskyRun entry. The controller (VisualizationPanel)
orchestrates the selection flow; subclasses handle display logic.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from PySide6.QtWidgets import QWidget


class BaseVisualization(QWidget, ABC):
    """Abstract base for visualization widgets that read from tiled.

    Every visualization receives a BlueskyRun tiled entry via set_run(),
    then displays data from a selected stream and field. The controller
    (VisualizationPanel) orchestrates the selection flow:

        1. can_handle(run) to score
        2. set_run(run) to bind
        3. get_streams() to populate stream combo
        4. set_stream(name) to display (auto-picks best field)
        5. get_fields() to populate field combo
        6. set_field(name) for user override
        7. refresh() on timer for live runs

    Subclasses must define class-level metadata:
        viz_name: str           — unique id (e.g. "image_stack")
        viz_display_name: str   — UI label (e.g. "Image Stack")
        viz_icon: str           — icon name (e.g. "images")
    """

    viz_name: str = ""
    viz_display_name: str = ""
    viz_icon: str = "chart-line"

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._run: Any | None = None
        self._stream_name: str = ""
        self._field_name: str = ""

    @staticmethod
    @abstractmethod
    def can_handle(run: Any) -> int:
        """Score 0-100 for how well this viz handles the given run."""

    @abstractmethod
    def set_run(self, run: Any) -> None:
        """Set the BlueskyRun tiled entry. Cache reference and start metadata."""

    @abstractmethod
    def get_streams(self) -> list[str]:
        """Stream names sorted by this viz's preference."""

    @abstractmethod
    def set_stream(self, stream_name: str) -> None:
        """Select stream. Read metadata, auto-pick best field, render."""

    @abstractmethod
    def get_fields(self) -> list[str]:
        """Field names for current stream, sorted by preference."""

    @abstractmethod
    def set_field(self, field_name: str) -> None:
        """Switch field within current stream."""

    @abstractmethod
    def refresh(self) -> None:
        """Poll for new data. No-op for completed runs."""
