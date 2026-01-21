"""Toast notification manager for NCS.

Provides application-wide toast notifications with:
- Theme-aware styling (light/dark)
- Convenient methods for success, error, warning, info messages
- Configurable positioning and duration
"""

from __future__ import annotations

import threading
from typing import TYPE_CHECKING

from PySide6.QtCore import QObject, Slot
from PySide6.QtWidgets import QApplication
from pyqttoast import Toast, ToastPosition, ToastPreset

from ncs.ui.theme import Theme, ThemeManager
from ncs.utils.logging import logger

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
            return self._parent_widget
        app = QApplication.instance()
        if app:
            return app.activeWindow()
        return None

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
        logger.debug("Toast manager notified of theme change: {}", theme.value)

    def show(
        self,
        title: str,
        text: str = "",
        preset: ToastPreset = ToastPreset.INFORMATION,
        duration: int | None = None,
    ) -> Toast:
        """Show a toast notification.

        Args:
            title: Toast title text.
            text: Optional detail text.
            preset: Toast preset for styling.
            duration: Display duration in ms (None for default).

        Returns:
            The Toast instance.
        """
        parent = self._get_parent()
        toast = Toast(parent)

        toast.setTitle(title)
        if text:
            toast.setText(text)

        toast.setDuration(duration if duration is not None else self.DEFAULT_DURATION)
        toast.applyPreset(self._get_preset(preset))

        toast.show()
        logger.debug("Showing toast: {}", title)

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

    def warning(self, title: str, text: str = "", duration: int | None = None) -> Toast:
        """Show a warning toast.

        Args:
            title: Warning message title.
            text: Optional detail text.
            duration: Display duration in ms.

        Returns:
            The Toast instance.
        """
        return self.show(title, text, ToastPreset.WARNING, duration)

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
