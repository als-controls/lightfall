"""Go To Position confirmation dialog.

Provides a confirmation dialog before moving motors to clicked positions
in visualization widgets.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QDialogButtonBox,
    QFormLayout,
    QLabel,
    QVBoxLayout,
)

from lucid.ui.dialogs.base import LucidDialog

if TYPE_CHECKING:
    from PySide6.QtWidgets import QWidget


class GoToPositionDialog(LucidDialog):
    """Confirmation dialog for motor movement from visualizations.

    Shows:
    - Motor name
    - Current position
    - Target position
    - Distance to move
    - Units (if available)
    - "Don't ask again" checkbox

    Example:
        >>> dialog = GoToPositionDialog(
        ...     motor_name="motor_x",
        ...     current_position=10.0,
        ...     target_position=25.5,
        ...     units="mm",
        ...     parent=self,
        ... )
        >>> if dialog.exec():
        ...     # User confirmed, proceed with move
        ...     if dialog.dont_ask_again:
        ...         # Skip confirmation in future
    """

    def __init__(
        self,
        motor_name: str,
        current_position: float | tuple[float | None, float | None] | None,
        target_position: float | tuple[float, float],
        units: str = "",
        is_2d: bool = False,
        motor_names: tuple[str, str] | None = None,
        parent: QWidget | None = None,
    ) -> None:
        """Initialize the dialog.

        Args:
            motor_name: Display name of the motor (or "x, y" for 2D).
            current_position: Current motor position (or tuple for 2D).
            target_position: Target position to move to (or tuple for 2D).
            units: Engineering units (e.g., "mm", "deg").
            is_2d: If True, this is a 2D move with X and Y motors.
            motor_names: Tuple of (x_name, y_name) for 2D moves.
            parent: Parent widget.
        """
        super().__init__(parent)

        self._motor_name = motor_name
        self._current_position = current_position
        self._target_position = target_position
        self._units = units
        self._is_2d = is_2d
        self._motor_names = motor_names
        self._dont_ask_again = False

        self._setup_ui()

    def _setup_ui(self) -> None:
        """Setup the dialog UI."""
        self.setWindowTitle("Confirm Motor Move")
        self.setMinimumWidth(350)

        layout = QVBoxLayout(self)

        # Header with warning icon and message
        header = QLabel(
            f"<b>Move motor to clicked position?</b>"
        )
        header.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(header)

        layout.addSpacing(10)

        # Position details
        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)

        if self._is_2d and self._motor_names:
            # 2D move - show both motors
            x_name, y_name = self._motor_names

            form.addRow("Motors:", QLabel(f"<b>{x_name}</b>, <b>{y_name}</b>"))

            # Current positions
            if isinstance(self._current_position, tuple):
                x_cur, y_cur = self._current_position
                x_cur_str = f"{x_cur:.4g}" if x_cur is not None else "Unknown"
                y_cur_str = f"{y_cur:.4g}" if y_cur is not None else "Unknown"
                form.addRow("Current:", QLabel(f"({x_cur_str}, {y_cur_str})"))
            else:
                form.addRow("Current:", QLabel("Unknown"))

            # Target positions
            if isinstance(self._target_position, tuple):
                x_tgt, y_tgt = self._target_position
                form.addRow("Target:", QLabel(f"<b>({x_tgt:.4g}, {y_tgt:.4g})</b>"))

                # Distance
                if isinstance(self._current_position, tuple):
                    x_cur, y_cur = self._current_position
                    if x_cur is not None and y_cur is not None:
                        x_dist = abs(x_tgt - x_cur)
                        y_dist = abs(y_tgt - y_cur)
                        form.addRow(
                            "Distance:",
                            QLabel(f"X: {x_dist:.4g}, Y: {y_dist:.4g}"),
                        )
        else:
            # 1D move
            form.addRow("Motor:", QLabel(f"<b>{self._motor_name}</b>"))

            # Current position
            if self._current_position is not None and not isinstance(
                self._current_position, tuple
            ):
                current_str = f"{self._current_position:.4g}"
                if self._units:
                    current_str += f" {self._units}"
                form.addRow("Current:", QLabel(current_str))
            else:
                form.addRow("Current:", QLabel("Unknown"))

            # Target position
            if not isinstance(self._target_position, tuple):
                target_str = f"<b>{self._target_position:.4g}</b>"
                if self._units:
                    target_str += f" {self._units}"
                form.addRow("Target:", QLabel(target_str))

                # Distance
                if (
                    self._current_position is not None
                    and not isinstance(self._current_position, tuple)
                ):
                    distance = abs(self._target_position - self._current_position)
                    dist_str = f"{distance:.4g}"
                    if self._units:
                        dist_str += f" {self._units}"
                    form.addRow("Distance:", QLabel(dist_str))

        layout.addLayout(form)

        layout.addSpacing(15)

        # Don't ask again checkbox
        self._dont_ask_checkbox = QCheckBox("Don't ask again for this session")
        self._dont_ask_checkbox.setToolTip(
            "Skip this confirmation dialog for future moves in this session"
        )
        layout.addWidget(self._dont_ask_checkbox)

        layout.addSpacing(10)

        # Dialog buttons
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Move")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    @property
    def dont_ask_again(self) -> bool:
        """Whether the user checked 'Don't ask again'."""
        return self._dont_ask_checkbox.isChecked()
