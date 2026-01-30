"""Dialog for creating new user-defined Bluesky plans.

Provides a simple dialog for entering plan name and description,
with validation to ensure the name is a valid Python identifier
and doesn't conflict with existing plans.
"""

from __future__ import annotations

import keyword
from pathlib import Path
from typing import TYPE_CHECKING

from PySide6.QtCore import Slot
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QVBoxLayout,
    QWidget,
)

if TYPE_CHECKING:
    pass


class CreatePlanDialog(QDialog):
    """Dialog for creating a new user plan.

    Prompts for:
    - Plan name (validated as Python identifier)
    - Description (optional)

    Shows a preview of the file path that will be created.

    Example:
        >>> dialog = CreatePlanDialog(parent)
        >>> if dialog.exec() == QDialog.Accepted:
        ...     name = dialog.get_plan_name()
        ...     desc = dialog.get_description()
        ...     # Create the plan file
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        """Initialize the dialog.

        Args:
            parent: Parent widget.
        """
        super().__init__(parent)
        self._plans_dir = Path.home() / "lucid" / "plans"
        self._setup_ui()
        self._connect_signals()

    def _setup_ui(self) -> None:
        """Set up the dialog UI."""
        self.setWindowTitle("Create New Plan")
        self.setMinimumWidth(400)

        layout = QVBoxLayout(self)

        # Form layout for inputs
        form = QFormLayout()

        # Plan name input
        self._name_input = QLineEdit()
        self._name_input.setPlaceholderText("e.g., my_scan, grid_alignment")
        form.addRow("Plan Name:", self._name_input)

        # Validation label
        self._validation_label = QLabel()
        self._validation_label.setStyleSheet("color: red;")
        self._validation_label.setWordWrap(True)
        form.addRow("", self._validation_label)

        # Description input
        self._description_input = QPlainTextEdit()
        self._description_input.setPlaceholderText(
            "Optional description of what this plan does..."
        )
        self._description_input.setMaximumHeight(80)
        form.addRow("Description:", self._description_input)

        # File path preview
        self._path_label = QLabel()
        self._path_label.setStyleSheet("color: gray; font-style: italic;")
        self._path_label.setWordWrap(True)
        form.addRow("File:", self._path_label)

        layout.addLayout(form)

        # Buttons
        self._button_box = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        self._button_box.accepted.connect(self.accept)
        self._button_box.rejected.connect(self.reject)
        self._button_box.button(QDialogButtonBox.StandardButton.Ok).setText("Create")
        self._button_box.button(QDialogButtonBox.StandardButton.Ok).setEnabled(False)

        layout.addWidget(self._button_box)

        # Update preview with empty state
        self._update_preview()

    def _connect_signals(self) -> None:
        """Connect widget signals."""
        self._name_input.textChanged.connect(self._on_name_changed)

    @Slot(str)
    def _on_name_changed(self, text: str) -> None:
        """Handle name input changes.

        Args:
            text: Current input text.
        """
        is_valid = self._validate_name(text)
        self._button_box.button(QDialogButtonBox.StandardButton.Ok).setEnabled(is_valid)
        self._update_preview()

    def _validate_name(self, name: str) -> bool:
        """Validate the plan name.

        Checks that the name is:
        - Not empty
        - A valid Python identifier
        - Not a Python keyword
        - Not already used by an existing file

        Args:
            name: Plan name to validate.

        Returns:
            True if valid.
        """
        if not name:
            self._validation_label.setText("")
            return False

        # Check Python identifier
        if not name.isidentifier():
            self._validation_label.setText(
                "Name must be a valid Python identifier "
                "(letters, digits, underscores; cannot start with digit)"
            )
            return False

        # Check not a keyword
        if keyword.iskeyword(name):
            self._validation_label.setText(f"'{name}' is a Python keyword")
            return False

        # Check file doesn't exist
        file_path = self._plans_dir / f"{name}.py"
        if file_path.exists():
            self._validation_label.setText(f"Plan '{name}' already exists")
            return False

        self._validation_label.setText("")
        return True

    def _update_preview(self) -> None:
        """Update the file path preview."""
        name = self._name_input.text().strip()
        if name:
            file_path = self._plans_dir / f"{name}.py"
            self._path_label.setText(str(file_path))
        else:
            self._path_label.setText(str(self._plans_dir / "<name>.py"))

    def get_plan_name(self) -> str:
        """Get the entered plan name.

        Returns:
            Plan name string.
        """
        return self._name_input.text().strip()

    def get_description(self) -> str:
        """Get the entered description.

        Returns:
            Description string (may be empty).
        """
        return self._description_input.toPlainText().strip()
