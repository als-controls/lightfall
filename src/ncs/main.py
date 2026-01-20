"""Main entry point for the NCS application."""

import sys

from loguru import logger
from PySide6.QtWidgets import QApplication, QMainWindow


class NCSMainWindow(QMainWindow):
    """Main window for the NCS application."""

    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("NCS - New Control System")
        self.setMinimumSize(1024, 768)


def main() -> int:
    """Run the NCS application."""
    logger.info("Starting NCS application")

    app = QApplication(sys.argv)
    app.setApplicationName("NCS")
    app.setOrganizationName("ALS")

    window = NCSMainWindow()
    window.show()

    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
