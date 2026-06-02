"""Dialog for editing queued plan parameters.

Allows users to modify the priority and parameters of a pending
procedure in the queue before it executes.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from PySide6.QtCore import Signal, Slot
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from lucid.utils.logging import logger

if TYPE_CHECKING:
    from lucid.acquire.engine.base import PrioritizedProcedure

try:
    from pyqtgraph.parametertree import Parameter, ParameterTree

    HAS_PYQTGRAPH = True
except ImportError:
    HAS_PYQTGRAPH = False
    Parameter = None
    ParameterTree = None


class PlanEditDialog(QDialog):
    """Dialog for editing a queued procedure's priority and parameters.

    Provides a priority spinbox and a parameter editor for modifying
    the procedure before execution.

    Signals:
        changes_saved(str, int, dict): Emitted when changes are confirmed.
            Args are (procedure_id, new_priority, new_kwargs).

    Example:
        >>> dialog = PlanEditDialog(procedure, parent)
        >>> if dialog.exec() == QDialog.DialogCode.Accepted:
        ...     proc_id, priority, kwargs = dialog.get_changes()
        ...     engine.update_priority(proc_id, priority)
    """

    changes_saved = Signal(str, int, dict)  # procedure_id, new_priority, new_kwargs

    def __init__(
        self,
        procedure: PrioritizedProcedure,
        parent: QWidget | None = None,
    ) -> None:
        """Initialize the edit dialog.

        Args:
            procedure: The procedure to edit.
            parent: Parent widget.
        """
        super().__init__(parent)
        self._procedure = procedure
        self._original_priority = procedure.priority
        self._original_kwargs = dict(procedure.kwargs)
        self._setup_ui()

    def _setup_ui(self) -> None:
        """Set up the dialog UI."""
        self.setWindowTitle(f"Edit: {self._procedure.name}")
        self.setMinimumSize(500, 400)

        layout = QVBoxLayout(self)

        # Header info
        header_layout = QFormLayout()

        id_label = QLabel(self._procedure.id[:16] + "...")
        id_label.setToolTip(self._procedure.id)
        header_layout.addRow("ID:", id_label)

        name_label = QLabel(self._procedure.name)
        header_layout.addRow("Plan:", name_label)

        submitted_label = QLabel(
            self._procedure.submitted_at.strftime("%Y-%m-%d %H:%M:%S")
        )
        header_layout.addRow("Submitted:", submitted_label)

        layout.addLayout(header_layout)

        # Priority editor
        priority_layout = QHBoxLayout()
        priority_layout.addWidget(QLabel("Priority:"))

        self._priority_spin = QSpinBox()
        self._priority_spin.setRange(-100, 100)
        self._priority_spin.setValue(self._procedure.priority)
        self._priority_spin.setToolTip(
            "Lower values = higher priority.\n"
            "Default is 1. Use 0 or negative for high priority."
        )
        priority_layout.addWidget(self._priority_spin)
        priority_layout.addStretch()

        layout.addLayout(priority_layout)

        # Parameters section
        params_label = QLabel("Parameters:")
        params_label.setStyleSheet("font-weight: bold;")
        layout.addWidget(params_label)

        if HAS_PYQTGRAPH and self._procedure.kwargs:
            # Use ParameterTree for editing
            self._param_tree = ParameterTree(showHeader=False)
            self._root_param = self._build_parameter_tree()
            self._param_tree.setParameters(self._root_param, showTop=False)
            layout.addWidget(self._param_tree, 1)
            self._json_edit = None
        else:
            # Fall back to JSON text edit
            self._param_tree = None
            self._root_param = None
            self._json_edit = QPlainTextEdit()
            self._json_edit.setPlainText(
                json.dumps(self._procedure.kwargs, indent=2, default=str)
            )
            layout.addWidget(self._json_edit, 1)

        # Button box
        self._button_box = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel
        )
        self._button_box.accepted.connect(self._on_save)
        self._button_box.rejected.connect(self.reject)
        layout.addWidget(self._button_box)

    def _build_parameter_tree(self) -> Parameter:
        """Build a pyqtgraph Parameter tree from kwargs.

        Returns:
            Root parameter group.
        """
        param_specs = []

        for name, value in self._procedure.kwargs.items():
            spec = self._value_to_param_spec(name, value)
            param_specs.append(spec)

        return Parameter.create(
            name="parameters",
            type="group",
            children=param_specs,
        )

    def _value_to_param_spec(self, name: str, value: Any) -> dict[str, Any]:
        """Convert a value to a parameter specification.

        Args:
            name: Parameter name.
            value: Parameter value.

        Returns:
            Parameter spec dict for pyqtgraph.
        """
        spec: dict[str, Any] = {"name": name}

        if isinstance(value, bool):
            spec["type"] = "bool"
            spec["value"] = value
        elif isinstance(value, int):
            spec["type"] = "int"
            spec["value"] = value
        elif isinstance(value, float):
            spec["type"] = "float"
            spec["value"] = value
        elif isinstance(value, str):
            spec["type"] = "str"
            spec["value"] = value
        elif isinstance(value, (list, tuple)):
            # Convert to comma-separated string for editing
            spec["type"] = "text"
            spec["value"] = ", ".join(str(v) for v in value)
        elif value is None:
            spec["type"] = "str"
            spec["value"] = ""
        else:
            # Complex types as JSON string
            spec["type"] = "text"
            spec["value"] = json.dumps(value, default=str)

        return spec

    def _get_param_value(self, name: str, original_value: Any, new_value: Any) -> Any:
        """Convert edited parameter value back to original type.

        Args:
            name: Parameter name.
            original_value: Original value for type inference.
            new_value: Edited value (usually string).

        Returns:
            Value converted to appropriate type.
        """
        if isinstance(original_value, bool):
            return bool(new_value)
        elif isinstance(original_value, int):
            return int(new_value)
        elif isinstance(original_value, float):
            return float(new_value)
        elif isinstance(original_value, str):
            return str(new_value)
        elif isinstance(original_value, (list, tuple)):
            # Parse comma-separated string back to list
            if isinstance(new_value, str):
                if not new_value.strip():
                    return []
                parts = [p.strip() for p in new_value.split(",")]
                # Try to preserve element types
                if original_value and len(original_value) > 0:
                    elem_type = type(original_value[0])
                    try:
                        return [elem_type(p) for p in parts]
                    except (ValueError, TypeError):
                        pass
                return parts
            return new_value
        elif original_value is None:
            # If originally None and now empty string, keep as None
            if isinstance(new_value, str) and not new_value.strip():
                return None
            return new_value
        else:
            # Try to parse as JSON for complex types
            if isinstance(new_value, str):
                try:
                    return json.loads(new_value)
                except json.JSONDecodeError:
                    pass
            return new_value

    def get_changes(self) -> tuple[str, int, dict[str, Any]]:
        """Get the edited values.

        Returns:
            Tuple of (procedure_id, new_priority, new_kwargs).
        """
        new_priority = self._priority_spin.value()

        if self._root_param is not None:
            # Get values from ParameterTree
            new_kwargs = {}
            for child in self._root_param.children():
                name = child.name()
                edited_value = child.value()
                original_value = self._original_kwargs.get(name)
                new_kwargs[name] = self._get_param_value(name, original_value, edited_value)
        elif self._json_edit is not None:
            # Parse from JSON text
            try:
                new_kwargs = json.loads(self._json_edit.toPlainText())
            except json.JSONDecodeError:
                logger.warning("Invalid JSON in parameter editor, using original values")
                new_kwargs = dict(self._original_kwargs)
        else:
            new_kwargs = dict(self._original_kwargs)

        return self._procedure.id, new_priority, new_kwargs

    def has_changes(self) -> bool:
        """Check if any values were modified.

        Returns:
            True if priority or parameters changed.
        """
        proc_id, new_priority, new_kwargs = self.get_changes()
        return new_priority != self._original_priority or new_kwargs != self._original_kwargs

    @Slot()
    def _on_save(self) -> None:
        """Handle save button click."""
        if self.has_changes():
            proc_id, priority, kwargs = self.get_changes()
            self.changes_saved.emit(proc_id, priority, kwargs)
            logger.debug(f"Saved changes for procedure {proc_id[:8]}")
        self.accept()
