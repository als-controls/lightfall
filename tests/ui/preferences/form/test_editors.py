"""Field editors: explicit not-set state versus real falsy values."""
from __future__ import annotations

import pytest
from qtpy.QtCore import Qt
from qtpy.QtWidgets import QComboBox, QLabel, QLineEdit, QToolButton

from lightfall.ui.preferences.form.editors import create_editor
from lightfall.ui.preferences.form.schema import (
    Choice,
    EditorSpec,
    Integer,
    Number,
    Secret,
    SettingField,
    Text,
    Toggle,
    Url,
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
    if kind in ("toggle", "choice"):
        combo = editor.findChild(QComboBox)
        combo.setCurrentIndex(combo.count() - 1)  # emits like a user pick
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


@pytest.mark.parametrize("kind", ["integer", "number"])
def test_invalid_state_is_visible_then_hidden(qtbot, kind):
    editor = make(qtbot, kind)
    editor.show()
    line = editor.findChild(QLineEdit)
    hint = editor.findChild(QLabel)
    assert not hint.isVisible()
    line.setFocus()
    qtbot.keyClicks(line, "999")
    assert not editor.is_valid()
    assert hint.isVisible() and "Out of range" in hint.text() and "60" in hint.text()
    assert line.toolTip() == hint.text()
    line.selectAll()
    qtbot.keyClicks(line, "x")
    assert hint.text().startswith("Not a")
    line.selectAll()
    qtbot.keyClicks(line, "7")
    assert editor.is_valid() and not hint.isVisible()


@pytest.mark.parametrize("text", ["nan", "inf", "-inf"])
def test_numeric_rejects_non_finite(qtbot, text):
    editor = make(qtbot, "number")
    line = editor.findChild(QLineEdit)
    line.setFocus()
    qtbot.keyClicks(line, text)
    assert not editor.is_valid()
    assert editor.value() is None


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


@pytest.mark.parametrize("kind", ["integer", "number"])
def test_number_placeholder(qtbot, kind):
    spec = CASES[kind][0]
    editor = create_editor(spec, SettingField("k", spec, placeholder="4222"))
    qtbot.addWidget(editor)
    line = editor.findChild(QLineEdit)
    assert line.placeholderText() == "4222"
    assert editor.value() is None  # a placeholder is not a value
    assert make(qtbot, kind).findChild(QLineEdit).placeholderText() == "Not set"


def test_toggle_is_combo_with_three_entries(qtbot):
    from lightfall.ui.preferences.form.editors import muted_color

    editor = make(qtbot, "toggle")
    combo = editor.findChild(QComboBox)
    assert [combo.itemText(i) for i in range(combo.count())] == ["Not set", "Off", "On"]
    assert editor.value() is None and combo.currentIndex() == 0
    for index, value in ((1, False), (2, True), (0, None)):
        combo.setCurrentIndex(index)
        assert editor.value() is value
    editor.set_value(False)
    assert combo.currentText() == "Off" and editor.value() is False
    editor.set_value(True)
    assert combo.currentText() == "On" and editor.value() is True
    # "Not set" is muted like Choice's
    assert combo.itemData(0, Qt.ItemDataRole.ForegroundRole) == muted_color(combo)


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
    if kind in ("toggle", "choice"):
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


def _normal_text(widget):
    from qtpy.QtGui import QPalette

    return widget.palette().color(QPalette.ColorGroup.Active, QPalette.ColorRole.Text)


@pytest.mark.parametrize("kind", ["choice", "toggle"])
def test_closed_combo_text_is_muted_only_while_not_set(qtbot, kind):
    """The closed combo shows "Not set" muted; real options stay in normal colour."""
    from qtpy.QtGui import QPalette

    from lightfall.ui.preferences.form.editors import muted_color

    editor = make(qtbot, kind)
    combo = editor.findChild(QComboBox)
    muted = muted_color(combo)

    def shown_muted():
        """Per group (Active, Inactive): is the closed label (QComboBox paints it with Text) muted?"""
        groups = (QPalette.ColorGroup.Active, QPalette.ColorGroup.Inactive)
        return [combo.palette().color(g, QPalette.ColorRole.Text) == muted for g in groups]

    assert combo.currentIndex() == 0 and all(shown_muted())
    combo.setCurrentIndex(combo.count() - 1)  # a user picks a value
    assert not any(shown_muted())
    editor.set_value(None)
    assert all(shown_muted())
    editor.set_value(CASES[kind][2])
    assert not any(shown_muted())
    # Drop-down items: "Not set" muted, real options explicitly not muted.
    editor.set_value(None)
    fg = [combo.itemData(i, Qt.ItemDataRole.ForegroundRole) for i in range(combo.count())]
    assert fg[0] == muted and all(c is not None and c != muted for c in fg[1:])
    # A palette change (theme switch) re-applies it.
    editor.set_value(None)
    editor.setPalette(QPalette())
    assert all(shown_muted())


@pytest.mark.parametrize("kind", ["choice", "toggle"])
def test_read_only_combo_is_muted(qtbot, kind):
    from qtpy.QtGui import QPalette

    from lightfall.ui.preferences.form.editors import muted_color

    editor = make(qtbot, kind)
    combo = editor.findChild(QComboBox)
    editor.set_read_only(True)
    assert not combo.isEnabled()
    muted = muted_color(combo)
    assert muted != _normal_text(editor)
    for role in (QPalette.ColorRole.Text, QPalette.ColorRole.ButtonText):
        assert combo.palette().color(QPalette.ColorGroup.Disabled, role) == muted


def test_muted_falls_back_to_palette_without_a_theme(qtbot, monkeypatch):
    from qtpy.QtGui import QPalette

    from lightfall.ui.preferences.form.editors import muted_color
    from lightfall.ui.theme import ThemeManager

    def no_theme():
        raise RuntimeError("no theme")

    monkeypatch.setattr(ThemeManager, "get_instance", no_theme)
    editor = make(qtbot, "toggle")
    assert muted_color(editor) == editor.palette().color(QPalette.ColorRole.PlaceholderText)


@pytest.mark.parametrize("kind", ["integer", "number", "text"])
def test_read_only_line_edit_looks_different_but_stays_selectable(qtbot, kind):
    from lightfall.ui.preferences.form.editors import muted_color

    editor = make(qtbot, kind)
    editor.set_value(CASES[kind][2])
    editor.resize(240, 30)
    line = editor.findChild(QLineEdit)
    editable = line.grab().toImage()
    normal = _normal_text(line)
    editor.set_read_only(True)
    assert line.isReadOnly() and line.isEnabled()  # text can still be selected and copied
    muted = muted_color(line)
    assert muted != normal
    assert muted.name() in line.styleSheet() and "transparent" in line.styleSheet()
    assert line.grab().toImage() != editable  # it actually renders differently
    editor.set_read_only(False)
    assert line.styleSheet() == ""
    assert line.grab().toImage() == editable


def test_number_editor_shows_unit_suffix(qtbot):
    spec = Integer(5, 60)
    editor = create_editor(spec, SettingField("visualization.update_rate", spec, unit="Hz"))
    qtbot.addWidget(editor)
    assert "Hz" in [lbl.text() for lbl in editor.findChildren(QLabel) if not lbl.isHidden()]


def test_number_editor_without_unit_has_no_suffix(qtbot):
    spec = Integer(5, 60)
    editor = create_editor(spec, SettingField("k", spec))
    qtbot.addWidget(editor)
    assert [lbl.text() for lbl in editor.findChildren(QLabel) if not lbl.isHidden()] == []


@pytest.mark.parametrize(
    ("spec", "start", "key", "expected"),
    [
        (Integer(0, 10, step=3), 4, Qt.Key_Up, 7),
        (Integer(0, 10, step=3), 9, Qt.Key_Up, 10),   # clamped to the maximum
        (Integer(0, 10, step=3), 1, Qt.Key_Down, 0),  # clamped to the minimum
        (Number(0, 1, step=0.1), 0.2, Qt.Key_Up, 0.3),
    ],
)
def test_arrow_keys_step_number(qtbot, spec, start, key, expected):
    editor = create_editor(spec, SettingField("k", spec))
    qtbot.addWidget(editor)
    editor.set_value(start)
    calls = []
    editor.changed.connect(lambda: calls.append(editor.value()))
    line = editor.findChild(QLineEdit)
    qtbot.keyClick(line, key)
    assert editor.value() == pytest.approx(expected)
    assert calls and editor.is_valid()


def test_arrow_keys_do_nothing_when_read_only_or_without_step(qtbot):
    spec = Integer(0, 10, step=1)
    editor = create_editor(spec, SettingField("k", spec))
    qtbot.addWidget(editor)
    editor.set_value(4)
    editor.set_read_only(True)
    qtbot.keyClick(editor.findChild(QLineEdit), Qt.Key_Up)
    assert editor.value() == 4
    plain = Number(0, 10)
    other = create_editor(plain, SettingField("k2", plain))
    qtbot.addWidget(other)
    other.set_value(4.0)
    qtbot.keyClick(other.findChild(QLineEdit), Qt.Key_Up)
    assert other.value() == 4.0


def _hint_text(editor):
    return next(
        (lb.text() for lb in editor.findChildren(QLabel) if lb.text() and not lb.isHidden() and lb is not editor.empty_hint),
        "",
    )


def _type(qtbot, editor, text):
    """Replace the line's text with real key events (so ``textEdited`` fires)."""
    line = editor.findChild(QLineEdit)
    line.setFocus()
    qtbot.keyClicks(line, "x")  # guarantees a real edit even when ``text`` is empty
    line.selectAll()
    qtbot.keyClick(line, Qt.Key.Key_Delete)
    qtbot.keyClicks(line, text)


def make_spec(qtbot, spec):
    editor = create_editor(spec, SettingField("k", spec))
    qtbot.addWidget(editor)
    editor.show()
    return editor


@pytest.mark.parametrize(
    ("text", "valid", "hint"),
    [
        ("https://a.b", True, ""),
        ("ftp://a", False, "Must start with http:// or https://"),
        ("http://", False, "Missing host"),
        ("", True, ""),
    ],
)
def test_url_valid_and_invalid(qtbot, text, valid, hint):
    editor = make_spec(qtbot, Url())
    assert editor.value() is None and editor.is_valid()
    _type(qtbot, editor, text)
    assert editor.is_valid() is valid
    assert _hint_text(editor) == hint
    assert editor.value() == text  # "" is a value, not "not set"


def test_url_not_set_and_empty_behave_like_text(qtbot):
    editor = make_spec(qtbot, Url())
    editor.set_value("")
    assert editor.value() == "" and editor.empty_hint.isVisible()
    editor.set_value(None)
    assert editor.value() is None and not editor.empty_hint.isVisible()


def test_url_custom_scheme(qtbot):
    editor = make_spec(qtbot, Url(schemes=("nats",)))
    _type(qtbot, editor, "nats://h:4222")
    assert editor.is_valid()
    _type(qtbot, editor, "http://h")
    assert not editor.is_valid()
    assert _hint_text(editor) == "Must start with nats://"


def test_secret_masks_and_toggles(qtbot):
    editor = make_spec(qtbot, Secret())
    line = editor.findChild(QLineEdit)
    button = editor.findChild(QToolButton)
    assert line.echoMode() == QLineEdit.EchoMode.Password
    _type(qtbot, editor, "hunter2")
    assert editor.value() == "hunter2"
    button.click()
    assert line.echoMode() == QLineEdit.EchoMode.Normal and button.text() == "Hide"
    button.click()
    assert line.echoMode() == QLineEdit.EchoMode.Password and button.text() == "Show"


def test_secret_not_set_and_empty_distinct(qtbot):
    editor = make_spec(qtbot, Secret())
    assert editor.value() is None
    editor.set_value("")
    assert editor.value() == "" and editor.empty_hint.isVisible()
    editor.set_value(None)
    assert editor.value() is None and not editor.empty_hint.isVisible()


@pytest.mark.parametrize(("typed", "expected"), [("https://a.b ", "https://a.b"), ("   ", "")])
def test_url_value_is_stripped(qtbot, typed, expected):
    editor = make_spec(qtbot, Url())
    _type(qtbot, editor, typed)
    assert editor.value() == expected
    assert editor.is_valid()


def test_text_editor_shows_the_start_of_a_long_value(qtbot):
    editor = create_editor(Url(), SettingField("t.url", Url()))
    qtbot.addWidget(editor)
    editor.set_value("http://logbook.example.org:9000/a/rather/long/path/that/does/not/fit")
    assert editor.findChild(QLineEdit).cursorPosition() == 0
