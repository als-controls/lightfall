"""RunEngine status bar plugin.

Surfaces the existing ``RunEngineStatusBar`` widget (compact RunEngine
controls + current plan name + progress) as a status-bar plugin. The
widget is already self-managing — this plugin is a thin adapter that
handles registration, lifecycle, and engine wiring.
"""

from __future__ import annotations

from typing import ClassVar

from PySide6.QtWidgets import QWidget

from lucid.plugins.statusbar_plugin import StatusBarPlugin, StatusBarPluginMetadata
from lucid.ui.widgets import RunEngineStatusBar
from lucid.utils.logging import logger


class RunEngineStatusPlugin(StatusBarPlugin):
    """Embeds ``RunEngineStatusBar`` in the main window's status bar."""

    metadata: ClassVar[StatusBarPluginMetadata] = StatusBarPluginMetadata(
        id="lucid.statusbar.run_engine",
        name="RunEngine",
        description="Compact RunEngine controls plus current plan name and progress.",
        priority=0,  # leftmost in the permanent section
        position="permanent",
        tooltip="RunEngine status and controls",
    )

    @property
    def name(self) -> str:
        return "run_engine"

    @property
    def description(self) -> str:
        return self.metadata.description

    def create_widget(self, parent: QWidget | None = None) -> QWidget:
        """Build the ``RunEngineStatusBar`` widget and remember it.

        Engine wiring is deferred to :meth:`connect_signals` so the
        engine has a chance to be initialised by the time we attach.
        """
        widget = RunEngineStatusBar(parent)
        self._widget = widget
        self._button = None  # not a QToolButton; default helpers don't apply
        return widget

    def update(self) -> None:
        """No-op — ``RunEngineStatusBar`` subscribes to engine signals itself."""

    def connect_signals(self) -> None:
        """Resolve the active engine and bind it to the widget."""
        if self._widget is None:
            return
        try:
            from lucid.acquire.engine import get_engine

            engine = get_engine()
        except Exception as exc:
            logger.debug("RunEngineStatusPlugin: engine unavailable ({})", exc)
            return
        try:
            self._widget.set_engine(engine)
        except Exception as exc:
            logger.warning(
                "RunEngineStatusPlugin: failed to bind engine: {}", exc
            )

    def disconnect_signals(self) -> None:
        """The widget owns its signal connections; nothing to undo here."""
