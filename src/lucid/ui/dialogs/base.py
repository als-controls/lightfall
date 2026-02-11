"""Base dialog class for LUCID.

Provides a QDialog subclass that automatically sets the application icon.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtWidgets import QDialog

if TYPE_CHECKING:
    from PySide6.QtWidgets import QWidget


class LucidDialog(QDialog):
    """Base dialog class that automatically sets the application icon.

    On Windows, dialogs without a parent don't inherit the application's
    window icon for the taskbar. This base class ensures all LUCID dialogs
    display the correct icon.

    Example:
        >>> class MyDialog(LucidDialog):
        ...     def __init__(self, parent=None):
        ...         super().__init__(parent)
        ...         # Dialog setup...
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._set_app_icon()

    def _set_app_icon(self) -> None:
        """Set the window icon from the application icon."""
        from lucid.resources import get_app_icon
        from lucid.utils.logging import logger

        app_icon = get_app_icon()
        if not app_icon.isNull():
            self.setWindowIcon(app_icon)
            logger.debug("LucidDialog._set_app_icon: icon set ({} sizes)", len(app_icon.availableSizes()))
        else:
            logger.warning("LucidDialog._set_app_icon: app icon is null")
