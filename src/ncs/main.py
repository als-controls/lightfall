"""Main entry point for the NCS application."""

from __future__ import annotations

import sys
from typing import TYPE_CHECKING

from PySide6.QtWidgets import QMainWindow

from ncs.core import NCSApplication
from ncs.utils.logging import logger

if TYPE_CHECKING:
    from ncs.config import ConfigManager


class NCSMainWindow(QMainWindow):
    """Main window for the NCS application."""

    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("NCS - New Control System")
        self.setMinimumSize(1024, 768)

        # Access configuration through the application
        app = NCSApplication.get_instance()
        config: ConfigManager = app.services.get(
            __import__("ncs.config", fromlist=["ConfigManager"]).ConfigManager
        )

        # Apply theme from configuration
        theme = config.get("ui.theme", "system")
        logger.debug("Applying theme: {}", theme)


def main() -> int:
    """Run the NCS application."""
    # Get/create the application singleton
    app = NCSApplication.get_instance()

    # Initialize with default settings
    app.initialize(log_level="DEBUG")

    # Create and set main window
    window = NCSMainWindow()
    app.set_main_window(window)

    # Run the application
    return app.run()


if __name__ == "__main__":
    sys.exit(main())
