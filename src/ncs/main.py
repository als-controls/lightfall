"""Main entry point for the NCS application."""

import sys

from PySide6.QtWidgets import QApplication, QMainWindow

from ncs.utils.logging import configure_logging, logger


class NCSMainWindow(QMainWindow):
    """Main window for the NCS application."""

    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("NCS - New Control System")
        self.setMinimumSize(1024, 768)


def main() -> int:
    """Run the NCS application."""
    configure_logging(level="DEBUG")
    logger.info("Starting NCS application")

    app = QApplication(sys.argv)
    app.setApplicationName("NCS")
    app.setOrganizationName("ALS")

    window = NCSMainWindow()
    window.show()

    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
