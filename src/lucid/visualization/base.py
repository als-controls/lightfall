"""Base visualization widget for live data display.

BaseVisualizationWidget provides the foundation for all visualization
widgets with common functionality for data updates, theming, and export.
"""

from __future__ import annotations

from abc import abstractmethod
from typing import TYPE_CHECKING, Any

from loguru import logger
from PySide6.QtCore import QTimer, Signal
from PySide6.QtWidgets import QVBoxLayout, QWidget

if TYPE_CHECKING:
    from lucid.acquire.buffer import LiveDataBuffer, MultiStreamBuffer
    from lucid.visualization.spec import VisualizationSpec


class BaseVisualizationWidget(QWidget):
    """Abstract base class for all visualization widgets.

    BaseVisualizationWidget provides:
    - Signal-based data update handling with rate limiting
    - Theme integration via ThemedVisualizationMixin
    - Common export functionality
    - Selection state management

    Subclasses must implement:
    - _setup_ui(): Build the visualization UI
    - _on_new_point(): Handle new data point
    - _export_data(): Export data in requested format

    Signals:
        data_updated: Emitted after data is processed.
        selection_changed(str, object): Emitted when user selects data.
            Args are (field_name, selected_value).
        error_occurred(str): Emitted when an error occurs.

    Example:
        >>> class MyPlotWidget(BaseVisualizationWidget):
        ...     def _setup_ui(self):
        ...         self._plot = pg.PlotWidget()
        ...         self.layout().addWidget(self._plot)
        ...
        ...     def _on_new_point(self, seq_num, data):
        ...         x = data.get(self._spec.x_field)
        ...         y = data.get(self._spec.y_field)
        ...         self._update_plot(x, y)
    """

    # Signals
    data_updated = Signal()
    selection_changed = Signal(str, object)  # field_name, value
    error_occurred = Signal(str)

    # Rate limiting
    UPDATE_INTERVAL_MS = 50  # 20 Hz max update rate
    BATCH_SIZE = 100  # Max points to process per update

    def __init__(
        self,
        spec: VisualizationSpec,
        buffer: MultiStreamBuffer,
        parent: QWidget | None = None,
    ) -> None:
        """Initialize the visualization widget.

        Args:
            spec: Visualization specification.
            buffer: MultiStreamBuffer providing live data.
            parent: Optional Qt parent widget.
        """
        super().__init__(parent)
        self._spec = spec
        self._buffer = buffer
        self._stream_name = "primary"

        # State
        self._is_running = False
        self._pending_updates: list[tuple[int, dict]] = []
        self._last_seq_num = -1

        # Rate limiting timer
        self._update_timer = QTimer(self)
        self._update_timer.setInterval(self.UPDATE_INTERVAL_MS)
        self._update_timer.timeout.connect(self._process_pending_updates)

        # Setup layout
        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(0, 0, 0, 0)
        self._layout.setSpacing(0)

        # Let subclass setup UI
        self._setup_ui()

        # Apply initial theme
        self._apply_theme()

        # Connect to buffer signals
        self._connect_buffer()

        logger.debug(
            "Created {} for {}",
            self.__class__.__name__,
            spec.viz_type.name,
        )

    @property
    def spec(self) -> VisualizationSpec:
        """Get the visualization specification."""
        return self._spec

    @property
    def buffer(self) -> MultiStreamBuffer:
        """Get the data buffer."""
        return self._buffer

    @property
    def stream_buffer(self) -> LiveDataBuffer | None:
        """Get the LiveDataBuffer for the current stream."""
        return self._buffer.get_stream(self._stream_name)

    # === Abstract Methods ===

    @abstractmethod
    def _setup_ui(self) -> None:
        """Setup the visualization UI.

        Override to create plot widgets, tables, etc.
        The layout is already created and available as self._layout.
        """
        ...

    @abstractmethod
    def _on_new_point(self, seq_num: int, data: dict[str, Any]) -> None:
        """Handle a new data point.

        Called for each new data point. Implementations should update
        the visualization incrementally.

        Args:
            seq_num: Sequence number of the data point.
            data: Dictionary of field values.
        """
        ...

    @abstractmethod
    def _export_data(self, format: str) -> bytes:
        """Export visualization data.

        Args:
            format: Export format ("csv", "json", "png", etc.)

        Returns:
            Exported data as bytes.

        Raises:
            ValueError: If format not supported.
        """
        ...

    # === Buffer Connection ===

    def _connect_buffer(self) -> None:
        """Connect to buffer signals."""
        self._buffer.stream_data_updated.connect(self._on_stream_data)

    def _disconnect_buffer(self) -> None:
        """Disconnect from buffer signals."""
        try:
            self._buffer.stream_data_updated.disconnect(self._on_stream_data)
        except RuntimeError:
            pass  # Already disconnected

    def _on_stream_data(
        self, stream_name: str, seq_num: int, data: dict[str, Any]
    ) -> None:
        """Handle data from any stream.

        Args:
            stream_name: Name of the stream.
            seq_num: Sequence number.
            data: Data dictionary.
        """
        if stream_name != self._stream_name:
            return

        # Queue update for rate-limited processing
        self._pending_updates.append((seq_num, data))

        # Start timer if not running
        if not self._update_timer.isActive():
            self._update_timer.start()

    def _process_pending_updates(self) -> None:
        """Process pending data updates.

        Called by rate-limiting timer to batch process updates.
        """
        if not self._pending_updates:
            self._update_timer.stop()
            return

        # Process up to BATCH_SIZE updates
        to_process = self._pending_updates[:self.BATCH_SIZE]
        self._pending_updates = self._pending_updates[self.BATCH_SIZE:]

        for seq_num, data in to_process:
            if seq_num > self._last_seq_num:
                try:
                    self._on_new_point(seq_num, data)
                    self._last_seq_num = seq_num
                except Exception as e:
                    logger.exception("Error processing point {}: {}", seq_num, e)
                    self.error_occurred.emit(str(e))

        self.data_updated.emit()

        # Stop timer if no more pending
        if not self._pending_updates:
            self._update_timer.stop()

    # === Theme Integration ===

    def _apply_theme(self) -> None:
        """Apply current theme colors.

        Override to customize theme application. Default implementation
        applies background color from ThemeManager.
        """
        try:
            from lucid.ui.theme import ThemeManager

            theme = ThemeManager.get_instance()
            colors = theme.colors

            self.setStyleSheet(
                f"background-color: {colors.background};"
            )

            # Let subclasses apply theme-specific styling
            self._on_theme_changed(colors)
        except ImportError:
            pass  # Theme system not available

    def _on_theme_changed(self, colors: Any) -> None:
        """Handle theme change.

        Override to update visualization colors when theme changes.

        Args:
            colors: ThemeColors from ThemeManager.
        """
        pass

    def _connect_theme_signal(self) -> None:
        """Connect to theme change signal."""
        try:
            from lucid.ui.theme import ThemeManager

            theme = ThemeManager.get_instance()
            theme.colors_changed.connect(self._apply_theme)
        except ImportError:
            pass

    # === Public API ===

    def start(self) -> None:
        """Start receiving and displaying data."""
        self._is_running = True
        logger.debug("{} started", self.__class__.__name__)

    def stop(self) -> None:
        """Stop receiving data updates."""
        self._is_running = False
        self._update_timer.stop()
        logger.debug("{} stopped", self.__class__.__name__)

    def clear(self) -> None:
        """Clear all displayed data."""
        self._pending_updates.clear()
        self._last_seq_num = -1
        self._on_clear()

    def _on_clear(self) -> None:
        """Handle clear request.

        Override to clear visualization-specific state.
        """
        pass

    def export_data(self, format: str = "csv") -> bytes:
        """Export visualization data.

        Args:
            format: Export format ("csv", "json", "png").

        Returns:
            Exported data as bytes.
        """
        return self._export_data(format)

    def get_supported_export_formats(self) -> list[str]:
        """Get list of supported export formats.

        Override to add format-specific exports.

        Returns:
            List of format strings (e.g., ["csv", "json", "png"]).
        """
        return ["csv"]

    # === Lifecycle ===

    def showEvent(self, event) -> None:
        """Handle show event."""
        super().showEvent(event)
        self._connect_theme_signal()

    def closeEvent(self, event) -> None:
        """Handle close event."""
        self.stop()
        self._disconnect_buffer()
        super().closeEvent(event)

    # === Introspection ===

    def get_introspection_data(self) -> dict[str, Any]:
        """Get introspection data for MCP tools.

        Returns:
            Dictionary with widget information.
        """
        return {
            "class": self.__class__.__name__,
            "viz_type": self._spec.viz_type.name,
            "is_running": self._is_running,
            "last_seq_num": self._last_seq_num,
            "pending_updates": len(self._pending_updates),
            "stream_name": self._stream_name,
            "x_field": self._spec.x_field,
            "y_field": self._spec.y_field,
            "z_field": self._spec.z_field,
            "export_formats": self.get_supported_export_formats(),
        }
