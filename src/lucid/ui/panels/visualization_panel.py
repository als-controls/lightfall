"""Visualization panel for live Bluesky data.

Provides the main panel integrating visualization selection,
display, and configuration for live data during scans.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, ClassVar

import numpy as np
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

from lucid.acquire.buffer import MultiStreamBuffer
from lucid.ui.panels.base import BasePanel, PanelMetadata
from lucid.visualization import (
    DocumentProcessor,
    SelectionEngine,
    VisualizationRegistry,
)
from lucid.ui.theater.manager import theater_manager
from lucid.ui.theater.proxy import TheaterProxy
from lucid.visualization.fitting.panel import FitPanel

if TYPE_CHECKING:
    from lucid.acquire.engine import BaseEngine
    from lucid.plugins.visualization_plugin import VisualizationPlugin
    from lucid.visualization.base import BaseVisualizationWidget
    from lucid.visualization.spec import DataCharacteristics


class VisualizationPanel(BasePanel):
    """Main panel for live data visualization.

    Provides:
    - Automatic visualization selection based on data characteristics
    - Manual visualization type override
    - Integration with data buffer
    - Fit panel for 1D plots
    """

    panel_metadata: ClassVar[PanelMetadata] = PanelMetadata(
        id="lucid.panels.visualization",
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
        """Initialize the visualization panel."""
        self._processor: DocumentProcessor | None = None
        self._selection_engine: SelectionEngine | None = None
        self._registry: VisualizationRegistry | None = None
        self._buffer: MultiStreamBuffer | None = None
        self._acquire_engine: BaseEngine | None = None

        self._current_widget: BaseVisualizationWidget | None = None
        self._current_proxy: TheaterProxy | None = None
        self._current_plugin: VisualizationPlugin | None = None
        self._characteristics: DataCharacteristics | None = None

        # Tiled stream switching state
        self._tiled_client_key: str = ""
        self._tiled_stream_names: list[str] = []
        self._stream_fetch: Any | None = None

        # Tiled live-run polling state
        self._poll_timer: QTimer | None = None
        self._poll_image_client: Any | None = None
        self._poll_entry: Any | None = None
        self._poll_stream: Any | None = None
        self._poll_scalar_fields: list[str] = []
        self._last_frame_count = 0
        self._last_scalar_count = 0

        super().__init__(parent)

    def _setup_ui(self) -> None:
        """Setup the panel UI."""
        # Initialize components
        self._registry = VisualizationRegistry.get_instance()
        self._selection_engine = SelectionEngine(self._registry)
        self._processor = DocumentProcessor(self)

        # Connect processor signals
        self._processor.characteristics_ready.connect(self._on_characteristics_ready)
        self._processor.run_started.connect(self._on_run_started)
        self._processor.run_stopped.connect(self._on_run_stopped)

        # Main layout
        main_layout = QVBoxLayout()
        main_layout.setContentsMargins(4, 4, 4, 4)
        main_layout.setSpacing(4)

        # Toolbar
        toolbar = self._create_toolbar()
        main_layout.addLayout(toolbar)

        # Splitter for viz + fit panel
        splitter = QSplitter(Qt.Orientation.Horizontal)

        # Visualization stack
        self._viz_stack = QStackedWidget()
        self._viz_stack.setMinimumSize(400, 300)

        # Placeholder widget
        placeholder = QWidget()
        placeholder_layout = QVBoxLayout(placeholder)
        placeholder_layout.addStretch()
        self._placeholder_label = QLabel("No data\nStart a scan to visualize")
        self._placeholder_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._placeholder_label.setStyleSheet("color: gray; font-size: 14px;")
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

        # Set layout
        container = QWidget()
        container.setLayout(main_layout)
        self._layout.addWidget(container)

        # Auto-configure with global Engine singleton
        self._auto_configure()

    def _auto_configure(self) -> None:
        """Auto-configure with Engine singleton.

        Attempts to connect to the global Engine instance if available.
        This enables automatic visualization of data without manual wiring.
        """
        try:
            from lucid.acquire import get_engine

            engine = get_engine()
            self.set_engine(engine)
            logger.debug("VisualizationPanel auto-configured with Engine")
        except Exception as e:
            logger.debug("Could not auto-configure Engine: {}", e)

    def set_engine(self, engine: BaseEngine) -> None:
        """Connect to an Engine instance.

        This wires up the document stream so the panel receives data:
        - Connects Engine.sigOutput to DocumentProcessor for characteristics
        - Creates and connects MultiStreamBuffer for live data
        - Enables visualization creation on scan start

        Args:
            engine: The Engine to use for data streaming.
        """
        if self._acquire_engine is not None:
            # Disconnect from previous engine
            try:
                self._acquire_engine.sigOutput.disconnect(self._on_document)
            except RuntimeError:
                pass

        self._acquire_engine = engine

        # Connect engine output to our document handler
        engine.sigOutput.connect(self._on_document)

        # Create buffer for live data streaming to visualizations
        self._buffer = MultiStreamBuffer(parent=self)

        # Connect engine to buffer as well (buffer also needs documents)
        engine.sigOutput.connect(self._buffer)

        logger.info("VisualizationPanel connected to Engine")

    def _on_document(self, name: str, doc: dict) -> None:
        """Handle document from Engine.

        Routes documents to the DocumentProcessor for characteristics
        extraction. The processor emits signals when ready.

        Args:
            name: Document type.
            doc: Document data.
        """
        if self._processor:
            self._processor(name, doc)

    def _create_toolbar(self) -> QHBoxLayout:
        """Create the toolbar."""
        toolbar = QHBoxLayout()
        toolbar.setSpacing(8)

        # Stream selector (visible only for tiled runs with multiple streams)
        self._stream_label = QLabel("Stream:")
        self._stream_combo = QComboBox()
        self._stream_combo.setMinimumWidth(100)
        self._stream_combo.currentTextChanged.connect(self._on_stream_changed)
        toolbar.addWidget(self._stream_label)
        toolbar.addWidget(self._stream_combo)
        self._stream_label.hide()
        self._stream_combo.hide()

        # Visualization type selector
        viz_label = QLabel("Visualization:")
        self._viz_combo = QComboBox()
        self._viz_combo.setMinimumWidth(120)
        self._viz_combo.addItem("Auto", None)
        # Add registered visualizations
        self._update_viz_combo()
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

    def _update_viz_combo(self) -> None:
        """Update visualization type combo box."""
        if not self._registry:
            return

        # Block signals during update
        self._viz_combo.blockSignals(True)

        # Remember current selection
        current = self._viz_combo.currentData()

        # Clear except "Auto"
        while self._viz_combo.count() > 1:
            self._viz_combo.removeItem(1)

        # Add registered visualizations
        for plugin in self._registry.get_all_visualizations():
            self._viz_combo.addItem(plugin.display_name, plugin.name)

        # Restore selection
        if current:
            index = self._viz_combo.findData(current)
            if index >= 0:
                self._viz_combo.setCurrentIndex(index)

        self._viz_combo.blockSignals(False)

    def _update_stream_combo(
        self, stream_names: list[str], active: str,
    ) -> None:
        """Update the stream selector combo box.

        Shows the combo only when there are multiple streams.
        """
        self._stream_combo.blockSignals(True)
        self._stream_combo.clear()
        self._stream_combo.addItems(stream_names)
        idx = self._stream_combo.findText(active)
        if idx >= 0:
            self._stream_combo.setCurrentIndex(idx)
        self._stream_combo.blockSignals(False)

        visible = len(stream_names) > 1
        self._stream_label.setVisible(visible)
        self._stream_combo.setVisible(visible)

    def _on_stream_changed(self, stream_name: str) -> None:
        """Handle stream selector change — re-fetch data for the new stream."""
        if not stream_name or not self._tiled_client_key:
            return

        from lucid.services.tiled_service import TiledService
        from lucid.ui.panels.tiled_browser_panel import TiledBrowserPanel
        from lucid.utils.threads import QThreadFuture

        tiled = TiledService.get_instance()
        client = tiled._client
        if client is None:
            return

        self._stream_fetch = QThreadFuture(
            TiledBrowserPanel._setup_visualization,
            client,
            self._tiled_client_key,
            stream_name,
            callback_slot=self._on_stream_fetch_ready,
            except_slot=self._on_stream_fetch_error,
            name="tiled_stream_switch",
        )
        self._stream_fetch.start()

    def _on_stream_fetch_ready(self, result: dict | None = None) -> None:
        """Handle stream switch result."""
        if not result:
            return
        self.open_tiled_run(
            start_doc=result["start_doc"],
            descriptor=result["descriptor"],
            image_client=result.get("image_client"),
            timestamps=result["timestamps"],
            frame_shape=result.get("frame_shape", ()),
            is_live=result.get("is_live", False),
            entry=result.get("entry"),
            scalar_data=result.get("scalar_data"),
            scalar_fields=result.get("scalar_fields", []),
            stream_names=result.get("stream_names", []),
            active_stream=result.get("active_stream", "primary"),
            client_key=result.get("client_key", ""),
        )

    def _on_stream_fetch_error(self, error: Exception) -> None:
        """Handle stream switch error."""
        logger.error("Failed to switch stream: {}", error)

    def _on_viz_selection_changed(self, index: int) -> None:
        """Handle visualization type selection change."""
        viz_name = self._viz_combo.itemData(index)

        if viz_name is None:
            # Auto mode - re-run selection
            if self._characteristics:
                self._select_visualization(self._characteristics)
        else:
            # Manual selection
            if self._characteristics:
                self._create_visualization_by_name(viz_name)

    def _on_fit_toggled(self, checked: bool) -> None:
        """Handle fit panel toggle."""
        self._fit_panel.setVisible(checked)

    def _on_export_clicked(self) -> None:
        """Handle export button click."""
        if not self._current_widget:
            return

        from PySide6.QtWidgets import QFileDialog, QMessageBox

        formats = self._current_widget.get_supported_export_formats()
        if not formats:
            return

        # Build filter string
        filter_parts = []
        for fmt in formats:
            filter_parts.append(f"{fmt.upper()} Files (*.{fmt})")
        filter_str = ";;".join(filter_parts)

        filename, _ = QFileDialog.getSaveFileName(
            self,
            "Export Data",
            "",
            filter_str,
        )

        if not filename:
            return

        # Determine format from extension
        ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else formats[0]

        try:
            data = self._current_widget.export_data(ext)
            with open(filename, "wb") as f:
                f.write(data)
            logger.info("Exported data to {}", filename)
        except Exception as e:
            logger.error("Export failed: {}", e)
            QMessageBox.warning(self, "Export Error", str(e))

    def _on_characteristics_ready(self, characteristics: DataCharacteristics) -> None:
        """Handle characteristics ready from processor."""
        self._characteristics = characteristics

        # Select visualization (if Auto mode)
        if self._viz_combo.currentData() is None:
            self._select_visualization(characteristics)

    def _on_run_started(self, doc: dict) -> None:
        """Handle run start."""
        # Clear current visualization
        if self._current_widget:
            self._current_widget.clear()
        # Hide stream selector for live scans (only used for tiled replay)
        self._stream_label.hide()
        self._stream_combo.hide()

    def _on_run_stopped(self, doc: dict) -> None:
        """Handle run stop."""
        pass  # Status now shown by visualization widgets themselves

    def _select_visualization(self, characteristics: DataCharacteristics) -> None:
        """Select and create visualization for characteristics."""
        if not self._selection_engine:
            return

        results = self._selection_engine.select_visualizations(characteristics, max_results=1)

        if not results:
            logger.warning("No suitable visualization found for data characteristics")
            return

        plugin, score = results[0]
        logger.info(
            "Selected visualization '{}' with score {}",
            plugin.name,
            score,
        )

        self._create_visualization(plugin, characteristics)

    def _create_visualization_by_name(self, name: str) -> None:
        """Create visualization by name."""
        if not self._registry or not self._characteristics:
            return

        plugin = self._registry.get_visualization(name)
        if plugin:
            self._create_visualization(plugin, self._characteristics)

    def _create_visualization(
        self,
        plugin: VisualizationPlugin,
        characteristics: DataCharacteristics,
    ) -> None:
        """Create and display a visualization widget."""
        if not self._buffer:
            # Create a dummy buffer for lazy-mode widgets (they won't use it)
            self._buffer = MultiStreamBuffer(parent=self)

        # Get spec
        spec = self._selection_engine.get_spec_for_visualization(plugin, characteristics)

        # Create widget
        try:
            widget = plugin.create_widget(spec, self._buffer, self)
        except Exception as e:
            logger.error("Failed to create visualization: {}", e)
            return

        # Remove old widget/proxy
        if self._current_proxy is not None:
            # Force-close theater mode if this widget is currently expanded
            if (
                theater_manager._overlay is not None
                and theater_manager._overlay._active_proxy is self._current_proxy
            ):
                theater_manager._overlay._finish_deactivate()
            theater_manager.unregister(self._current_proxy)
            self._viz_stack.removeWidget(self._current_proxy)
            self._current_proxy.deleteLater()
        elif self._current_widget is not None:
            self._viz_stack.removeWidget(self._current_widget)
            self._current_widget.deleteLater()

        # Wrap in theater proxy and add to stack
        proxy = TheaterProxy(widget)
        self._viz_stack.addWidget(proxy)
        self._viz_stack.setCurrentWidget(proxy)

        self._current_widget = widget
        self._current_proxy = proxy
        self._current_plugin = plugin

        # Connect fit panel if plot
        if hasattr(widget, "fit_requested"):
            widget.fit_requested.connect(lambda: self._fit_btn.setChecked(True))
            self._fit_panel.set_plot(widget)

        widget.start()
        self.visualization_changed.emit(plugin.name)

        logger.debug("Created visualization: {}", plugin.name)

    def set_buffer(self, buffer: MultiStreamBuffer) -> None:
        """Set the data buffer.

        Use this to provide a custom buffer instead of the auto-created one.
        The buffer should already be subscribed to the Engine's document stream.

        Args:
            buffer: MultiStreamBuffer to visualize.
        """
        self._buffer = buffer
        logger.debug("VisualizationPanel buffer set manually")

    # === Tiled visualization path ===

    def open_tiled_run(
        self,
        start_doc: dict,
        descriptor: dict,
        image_client: Any | None,
        timestamps: np.ndarray,
        frame_shape: tuple[int, ...],
        is_live: bool,
        entry: Any | None = None,
        scalar_data: dict[str, np.ndarray] | None = None,
        scalar_fields: list[str] | None = None,
        stream_names: list[str] | None = None,
        active_stream: str = "primary",
        client_key: str = "",
    ) -> None:
        """Open a tiled run — unified entry point for all run types.

        Feeds synthetic start + descriptor through the processor so
        auto-selection picks the right widget, then hands data to the
        widget via the appropriate method:
        - Image runs: ``set_array_source`` (lazy, one frame at a time)
        - Scalar runs: ``set_data`` (eager, all data bulk-loaded)

        Args:
            start_doc: Start document (or equivalent metadata dict).
            descriptor: Descriptor document for the primary stream.
            image_client: Tiled ArrayClient for the image field, or None.
            timestamps: 1-D numpy array of epoch timestamps.
            frame_shape: (H, W) shape of each image frame.
            is_live: True if the run has no stop document yet.
            entry: Tiled entry (BlueskyRun) for polling metadata.
            scalar_data: Dict mapping field name to 1-D numpy array.
            scalar_fields: List of scalar field names (for combo boxes).
            stream_names: All available stream names for the run.
            active_stream: Currently selected stream name.
            client_key: Tiled catalog key for the run (for stream switching).
        """
        # Stop any prior poll
        self._stop_tiled_poll()

        # Update stream selector
        self._tiled_client_key = client_key
        self._tiled_stream_names = stream_names or []
        self._update_stream_combo(self._tiled_stream_names, active_stream)

        # Feed start + descriptor through processor for characteristics
        if self._processor:
            self._processor("start", start_doc)
            self._processor("descriptor", descriptor)
        # _on_characteristics_ready fires -> creates visualization widget

        # Dispatch to widget based on type and available data
        from lucid.visualization.widgets.image_sequence import ImageStackVisualization

        if isinstance(self._current_widget, ImageStackVisualization) and image_client is not None:
            self._current_widget.set_array_source(
                image_client, timestamps, frame_shape
            )
        elif scalar_data is not None and hasattr(self._current_widget, "set_data"):
            self._current_widget.set_data(
                scalar_data, scalar_fields or list(scalar_data.keys())
            )

        # Start polling if live
        if is_live:
            self._start_tiled_poll(
                image_client=image_client,
                entry=entry,
                scalar_fields=scalar_fields or [],
            )

        logger.info(
            "Opened tiled run: stream={}, image={}, scalars={}, live={}",
            active_stream,
            image_client is not None,
            len(scalar_data) if scalar_data else 0,
            is_live,
        )

    def _start_tiled_poll(
        self,
        image_client: Any | None,
        entry: Any | None,
        scalar_fields: list[str],
    ) -> None:
        """Start polling tiled for new data (live run)."""
        self._poll_image_client = image_client
        self._poll_entry = entry
        self._poll_scalar_fields = scalar_fields
        self._last_frame_count = image_client.shape[0] if image_client else 0
        self._last_scalar_count = 0

        # Store stream ref for re-reading scalar data on poll
        try:
            self._poll_stream = entry["primary"] if entry is not None else None
        except Exception:
            self._poll_stream = None

        self._poll_timer = QTimer(self)
        self._poll_timer.timeout.connect(self._poll_tiled)
        self._poll_timer.start(2000)
        logger.debug("Started tiled poll timer (2s)")

    def _stop_tiled_poll(self) -> None:
        """Stop the tiled polling timer if active."""
        if self._poll_timer is not None:
            self._poll_timer.stop()
            self._poll_timer.deleteLater()
            self._poll_timer = None
        self._poll_image_client = None
        self._poll_entry = None
        self._poll_stream = None
        self._poll_scalar_fields = []

    def _poll_tiled(self) -> None:
        """Check for new data from tiled (live run).

        Image path: re-checks ``ArrayClient.shape`` for new frames.
        Scalar path: re-reads all scalar arrays if count grew.
        Stops polling when a stop document appears.
        """
        try:
            # Image polling
            if self._poll_image_client is not None:
                new_count = self._poll_image_client.shape[0]
                if new_count > self._last_frame_count:
                    self._last_frame_count = new_count
                    from lucid.visualization.widgets.image_sequence import ImageStackVisualization
                    if isinstance(self._current_widget, ImageStackVisualization):
                        self._current_widget.update_lazy_frame_count(
                            new_count, np.arange(new_count, dtype=np.float64)
                        )

            # Scalar polling
            elif self._poll_stream is not None and self._poll_scalar_fields:
                stream_keys = list(self._poll_stream.keys())
                sample = self._poll_scalar_fields[0]
                if sample in stream_keys:
                    new_count = self._poll_stream[sample].shape[0]
                    if new_count > self._last_scalar_count:
                        self._last_scalar_count = new_count
                        scalar_data = {}
                        for f in self._poll_scalar_fields:
                            if f in stream_keys:
                                scalar_data[f] = np.asarray(
                                    self._poll_stream[f].read()
                                )
                        if hasattr(self._current_widget, "set_data"):
                            self._current_widget.set_data(
                                scalar_data, self._poll_scalar_fields
                            )

            # Check for stop doc
            if self._poll_entry is not None:
                stop = self._poll_entry.metadata.get("stop")
                if stop is not None:
                    logger.info("Live run completed, stopping poll")
                    self._stop_tiled_poll()

        except Exception as e:
            logger.warning("Tiled poll error: {}", e)

    def get_processor(self) -> DocumentProcessor:
        """Get the document processor for RunEngine subscription.

        Returns:
            DocumentProcessor instance.
        """
        return self._processor

    def _get_specific_introspection_data(self) -> dict[str, Any]:
        """Get panel-specific introspection data."""
        return {
            "current_visualization": (
                self._current_plugin.name if self._current_plugin else None
            ),
            "characteristics": (
                {
                    "ndim": self._characteristics.ndim,
                    "plan_name": self._characteristics.plan_name,
                    "dim_fields": self._characteristics.dim_fields,
                    "dep_fields": self._characteristics.dep_fields,
                }
                if self._characteristics
                else None
            ),
            "available_visualizations": [
                p.name for p in self._registry.get_all_visualizations()
            ]
            if self._registry
            else [],
        }
