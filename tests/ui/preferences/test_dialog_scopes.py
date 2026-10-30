"""PreferencesDialog with the scope header, generated form pages and legacy pages."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtGui import QPalette
from PySide6.QtWidgets import QAbstractButton, QLabel, QMessageBox, QPushButton, QWidget

from lightfall.ui.preferences.dialog import PreferencesDialog
from lightfall.ui.preferences.form.header import LEGACY_TEXT, ScopeHeader
from lightfall.ui.preferences.form.row import SettingRow
from lightfall.ui.preferences.form.schema import Integer, Section, SettingField, Text, Toggle
from lightfall.ui.preferences.form.view import SettingsForm
from lightfall.ui.preferences.scope_api import ScopeChoice

USER, LOCAL, BEAMLINE = "user:u1", "local", "beamline:7.0.1"

FLAG = "dlg.flag"        # user + beamline toggle
COUNT = "dlg.count"      # user + beamline integer, needs a restart
LEVEL = "dlg.level"      # user integer, needs a restart
CONSOLE = "dlg.console"  # console-only text (on the second form page)

ALLOWED = {FLAG: {USER, BEAMLINE}, COUNT: {USER, BEAMLINE}, LEVEL: {USER}, CONSOLE: {LOCAL}}


def _choices():
    return [
        ScopeChoice(USER, "user", "User — u1", None, ""),
        ScopeChoice(LOCAL, "local", "This console", None, ""),
        ScopeChoice(BEAMLINE, "beamline", "Beamline 7.0.1", "operator", ""),
    ]


class FakePrefs(QObject):
    """Records writes; reads come from ``layers``."""

    scopes_changed = Signal(object)

    def __init__(self):
        super().__init__()
        self.layers: dict[tuple[str, str], object] = {}
        self.calls: list[tuple] = []
        self.choices = _choices()
        self.default = USER
        self.subscribers: dict[str, list] = {}
        self.fail_on: str | None = None
        self.allowed = {k: set(v) for k, v in ALLOWED.items()}
        self.read_only_scopes: set[str] = set()

    def scope_choices(self):
        return self.choices

    def default_scope(self):
        return self.default

    def allowed_at(self, key, scope):
        return scope in self.allowed.get(key, set())

    def writable_at(self, key, scope):
        return self.allowed_at(key, scope) and scope not in self.read_only_scopes

    def lock_at(self, key, scope):
        return None

    def layer_value(self, key, scope):
        return self.layers.get((scope, key))

    def set(self, key, value, *, scope=None):
        if key == self.fail_on:
            raise RuntimeError("server said no")
        self.calls.append(("set", key, value, scope))
        self.layers[(scope, key)] = value

    def remove(self, key, *, scope=None):
        self.calls.append(("remove", key, scope))
        self.layers.pop((scope, key), None)

    def subscribe(self, key, slot):
        self.subscribers.setdefault(key, []).append(slot)

    def unsubscribe(self, key, slot):
        self.subscribers.get(key, []).remove(slot)

    def push(self, scope, key, value):
        """Simulate a remote change arriving for ``key``."""
        self.layers[(scope, key)] = value
        for slot in list(self.subscribers.get(key, [])):
            slot(value)


class FormPlugin:
    category = "A"
    icon = None
    scope_aware = True

    def __init__(self, name, sections, priority=0):
        self.name = name
        self.display_name = name.title()
        self.priority = priority
        self._sections = sections
        self.after_save_calls: list[tuple[str, dict]] = []
        self.reverted = 0
        self.cross_errors: list[str] = []

    def sections(self):
        return self._sections

    def is_form_page(self):
        return True

    def validate_values(self, values):
        return list(self.cross_errors)

    def after_save(self, scope, changed):
        self.after_save_calls.append((scope, dict(changed)))

    def revert_preview(self):
        self.reverted += 1

    def save_settings(self):  # pragma: no cover - must never be called
        raise AssertionError("save_settings called on a form page")


class LegacyPlugin:
    name = "legacy"
    display_name = "Legacy"
    category = "B"
    priority = 0
    icon = None

    def __init__(self):
        self.saved = 0
        self.loaded = 0
        self.reverted = 0
        self.widget = None

    def is_form_page(self):
        return False

    def create_widget(self, parent):
        self.widget = QWidget()
        return self.widget

    def load_settings(self):
        self.loaded += 1

    def save_settings(self):
        self.saved += 1

    def validate(self):
        return []

    def revert_preview(self):
        self.reverted += 1


def _main_sections():
    return [
        Section("General", [
            SettingField(FLAG, Toggle(), label="Flag"),
            SettingField(COUNT, Integer(minimum=0, maximum=60), label="Count", restart_required=True),
            SettingField(LEVEL, Integer(minimum=0, maximum=9), label="Level", restart_required=True),
        ]),
    ]


@pytest.fixture
def env(monkeypatch):
    prefs = FakePrefs()
    main = FormPlugin("main", _main_sections(), priority=0)
    console = FormPlugin("console", [Section("Console", [SettingField(CONSOLE, Text(), label="Console")])], 1)
    legacy = LegacyPlugin()
    infos = [SimpleNamespace(instance=p) for p in (main, console, legacy)]
    registry = SimpleNamespace(get_ready_by_type=lambda _t: list(infos))
    services = SimpleNamespace(get=lambda _cls: registry)
    monkeypatch.setattr(
        "lightfall.core.services.ServiceRegistry.get_instance", classmethod(lambda cls: services)
    )
    monkeypatch.setattr(
        "lightfall.ui.preferences.manager.PreferencesManager.get_instance",
        classmethod(lambda cls: prefs),
    )
    restarts: list[int] = []
    monkeypatch.setattr("lightfall.utils.restart.request_restart", lambda: restarts.append(1))
    boxes: list[tuple[str, str]] = []

    def record(kind):
        def _box(_parent, title, text, *a, **k):
            boxes.append((kind, text))
            return QMessageBox.StandardButton.Ok
        return _box

    monkeypatch.setattr(QMessageBox, "warning", record("warning"))
    monkeypatch.setattr(QMessageBox, "critical", record("critical"))
    return SimpleNamespace(prefs=prefs, main=main, console=console, legacy=legacy, restarts=restarts, boxes=boxes)


def make_dialog(qtbot, env):
    dialog = PreferencesDialog()
    qtbot.addWidget(dialog)
    return dialog


def header(dialog) -> ScopeHeader:
    return dialog.findChild(ScopeHeader)


def form_of(dialog, name) -> SettingsForm:
    return next(f for f in dialog.findChildren(SettingsForm) if f._plugin.name == name)


def row(dialog, name, key) -> SettingRow:
    return next(r for r in form_of(dialog, name).findChildren(SettingRow) if r.key == key)


def click_toggle(qtbot, r, text):
    buttons = {b.text(): b for b in r.editor.findChildren(QAbstractButton)}
    qtbot.mouseClick(buttons[text], Qt.LeftButton)


def type_number(qtbot, r, text):
    from PySide6.QtWidgets import QLineEdit

    line = r.editor.findChild(QLineEdit)
    line.clear()
    qtbot.keyClicks(line, text)


def page_index(dialog, name):
    return dialog._page_names.index(name)


def combo_texts(h):
    return [h._combo.itemText(i) for i in range(h._combo.count())]


# ---------------------------------------------------------------------------


def test_header_lists_choices_and_opens_on_default_scope(qtbot, env):
    env.prefs.default = USER
    dialog = make_dialog(qtbot, env)
    h = header(dialog)
    assert combo_texts(h) == ["User — u1", "This console", "Beamline 7.0.1"]
    assert dialog.current_scope() == USER
    assert h.selected() == USER
    assert form_of(dialog, "main").scope == USER


def test_ok_without_edits_writes_nothing(qtbot, env):
    env.prefs.layers[(USER, FLAG)] = True
    dialog = make_dialog(qtbot, env)
    dialog.accept()
    assert env.prefs.calls == []
    assert env.main.after_save_calls == []
    assert env.restarts == []


def test_unset_bool_not_written_on_ok(qtbot, env):
    dialog = make_dialog(qtbot, env)
    click_toggle(qtbot, row(dialog, "main", FLAG), "On")
    click_toggle(qtbot, row(dialog, "main", FLAG), "Not set")  # back to unset: not dirty
    dialog.accept()
    assert env.prefs.calls == []


def test_edits_in_two_scopes_write_to_each(qtbot, env):
    dialog = make_dialog(qtbot, env)
    click_toggle(qtbot, row(dialog, "main", FLAG), "On")
    dialog.select_scope(BEAMLINE)
    click_toggle(qtbot, row(dialog, "main", FLAG), "Off")
    assert header(dialog)._combo.itemText(0).endswith(" •")
    assert header(dialog)._combo.itemText(2).endswith(" •")
    assert dialog._apply() is True
    assert sorted(env.prefs.calls, key=str) == sorted(
        [("set", FLAG, True, USER), ("set", FLAG, False, BEAMLINE)], key=str
    )
    assert sorted(env.main.after_save_calls) == sorted([(USER, {FLAG: True}), (BEAMLINE, {FLAG: False})])
    assert not any(header(dialog)._combo.itemText(i).endswith(" •") for i in range(3))
    assert env.legacy.saved == 1


def test_clear_issues_remove_with_scope(qtbot, env):
    env.prefs.layers[(BEAMLINE, FLAG)] = True
    dialog = make_dialog(qtbot, env)
    dialog.select_scope(BEAMLINE)
    row(dialog, "main", FLAG).clear_action.trigger()
    dialog.accept()
    assert env.prefs.calls == [("remove", FLAG, BEAMLINE)]
    assert env.main.after_save_calls == [(BEAMLINE, {FLAG: None})]


def test_switching_page_keeps_scope(qtbot, env):
    dialog = make_dialog(qtbot, env)
    dialog.select_scope(BEAMLINE)
    dialog._sidebar.setCurrentRow(page_index(dialog, "legacy"))
    h = header(dialog)
    assert not h._combo.isEnabled()
    assert h._state.text() == LEGACY_TEXT
    dialog._sidebar.setCurrentRow(page_index(dialog, "main"))
    assert h._combo.isEnabled()
    assert dialog.current_scope() == BEAMLINE
    assert h.selected() == BEAMLINE


def test_page_without_fields_is_dimmed_and_offers_scopes(qtbot, env):
    dialog = make_dialog(qtbot, env)
    item = dialog._sidebar.item(page_index(dialog, "console"))
    placeholder = dialog.palette().color(QPalette.ColorRole.PlaceholderText)
    assert item.foreground().color() == placeholder
    main_item = dialog._sidebar.item(page_index(dialog, "main"))
    assert main_item.foreground().color() != placeholder or main_item.data(Qt.ItemDataRole.ForegroundRole) is None

    dialog._sidebar.setCurrentRow(page_index(dialog, "console"))
    page = dialog._stack.currentWidget().widget()
    labels = [lbl.text() for lbl in page.findChildren(QLabel) if lbl.isVisibleTo(page)]
    assert "Nothing on this page can be set at User — u1." in labels
    buttons = [b for b in page.findChildren(QPushButton) if b.isVisibleTo(page)]
    assert [b.text() for b in buttons] == ["This console"]
    assert not form_of(dialog, "console").isVisibleTo(page)

    qtbot.mouseClick(buttons[0], Qt.LeftButton)
    assert dialog.current_scope() == LOCAL
    assert header(dialog).selected() == LOCAL
    assert form_of(dialog, "console").isVisibleTo(page)
    assert dialog._sidebar.item(page_index(dialog, "console")).data(Qt.ItemDataRole.ForegroundRole) is None
    assert dialog._sidebar.item(page_index(dialog, "main")).foreground().color() == placeholder


def test_legacy_page_still_saves_and_header_not_scope_aware(qtbot, env):
    dialog = make_dialog(qtbot, env)
    assert env.legacy.loaded == 1
    page = dialog._stack.widget(page_index(dialog, "legacy"))
    assert page.widget() is env.legacy.widget
    dialog._sidebar.setCurrentRow(page_index(dialog, "legacy"))
    h = header(dialog)
    assert not h._combo.isEnabled()
    assert h._state.text() == LEGACY_TEXT
    dialog.accept()
    assert env.legacy.saved == 1
    assert env.prefs.calls == []


def test_scopes_changed_rebuilds_header_and_keeps_dirty_edit(qtbot, env):
    dialog = make_dialog(qtbot, env)
    dialog.select_scope(BEAMLINE)
    click_toggle(qtbot, row(dialog, "main", FLAG), "On")
    # Beamline goes away (unbound); the dialog falls back to the default scope.
    env.prefs.choices = [c for c in _choices() if c.scope != BEAMLINE]
    env.prefs.scopes_changed.emit(None)
    h = header(dialog)
    assert combo_texts(h) == ["User — u1", "This console"]
    assert dialog.current_scope() == USER
    # It comes back: the pending beamline edit is still there.
    env.prefs.choices = _choices()
    env.prefs.scopes_changed.emit(None)
    assert combo_texts(h)[2] == "Beamline 7.0.1 •"
    dialog.select_scope(BEAMLINE)
    assert row(dialog, "main", FLAG).editor.value() is True
    dialog.accept()
    assert env.prefs.calls == [("set", FLAG, True, BEAMLINE)]


def test_scopes_changed_keeps_selection_when_still_present(qtbot, env):
    dialog = make_dialog(qtbot, env)
    dialog.select_scope(LOCAL)
    env.prefs.scopes_changed.emit(None)
    assert dialog.current_scope() == LOCAL
    assert header(dialog).selected() == LOCAL


def test_remote_change_refreshes_clean_field(qtbot, env):
    dialog = make_dialog(qtbot, env)
    env.prefs.push(USER, COUNT, 7)
    assert row(dialog, "main", COUNT).editor.value() == 7
    dialog.reject()
    assert all(not slots for slots in env.prefs.subscribers.values())


def test_validation_error_blocks_all_writes(qtbot, env):
    env.main.cross_errors = ["Flag and Count disagree"]
    dialog = make_dialog(qtbot, env)
    click_toggle(qtbot, row(dialog, "main", FLAG), "On")
    assert dialog._apply() is False
    assert env.prefs.calls == []
    assert env.legacy.saved == 0
    assert any("Flag and Count disagree" in text for kind, text in env.boxes if kind == "warning")


def test_field_validator_blocks_all_writes(qtbot, env):
    env.main._sections = [Section("General", [
        SettingField(FLAG, Toggle(), label="Flag", validate=lambda v, _vals: "must be off" if v else None),
    ])]
    dialog = make_dialog(qtbot, env)
    click_toggle(qtbot, row(dialog, "main", FLAG), "On")
    assert dialog._apply() is False
    assert env.prefs.calls == []
    assert any("Flag" in text and "must be off" in text for _k, text in env.boxes)


def test_invalid_editor_blocks_save_and_scope_switch(qtbot, env):
    dialog = make_dialog(qtbot, env)
    click_toggle(qtbot, row(dialog, "main", FLAG), "On")
    type_number(qtbot, row(dialog, "main", COUNT), "999")
    assert dialog._apply() is False
    assert env.prefs.calls == []
    assert any("Count" in text for kind, text in env.boxes if kind == "warning")

    env.boxes.clear()
    header(dialog)._combo.setCurrentIndex(2)  # user picks Beamline in the combo
    assert dialog.current_scope() == USER
    assert header(dialog).selected() == USER
    assert len(env.boxes) == 1 and "Count" in env.boxes[0][1]


def test_write_failure_stops_and_keeps_remaining_dirty(qtbot, env, monkeypatch):
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.No)
    env.prefs.fail_on = COUNT
    dialog = make_dialog(qtbot, env)
    click_toggle(qtbot, row(dialog, "main", FLAG), "On")
    type_number(qtbot, row(dialog, "main", COUNT), "5")
    type_number(qtbot, row(dialog, "main", LEVEL), "3")
    assert dialog._apply() is False
    critical = [text for kind, text in env.boxes if kind == "critical"]
    assert len(critical) == 1 and COUNT in critical[0] and USER in critical[0]
    written = [c[1] for c in env.prefs.calls]
    assert COUNT not in written
    model = dialog._model
    assert model.is_dirty(USER, COUNT)
    assert env.legacy.saved == 0


def test_restart_prompt_once_for_restart_fields(qtbot, env, monkeypatch):
    asked: list[str] = []

    class FakeBox:
        class ButtonRole:
            AcceptRole = QMessageBox.ButtonRole.AcceptRole
            RejectRole = QMessageBox.ButtonRole.RejectRole

        def __init__(self, *a, **k):
            self._buttons = []
            self._clicked = None

        def setWindowTitle(self, t): ...  # noqa: N802
        def setText(self, t): asked.append(t)  # noqa: N802
        def setIcon(self, i): ...  # noqa: N802

        def addButton(self, text, role):  # noqa: N802
            btn = SimpleNamespace(text=text)
            self._buttons.append(btn)
            return btn

        def exec(self):
            self._clicked = next(b for b in self._buttons if b.text == "Restart now")

        def clickedButton(self):  # noqa: N802
            return self._clicked

    monkeypatch.setattr("lightfall.ui.preferences.dialog.QMessageBox", FakeBox)
    dialog = make_dialog(qtbot, env)
    type_number(qtbot, row(dialog, "main", COUNT), "5")
    type_number(qtbot, row(dialog, "main", LEVEL), "3")
    assert dialog._apply() is True
    assert len(asked) == 1
    assert env.restarts == [1]


def test_no_restart_prompt_without_restart_fields(qtbot, env, monkeypatch):
    monkeypatch.setattr(
        "lightfall.ui.preferences.dialog.QMessageBox",
        SimpleNamespace(warning=None, critical=None),  # any prompt would blow up
    )
    dialog = make_dialog(qtbot, env)
    click_toggle(qtbot, row(dialog, "main", FLAG), "On")
    assert dialog._apply() is True
    assert env.restarts == []


def test_cancel_discards_and_reverts(qtbot, env):
    dialog = make_dialog(qtbot, env)
    click_toggle(qtbot, row(dialog, "main", FLAG), "On")
    dialog.reject()
    assert env.prefs.calls == []
    assert not dialog._model.dirty_scopes()
    assert env.main.reverted == 1 and env.legacy.reverted == 1


def test_scopes_changed_at_same_scope_updates_editability(qtbot, env):
    dialog = make_dialog(qtbot, env)
    assert not row(dialog, "main", FLAG).is_read_only()
    env.prefs.read_only_scopes.add(USER)  # e.g. the role at this scope changed
    env.prefs.scopes_changed.emit(None)
    assert dialog.current_scope() == USER
    assert row(dialog, "main", FLAG).is_read_only()
    assert row(dialog, "main", COUNT).is_read_only()


def test_scopes_changed_at_same_scope_updates_field_set(qtbot, env):
    dialog = make_dialog(qtbot, env)
    dialog.select_scope(LOCAL)
    assert [r.key for r in form_of(dialog, "main").findChildren(SettingRow)] == []
    env.prefs.allowed[FLAG].add(LOCAL)  # mode change: Flag is now settable on this console
    env.prefs.scopes_changed.emit(None)
    assert dialog.current_scope() == LOCAL
    assert [r.key for r in form_of(dialog, "main").findChildren(SettingRow)] == [FLAG]
    page = dialog._form_pages["main"]
    assert form_of(dialog, "main").isVisibleTo(page)


def test_switching_back_shows_remote_change_made_while_away(qtbot, env):
    env.prefs.layers[(USER, COUNT)] = 5
    dialog = make_dialog(qtbot, env)
    dialog.select_scope(BEAMLINE)
    env.prefs.push(USER, COUNT, 8)
    dialog.select_scope(USER)
    assert row(dialog, "main", COUNT).editor.value() == 8


def test_edit_that_became_unwritable_blocks_save_with_warning(qtbot, env):
    dialog = make_dialog(qtbot, env)
    click_toggle(qtbot, row(dialog, "main", FLAG), "On")
    dialog.select_scope(BEAMLINE)
    type_number(qtbot, row(dialog, "main", COUNT), "5")
    env.prefs.read_only_scopes.add(USER)
    env.prefs.scopes_changed.emit(None)
    assert dialog._apply() is False
    assert env.prefs.calls == []
    assert env.legacy.saved == 0
    warnings = [text for kind, text in env.boxes if kind == "warning"]
    assert len(warnings) == 1
    assert "Flag (User — u1)" in warnings[0]
    assert "Count" not in warnings[0]
    assert dialog._model.is_dirty(USER, FLAG) and dialog._model.is_dirty(BEAMLINE, COUNT)
    dialog.accept()
    assert dialog.result() != dialog.DialogCode.Accepted


def test_header_combo_stays_put_when_state_text_hides(qtbot):
    h = ScopeHeader()
    qtbot.addWidget(h)
    viewer = ScopeChoice(BEAMLINE, "beamline", "Beamline 7.0.1", "viewer", "Read-only: you are a viewer here")
    h.set_choices([_choices()[0], viewer], BEAMLINE)
    h.resize(900, 40)
    h.show()
    qtbot.waitExposed(h)
    assert h._state.isVisible()
    with_state = h._combo.geometry()
    h.set_selected(USER)
    h.layout().activate()
    assert not h._state.isVisible()
    assert h._combo.x() == with_state.x()
    assert h._combo.width() == with_state.width()
