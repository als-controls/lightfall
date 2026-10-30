"""Field editors: explicit not-set state versus real falsy values."""
from __future__ import annotations

import pytest
from qtpy.QtCore import Qt
from qtpy.QtWidgets import QAbstractButton, QComboBox, QLineEdit

from lightfall.ui.preferences.form.editors import create_editor
from lightfall.ui.preferences.form.schema import (
    Choice,
    EditorSpec,
    Integer,
    Number,
    SettingField,
    Text,
    Toggle,
)

OPTIONS = [("a", "Alpha"), ("", "Blank"), ("b", "Beta")]

# kind id -> (spec, falsy value, truthy value)
CASES = {
    "toggle": (Toggle(), False, True),
    "integer": (Integer(minimum=0, maximum=60), 0, 5),
    "number": (Number(minimum=0, maximum=60), 0.0, 2.5),
    "text": (Text(), "", "abc"),
    "choice": (Choice(OPTIONS), "", "b"),
}


def make(qtbot, kind):
    spec = CASES[kind][0]
    editor = create_editor(spec, SettingField("k", spec))
    qtbot.addWidget(editor)
    return editor


def user_edit(qtbot, editor, kind):
    """Perform one real user edit that yields a value."""
    if kind == "toggle":
        buttons = {b.text(): b for b in editor.findChildren(QAbstractButton)}
        qtbot.mouseClick(buttons["On"], Qt.LeftButton)
    elif kind == "choice":
        combo = editor.findChild(QComboBox)
        combo.setCurrentIndex(3)  # emits like a user pick
    else:
        line = editor.findChild(QLineEdit)
        line.setFocus()
        qtbot.keyClicks(line, "5")


@pytest.mark.parametrize("kind", CASES)
def test_none_round_trips(qtbot, kind):
    editor = make(qtbot, kind)
    editor.set_value(CASES[kind][2])
    editor.set_value(None)
    assert editor.value() is None
    assert editor.is_valid()


@pytest.mark.parametrize("kind", CASES)
def test_falsy_values_round_trip(qtbot, kind):
    editor = make(qtbot, kind)
    falsy = CASES[kind][1]
    editor.set_value(falsy)
    assert editor.value() is not None
    assert editor.value() == falsy
    assert type(editor.value()) is type(falsy)


@pytest.mark.parametrize("kind", CASES)
def test_set_value_does_not_emit_changed(qtbot, kind):
    editor = make(qtbot, kind)
    with qtbot.assertNotEmitted(editor.changed):
        editor.set_value(CASES[kind][2])
        editor.set_value(CASES[kind][1])
        editor.set_value(None)


@pytest.mark.parametrize("kind", CASES)
def test_user_edit_emits_changed(qtbot, kind):
    editor = make(qtbot, kind)
    with qtbot.waitSignal(editor.changed, timeout=500):
        user_edit(qtbot, editor, kind)
    assert editor.value() is not None


@pytest.mark.parametrize("kind", ["integer", "number"])
def test_numeric_rejects_out_of_range(qtbot, kind):
    editor = make(qtbot, kind)
    line = editor.findChild(QLineEdit)
    editor.set_value(5)
    line.setFocus()
    line.selectAll()
    qtbot.keyClicks(line, "999")
    assert not editor.is_valid()
    assert editor.value() == 9  # last valid keystroke prefix (99 > 60)
    line.selectAll()
    qtbot.keyClicks(line, "7")
    assert editor.is_valid()
    assert editor.value() == 7


def test_integer_rejects_fraction_and_garbage_keeps_last_valid(qtbot):
    editor = make(qtbot, "integer")
    line = editor.findChild(QLineEdit)
    line.setFocus()
    qtbot.keyClicks(line, "1.5x")
    assert not editor.is_valid()
    assert editor.value() == 1  # last valid prefix


def test_numeric_clear_means_not_set(qtbot):
    editor = make(qtbot, "integer")
    editor.set_value(5)
    line = editor.findChild(QLineEdit)
    line.setFocus()
    line.selectAll()
    with qtbot.waitSignal(editor.changed, timeout=500):
        qtbot.keyClick(line, Qt.Key_Delete)
    assert editor.value() is None
    assert editor.is_valid()


def test_text_empty_hint_distinguishes_empty_from_not_set(qtbot):
    spec = Text()
    editor = create_editor(spec, SettingField("k", spec, placeholder="host"))
    qtbot.addWidget(editor)
    editor.show()
    line = editor.findChild(QLineEdit)
    editor.set_value(None)
    assert line.placeholderText() == "host"
    assert not editor.empty_hint.isVisible()
    editor.set_value("")
    assert line.placeholderText() == ""
    assert editor.empty_hint.isVisible()
    editor.set_value("x")
    assert not editor.empty_hint.isVisible()


def test_text_default_placeholder(qtbot):
    editor = make(qtbot, "text")
    assert editor.findChild(QLineEdit).placeholderText() == "Not set"


def test_toggle_three_states(qtbot):
    editor = make(qtbot, "toggle")
    buttons = {b.text(): b for b in editor.findChildren(QAbstractButton)}
    assert set(buttons) == {"Not set", "Off", "On"}
    assert editor.value() is None and buttons["Not set"].isChecked()
    editor.set_value(False)
    assert buttons["Off"].isChecked() and editor.value() is False
    editor.set_value(True)
    assert buttons["On"].isChecked() and editor.value() is True


def test_choice_not_set_entry_is_distinct_from_blank_option(qtbot):
    editor = make(qtbot, "choice")
    combo = editor.findChild(QComboBox)
    assert combo.itemText(0) == "Not set"
    assert [combo.itemText(i) for i in range(1, 4)] == ["Alpha", "Blank", "Beta"]
    assert editor.value() is None
    editor.set_value("")
    assert combo.currentIndex() == 2
    assert editor.value() == ""


def test_choice_options_callable_evaluated_at_creation(qtbot):
    calls = []

    def opts():
        calls.append(1)
        return [("x", "X")]

    spec = Choice(opts)
    editor = create_editor(spec, SettingField("k", spec))
    qtbot.addWidget(editor)
    assert len(calls) == 1
    editor.set_value("x")
    assert editor.value() == "x"
    assert len(calls) == 1


@pytest.mark.parametrize("kind", CASES)
def test_read_only_blocks_input(qtbot, kind):
    editor = make(qtbot, kind)
    editor.show()
    editor.set_read_only(True)
    before = editor.value()
    if kind == "toggle":
        buttons = {b.text(): b for b in editor.findChildren(QAbstractButton)}
        qtbot.mouseClick(buttons["On"], Qt.LeftButton)
    elif kind == "choice":
        combo = editor.findChild(QComboBox)
        qtbot.keyClick(combo, Qt.Key_Down)
    else:
        line = editor.findChild(QLineEdit)
        line.setFocus()
        qtbot.keyClicks(line, "5")
    assert editor.value() == before
    editor.set_read_only(False)
    user_edit(qtbot, editor, kind)
    assert editor.value() != before


def test_unknown_kind_raises():
    spec = EditorSpec("slider")
    with pytest.raises(ValueError):
        create_editor(spec, SettingField("k", spec))
