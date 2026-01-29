"""IPython console panel for interactive Python access.

Provides an embedded IPython console with:
- Direct access to live application objects
- Widget targeting feature to capture UI widgets into the namespace
- Access to main_window and app for scripting
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, ClassVar

from PySide6.QtCore import QEvent, QObject, Qt, Signal
from PySide6.QtGui import QAction, QCursor
from PySide6.QtWidgets import (
    QApplication,
    QHBoxLayout,
    QLabel,
    QToolBar,
    QVBoxLayout,
    QWidget,
)

from lucid.ui.panels.base import BasePanel, PanelMetadata
from lucid.ui.toast import ToastManager
from lucid.utils.logging import logger

if TYPE_CHECKING:
    from PySide6.QtGui import QKeyEvent, QMouseEvent


class WidgetTargetingFilter(QObject):
    """Event filter for capturing widget clicks during targeting mode.

    When active, this filter intercepts mouse clicks anywhere in the application
    and emits the clicked widget. Escape key cancels targeting mode.

    Signals:
        widget_captured: Emitted when a widget is clicked during targeting.
        targeting_cancelled: Emitted when targeting is cancelled (Escape).
    """

    widget_captured = Signal(QWidget)
    targeting_cancelled = Signal()

    def __init__(self, parent: QObject | None = None) -> None:
        """Initialize the targeting filter.

        Args:
            parent: Parent object.
        """
        super().__init__(parent)
        self._active = False
        self._original_cursor: QCursor | None = None

    @property
    def is_active(self) -> bool:
        """Whether targeting mode is currently active."""
        return self._active

    def activate(self) -> None:
        """Activate widget targeting mode.

        Installs the event filter on QApplication and sets crosshair cursor.
        """
        if self._active:
            return

        app = QApplication.instance()
        if app is None:
            logger.warning("Cannot activate targeting: no QApplication instance")
            return

        self._active = True
        app.installEventFilter(self)

        # Store original cursor and set crosshair
        self._original_cursor = QCursor(app.overrideCursor() or Qt.CursorShape.ArrowCursor)
        app.setOverrideCursor(QCursor(Qt.CursorShape.CrossCursor))

        logger.debug("Widget targeting mode activated")

    def deactivate(self) -> None:
        """Deactivate widget targeting mode.

        Removes the event filter and restores the original cursor.
        """
        if not self._active:
            return

        self._active = False

        app = QApplication.instance()
        if app:
            app.removeEventFilter(self)
            app.restoreOverrideCursor()

        logger.debug("Widget targeting mode deactivated")

    def eventFilter(self, obj: QObject, event: QEvent) -> bool:
        """Filter events to capture widget clicks and escape key.

        Args:
            obj: Object receiving the event.
            event: The event.

        Returns:
            True if event was handled, False otherwise.
        """
        if not self._active:
            return False

        if event.type() == QEvent.Type.MouseButtonPress:
            # Capture the widget under the mouse
            if isinstance(obj, QWidget):
                self.widget_captured.emit(obj)
                self.deactivate()
                return True

        elif event.type() == QEvent.Type.KeyPress:
            key_event: QKeyEvent = event  # type: ignore[assignment]
            if key_event.key() == Qt.Key.Key_Escape:
                self.targeting_cancelled.emit()
                self.deactivate()
                return True

        return False


class IPythonPanel(BasePanel):
    """Interactive IPython console panel.

    Provides an embedded IPython console using QtConsole with:
    - Access to `main_window` and `app` objects in the namespace
    - Widget targeting feature to capture any UI widget
    - Kernel reset capability

    The console uses an in-process kernel for direct access to live
    application objects, allowing interactive exploration and scripting.

    Example:
        >>> # In the IPython console:
        >>> main_window.setWindowTitle("New Title")
        >>> app.services  # Access application services
        >>> # After capturing a widget:
        >>> widget_1.setStyleSheet("background: red")
    """

    panel_metadata: ClassVar[PanelMetadata] = PanelMetadata(
        id="lucid.panels.ipython",
        name="IPython Console",
        description="Interactive Python console with access to application objects",
        icon="terminal",
        category="Tools",
        singleton=True,
        closable=True,
        keywords=["ipython", "console", "python", "scripting", "repl", "terminal"],
    )

    def __init__(self, parent: QWidget | None = None) -> None:
        """Initialize the IPython panel.

        Args:
            parent: Parent widget.
        """
        self._kernel_manager = None
        self._kernel_client = None
        self._jupyter_widget = None
        self._targeting_filter: WidgetTargetingFilter | None = None
        self._target_action: QAction | None = None
        self._status_label: QLabel | None = None
        self._widget_counter = 0
        self._qtconsole_available = False
        super().__init__(parent)

    def _setup_ui(self) -> None:
        """Set up the panel UI."""
        # Check if qtconsole is available
        try:
            from qtconsole.inprocess import QtInProcessKernelManager
            from qtconsole.rich_jupyter_widget import RichJupyterWidget

            self._qtconsole_available = True
        except ImportError:
            self._qtconsole_available = False
            self._setup_unavailable_ui()
            return

        # Create toolbar
        toolbar = self._create_toolbar()
        self._layout.addWidget(toolbar)

        # Create Jupyter widget
        self._setup_jupyter_widget()

    def _setup_unavailable_ui(self) -> None:
        """Set up UI when qtconsole is not available."""
        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(20, 20, 20, 20)

        label = QLabel(
            "IPython console is not available.\n\n"
            "To enable it, install the optional dependencies:\n\n"
            "    pip install lucid[ipython]\n\n"
            "Or install directly:\n\n"
            "    pip install qtconsole ipykernel"
        )
        label.setWordWrap(True)
        label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(label)

        self._layout.addWidget(container)
        logger.warning("qtconsole not available - IPython panel disabled")

    def _create_toolbar(self) -> QToolBar:
        """Create the panel toolbar.

        Returns:
            The configured toolbar.
        """
        toolbar = QToolBar()
        toolbar.setMovable(False)

        # Target Widget action (checkable)
        self._target_action = QAction("Target Widget", toolbar)
        self._target_action.setCheckable(True)
        self._target_action.setToolTip("Click to capture a widget into the console namespace")
        self._target_action.triggered.connect(self._on_target_triggered)
        toolbar.addAction(self._target_action)

        toolbar.addSeparator()

        # Clear action
        clear_action = QAction("Clear", toolbar)
        clear_action.setToolTip("Clear the console output")
        clear_action.triggered.connect(self._on_clear)
        toolbar.addAction(clear_action)

        # Reset Kernel action
        reset_action = QAction("Reset Kernel", toolbar)
        reset_action.setToolTip("Restart the IPython kernel")
        reset_action.triggered.connect(self._on_reset_kernel)
        toolbar.addAction(reset_action)

        # Spacer
        spacer = QWidget()
        spacer.setSizePolicy(
            spacer.sizePolicy().horizontalPolicy(),
            spacer.sizePolicy().verticalPolicy(),
        )
        from PySide6.QtWidgets import QSizePolicy

        spacer.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        toolbar.addWidget(spacer)

        # Status label
        self._status_label = QLabel()
        toolbar.addWidget(self._status_label)

        return toolbar

    def _setup_jupyter_widget(self) -> None:
        """Set up the Jupyter/IPython widget with in-process kernel."""
        from qtconsole.inprocess import QtInProcessKernelManager
        from qtconsole.rich_jupyter_widget import RichJupyterWidget

        # Create kernel manager and start kernel
        self._kernel_manager = QtInProcessKernelManager()
        self._kernel_manager.start_kernel()

        # Get kernel and set up namespace
        kernel = self._kernel_manager.kernel
        self._setup_initial_namespace(kernel)

        # Create client
        self._kernel_client = self._kernel_manager.client()
        self._kernel_client.start_channels()

        # Create the Jupyter widget
        self._jupyter_widget = RichJupyterWidget()
        self._jupyter_widget.kernel_manager = self._kernel_manager
        self._jupyter_widget.kernel_client = self._kernel_client

        self._layout.addWidget(self._jupyter_widget)

        # Set up widget targeting filter
        self._targeting_filter = WidgetTargetingFilter(self)
        self._targeting_filter.widget_captured.connect(self._on_widget_captured)
        self._targeting_filter.targeting_cancelled.connect(self._on_targeting_cancelled)

        logger.info("IPython console initialized")

    def _setup_initial_namespace(self, kernel: Any) -> None:
        """Set up the initial kernel namespace with application objects.

        Args:
            kernel: The IPython kernel.
        """
        from lucid.core.application import NCSApplication

        app = NCSApplication.get_instance()

        # Add main_window and app to namespace
        kernel.shell.push({
            "main_window": app.main_window,
            "app": app,
        })

        logger.debug("Initial namespace set up with main_window and app")

    def _on_target_triggered(self, checked: bool) -> None:
        """Handle target widget action toggled.

        Args:
            checked: Whether the action is checked.
        """
        if self._targeting_filter is None:
            return

        if checked:
            self._targeting_filter.activate()
            if self._status_label:
                self._status_label.setText("Click a widget to capture...")
        else:
            self._targeting_filter.deactivate()
            if self._status_label:
                self._status_label.clear()

    def _on_widget_captured(self, widget: QWidget) -> None:
        """Handle widget capture.

        Args:
            widget: The captured widget.
        """
        # Generate variable name
        var_name = self._generate_variable_name(widget)

        # Push to kernel namespace
        self.push_variable(var_name, widget)

        # Update UI
        if self._target_action:
            self._target_action.setChecked(False)
        if self._status_label:
            self._status_label.clear()

        # Show toast notification
        widget_class = widget.__class__.__name__
        object_name = widget.objectName() or "(no name)"
        ToastManager.get_instance().success(
            "Widget captured",
            f"{var_name} = {widget_class} ({object_name})",
        )

        logger.info("Captured widget: {} -> {}", widget_class, var_name)

    def _on_targeting_cancelled(self) -> None:
        """Handle targeting mode cancelled."""
        if self._target_action:
            self._target_action.setChecked(False)
        if self._status_label:
            self._status_label.clear()

        ToastManager.get_instance().info("Widget targeting cancelled")

    def _generate_variable_name(self, widget: QWidget) -> str:
        """Generate a variable name for a captured widget.

        Uses the widget's objectName if available, otherwise generates
        a sequential name like widget_1, widget_2, etc.

        Args:
            widget: The widget to name.

        Returns:
            A valid Python variable name.
        """
        object_name = widget.objectName()

        if object_name:
            # Clean up object name to be a valid Python identifier
            # Replace dots and dashes with underscores
            clean_name = object_name.replace(".", "_").replace("-", "_")
            # Remove leading digits
            while clean_name and clean_name[0].isdigit():
                clean_name = clean_name[1:]
            # Ensure it's not empty and is a valid identifier
            if clean_name and clean_name.isidentifier():
                return f"w_{clean_name}"

        # Fall back to sequential naming
        self._widget_counter += 1
        return f"widget_{self._widget_counter}"

    def push_variable(self, name: str, value: Any) -> None:
        """Push a variable to the kernel namespace.

        Args:
            name: Variable name.
            value: Variable value.
        """
        if self._kernel_manager is None:
            logger.warning("Cannot push variable: kernel not available")
            return

        kernel = self._kernel_manager.kernel
        kernel.shell.push({name: value})
        logger.debug("Pushed variable to namespace: {}", name)

    def _on_clear(self) -> None:
        """Clear the console output."""
        if self._jupyter_widget:
            self._jupyter_widget.clear()
            logger.debug("IPython console cleared")

    def _on_reset_kernel(self) -> None:
        """Reset the IPython kernel by fully recreating it.

        In-process kernels don't support restart_kernel() properly,
        so we shut down and recreate the kernel from scratch.
        """
        if not self._qtconsole_available:
            return

        from qtconsole.inprocess import QtInProcessKernelManager

        # Stop existing client channels
        if self._kernel_client:
            try:
                self._kernel_client.stop_channels()
            except Exception as e:
                logger.warning("Error stopping kernel client: {}", e)

        # Shutdown existing kernel
        if self._kernel_manager:
            try:
                self._kernel_manager.shutdown_kernel()
            except Exception as e:
                logger.warning("Error shutting down kernel: {}", e)

        # Create fresh kernel manager and start kernel
        self._kernel_manager = QtInProcessKernelManager()
        self._kernel_manager.start_kernel()

        # Setup namespace on fresh kernel
        kernel = self._kernel_manager.kernel
        self._setup_initial_namespace(kernel)

        # Create fresh client and start channels
        self._kernel_client = self._kernel_manager.client()
        self._kernel_client.start_channels()

        # Reconnect the Jupyter widget to the new kernel
        if self._jupyter_widget:
            self._jupyter_widget.kernel_manager = self._kernel_manager
            self._jupyter_widget.kernel_client = self._kernel_client

        # Reset widget counter
        self._widget_counter = 0

        ToastManager.get_instance().info("Kernel reset", "Namespace restored")
        logger.info("IPython kernel reset")

    def _on_closing(self) -> None:
        """Clean up when panel closes."""
        # Deactivate targeting if active
        if self._targeting_filter and self._targeting_filter.is_active:
            self._targeting_filter.deactivate()

        # Stop kernel client channels
        if self._kernel_client:
            try:
                self._kernel_client.stop_channels()
            except Exception as e:
                logger.warning("Error stopping kernel client: {}", e)

        # Shutdown kernel
        if self._kernel_manager:
            try:
                self._kernel_manager.shutdown_kernel()
            except Exception as e:
                logger.warning("Error shutting down kernel: {}", e)

        logger.info("IPython panel closed")

    # === Introspection API for MCP tools ===

    def _get_specific_introspection_data(self) -> dict[str, Any]:
        """Get panel-specific introspection data."""
        return {
            "qtconsole_available": self._qtconsole_available,
            "targeting_active": (
                self._targeting_filter.is_active if self._targeting_filter else False
            ),
            "widget_counter": self._widget_counter,
        }

    def _get_available_actions(self) -> list[dict[str, Any]]:
        """Get available actions for this panel."""
        base_actions = super()._get_available_actions()
        return base_actions + [
            {
                "name": "clear",
                "description": "Clear the console output",
                "method": "action_clear",
            },
            {
                "name": "reset_kernel",
                "description": "Reset the IPython kernel",
                "method": "action_reset_kernel",
            },
            {
                "name": "push_variable",
                "description": "Add a variable to the kernel namespace",
                "method": "push_variable",
                "params": {"name": "str", "value": "Any"},
            },
        ]

    def action_clear(self) -> bool:
        """Clear action handler for MCP tools."""
        self._on_clear()
        return True

    def action_reset_kernel(self) -> bool:
        """Reset kernel action handler for MCP tools."""
        self._on_reset_kernel()
        return True
