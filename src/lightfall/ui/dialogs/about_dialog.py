"""About dialog for Lightfall.

Displays application information, version, and branding.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from lightfall.ui.dialogs.base import LFDialog
from lightfall.utils.logging import logger


class AboutDialog(LFDialog):
    """About dialog showing Lightfall branding and version information.

    Displays the logo, application name, description, and version.
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        """Initialize the about dialog.

        Args:
            parent: Parent widget.
        """
        super().__init__(parent)
        self.setWindowTitle("About Lightfall")
        self.setModal(True)
        self.setFixedWidth(400)
        self._setup_ui()

    def _setup_ui(self) -> None:
        """Setup the dialog UI."""
        layout = QVBoxLayout(self)
        layout.setSpacing(16)
        layout.setContentsMargins(32, 32, 32, 32)

        # Logo
        logo_loaded = False
        try:
            from lightfall.resources import get_logo_pixmap

            logo_pixmap = get_logo_pixmap(size=320)
            if not logo_pixmap.isNull():
                logo_label = QLabel()
                logo_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
                logo_label.setPixmap(logo_pixmap)
                layout.addWidget(logo_label)
                logo_loaded = True
                logger.debug("About dialog logo loaded successfully")
            else:
                logger.warning("About dialog logo pixmap is null")
        except Exception as e:
            logger.warning("Failed to load about dialog logo: {}", e)

        # Fallback text if logo couldn't be loaded
        if not logo_loaded:
            fallback_label = QLabel("Lightfall")
            fallback_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            fallback_label.setStyleSheet("font-size: 32px; font-weight: bold;")
            layout.addWidget(fallback_label)

        # App name (below logo since logo includes text)
        # Removed since logo already contains "Lightfall" text

        # Full name
        full_name = QLabel("Advanced Light Source Control System")
        full_name.setAlignment(Qt.AlignmentFlag.AlignCenter)
        full_name.setStyleSheet("color: gray; font-size: 11px;")
        layout.addWidget(full_name)

        layout.addSpacing(8)

        # Description
        description = QLabel(
            "A modern control system for scientific data acquisition "
            "and hardware controls at the ALS facility."
        )
        description.setWordWrap(True)
        description.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(description)

        layout.addSpacing(8)

        # Version info
        version_label = QLabel("Version: Development")
        version_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        version_font = QFont()
        version_font.setPointSize(10)
        version_label.setFont(version_font)
        version_label.setStyleSheet("color: gray;")
        layout.addWidget(version_label)

        layout.addSpacing(16)

        # Copyright
        copyright_label = QLabel("Copyright 2024-2026 ALS Controls Team")
        copyright_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        copyright_label.setStyleSheet("color: gray; font-size: 10px;")
        layout.addWidget(copyright_label)

        # OK button
        button_layout = QHBoxLayout()
        button_layout.addStretch()

        ok_button = QPushButton("OK")
        ok_button.setMinimumWidth(80)
        ok_button.clicked.connect(self.accept)
        ok_button.setDefault(True)
        button_layout.addWidget(ok_button)

        button_layout.addStretch()
        layout.addLayout(button_layout)


def show_about_dialog(parent: QWidget | None = None) -> None:
    """Show the about dialog.

    Args:
        parent: Parent widget.
    """
    dialog = AboutDialog(parent)
    dialog.exec()
