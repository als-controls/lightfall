"""Widget tree serialization for Claude understanding."""

from typing import Any

from PySide6.QtCore import QRect
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QLineEdit,
    QPushButton,
    QRadioButton,
    QTextEdit,
    QWidget,
)


def serialize_widget(widget: QWidget) -> dict[str, Any]:
    """
    Serialize a single widget to a dictionary.

    Args:
        widget: The Qt widget to serialize

    Returns:
        Dictionary containing widget information
    """
    info = {
        "type": type(widget).__name__,
        "objectName": widget.objectName() or f"<unnamed_{type(widget).__name__}>",
        "enabled": widget.isEnabled(),
        "visible": widget.isVisible(),
        "geometry": _serialize_geometry(widget.geometry()),
    }

    # Add type-specific properties
    if hasattr(widget, "text"):
        try:
            info["text"] = widget.text()
        except Exception:
            pass

    if hasattr(widget, "placeholderText"):
        try:
            info["placeholderText"] = widget.placeholderText()
        except Exception:
            pass

    if hasattr(widget, "isChecked"):
        try:
            info["checked"] = widget.isChecked()
        except Exception:
            pass

    if hasattr(widget, "currentText"):
        try:
            info["currentText"] = widget.currentText()
        except Exception:
            pass

    if hasattr(widget, "toPlainText"):
        try:
            info["plainText"] = widget.toPlainText()
        except Exception:
            pass

    # Add interaction hints
    info["interactive"] = _is_interactive(widget)
    info["clickable"] = isinstance(widget, (QPushButton, QCheckBox, QRadioButton))
    info["editable"] = isinstance(widget, (QLineEdit, QTextEdit))

    return info


def serialize_widget_tree(widget: QWidget, max_depth: int = 5, current_depth: int = 0) -> dict[str, Any]:
    """
    Recursively serialize a widget tree to JSON-compatible dictionary.

    Args:
        widget: The root widget to serialize
        max_depth: Maximum depth to traverse
        current_depth: Current recursion depth

    Returns:
        Nested dictionary representing the widget tree
    """
    info = serialize_widget(widget)

    # Add layout information
    layout = widget.layout()
    if layout:
        info["layout"] = {
            "type": type(layout).__name__,
            "spacing": layout.spacing(),
            "margins": {
                "left": layout.contentsMargins().left(),
                "top": layout.contentsMargins().top(),
                "right": layout.contentsMargins().right(),
                "bottom": layout.contentsMargins().bottom(),
            }
        }

    # Recursively add children if not at max depth
    if current_depth < max_depth:
        children = []
        # Use children() to get direct children (findChildren with FindDirectChildrenOnly has issues)
        for child in widget.children():
            if not isinstance(child, QWidget):
                continue
            # Include all widget children - visibility info is in the serialized data
            children.append(serialize_widget_tree(child, max_depth, current_depth + 1))

        if children:
            info["children"] = children
        else:
            info["children"] = []
    else:
        direct_children = [c for c in widget.children() if isinstance(c, QWidget)]
        info["children"] = f"... {len(direct_children)} more"

    return info


def _serialize_geometry(geometry: QRect) -> dict[str, int]:
    """Serialize a QRect to a dictionary."""
    return {
        "x": geometry.x(),
        "y": geometry.y(),
        "width": geometry.width(),
        "height": geometry.height(),
    }


def _is_interactive(widget: QWidget) -> bool:
    """Determine if a widget is interactive (user can interact with it)."""
    if not widget.isEnabled():
        return False

    interactive_types = (
        QPushButton,
        QLineEdit,
        QTextEdit,
        QComboBox,
        QCheckBox,
        QRadioButton,
    )

    return isinstance(widget, interactive_types)


def find_widget_by_name(root: QWidget, object_name: str) -> QWidget | None:
    """
    Find a widget by its object name within a widget tree.

    Args:
        root: The root widget to search from
        object_name: The objectName to search for

    Returns:
        The widget if found, None otherwise
    """
    return root.findChild(QWidget, object_name)


def get_widget_summary(widget: QWidget) -> str:
    """
    Get a human-readable summary of a widget.

    Args:
        widget: The widget to summarize

    Returns:
        String summary like "QPushButton 'Submit' (enabled, visible)"
    """
    type_name = type(widget).__name__
    obj_name = widget.objectName() or "<unnamed>"

    text = ""
    if hasattr(widget, "text"):
        try:
            text = f" '{widget.text()}'"
        except Exception:
            pass

    state = []
    if widget.isEnabled():
        state.append("enabled")
    else:
        state.append("disabled")

    if widget.isVisible():
        state.append("visible")
    else:
        state.append("hidden")

    return f"{type_name} {obj_name}{text} ({', '.join(state)})"
