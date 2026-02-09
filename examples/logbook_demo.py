#!/usr/bin/env python3
"""
Demonstration of the LogbookWidget with protected content regions.

This example shows:
- Creating a logbook with mixed editable and protected content
- Handling protection violation signals
- Switching between raw and WYSIWYG modes
- Unlocking protected regions for editing
"""

import sys

from loguru import logger
from ncs.logbook import LogbookWidget
from PySide6.QtWidgets import (
    QApplication,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

# Configure logging
logger.remove()
logger.add(sys.stderr, level="DEBUG")

# Sample content with protected regions
SAMPLE_CONTENT = """\
# Experiment Log

**Date:** 2024-01-15
**Operator:** Jane Smith

## Objective

Investigate the relationship between temperature and sample conductivity.

<!-- PROTECTED:auto-header -->
**Experiment ID:** EXP-2024-0042
**Start Time:** 2024-01-15 14:30:00 UTC
**Beamline:** 7.0.1
<!-- /PROTECTED:auto-header -->

## Procedure

1. Mount sample on the stage
2. Align beam to sample center
3. Begin temperature ramp

## Observations

Sample shows expected behavior at room temperature.

<!-- PROTECTED:device-snapshot -->
### Device Readings (Auto-captured)

| Device | Value | Units |
|--------|-------|-------|
| motor_x | 45.320 | mm |
| motor_y | -12.540 | mm |
| temperature | 298.5 | K |
| pressure | 1.2e-6 | Torr |
<!-- /PROTECTED:device-snapshot -->

## Notes

- Sample alignment was straightforward
- No issues with beam stability

## Conclusions

*To be completed after analysis.*
"""


class LogbookDemo(QMainWindow):
    """Main window for the logbook demonstration."""

    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Logbook Widget Demo")
        self.setMinimumSize(800, 600)

        # Create central widget
        central = QWidget()
        self.setCentralWidget(central)
        layout = QVBoxLayout(central)

        # Create logbook widget
        self.logbook = LogbookWidget()
        self.logbook.set_content(SAMPLE_CONTENT)
        layout.addWidget(self.logbook)

        # Connect signals
        self.logbook.protection_violated.connect(self._on_protection_violated)
        self.logbook.mode_changed.connect(self._on_mode_changed)
        self.logbook.content_changed.connect(self._on_content_changed)

        # Add unlock button for demo
        unlock_btn = QPushButton("Unlock 'device-snapshot' Region")
        unlock_btn.clicked.connect(self._toggle_device_snapshot_lock)
        layout.addWidget(unlock_btn)
        self._unlock_btn = unlock_btn

        # Show protected regions info
        regions = self.logbook.get_protected_regions()
        logger.info(f"Found {len(regions)} protected regions:")
        for region in regions:
            logger.info(f"  - {region.region_id}: {region.start_offset}-{region.end_offset}")

    def _on_protection_violated(self, region_id: str, position: int) -> None:
        """Handle protection violation."""
        logger.warning(f"Protection violated: {region_id} at position {position}")

        # Show a message to the user
        reply = QMessageBox.question(
            self,
            "Protected Content",
            f"The region '{region_id}' is protected.\n\n"
            "This content was automatically captured by the system "
            "and should not be modified manually.\n\n"
            "Do you want to unlock it for editing?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )

        if reply == QMessageBox.StandardButton.Yes:
            if self.logbook.unlock_region(region_id):
                logger.info(f"Unlocked region: {region_id}")
                QMessageBox.information(
                    self,
                    "Region Unlocked",
                    f"The region '{region_id}' is now unlocked for editing.\n\n"
                    "Changes will be tracked in the audit log.",
                )

    def _on_mode_changed(self, mode: str) -> None:
        """Handle mode changes."""
        logger.info(f"Mode changed to: {mode}")

    def _on_content_changed(self) -> None:
        """Handle content changes."""
        logger.debug("Content changed")

    def _toggle_device_snapshot_lock(self) -> None:
        """Toggle the lock on the device-snapshot region."""
        if self.logbook.is_region_unlocked("device-snapshot"):
            self.logbook.lock_region("device-snapshot")
            self._unlock_btn.setText("Unlock 'device-snapshot' Region")
            logger.info("Locked device-snapshot region")
        else:
            self.logbook.unlock_region("device-snapshot")
            self._unlock_btn.setText("Lock 'device-snapshot' Region")
            logger.info("Unlocked device-snapshot region")


def main() -> None:
    """Run the demo application."""
    app = QApplication(sys.argv)
    app.setApplicationName("Logbook Demo")

    window = LogbookDemo()
    window.show()

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
