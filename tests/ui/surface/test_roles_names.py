"""Widget roles and accessible names (the agent UI surface)."""

from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFormLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QToolButton,
    QWidget,
)

from lightfall.ui.surface.names import accessible_name
from lightfall.ui.surface.roles import role_of


def test_button_name_is_text_without_mnemonic(qtbot):
    b = QPushButton("&Run")
    qtbot.addWidget(b)
    assert role_of(b) == "button"
    assert accessible_name(b) == "Run"


def test_icon_only_tool_button_uses_accessible_name_then_tooltip(qtbot):
    b = QToolButton()
    qtbot.addWidget(b)
    b.setToolTip("Devices")
    assert accessible_name(b) == "Devices"
    b.setAccessibleName("Device tree")
    assert accessible_name(b) == "Device tree"


def test_field_named_by_form_label(qtbot):
    w = QWidget()
    qtbot.addWidget(w)
    form = QFormLayout(w)
    start = QDoubleSpinBox()
    form.addRow("Start:", start)
    assert role_of(start) == "field"
    assert accessible_name(start) == "Start"


def test_field_named_by_buddy(qtbot):
    w = QWidget()
    qtbot.addWidget(w)
    label, edit = QLabel("&Title", w), QLineEdit(w)
    label.setBuddy(edit)
    assert accessible_name(edit) == "Title"


def test_placeholder_is_last_resort_and_object_name_is_never_a_name(qtbot):
    e = QLineEdit()
    qtbot.addWidget(e)
    e.setObjectName("searchBox")
    assert accessible_name(e) == ""
    e.setPlaceholderText("Search devices")
    assert accessible_name(e) == "Search devices"


def test_roles(qtbot):
    assert role_of(QCheckBox("x")) == "checkbox"
    assert role_of(QComboBox()) == "combo"
    toggle = QPushButton("t")
    toggle.setCheckable(True)
    assert role_of(toggle) == "toggle"
    plain = QWidget()
    child = QWidget(plain)
    assert role_of(child) is None
