"""Visualization panel for live Bluesky data.

Provides the main panel integrating visualization selection,
display, and configuration for live data during scans.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, ClassVar

from loguru import logger
from PySide6.QtCore import Qt, Signal
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
        self._current_plugin: VisualizationPlugin | None = None
        self._characteristics: DataCharacteristics | None = None

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
            logger.warning("No buffer connected, cannot create visualization")
            return

        # Get spec
        spec = self._selection_engine.get_spec_for_visualization(plugin, characteristics)

        # Create widget
        try:
            widget = plugin.create_widget(spec, self._buffer, self)
        except Exception as e:
            logger.error("Failed to create visualization: {}", e)
            return

        # Remove old widget
        if self._current_widget:
            self._viz_stack.removeWidget(self._current_widget)
            self._current_widget.deleteLater()

        # Add new widget
        self._viz_stack.addWidget(widget)
        self._viz_stack.setCurrentWidget(widget)

        self._current_widget = widget
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
