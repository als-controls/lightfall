"""Toast notification manager for NCS.

Provides application-wide toast notifications with:
- Theme-aware styling (light/dark)
- Convenient methods for success, error, warning, info messages
- Configurable positioning and duration
"""

from __future__ import annotations

import threading
from typing import TYPE_CHECKING

from pyqttoast import Toast, ToastPosition, ToastPreset
from PySide6.QtCore import QObject, Qt, Slot
from PySide6.QtWidgets import QApplication

from lightfall.ui.style.windows_frame import suppress_windows_frame
from lightfall.ui.theme import Theme, ThemeManager
from lightfall.utils.logging import logger

if TYPE_CHECKING:
    from PySide6.QtWidgets import QWidget


class ToastManager(QObject):
    """
    Application-wide toast notification manager.

    ToastManager provides:
    - Singleton access for showing toasts from anywhere
    - Theme integration with automatic light/dark presets
    - Simple API: success(), error(), warning(), info()

    Example:
        >>> toast = ToastManager.get_instance()
        >>> toast.success("File saved successfully")
        >>> toast.error("Connection failed", "Check your network settings")
        >>> toast.warning("Unsaved changes")
        >>> toast.info("Update available")
    """

    _instance: ToastManager | None = None
    _lock = threading.RLock()

    # Default configuration
    DEFAULT_DURATION = 5000  # milliseconds
    DEFAULT_POSITION = ToastPosition.BOTTOM_RIGHT
    DEFAULT_MAX_ON_SCREEN = 3

    def __init__(self, parent: QObject | None = None) -> None:
        """Initialize the toast manager."""
        super().__init__(parent)
        self._theme_manager = ThemeManager.get_instance()
        self._parent_widget: QWidget | None = None

        # Configure global toast settings
        Toast.setPosition(self.DEFAULT_POSITION)
        Toast.setMaximumOnScreen(self.DEFAULT_MAX_ON_SCREEN)

        # Connect to theme changes
        self._theme_manager.theme_changed.connect(self._on_theme_changed)

        logger.debug("ToastManager initialized")

    @classmethod
    def get_instance(cls) -> ToastManager:
        """Get the singleton ToastManager instance."""
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    @classmethod
    def reset(cls) -> None:
        """Reset the singleton instance (for testing)."""
        with cls._lock:
            if cls._instance is not None:
                cls._instance.deleteLater()
            cls._instance = None

    def set_parent_widget(self, widget: QWidget | None) -> None:
        """Set the parent widget for toasts.

        If not set, uses the active window.

        Args:
            widget: Parent widget for toast positioning.
        """
        self._parent_widget = widget

    def _get_parent(self) -> QWidget | None:
        """Get the parent widget for toasts."""
        if self._parent_widget is not None:
            return self._durable_parent(self._parent_widget)
        app = QApplication.instance()
        if app:
            return self._durable_parent(app.activeWindow())
        return None

    @staticmethod
    def _durable_parent(widget: QWidget | None) -> QWidget | None:
        """The window a toast can safely be parented to, or None for no parent.

        Qt deletes a toast together with its parent, but pyqttoast keeps listing it,
        and the next toast to show or hide touches the dead object ("Internal C++
        object (Toast) already deleted", GlitchTip LIGHTFALL-H). A dialog usually
        closes before its toast expires, so use the window at the top of the parent
        chain (the main window), and no parent if that window is a toast (the one
        shown last, when there is no main window) or deletes itself on close.
        """
        if widget is None:
            return None
        root = widget.window()
        while root.parentWidget() is not None:
            root = root.parentWidget().window()
        if isinstance(root, Toast) or root.testAttribute(Qt.WidgetAttribute.WA_DeleteOnClose):
            return None
        return root

    def _get_preset(self, base_preset: ToastPreset) -> ToastPreset:
        """Get the appropriate preset variant based on current theme.

        Args:
            base_preset: Base preset (e.g., ToastPreset.SUCCESS).

        Returns:
            Theme-appropriate preset variant.
        """
        is_dark = self._theme_manager.is_dark

        preset_map = {
            ToastPreset.SUCCESS: ToastPreset.SUCCESS_DARK if is_dark else ToastPreset.SUCCESS,
            ToastPreset.WARNING: ToastPreset.WARNING_DARK if is_dark else ToastPreset.WARNING,
            ToastPreset.ERROR: ToastPreset.ERROR_DARK if is_dark else ToastPreset.ERROR,
            ToastPreset.INFORMATION: ToastPreset.INFORMATION_DARK if is_dark else ToastPreset.INFORMATION,
        }

        return preset_map.get(base_preset, base_preset)

    @Slot(Theme)
    def _on_theme_changed(self, theme: Theme) -> None:
        """Handle theme change - no action needed, presets are selected per-toast."""
        logger.debug("Toast manager notified of theme change: {}", theme.value if hasattr(theme, 'value') else theme)

    def show(
        self,
        title: str,
        text: str = "",
        preset: ToastPreset = ToastPreset.INFORMATION,
        duration: int | None = None,
        parent: QWidget | None = None,
    ) -> Toast:
        """Show a toast notification.

        Args:
            title: Toast title text.
            text: Optional detail text.
            preset: Toast preset for styling.
            duration: Display duration in ms (None for default).
            parent: Widget whose window the toast is parented to (default: the
                manager's parent widget, else the active window). A dialog gives way
                to its main window; see :meth:`_durable_parent`.

        Returns:
            The Toast instance.
        """
        parent = self._get_parent() if parent is None else self._durable_parent(parent)
        toast = Toast(parent)

        toast.setTitle(title)
        if text:
            toast.setText(text)

        toast.setDuration(duration if duration is not None else self.DEFAULT_DURATION)
        toast.applyPreset(self._get_preset(preset))

        try:
            toast.show()
        except RuntimeError:
            # pyqttoast iterates over all toast instances for positioning,
            # including ones with deleted C++ widgets. Safe to ignore.
            logger.debug("Toast show() hit deleted C++ object, retrying")
            try:
                Toast.setMaximumOnScreen(self.DEFAULT_MAX_ON_SCREEN)
                toast.show()
            except RuntimeError:
                logger.warning("Could not show toast: {}", title)
                return toast
        suppress_windows_frame(toast)

        logger.debug("Showing toast: {}{}", title, f" — {text}" if text else "")

        return toast

    def success(self, title: str, text: str = "", duration: int | None = None) -> Toast:
        """Show a success toast.

        Args:
            title: Success message title.
            text: Optional detail text.
            duration: Display duration in ms.

        Returns:
            The Toast instance.
        """
        return self.show(title, text, ToastPreset.SUCCESS, duration)

    def error(self, title: str, text: str = "", duration: int | None = None) -> Toast:
        """Show an error toast.

        Args:
            title: Error message title.
            text: Optional detail text.
            duration: Display duration in ms.

        Returns:
            The Toast instance.
        """
        return self.show(title, text, ToastPreset.ERROR, duration)

    def warning(
        self,
        title: str,
        text: str = "",
        duration: int | None = None,
        parent: QWidget | None = None,
    ) -> Toast:
        """Show a warning toast.

        Args:
            title: Warning message title.
            text: Optional detail text.
            duration: Display duration in ms.
            parent: Widget to parent the toast to (see :meth:`show`).

        Returns:
            The Toast instance.
        """
        return self.show(title, text, ToastPreset.WARNING, duration, parent)

    def info(self, title: str, text: str = "", duration: int | None = None) -> Toast:
        """Show an info toast.

        Args:
            title: Info message title.
            text: Optional detail text.
            duration: Display duration in ms.

        Returns:
            The Toast instance.
        """
        return self.show(title, text, ToastPreset.INFORMATION, duration)
