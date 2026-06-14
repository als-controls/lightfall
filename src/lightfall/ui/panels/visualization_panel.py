"""Visualization panel for tiled Bluesky data.

Orchestrates visualization selection, display, and configuration
for BlueskyRun entries accessed via tiled.
"""

from __future__ import annotations

from typing import Any, ClassVar

from loguru import logger
from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSplitter,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from lightfall.ui.panels.base import BasePanel, PanelMetadata
from lightfall.ui.theater.manager import theater_manager
from lightfall.ui.theme import scaled_px
from lightfall.ui.theater.proxy import TheaterProxy
from lightfall.visualization.base_visualization import BaseVisualization
from lightfall.visualization.fitting.panel import FitPanel


def _widget_classes() -> list[type[BaseVisualization]]:
    """Import and return all available visualization widget classes."""
    from lightfall.visualization.widgets.adaptive.heatmap import (
        AdaptiveHeatmapVisualization,
    )
    from lightfall.visualization.widgets.adaptive.plot import (
        AdaptivePlotVisualization,
    )
    from lightfall.visualization.widgets.heatmap import HeatmapVisualization
    from lightfall.visualization.widgets.image_stack import ImageStackVisualization
    from lightfall.visualization.widgets.plot_1d import Plot1DVisualization
    from lightfall.visualization.widgets.scatter import ScatterVisualization
    from lightfall.visualization.widgets.table import TableVisualization

    return [
        ImageStackVisualization,
        Plot1DVisualization,
        HeatmapVisualization,
        ScatterVisualization,
        TableVisualization,
        AdaptiveHeatmapVisualization,
        AdaptivePlotVisualization,
    ]


class VisualizationPanel(BasePanel):
    """Panel for tiled data visualization.

    Receives a tiled BlueskyRun entry, scores registered widgets,
    creates the best match, and drives the set_run / set_stream /
    set_field / refresh flow.
    """

    panel_metadata: ClassVar[PanelMetadata] = PanelMetadata(
        id="lightfall.panels.visualization",
        name="Visualization",
        description="Live data visualization during scans",
        icon="chart-line",
        category="Acquisition",
        singleton=True,
        closable=True,
        keywords=["plot", "chart", "graph", "visualization", "live", "data"],
        # Docking preferences - bottom sidebar
        default_area="bottom",
        sidebar_group="bottom",
        auto_hide=True,
        sidebar_order=1,
    )

    visualization_changed = Signal(str)  # viz_name

    def __init__(self, parent: QWidget | None = None) -> None:
        self._entry: Any | None = None
        self._current_widget: BaseVisualization | None = None
        self._current_proxy: TheaterProxy | None = None
        self._refresh_timer: QTimer | None = None
        super().__init__(parent)

    # ---- UI setup --------------------------------------------------------

    def _setup_ui(self) -> None:
        """Build the panel UI."""
        main_layout = QVBoxLayout()
        main_layout.setContentsMargins(4, 4, 4, 4)
        main_layout.setSpacing(4)

        # Toolbar
        toolbar = self._create_toolbar()
        main_layout.addLayout(toolbar)

        # Splitter: visualization + fit panel
        splitter = QSplitter(Qt.Orientation.Horizontal)

        # Visualization stack (placeholder + active widget)
        self._viz_stack = QStackedWidget()
        self._viz_stack.setMinimumSize(400, 300)

        placeholder = QWidget()
        placeholder_layout = QVBoxLayout(placeholder)
        placeholder_layout.addStretch()
        self._placeholder_label = QLabel("No data\nStart a scan to visualize")
        self._placeholder_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._placeholder_label.setStyleSheet(f"color: gray; font-size: {scaled_px(14)}px;")
        placeholder_layout.addWidget(self._placeholder_label)
        placeholder_layout.addStretch()
        self._viz_stack.addWidget(placeholder)

        splitter.addWidget(self._viz_stack)

        # Fit panel (collapsible)
        self._fit_panel = FitPanel()
        self._fit_panel.setMaximumWidth(300)
        self._fit_panel.setVisible(False)
        splitter.addWidget(self._fit_panel)

        main_layout.addWidget(splitter)

        container = QWidget()
        container.setLayout(main_layout)
        self._layout.addWidget(container)

    def _create_toolbar(self) -> QHBoxLayout:
        """Create the toolbar with stream/field/viz combos and buttons."""
        toolbar = QHBoxLayout()
        toolbar.setSpacing(8)

        # Stream selector
        self._stream_label = QLabel("Stream:")
        self._stream_combo = QComboBox()
        self._stream_combo.setMinimumWidth(100)
        self._stream_combo.currentTextChanged.connect(self._on_stream_changed)
        toolbar.addWidget(self._stream_label)
        toolbar.addWidget(self._stream_combo)
        self._stream_label.hide()
        self._stream_combo.hide()

        # Field selector
        self._field_label = QLabel("Field:")
        self._field_combo = QComboBox()
        self._field_combo.setMinimumWidth(100)
        self._field_combo.currentTextChanged.connect(self._on_field_changed)
        toolbar.addWidget(self._field_label)
        toolbar.addWidget(self._field_combo)
        self._field_label.hide()
        self._field_combo.hide()

        # Visualization type selector
        viz_label = QLabel("Visualization:")
        self._viz_combo = QComboBox()
        self._viz_combo.setMinimumWidth(120)
        self._viz_combo.addItem("Auto", None)
        for cls in _widget_classes():
            self._viz_combo.addItem(cls.viz_display_name, cls.viz_name)
        self._viz_combo.currentIndexChanged.connect(self._on_viz_selection_changed)
        toolbar.addWidget(viz_label)
        toolbar.addWidget(self._viz_combo)

        toolbar.addStretch()

        # Fit button
        self._fit_btn = QPushButton("Fit Panel")
        self._fit_btn.setCheckable(True)
        self._fit_btn.toggled.connect(self._on_fit_toggled)
        toolbar.addWidget(self._fit_btn)

        # Export button
        export_btn = QPushButton("Export...")
        export_btn.clicked.connect(self._on_export_clicked)
        toolbar.addWidget(export_btn)

        return toolbar

    # ---- Main entry point ------------------------------------------------

    def open_run(self, entry: Any) -> None:
        """Open a tiled BlueskyRun for visualization.

        Scores all registered widget classes, creates the winner,
        and drives the set_run / set_stream / set_field flow.

        Args:
            entry: A tiled BlueskyRun (or compatible mapping).
        """
        import time as _time
        t0 = _time.monotonic()

        self._stop_refresh()
        self._entry = entry

        classes = _widget_classes()

        # Score and pick winner (or honour manual override)
        best_cls = self._pick_widget_class(classes, entry)
        t1 = _time.monotonic()
        logger.debug("open_run: scoring took {:.1f}s → {}", t1 - t0, best_cls.viz_name if best_cls else None)

        if best_cls is None:
            logger.warning("No visualization can handle this run")
            return

        self._activate_widget(best_cls, entry)
        logger.debug("open_run: total {:.1f}s", _time.monotonic() - t0)

    # ---- Widget lifecycle ------------------------------------------------

    def _pick_widget_class(
        self,
        classes: list[type[BaseVisualization]],
        entry: Any,
    ) -> type[BaseVisualization] | None:
        """Score classes and return the best, or None."""
        # If user selected a specific viz type, use that
        viz_name = self._viz_combo.currentData()
        if viz_name is not None:
            for cls in classes:
                if cls.viz_name == viz_name:
                    return cls

        # Auto mode — highest score wins
        best_cls: type[BaseVisualization] | None = None
        best_score = 0
        for cls in classes:
            try:
                score = cls.can_handle(entry)
                if score > best_score:
                    best_score = score
                    best_cls = cls
            except Exception as e:
                logger.warning("Error in {}.can_handle: {}", cls.viz_name, e)
        return best_cls

    def _activate_widget(
        self,
        cls: type[BaseVisualization],
        entry: Any,
    ) -> None:
        """Create widget, wire combos, start refresh if needed."""
        import time as _time
        t0 = _time.monotonic()

        widget = cls()
        self._set_current_widget(widget)
        widget.set_run(entry)
        t1 = _time.monotonic()

        # Populate stream combo
        streams = widget.get_streams()
        t2 = _time.monotonic()
        logger.debug("_activate: create={:.1f}s get_streams={:.1f}s", t1 - t0, t2 - t1)

        self._stream_combo.blockSignals(True)
        self._stream_combo.clear()
        self._stream_combo.addItems(streams)
        self._stream_combo.blockSignals(False)

        visible = len(streams) > 1
        self._stream_label.setVisible(visible)
        self._stream_combo.setVisible(visible)

        if streams:
            widget.set_stream(streams[0])
            self._populate_field_combo()

        # Connect fit panel if the widget exposes fit_requested
        if hasattr(widget, "fit_requested"):
            widget.fit_requested.connect(lambda: self._fit_btn.setChecked(True))
            self._fit_panel.set_plot(widget)

        self.visualization_changed.emit(cls.viz_name)

        # Start refresh timer for live runs (no stop doc)
        if entry.metadata.get("stop") is None:
            self._start_refresh()

        logger.info("Opened run with visualization '{}'", cls.viz_display_name)

    def _set_current_widget(self, widget: BaseVisualization) -> None:
        """Swap the active visualization widget."""
        # Remove old widget and proxy (hide, don't delete — avoids pyqtgraph segfaults)
        if self._current_proxy is not None:
            if (
                theater_manager._overlay is not None
                and theater_manager._overlay._active_proxy is self._current_proxy
            ):
                theater_manager._overlay._finish_deactivate()
            theater_manager.unregister(self._current_proxy)
            self._viz_stack.removeWidget(self._current_proxy)
            self._current_proxy.setParent(None)
            self._current_proxy = None
        elif self._current_widget is not None:
            self._viz_stack.removeWidget(self._current_widget)
            self._current_widget.setParent(None)
            self._current_widget = None

        # Wrap in theater proxy and display
        proxy = TheaterProxy(widget)
        self._viz_stack.addWidget(proxy)
        self._viz_stack.setCurrentWidget(proxy)

        self._current_widget = widget
        self._current_proxy = proxy

    # ---- Combo handlers --------------------------------------------------

    def _populate_field_combo(self) -> None:
        """Fill the field combo from the current widget's get_fields().

        Does NOT call set_field — set_stream already picked the best field.
        This just syncs the combo UI.
        """
        if self._current_widget is None:
            return

        fields = self._current_widget.get_fields()
        self._field_combo.blockSignals(True)
        self._field_combo.clear()
        self._field_combo.addItems(fields)
        self._field_combo.blockSignals(False)

        visible = len(fields) > 1
        self._field_label.setVisible(visible)
        self._field_combo.setVisible(visible)

    def _on_stream_changed(self, stream_name: str) -> None:
        """User changed the stream combo."""
        if not stream_name or self._current_widget is None:
            return
        self._current_widget.set_stream(stream_name)
        self._populate_field_combo()

    def _on_field_changed(self, field_name: str) -> None:
        """User changed the field combo."""
        if not field_name or self._current_widget is None:
            return
        self._current_widget.set_field(field_name)

    def _on_viz_selection_changed(self, index: int) -> None:
        """User changed the visualization type combo."""
        if self._entry is None:
            return

        viz_name = self._viz_combo.itemData(index)
        classes = _widget_classes()

        if viz_name is None:
            # Auto — re-score and pick winner
            best_cls = self._pick_widget_class(classes, self._entry)
        else:
            # Manual — find matching class
            best_cls = None
            for cls in classes:
                if cls.viz_name == viz_name:
                    best_cls = cls
                    break

        if best_cls is None:
            return

        self._stop_refresh()
        self._activate_widget(best_cls, self._entry)

    # ---- Refresh timer (live runs) ---------------------------------------

    def _start_refresh(self) -> None:
        """Start polling for new data every 2 seconds."""
        if self._refresh_timer is not None:
            return
        self._refresh_timer = QTimer(self)
        self._refresh_timer.timeout.connect(self._on_refresh_tick)
        self._refresh_timer.start(2000)
        logger.debug("Started refresh timer (2s)")

    def _stop_refresh(self) -> None:
        """Stop the refresh timer if active."""
        if self._refresh_timer is not None:
            self._refresh_timer.stop()
            self._refresh_timer.deleteLater()
            self._refresh_timer = None

    def _on_refresh_tick(self) -> None:
        """Periodic refresh: push new data, check for stop doc."""
        if self._current_widget is None or self._entry is None:
            self._stop_refresh()
            return

        try:
            self._current_widget.refresh()
        except Exception as e:
            logger.warning("Refresh error: {}", e)

        # Check if run completed — re-fetch metadata if the entry supports it
        try:
            if hasattr(self._entry, "refresh"):
                self._entry.refresh()
            if self._entry.metadata.get("stop") is not None:
                logger.info("Live run completed, stopping refresh")
                self._stop_refresh()
        except Exception as e:
            logger.warning("Error checking stop doc: {}", e)

    # ---- Fit / Export ----------------------------------------------------

    def _on_fit_toggled(self, checked: bool) -> None:
        """Toggle fit panel visibility."""
        self._fit_panel.setVisible(checked)

    def _on_export_clicked(self) -> None:
        """Export data from the current visualization."""
        if not self._current_widget:
            return

        from PySide6.QtWidgets import QFileDialog, QMessageBox

        if not hasattr(self._current_widget, "get_supported_export_formats"):
            return

        formats = self._current_widget.get_supported_export_formats()
        if not formats:
            return

        filter_parts = [f"{fmt.upper()} Files (*.{fmt})" for fmt in formats]
        filter_str = ";;".join(filter_parts)

        filename, _ = QFileDialog.getSaveFileName(
            self, "Export Data", "", filter_str,
        )
        if not filename:
            return

        ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else formats[0]

        try:
            data = self._current_widget.export_data(ext)
            with open(filename, "wb") as f:
                f.write(data)
            logger.info("Exported data to {}", filename)
        except Exception as e:
            logger.error("Export failed: {}", e)
            QMessageBox.warning(self, "Export Error", str(e))

    # ---- Cleanup ---------------------------------------------------------

    def _on_closing(self) -> None:
        """Clean up on panel close."""
        self._stop_refresh()

    # ---- Introspection ---------------------------------------------------

    def _get_specific_introspection_data(self) -> dict[str, Any]:
        """Panel-specific introspection data for MCP tools."""
        return {
            "current_visualization": (
                self._current_widget.viz_name
                if self._current_widget
                else None
            ),
            "available_visualizations": [
                cls.viz_name for cls in _widget_classes()
            ],
        }

    # ---- Actions ---------------------------------------------------------

    def _get_available_actions(self) -> list[dict[str, Any]]:
        actions = super()._get_available_actions()
        actions.append(
            {
                "name": "open_run",
                "description": (
                    "Display a Bluesky run by uid in this panel. "
                    "kwargs: uid (str) — start-document uid of the run."
                ),
                "method": "open_run",
                "kwargs": {"uid": "Run uid (start document uid)"},
            }
        )
        return actions

    def invoke_action(self, action_name: str, **kwargs: Any) -> Any:
        if action_name == "open_run":
            uid = kwargs.get("uid")
            if not uid:
                raise ValueError("open_run requires 'uid'")
            from lightfall.services.tiled_service import TiledService

            service = TiledService.get_instance()
            client = service._client
            if client is None or not service.is_connected:
                raise ValueError("Tiled service is not connected")
            try:
                entry = client[uid]
            except KeyError as e:
                raise ValueError(
                    f"Run uid {uid!r} not found in Tiled catalog"
                ) from e
            self.open_run(entry)
            return {"uid": uid}
        return super().invoke_action(action_name, **kwargs)
