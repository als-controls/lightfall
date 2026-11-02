"""Settings -> Devices rows editor: one scope's own rows plus greyed inherited rows."""

from __future__ import annotations

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QCheckBox, QLabel, QMessageBox

from lightfall.devices.backend_types.registry import BackendTypeRegistry
from lightfall.devices.backend_types.rows import EXCLUSIVE_RESTORE_KEY, BackendRow
from lightfall.ui.preferences import device_rows
from lightfall.ui.preferences.form.editors import muted_color
from lightfall.ui.preferences.form.schema import Custom, SettingField
from tests.devices.backend_types.excl_type import ExclType, no_session_mode  # noqa: F401

ORIGIN = "7.0.1 (beamline)"


class _Asked(list):
    answer: dict


@pytest.fixture()
def asked(monkeypatch):
    """Answers for the exclusive confirm; records (text) of each question."""
    calls = _Asked()
    answer = {"value": QMessageBox.StandardButton.Yes}

    def question(_parent, _title, text, *_a, **_k):
        calls.append(text)
        return answer["value"]

    monkeypatch.setattr(QMessageBox, "question", staticmethod(question))
    calls.answer = answer
    return calls


@pytest.fixture()
def editor(qtbot, monkeypatch, asked, no_session_mode):  # noqa: F811
    BackendTypeRegistry.reset()
    BackendTypeRegistry.get_instance().register(ExclType())
    monkeypatch.setattr(device_rows, "_device_backend_plugin_names", lambda: [])
    ed = device_rows.device_rows_editor(SettingField("device_backends", Custom(device_rows.device_rows_editor)))
    qtbot.addWidget(ed)
    ed.edits = []
    ed.changed.connect(lambda: ed.edits.append(1))
    yield ed
    BackendTypeRegistry.reset()


def _row(type_id, name, enabled=True, config=None, rid=None):
    row = BackendRow(type_id, name, enabled=enabled, config=dict(config or {}))
    if rid:
        row.id = rid
    return row


def _own(*rows):
    return {r.id: r.to_dict() for r in rows}


def _inherited(*rows, origin=ORIGIN):
    return {r.id: (r.to_dict(), origin) for r in rows}


def _load(ed, own=None, inherited=None, restore=None):
    ed.set_value(own)
    ed.set_inherited(inherited or {})
    ed.set_companion_values({EXCLUSIVE_RESTORE_KEY: restore})
    ed.edits.clear()


def _enabled(ed):
    shown = {s.row.id: s.row.enabled for s in ed._shown}
    return [shown[rid] for rid in ed.row_ids()]


def _three_rows(ed):
    """a (inherited mock), b (own mock), sim (own exclusive, off)."""
    a = _row("mock", "a", rid="a")
    b = _row("mock", "b", rid="b")
    sim = _row("excl", "sim", enabled=False, config={"addr": "127.0.0.1"}, rid="sim")
    _load(ed, own=_own(b, sim), inherited=_inherited(a))
    return a, b, sim


def test_full_width_and_columns(editor):
    assert editor.full_width
    headers = [editor.table.horizontalHeaderItem(c).text() for c in range(editor.table.columnCount())]
    assert headers == ["", "Name", "Type", "From", "Details"]


def test_shows_own_and_inherited_rows_with_origin(editor):
    a = _row("mock", "a", rid="a")
    b = _row("mock", "b", rid="b")
    c = _row("mock", "c", rid="c")
    b_off = _row("mock", "b", enabled=False, rid="b")
    _load(editor, own=_own(b_off, c), inherited=_inherited(a, b))
    assert editor.row_ids() == ["a", "b", "c"]
    assert editor.cell_text("a", "From") == ORIGIN
    assert editor.cell_text("b", "From") == f"{ORIGIN}, changed here"
    assert editor.cell_text("c", "From") == "Here"
    assert editor.cell_text("a", "Type") == "Mock"
    assert editor.cell_text("c", "Name") == "c"
    muted = muted_color(editor.table)
    assert editor.table.item(0, 1).foreground().color() == muted
    assert editor.table.item(2, 1).foreground().color() != muted
    assert editor.edits == []


def test_unticking_inherited_row_writes_disabled_override(editor):
    a = _row("mock", "a", rid="a")
    _load(editor, inherited=_inherited(a))
    editor.set_checked("a", False)
    assert editor.edits == [1]
    assert editor.value() == {"a": {**a.to_dict(), "enabled": False}}
    assert editor.cell_text("a", "From") == f"{ORIGIN}, changed here"


def test_retick_inherited_row_leaves_scope_unset(editor):
    a = _row("mock", "a", rid="a")
    _load(editor, inherited=_inherited(a))
    editor.set_checked("a", False)
    editor.set_checked("a", True)
    assert editor.value() is None
    assert editor.edits == [1, 1]


def test_override_here_then_edit_name(editor):
    a = _row("mock", "a", rid="a")
    _load(editor, inherited=_inherited(a))
    editor.select("a")
    assert not editor.override_button.isHidden()
    assert not editor.pane.isEnabled()
    assert editor.detail_group.title() == "a (Mock)"
    editor.override_button.click()
    assert editor.edits == [1]
    assert editor.pane.isEnabled()
    assert editor.override_button.isHidden()
    editor.name_edit.setText("renamed")
    assert editor.edits == [1, 1]
    assert editor.value() == {"a": {**a.to_dict(), "name": "renamed"}}
    assert editor.cell_text("a", "Name") == "renamed"
    assert editor.cell_text("a", "From") == f"{ORIGIN}, changed here"
    assert editor.detail_group.title() == "renamed (Mock)"


def test_config_edit_updates_own_row(editor):
    c = _row("mock", "c", config={"include_noisy": True}, rid="c")
    _load(editor, own=_own(c))
    editor.select("c")
    editor.stack.currentWidget().findChild(QCheckBox).setChecked(False)
    assert editor.edits == [1]
    assert editor.value()["c"]["config"] == {"include_noisy": False}
    assert editor.cell_text("c", "Details") == "quiet devices only"


def test_use_inherited_drops_override(editor):
    a = _row("mock", "a", rid="a")
    b = _row("mock", "b", rid="b")
    _load(editor, own=_own(_row("mock", "b", enabled=False, rid="b")), inherited=_inherited(a, b))
    editor.select("a")
    assert not editor.remove_button.isEnabled()
    editor.select("b")
    assert editor.remove_button.isEnabled()
    assert editor.remove_button.text() == "Use inherited"
    editor.remove_button.click()
    assert editor.value() is None
    assert editor.edits == [1]
    assert editor.row_ids() == ["a", "b"]
    assert editor.cell_text("b", "From") == ORIGIN


def test_add_row_gets_unique_name_and_is_own(editor):
    mock = _row("mock", "mock", rid="m1")
    _load(editor, own=_own(_row("mock", "mock-2", rid="m2")), inherited=_inherited(mock))
    action = next(a for a in editor.add_button.menu().actions() if a.text() == "Mock")
    action.trigger()
    assert editor.edits == [1]
    new_id = editor.row_ids()[-1]
    assert new_id not in ("m1", "m2")
    value = editor.value()
    assert value[new_id]["name"] == "mock-3"
    assert value[new_id]["enabled"] is True
    assert editor.cell_text(new_id, "From") == "Here"
    assert editor.remove_button.text() == "Remove"


def test_ticking_exclusive_confirms_and_records_restore(editor, asked):
    a, b, sim = _three_rows(editor)
    editor.set_checked("sim", True)
    assert asked == ["Excl mode disables the other 2 device sources. Continue?"]
    assert _enabled(editor) == [False, False, True]
    assert editor.edits == [1]
    assert editor.companion_values() == {EXCLUSIVE_RESTORE_KEY: {"a": True, "b": True}}
    value = editor.value()
    assert value["a"]["enabled"] is False  # an override of the inherited row
    assert value["sim"]["enabled"] is True


def test_cancel_keeps_exclusive_off(editor, asked):
    asked.answer["value"] = QMessageBox.StandardButton.No
    _three_rows(editor)
    before = editor.value()
    editor.set_checked("sim", True)
    assert _enabled(editor) == [True, True, False]
    assert editor.table.item(2, 0).checkState() == Qt.CheckState.Unchecked
    assert editor.value() == before
    assert editor.companion_values() == {EXCLUSIVE_RESTORE_KEY: None}
    assert editor.edits == []


def test_other_rows_locked_while_exclusive_on(editor, monkeypatch):
    monkeypatch.setattr(device_rows, "_device_backend_plugin_names", lambda: ["cms"])
    _three_rows(editor)
    assert editor.exclusive_hint.isHidden()
    editor.set_checked("sim", True)
    for index in (0, 1):
        flags = editor.table.item(index, 0).flags()
        assert not flags & Qt.ItemFlag.ItemIsUserCheckable
        assert not flags & Qt.ItemFlag.ItemIsEnabled
    assert editor.table.item(2, 0).flags() & Qt.ItemFlag.ItemIsUserCheckable
    assert editor.exclusive_hint.text() == (
        "Other device sources are off while Excl: 127.0.0.1 is on. Also off: cms."
    )
    assert not editor.exclusive_hint.isHidden()


def test_unticking_exclusive_restores(editor):
    a = _row("mock", "a", rid="a")
    b = _row("mock", "b", enabled=False, rid="b")  # already off: stays off afterwards
    sim = _row("excl", "sim", enabled=False, config={"addr": "127.0.0.1"}, rid="sim")
    _load(editor, own=_own(b, sim), inherited=_inherited(a))
    editor.set_checked("sim", True)
    editor.set_checked("sim", False)
    assert _enabled(editor) == [True, False, False]
    assert editor.companion_values() == {EXCLUSIVE_RESTORE_KEY: None}
    assert editor.exclusive_hint.isHidden()
    assert editor.edits == [1, 1]


def test_restore_list_comes_from_companion(editor):
    a = _row("mock", "a", enabled=False, rid="a")
    sim = _row("excl", "sim", config={"addr": "127.0.0.1"}, rid="sim")
    _load(editor, own=_own(a, sim), restore={"a": True})
    editor.set_checked("sim", False)
    assert _enabled(editor) == [True, False]
    assert editor.companion_values() == {EXCLUSIVE_RESTORE_KEY: None}


def test_added_and_duplicated_exclusive_rows_start_off(editor):
    _three_rows(editor)
    editor.set_checked("sim", True)
    action = next(a for a in editor.add_button.menu().actions() if a.text() == "Excl")
    action.trigger()
    added = editor.row_ids()[-1]
    assert editor.value()[added]["enabled"] is False
    editor.select("sim")
    editor.duplicate_button.click()
    dup = editor.row_ids()[-1]
    assert dup not in ("a", "b", "sim", added)
    assert editor.value()[dup]["enabled"] is False
    assert editor.value()[dup]["name"] == "excl-2"
    assert _enabled(editor) == [False, False, True, False, False]
    assert editor.edits == [1, 1, 1]


def test_removing_on_exclusive_row_restores(editor):
    _three_rows(editor)
    editor.set_checked("sim", True)
    editor.select("sim")
    editor.remove_button.click()
    assert editor.row_ids() == ["a", "b"]
    assert _enabled(editor) == [True, True]
    assert editor.value() == {"b": _row("mock", "b", rid="b").to_dict()}
    assert editor.companion_values() == {EXCLUSIVE_RESTORE_KEY: None}
    assert editor.edits == [1, 1]


def test_read_only_scope_is_inert(editor):
    a = _row("mock", "a", rid="a")
    c = _row("mock", "c", rid="c")
    _load(editor, own=_own(c), inherited=_inherited(a))
    editor.set_read_only(True)
    for index in (0, 1):
        assert not editor.table.item(index, 0).flags() & Qt.ItemFlag.ItemIsUserCheckable
    editor.set_checked("c", False)
    assert editor.edits == []
    assert editor.value() == _own(c)
    assert not editor.add_button.isEnabled()
    editor.select("c")
    assert not editor.duplicate_button.isEnabled()
    assert not editor.remove_button.isEnabled()
    assert not editor.pane.isEnabled()
    editor.select("a")
    assert not editor.override_button.isEnabled()
    editor.set_read_only(False)
    editor.select("c")
    assert editor.pane.isEnabled()
    assert editor.remove_button.isEnabled()


def test_unknown_type_row_is_kept(editor):
    gone = _row("gone", "old", config={"x": 1}, rid="g")
    _load(editor, own=_own(gone))
    assert editor.cell_text("g", "Type") == "(unavailable: gone)"
    editor.select("g")
    label = editor.stack.currentWidget()
    assert isinstance(label, QLabel)
    assert "not available" in label.text()
    assert editor.value() == _own(gone)


def test_set_value_none_shows_only_inherited(editor):
    a = _row("mock", "a", rid="a")
    c = _row("mock", "c", rid="c")
    _load(editor, own=_own(c), inherited=_inherited(a))
    editor.select("c")
    editor.set_value(None)
    assert editor.row_ids() == ["a"]
    assert editor.value() is None
    assert editor.edits == []
    assert "c" not in editor._editors
