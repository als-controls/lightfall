"""SettingsForm: one generated settings page rendered at one scope."""
from __future__ import annotations

import pytest
from qtpy.QtCore import QPoint
from qtpy.QtGui import QContextMenuEvent
from qtpy.QtWidgets import QApplication, QComboBox, QGroupBox, QLineEdit, QMenu, QToolButton

from lightfall.ui.preferences.form.editors import FieldEditor
from lightfall.ui.preferences.form.model import FormModel
from lightfall.ui.preferences.form.row import SettingRow
from lightfall.ui.preferences.form.schema import (
    Custom,
    Integer,
    Section,
    SettingField,
    Text,
    Toggle,
)
from lightfall.ui.preferences.form.view import SettingsForm
from lightfall.ui.preferences.scope_api import LockInfo, ScopeChoice

USER, LOCAL, BEAMLINE, FACILITY = "user:u1", "local", "beamline:7.0.1", "facility"

FLAG = "test.flag"        # user-only toggle
COUNT = "test.count"      # beamline-capable integer (0..60)
CONSOLE = "test.console"  # console-only text

ROWS = "test.rows"        # user-only, custom editor owning COMPANION
COMPANION = "test.rows_extra"

ALLOWED = {FLAG: {USER}, COUNT: {USER, BEAMLINE}, CONSOLE: {LOCAL}, ROWS: {USER}, COMPANION: {USER}}


class FakePrefs:
    """Duck-typed stand-in for PreferencesManager's scope-aware read API."""

    def __init__(self, layers=None, writable=None, locks=None, choices=None):
        self.layers = dict(layers or {})  # (scope, key) -> value
        self.writable = writable if writable is not None else {USER, LOCAL}
        self.locks = dict(locks or {})  # (scope, key) -> LockInfo
        self.choices = choices or [
            ScopeChoice(USER, "user", "User — u1", None, ""),
            ScopeChoice(LOCAL, "local", "This console", None, ""),
            ScopeChoice(BEAMLINE, "beamline", "Beamline 7.0.1", "viewer", "Read-only: you are a viewer here"),
            ScopeChoice(FACILITY, "facility", "Facility — ALS", None, ""),
        ]

    def allowed_at(self, key, scope):
        return scope in ALLOWED.get(key, set())

    def writable_at(self, key, scope):
        return self.allowed_at(key, scope) and scope in self.writable

    def lock_at(self, key, scope):
        return self.locks.get((scope, key))

    def layer_value(self, key, scope):
        return self.layers.get((scope, key))

    def scope_choices(self):
        return self.choices


class FakePlugin:
    def __init__(self, sections):
        self._sections = sections

    def sections(self):
        return self._sections


def default_sections():
    return [
        Section("General", [
            SettingField(FLAG, Toggle(), label="Flag", description="A user-only flag"),
            SettingField(
                COUNT, Integer(minimum=0, maximum=60), label="Count",
                enabled_when=lambda v: v.get(FLAG) is True, restart_required=True,
            ),
        ]),
        Section("Console", [SettingField(CONSOLE, Text(), label="Console text")]),
    ]


def make_form(qtbot, prefs=None, sections=None, scope=USER):
    prefs = prefs or FakePrefs()
    model = FormModel(prefs.layer_value)
    form = SettingsForm(FakePlugin(sections or default_sections()), model, prefs)
    qtbot.addWidget(form)
    form.show_scope(scope)
    return form, model, prefs


def rows(form):
    return {r.key: r for r in form.findChildren(SettingRow)}


def click_toggle(qtbot, row, text):
    combo = row.editor.findChild(QComboBox)
    combo.setCurrentIndex(combo.findText(text))  # emits like a user pick


@pytest.fixture
def menus(monkeypatch):
    """Capture context menus instead of showing them; returns the shown menus."""
    shown: list[QMenu] = []
    monkeypatch.setattr(SettingRow, "_popup", staticmethod(lambda menu, _pos: shown.append(menu)))
    return shown


def right_click(widget):
    """Deliver a mouse context-menu event to ``widget``; unhandled, it travels up to the row."""
    pos = QPoint(2, 2)
    QApplication.sendEvent(widget, QContextMenuEvent(QContextMenuEvent.Reason.Mouse, pos, widget.mapToGlobal(pos)))


def clear_via_menu(qtbot, row, menus, target=None):
    """Right-click ``target`` (the row's label by default) and pick "Clear (not set)"."""
    menus.clear()
    right_click(target or row._label)
    assert menus, "no context menu shown"
    action = next(a for a in menus[-1].actions() if a.text() == "Clear (not set)")
    action.trigger()
    return action


def type_into(qtbot, row, text):
    line = row.editor.findChild(QLineEdit)
    line.setFocus()
    qtbot.keyClicks(line, text)


def test_only_fields_allowed_at_scope_render(qtbot):
    form, _, _ = make_form(qtbot)
    assert set(rows(form)) == {FLAG, COUNT}
    form.show_scope(LOCAL)
    assert set(rows(form)) == {CONSOLE}
    form.show_scope(BEAMLINE)
    assert set(rows(form)) == {COUNT}


def test_sections_without_visible_fields_are_omitted(qtbot):
    form, _, _ = make_form(qtbot, scope=LOCAL)
    assert [box.title() for box in form.findChildren(QGroupBox)] == ["Console"]


def test_sections_are_group_boxes(qtbot):
    sections = default_sections()
    sections[0] = Section("General", sections[0].items, description="General things")
    form, _, _ = make_form(qtbot, sections=sections)
    boxes = form.findChildren(QGroupBox)
    assert [box.title() for box in boxes] == ["General"]
    box = boxes[0]
    assert box.styleSheet() == ""  # the app theme's QGroupBox rules apply
    assert {r.key for r in box.findChildren(type(rows(form)[FLAG]))} == {FLAG, COUNT}
    first = box.layout().itemAt(0).widget()
    assert first.text() == "General things"


def test_unset_field_has_no_accent_and_not_set_editor(qtbot):
    prefs = FakePrefs(layers={(USER, COUNT): 5})
    form, _, _ = make_form(qtbot, prefs)
    r = rows(form)
    assert r[FLAG].editor.value() is None
    assert not r[FLAG].is_marked_set()
    assert not r[FLAG].is_marked_dirty()
    assert r[COUNT].editor.value() == 5
    assert r[COUNT].is_marked_set()


def test_edit_pushes_into_model_and_emits_edited(qtbot):
    form, model, prefs = make_form(qtbot)
    with qtbot.waitSignal(form.edited):
        click_toggle(qtbot, rows(form)[FLAG], "On")
    assert model.pending(USER, FLAG) is True
    assert rows(form)[FLAG].is_marked_dirty()
    assert rows(form)[FLAG].is_marked_set()
    assert [(o.scope, o.key, o.value) for o in model.ops(prefs.writable_at)] == [(USER, FLAG, True)]


def test_row_has_no_menu_button(qtbot):
    form, _, _ = make_form(qtbot)
    for row in rows(form).values():
        assert row.findChildren(QToolButton) == []


def test_context_menu_clear_sets_not_set(qtbot, menus):
    prefs = FakePrefs(layers={(USER, COUNT): 5, (USER, FLAG): True})
    form, model, _ = make_form(qtbot, prefs)
    row = rows(form)[COUNT]
    assert row.clear_action.text() == "Clear (not set)"
    with qtbot.waitSignal(form.edited):
        clear_via_menu(qtbot, row, menus)
    assert model.pending(USER, COUNT) is None
    assert row.editor.value() is None
    # Once not set, the action is disabled; right-clicking the editor area shows it too.
    flag = rows(form)[FLAG]
    clear_via_menu(qtbot, flag, menus, target=flag.editor.findChild(QComboBox))
    assert model.pending(USER, FLAG) is None
    menus.clear()
    right_click(flag._label)
    assert [a.isEnabled() for a in menus[-1].actions() if a.text() == "Clear (not set)"] == [False]


def test_context_menu_on_line_edit_keeps_standard_actions_and_adds_clear(qtbot, menus):
    form, model, _ = make_form(qtbot, FakePrefs(layers={(LOCAL, CONSOLE): "abc"}), scope=LOCAL)
    row = rows(form)[CONSOLE]
    menus.clear()
    right_click(row.editor.findChild(QLineEdit))
    texts = [a.text() for a in menus[-1].actions()]
    assert "Clear (not set)" in texts and len(texts) > 2  # the line edit's own Copy/Paste survive
    next(a for a in menus[-1].actions() if a.text() == "Clear (not set)").trigger()
    assert model.pending(LOCAL, CONSOLE) is None


def test_context_menu_clear_disabled_when_editor_disabled(qtbot, menus):
    prefs = FakePrefs(layers={(USER, COUNT): 5})  # Count holds a value but Flag is not On
    form, _, _ = make_form(qtbot, prefs)
    row = rows(form)[COUNT]
    assert not row.editor.isEnabled()
    right_click(row._label)
    assert [a.isEnabled() for a in menus[-1].actions() if a.text() == "Clear (not set)"] == [False]
    click_toggle(qtbot, rows(form)[FLAG], "On")  # enabled_when now holds
    menus.clear()
    right_click(row._label)
    assert [a.isEnabled() for a in menus[-1].actions() if a.text() == "Clear (not set)"] == [True]


def test_context_menu_clear_disabled_when_read_only(qtbot, menus):
    prefs = FakePrefs(layers={(BEAMLINE, COUNT): 7})
    form, _, _ = make_form(qtbot, prefs, scope=BEAMLINE)
    row = rows(form)[COUNT]
    right_click(row._label)
    assert [a.isEnabled() for a in menus[-1].actions() if a.text() == "Clear (not set)"] == [False]


def test_clear_action_marks_dirty_and_none(qtbot, menus):
    prefs = FakePrefs(layers={(USER, COUNT): 5, (USER, FLAG): True})  # Flag On enables Count
    form, model, _ = make_form(qtbot, prefs)
    row = rows(form)[COUNT]
    with qtbot.waitSignal(form.edited):
        clear_via_menu(qtbot, row, menus)
    assert model.pending(USER, COUNT) is None
    assert model.is_dirty(USER, COUNT)
    assert row.editor.value() is None
    assert row.is_marked_dirty()
    assert not row.is_marked_set()


def test_invalid_input_keeps_model_and_reports_invalid_field(qtbot, menus):
    prefs = FakePrefs(layers={(USER, COUNT): 5, (USER, FLAG): True})
    form, model, _ = make_form(qtbot, prefs)
    row = rows(form)[COUNT]
    row.editor.findChild(QLineEdit).clear()
    type_into(qtbot, row, "-5")  # "-" unparsable, then out of range 0..60
    assert model.pending(USER, COUNT) == 5
    assert not model.is_dirty(USER, COUNT)
    assert form.invalid_fields(USER) == [COUNT]
    assert row.is_marked_invalid()
    clear_via_menu(qtbot, row, menus)
    assert form.invalid_fields(USER) == []


def test_hidden_invalid_row_does_not_block(qtbot):
    sections = [Section("S", [
        SettingField(FLAG, Toggle()),
        SettingField(COUNT, Integer(minimum=0, maximum=60), visible_when=lambda v: v.get(FLAG) is True),
    ])]
    form, _, _ = make_form(qtbot, sections=sections)
    click_toggle(qtbot, rows(form)[FLAG], "On")
    type_into(qtbot, rows(form)[COUNT], "999")
    assert form.invalid_fields(USER) == [COUNT]
    click_toggle(qtbot, rows(form)[FLAG], "Off")  # Count is hidden now
    assert form.invalid_fields(USER) == []


def test_disabled_invalid_row_does_not_block(qtbot):
    form, _, _ = make_form(qtbot)  # Count is enabled only while Flag is On
    click_toggle(qtbot, rows(form)[FLAG], "On")
    type_into(qtbot, rows(form)[COUNT], "999")
    assert form.invalid_fields(USER) == [COUNT]
    click_toggle(qtbot, rows(form)[FLAG], "Off")
    assert form.invalid_fields(USER) == []


def test_read_only_scope_disables_editors_and_shows_reason(qtbot):
    prefs = FakePrefs(layers={(BEAMLINE, COUNT): 7})
    form, model, _ = make_form(qtbot, prefs, scope=BEAMLINE)
    row = rows(form)[COUNT]
    assert row.is_read_only()
    assert row.reason_text() == "Read-only: you are a viewer here"
    line = row.editor.findChild(QLineEdit)
    line.setFocus()
    qtbot.keyClicks(line, "3")
    assert line.text() == "7"
    assert model.pending(BEAMLINE, COUNT) == 7
    assert model.ops(prefs.writable_at) == []


def test_read_only_without_reason_uses_generic_text(qtbot):
    prefs = FakePrefs(writable={LOCAL})
    form, _, _ = make_form(qtbot, prefs)
    assert rows(form)[FLAG].reason_text() == "You can't change this here"


def test_locked_above_shows_lock_reason(qtbot):
    lock = LockInfo("beamline:7.0.1", "admin", "Set by beamline staff")
    prefs = FakePrefs(locks={(USER, COUNT): lock})
    form, _, _ = make_form(qtbot, prefs)
    r = rows(form)
    assert r[COUNT].is_read_only()
    assert r[COUNT].reason_text() == "Locked at beamline:7.0.1 by admin: Set by beamline staff"
    assert not r[FLAG].is_read_only()


def test_lock_reason_omits_empty_parts(qtbot):
    prefs = FakePrefs(locks={(USER, COUNT): LockInfo("facility", "", "")})
    form, _, _ = make_form(qtbot, prefs)
    assert rows(form)[COUNT].reason_text() == "Locked at facility"


def test_enabled_when_reevaluates_on_edit(qtbot):
    form, _, _ = make_form(qtbot)
    count = rows(form)[COUNT]
    assert not count.editor.isEnabled()
    click_toggle(qtbot, rows(form)[FLAG], "On")
    assert count.editor.isEnabled()
    click_toggle(qtbot, rows(form)[FLAG], "Off")
    assert not count.editor.isEnabled()


def test_visible_when_reevaluates_on_edit(qtbot):
    sections = [Section("S", [
        SettingField(FLAG, Toggle()),
        SettingField(COUNT, Integer(), visible_when=lambda v: v.get(FLAG) is True),
    ])]
    form, _, _ = make_form(qtbot, sections=sections)
    form.show()
    assert not rows(form)[COUNT].isVisible()
    click_toggle(qtbot, rows(form)[FLAG], "On")
    assert rows(form)[COUNT].isVisible()


def test_has_fields_false_for_scope_with_nothing(qtbot):
    form, _, _ = make_form(qtbot)
    assert form.has_fields(USER)
    assert form.has_fields(LOCAL)
    assert not form.has_fields(FACILITY)


def test_restart_tag_from_field_or_spec(qtbot):
    from lightfall_settings import SettingSpec

    from lightfall.scopes.keys import _reset_client_registry, register_client_specs

    _reset_client_registry()
    try:
        register_client_specs([SettingSpec(key=FLAG, restart_required=True, label="Spec flag")])
        sections = [Section("S", [SettingField(FLAG, Toggle()), SettingField(COUNT, Integer(), restart_required=True)])]
        form, _, _ = make_form(qtbot, sections=sections)
        r = rows(form)
        assert r[FLAG].has_restart_tag()
        assert r[COUNT].has_restart_tag()
        assert r[FLAG].label_text() == "Spec flag"
    finally:
        _reset_client_registry()


def test_no_restart_tag_by_default(qtbot):
    form, _, _ = make_form(qtbot)
    assert not rows(form)[FLAG].has_restart_tag()


def test_editors_show_pending_per_scope(qtbot):
    prefs = FakePrefs(layers={(USER, COUNT): 5, (BEAMLINE, COUNT): 9})
    form, model, _ = make_form(qtbot, prefs)
    model.edit(USER, COUNT, 6)
    form.show_scope(BEAMLINE)
    assert rows(form)[COUNT].editor.value() == 9
    form.show_scope(USER)
    assert rows(form)[COUNT].editor.value() == 6
    assert rows(form)[COUNT].is_marked_dirty()


def test_refresh_from_store_updates_clean_and_flags_conflict(qtbot):
    prefs = FakePrefs(layers={(USER, COUNT): 5, (USER, FLAG): True})
    form, model, _ = make_form(qtbot, prefs)
    click_toggle(qtbot, rows(form)[FLAG], "Off")  # dirty edit
    prefs.layers[(USER, COUNT)] = 8
    prefs.layers[(USER, FLAG)] = None
    form.refresh_from_store()
    r = rows(form)
    assert r[COUNT].editor.value() == 8
    assert not r[COUNT].has_conflict_note()
    assert r[FLAG].editor.value() is False
    assert model.conflict(USER, FLAG)
    assert r[FLAG].has_conflict_note()


@pytest.mark.parametrize("scope", [USER, LOCAL])
def test_show_scope_twice_does_not_duplicate_rows(qtbot, scope):
    form, _, _ = make_form(qtbot, scope=scope)
    before = len(rows(form))
    form.show_scope(scope)
    assert len(rows(form)) == before


def test_show_scope_reloads_values_changed_while_another_scope_was_shown(qtbot):
    prefs = FakePrefs(layers={(USER, COUNT): 5})
    form, model, _ = make_form(qtbot, prefs)
    form.show_scope(BEAMLINE)
    prefs.layers[(USER, COUNT)] = 9  # remote change at User while Beamline is shown
    form.show_scope(USER)
    assert rows(form)[COUNT].editor.value() == 9
    assert not model.is_dirty(USER, COUNT)


def test_show_scope_keeps_dirty_edit_and_flags_conflict(qtbot):
    prefs = FakePrefs(layers={(USER, COUNT): 5})
    form, model, _ = make_form(qtbot, prefs)
    model.edit(USER, COUNT, 12)
    form.show_scope(BEAMLINE)
    prefs.layers[(USER, COUNT)] = 9
    form.show_scope(USER)
    row = rows(form)[COUNT]
    assert row.editor.value() == 12
    assert row.has_conflict_note()


def test_reason_matching_scope_header_shows_only_the_lock(qtbot):
    form, _, _ = make_form(qtbot, scope=BEAMLINE)
    row = rows(form)[COUNT]
    assert row.is_read_only()
    assert row.reason_text() == "Read-only: you are a viewer here"
    assert not row.reason_shown_inline()
    assert row.lock_tooltip() == "Read-only: you are a viewer here"


def test_lock_and_generic_reasons_still_show_inline(qtbot):
    lock = LockInfo("facility", "", "")
    prefs = FakePrefs(writable={LOCAL}, locks={(USER, COUNT): lock})
    form, _, _ = make_form(qtbot, prefs)
    r = rows(form)
    assert r[COUNT].reason_shown_inline()
    assert r[FLAG].reason_shown_inline()  # generic text: the User header has no reason


def _approval_sections():
    other = "test.other"  # a second beamline-capable field
    ALLOWED[other] = {USER, BEAMLINE}
    return other, [Section("General", [
        SettingField(COUNT, Integer(minimum=0, maximum=60), label="Count"),
        SettingField(other, Integer(minimum=0, maximum=9), label="Other"),
    ])]


def test_row_shows_approval_note_only_where_needed(qtbot):
    other, sections = _approval_sections()
    try:
        prefs = FakePrefs(writable={USER, LOCAL, BEAMLINE})
        prefs.needs_approval = lambda key, scope: key == COUNT and scope == BEAMLINE
        form, _, _ = make_form(qtbot, prefs, sections=sections, scope=BEAMLINE)
        r = rows(form)
        assert r[COUNT].approval_note_shown() and not r[other].approval_note_shown()
        assert not r[COUNT].is_read_only()
        assert r[COUNT]._approval.text() == "Saving here asks a facility admin to approve this setting"
        assert not form.approval_for_all()
        form.show_scope(USER)
        assert not any(row.approval_note_shown() for row in rows(form).values())
        assert not form.approval_for_all()
    finally:
        ALLOWED.pop(other)


def test_approval_note_moves_off_rows_when_every_field_needs_it(qtbot):
    other, sections = _approval_sections()
    try:
        prefs = FakePrefs(writable={USER, LOCAL, BEAMLINE})
        prefs.needs_approval = lambda key, scope: scope == BEAMLINE
        form, _, _ = make_form(qtbot, prefs, sections=sections, scope=BEAMLINE)
        assert form.approval_for_all()  # the scope header says it once
        assert not any(row.approval_note_shown() for row in rows(form).values())
        prefs.writable = {USER, LOCAL}  # read-only scope: nothing to approve
        form.show_scope(BEAMLINE)
        assert not form.approval_for_all()
    finally:
        ALLOWED.pop(other)


def test_lock_reason_wins_over_approval_note(qtbot):
    lock = LockInfo("facility", "admin", "")
    prefs = FakePrefs(writable={USER, LOCAL, BEAMLINE}, locks={(BEAMLINE, COUNT): lock})
    prefs.needs_approval = lambda key, scope: True
    form, _, _ = make_form(qtbot, prefs, scope=BEAMLINE)
    row = rows(form)[COUNT]
    assert row.is_read_only() and not row.approval_note_shown()


def test_text_editors_fill_the_row_and_editors_line_up(qtbot):
    from lightfall.ui.preferences.form.row import EDITOR_COLUMN_MIN
    from lightfall.ui.preferences.form.schema import Choice, Secret, Url

    keys = {"t.text": Text(), "t.url": Url(), "t.secret": Secret(), "t.choice": Choice([("a", "A"), ("b", "B")]),
            "t.count": Integer(0, 9)}
    ALLOWED.update({k: {USER} for k in keys})
    try:
        sections = [Section("S", [
            SettingField(k, spec, label=k, description="A rather long description " * 4)
            for k, spec in keys.items()
        ])]
        form, _model, _prefs = make_form(qtbot, sections=sections)
        form.resize(900, 900)
        form.show()
        QApplication.processEvents()
        by_key = rows(form)
        left = {k: r.editor.mapTo(form, QPoint(0, 0)).x() for k, r in by_key.items()}
        assert len(set(left.values())) == 1, left  # one editor column
        for k in ("t.text", "t.url", "t.secret"):
            editor = by_key[k].editor
            right = editor.mapTo(form, QPoint(editor.width(), 0)).x()
            assert editor.width() > EDITOR_COLUMN_MIN + 40, k
            assert right >= by_key[k].mapTo(form, QPoint(by_key[k].width(), 0)).x() - 2, k
        for k in ("t.choice", "t.count"):
            assert by_key[k].editor.width() == max(EDITOR_COLUMN_MIN, by_key[k].editor.sizeHint().width()), k
    finally:
        for k in keys:
            ALLOWED.pop(k, None)


def test_field_info_lines_up_with_the_field_labels(qtbot):
    from lightfall.ui.preferences.form.items import InfoItem
    from lightfall.ui.preferences.form.schema import Info

    sections = [Section("S", [
        SettingField(CONSOLE, Text(), label="Console text"),
        Info("About the console text", field=CONSOLE),
        Info("About the section"),
    ], description="A section description")]
    form, _model, _prefs = make_form(qtbot, sections=sections, scope=LOCAL)
    form.resize(800, 600)
    form.show()
    QApplication.processEvents()
    label = rows(form)[CONSOLE]._label
    field_info, plain_info = sorted(form.findChildren(InfoItem), key=lambda i: i.info.field is None)
    label_x = label.mapTo(form, QPoint(0, 0)).x()

    def text_x(info):
        indent = max(info.indent(), 0)  # -1 = none for a plain-text label
        return info.mapTo(form, info.contentsRect().topLeft()).x() + info.margin() + indent

    assert text_x(field_info) == label_x
    assert text_x(plain_info) < label_x
    assert field_info.margin() > 0  # spaced from the section description


# Custom editors and companion keys ---------------------------------------------------


class PairEditor(FieldEditor):
    """Test-only editor owning its field's value and one companion value."""

    def __init__(self, field):
        super().__init__()
        self.field = field
        self._value = None
        self._companion = None
        self.read_only = False

    def value(self):
        return self._value

    def set_value(self, value):
        self._value = value

    def set_read_only(self, read_only):
        self.read_only = read_only

    def companion_values(self):
        return {COMPANION: self._companion}

    def set_companion_values(self, values):
        self._companion = values.get(COMPANION)

    def user_edit(self, value, companion):
        self._value, self._companion = value, companion
        self.changed.emit()


def custom_sections():
    return [Section("Rows", [SettingField(ROWS, Custom(PairEditor), label="Rows", companions=(COMPANION,))])]


def test_custom_editor_is_built_by_its_factory(qtbot):
    form, _, _ = make_form(qtbot, sections=custom_sections())
    editor = rows(form)[ROWS].editor
    assert isinstance(editor, PairEditor)
    assert editor.field.key == ROWS


def test_companion_loaded_with_field(qtbot):
    prefs = FakePrefs(layers={(USER, ROWS): "a", (USER, COMPANION): ["x"]})
    form, _, _ = make_form(qtbot, prefs, sections=custom_sections())
    editor = rows(form)[ROWS].editor
    assert editor.value() == "a"
    assert editor._companion == ["x"]
    assert set(rows(form)) == {ROWS}  # the companion is not a row of its own


def test_editor_change_edits_companion_at_scope(qtbot):
    form, model, prefs = make_form(qtbot, sections=custom_sections())
    rows(form)[ROWS].editor.user_edit("b", ["y"])
    ops = {(op.scope, op.key): op.value for op in model.ops(prefs.writable_at)}
    assert ops == {(USER, ROWS): "b", (USER, COMPANION): ["y"]}
    assert rows(form)[ROWS].is_marked_dirty()


def test_companion_edit_alone_marks_row_dirty(qtbot):
    prefs = FakePrefs(layers={(USER, ROWS): "a"})
    form, model, _ = make_form(qtbot, prefs, sections=custom_sections())
    rows(form)[ROWS].editor.user_edit("a", ["y"])
    assert not model.is_dirty(USER, ROWS)
    assert rows(form)[ROWS].is_marked_dirty()


def test_clear_clears_companion(qtbot):
    prefs = FakePrefs(layers={(USER, ROWS): "a", (USER, COMPANION): ["x"]})
    form, model, _ = make_form(qtbot, prefs, sections=custom_sections())
    form._on_clear(ROWS)
    assert model.pending(USER, ROWS) is None
    assert model.pending(USER, COMPANION) is None
    assert model.is_dirty(USER, COMPANION)


def test_refresh_keeps_dirty_companion(qtbot):
    prefs = FakePrefs(layers={(USER, ROWS): "a", (USER, COMPANION): ["x"]})
    form, model, _ = make_form(qtbot, prefs, sections=custom_sections())
    editor = rows(form)[ROWS].editor
    editor.user_edit("a", ["y"])  # only the companion is dirty
    prefs.layers[(USER, ROWS)] = "remote"
    prefs.layers[(USER, COMPANION)] = ["remote"]
    form.refresh_from_store()
    assert editor.value() == "a"
    assert editor._companion == ["y"]
    assert model.pending(USER, COMPANION) == ["y"]


def test_refresh_reloads_clean_companion(qtbot):
    prefs = FakePrefs(layers={(USER, ROWS): "a", (USER, COMPANION): ["x"]})
    form, _, _ = make_form(qtbot, prefs, sections=custom_sections())
    prefs.layers[(USER, COMPANION)] = ["remote"]
    form.refresh_from_store()
    assert rows(form)[ROWS].editor._companion == ["remote"]
