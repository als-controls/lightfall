"""PreferencesDialog with the scope header, generated form pages, custom pages and outdated plugins."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtGui import QPalette
from PySide6.QtWidgets import QComboBox, QLabel, QMessageBox, QPushButton, QWidget

from lightfall.ui.preferences.dialog import PreferencesDialog
from lightfall.ui.preferences.form.editors import FieldEditor
from lightfall.ui.preferences.form.header import (
    APPROVAL_SCOPE_TEXT,
    CUSTOM_TEXT,
    ScopeHeader,
)
from lightfall.ui.preferences.form.row import SettingRow
from lightfall.ui.preferences.form.schema import (
    Custom,
    Info,
    Integer,
    Secret,
    Section,
    SettingField,
    Text,
    Toggle,
)
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
        self.resolved: dict[str, object] = {}  # what get() returns across scopes

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

    def get(self, key, default=None):
        return self.resolved.get(key, default)

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
        self.reason = ""  # restart_reason() result
        self.reason_calls: list[tuple[str, dict]] = []

    def restart_reason(self, scope, changed):
        self.reason_calls.append((scope, dict(changed)))
        return self.reason

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


class OutdatedPlugin:
    """A third-party page still on the removed hand-built API: neither a form nor a custom page."""

    name = "outdated"
    display_name = "Outdated"
    category = "B"
    priority = 0
    icon = None

    def __init__(self):
        self.calls: list[str] = []  # every method the dialog called on it

    def is_form_page(self):
        return False

    def create_widget(self, parent):
        self.calls.append("create_widget")
        return QWidget()

    def load_settings(self):
        self.calls.append("load_settings")

    def save_settings(self):
        self.calls.append("save_settings")

    def validate(self):
        self.calls.append("validate")
        return []

    def apply_preview(self):
        self.calls.append("apply_preview")

    def revert_preview(self):
        self.calls.append("revert_preview")


class CustomPlugin:
    """A declared custom page: not scope aware, builds its own widget, never loaded or saved."""

    name = "custom"
    display_name = "Custom"
    category = "C"
    priority = 0
    icon = None
    scope_aware = False

    def __init__(self):
        self.widget = None
        self.reverted = 0

    def is_form_page(self):
        return False

    def create_widget(self, parent):
        self.widget = QWidget()
        return self.widget

    def load_settings(self):  # pragma: no cover - must never be called
        raise AssertionError("load_settings called on a custom page")

    def save_settings(self):  # pragma: no cover - must never be called
        raise AssertionError("save_settings called on a custom page")

    def validate(self):  # pragma: no cover - must never be called
        raise AssertionError("validate called on a custom page")

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
    outdated = OutdatedPlugin()
    custom = CustomPlugin()
    infos = [SimpleNamespace(instance=p) for p in (main, console, outdated, custom)]
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
    ns = SimpleNamespace(
        prefs=prefs, main=main, console=console, outdated=outdated, custom=custom, restarts=restarts, boxes=boxes, box_choice=None,
        default_buttons=[],
    )

    def fake_exec(box):
        """Record a modal box and click the button named ``ns.box_choice`` (or none)."""
        boxes.append(("box", box.text()))
        ns.default_buttons.append(box.defaultButton().text() if box.defaultButton() else None)
        chosen = next((b for b in box.buttons() if b.text() == ns.box_choice), None)
        if chosen is not None:
            chosen.click()
        return 0

    monkeypatch.setattr(QMessageBox, "exec", fake_exec)
    return ns


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
    combo = r.editor.findChild(QComboBox)
    combo.setCurrentIndex(combo.findText(text))  # emits like a user pick


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
    assert env.outdated.calls == []


def test_clear_issues_remove_with_scope(qtbot, env):
    env.prefs.layers[(BEAMLINE, FLAG)] = True
    dialog = make_dialog(qtbot, env)
    dialog.select_scope(BEAMLINE)
    r = row(dialog, "main", FLAG)
    assert r.clear_action.isEnabled()
    r.clear_action.trigger()  # what "Clear (not set)" in the row's context menu does
    dialog.accept()
    assert env.prefs.calls == [("remove", FLAG, BEAMLINE)]
    assert env.main.after_save_calls == [(BEAMLINE, {FLAG: None})]


def test_switching_page_keeps_scope(qtbot, env):
    dialog = make_dialog(qtbot, env)
    dialog.select_scope(BEAMLINE)
    dialog._sidebar.setCurrentRow(page_index(dialog, "custom"))
    h = header(dialog)
    assert not h._combo.isEnabled()
    assert h._state.text() == CUSTOM_TEXT
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
    assert env.outdated.calls == []
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
    assert env.outdated.calls == []


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
    assert env.main.reverted == 1 and env.custom.reverted == 1
    assert env.outdated.calls == []


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


def test_edit_that_became_unwritable_blocks_save_with_warning_cancel_keeps_edits(qtbot, env):
    dialog = make_dialog(qtbot, env)
    click_toggle(qtbot, row(dialog, "main", FLAG), "On")
    dialog.select_scope(BEAMLINE)
    type_number(qtbot, row(dialog, "main", COUNT), "5")
    env.prefs.read_only_scopes.add(USER)
    env.prefs.scopes_changed.emit(None)
    assert dialog._apply() is False  # the user closes the warning without discarding
    assert env.prefs.calls == []
    assert env.outdated.calls == []
    warnings = [text for kind, text in env.boxes if kind == "box"]
    assert len(warnings) == 1
    assert "Flag (User — u1)" in warnings[0]
    assert "Count" not in warnings[0]
    assert dialog._model.is_dirty(USER, FLAG) and dialog._model.is_dirty(BEAMLINE, COUNT)
    dialog.accept()
    assert dialog.result() != dialog.DialogCode.Accepted


def test_discard_blocked_edits_then_save_continues(qtbot, env):
    dialog = make_dialog(qtbot, env)
    click_toggle(qtbot, row(dialog, "main", FLAG), "On")
    dialog.select_scope(BEAMLINE)
    type_number(qtbot, row(dialog, "main", COUNT), "5")
    env.prefs.read_only_scopes.add(BEAMLINE)
    env.prefs.scopes_changed.emit(None)
    env.box_choice = "Discard these edits"
    dialog.accept()
    assert [text for kind, text in env.boxes if kind == "box"] and "Count (Beamline 7.0.1)" in env.boxes[0][1]
    assert env.default_buttons == ["Cancel"]  # Enter never discards
    assert env.prefs.calls == [("set", FLAG, True, USER)]
    assert not dialog._model.is_dirty(BEAMLINE, COUNT)
    assert dialog._model.dirty_cells() == []
    assert env.outdated.calls == []
    assert dialog.result() == dialog.DialogCode.Accepted


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


def test_secret_value_not_logged(qtbot, env):
    from loguru import logger as loguru_logger
    from PySide6.QtWidgets import QLineEdit

    secret = "s3cr3t-T0KEN-xyz"
    env.prefs.allowed["api_token"] = {LOCAL}
    env.console._sections = [Section("Console", [SettingField("api_token", Secret(), label="Token")])]
    dialog = make_dialog(qtbot, env)
    dialog.select_scope(LOCAL)
    lines: list[str] = []
    sink = loguru_logger.add(lambda m: lines.append(str(m)), level="TRACE")
    try:
        line = row(dialog, "console", "api_token").editor.findChild(QLineEdit)
        qtbot.keyClicks(line, secret)
        assert dialog._apply() is True
    finally:
        loguru_logger.remove(sink)
    assert ("set", "api_token", secret, LOCAL) in env.prefs.calls
    assert not any(secret in m for m in lines)


@pytest.mark.parametrize("key", ["api_token", "weird_field"])
def test_failed_secret_save_does_not_leak_value(qtbot, env, key):
    from loguru import logger as loguru_logger
    from PySide6.QtWidgets import QLineEdit

    secret = "s3cr3t-T0KEN-xyz"
    env.prefs.allowed[key] = {LOCAL}
    env.prefs.fail_on = key
    original_set = env.prefs.set

    def failing_set(k, value, *, scope=None):
        if k == key:
            raise RuntimeError(f"rejected value {value!r}")
        original_set(k, value, scope=scope)

    env.prefs.set = failing_set
    env.console._sections = [Section("Console", [SettingField(key, Secret(), label="Token")])]
    dialog = make_dialog(qtbot, env)
    dialog.select_scope(LOCAL)
    lines: list[str] = []
    sink = loguru_logger.add(lambda m: lines.append(str(m)), level="TRACE")
    try:
        qtbot.keyClicks(row(dialog, "console", key).editor.findChild(QLineEdit), secret)
        assert dialog._apply() is False
    finally:
        loguru_logger.remove(sink)
    assert any("Failed to save" in m and "RuntimeError" in m for m in lines)
    assert not any(secret in m for m in lines)
    assert not any(secret in text for _kind, text in env.boxes)
    assert any("RuntimeError" in text for _kind, text in env.boxes)


def test_effective_values_resolve_through_prefs_get(qtbot, env):
    env.prefs.resolved[COUNT] = 7
    env.main._sections = [Section("General", [
        SettingField(COUNT, Integer(minimum=0, maximum=60), label="Count"),
        Info(lambda v: f"count is {v.effective(COUNT)}"),
    ])]
    dialog = make_dialog(qtbot, env)
    info = next(lbl for lbl in form_of(dialog, "main").findChildren(QLabel) if lbl.text().startswith("count is"))
    assert info.text() == "count is 7"


def test_header_states_approval_once_when_every_field_needs_it(qtbot, env):
    env.prefs.needs_approval = lambda key, scope: scope == BEAMLINE
    dialog = make_dialog(qtbot, env)
    h = header(dialog)
    assert h._state.text() == ""
    dialog.select_scope(BEAMLINE)
    assert h._state.text() == APPROVAL_SCOPE_TEXT
    assert not any(r.approval_note_shown() for r in form_of(dialog, "main").findChildren(SettingRow))
    dialog._sidebar.setCurrentRow(page_index(dialog, "custom"))
    assert h._state.text() == CUSTOM_TEXT
    dialog._sidebar.setCurrentRow(page_index(dialog, "main"))
    assert h._state.text() == APPROVAL_SCOPE_TEXT
    dialog.select_scope(USER)
    assert h._state.text() == ""


def test_header_has_no_approval_text_when_only_some_fields_need_it(qtbot, env):
    env.prefs.needs_approval = lambda key, scope: key == COUNT and scope == BEAMLINE
    dialog = make_dialog(qtbot, env)
    dialog.select_scope(BEAMLINE)
    assert header(dialog)._state.text() == ""
    assert row(dialog, "main", COUNT).approval_note_shown()
    assert not row(dialog, "main", FLAG).approval_note_shown()


# Custom editors, companion keys and restart reasons ----------------------------------

ROWS = "dlg.rows"
COMPANION = "dlg.rows_extra"


class PairEditor(FieldEditor):
    """Test-only editor owning its field's value and one companion value."""

    def __init__(self, _field):
        super().__init__()
        self._value = None
        self._companion = None

    def value(self):
        return self._value

    def set_value(self, value):
        self._value = value

    def set_read_only(self, read_only): ...

    def companion_values(self):
        return {COMPANION: self._companion}

    def set_companion_values(self, values):
        self._companion = values.get(COMPANION)

    def user_edit(self, value, companion):
        self._value, self._companion = value, companion
        self.changed.emit()


def test_companion_written_and_reported_to_after_save(qtbot, env):
    env.prefs.allowed.update({ROWS: {USER}, COMPANION: {USER}})
    env.main._sections = [
        Section("Rows", [SettingField(ROWS, Custom(PairEditor), label="Rows", companions=(COMPANION,))])
    ]
    dialog = make_dialog(qtbot, env)
    dialog._prompt_restart = lambda: pytest.fail("no restart expected")
    row(dialog, "main", ROWS).editor.user_edit("a", ["x"])
    assert dialog._apply() is True
    assert ("set", ROWS, "a", USER) in env.prefs.calls
    assert ("set", COMPANION, ["x"], USER) in env.prefs.calls
    assert env.main.after_save_calls == [(USER, {ROWS: "a", COMPANION: ["x"]})]
    assert env.main.reason_calls == [(USER, {ROWS: "a", COMPANION: ["x"]})]
    assert dialog._label(COMPANION) == "Rows"


def test_restart_reason_prompts_once_with_reasons(qtbot, env):
    reason = "Device backends changed."
    env.main.reason = reason
    dialog = make_dialog(qtbot, env)
    prompts: list[list[str]] = []
    dialog._prompt_restart = lambda: prompts.append(list(dialog._restart_reasons))
    click_toggle(qtbot, row(dialog, "main", FLAG), "On")
    dialog.select_scope(BEAMLINE)
    click_toggle(qtbot, row(dialog, "main", FLAG), "Off")
    assert dialog._apply() is True
    assert sorted(scope for scope, _ in env.main.reason_calls) == sorted([USER, BEAMLINE])
    assert prompts == [[reason]]
    assert dialog._restart_reasons == [reason]


def test_no_reason_no_restart_field_no_prompt(qtbot, env):
    dialog = make_dialog(qtbot, env)
    prompts: list[int] = []
    dialog._prompt_restart = lambda: prompts.append(1)
    click_toggle(qtbot, row(dialog, "main", FLAG), "On")
    assert dialog._apply() is True
    assert env.main.reason_calls == [(USER, {FLAG: True})]
    assert prompts == []
    assert dialog._restart_reasons == []


def test_restart_reasons_reset_on_each_apply(qtbot, env):
    env.main.reason = "Backends changed."
    dialog = make_dialog(qtbot, env)
    dialog._prompt_restart = lambda: None
    click_toggle(qtbot, row(dialog, "main", FLAG), "On")
    dialog._apply()
    env.main.reason = ""
    click_toggle(qtbot, row(dialog, "main", FLAG), "Off")
    dialog._apply()
    assert dialog._restart_reasons == []


def test_restart_prompt_lists_reasons_under_its_sentence(qtbot, env):
    dialog = make_dialog(qtbot, env)
    dialog._restart_reasons = ["First reason.", "Second reason."]
    dialog._prompt_restart()
    text = env.boxes[-1][1]
    assert text.startswith("Some of the changed settings take effect after Lightfall restarts.")
    assert text.endswith("First reason.\nSecond reason.")


def test_custom_page_header_text_and_no_load_save(qtbot, env):
    dialog = make_dialog(qtbot, env)  # would raise if load_settings were called
    page = dialog._stack.widget(page_index(dialog, "custom"))
    assert page.widget() is env.custom.widget
    dialog._sidebar.setCurrentRow(page_index(dialog, "custom"))
    h = header(dialog)
    assert not h._combo.isEnabled()
    assert h._state.text() == CUSTOM_TEXT == "Changes on this page apply immediately"
    dialog.accept()  # would raise if validate or save_settings were called
    assert env.prefs.calls == []


def test_custom_page_logs_no_warning(qtbot, env, monkeypatch):
    warnings: list[str] = []
    monkeypatch.setattr("lightfall.ui.preferences.dialog.logger.warning", lambda msg, *a, **k: warnings.append(msg))
    make_dialog(qtbot, env)
    assert warnings == []


def test_scopes_setting_keys_profile_are_custom_pages():
    from lightfall.plugins.settings_plugin import SettingsPlugin
    from lightfall.ui.preferences.dialog import _is_custom_page
    from lightfall.ui.preferences.scopes_settings import ScopesSettingsPlugin
    from lightfall.ui.preferences.setting_keys_settings import SettingKeysSettingsPlugin
    from lightfall.ui.preferences.user_profile_settings import UserProfileSettingsPlugin

    for cls in (ScopesSettingsPlugin, SettingKeysSettingsPlugin, UserProfileSettingsPlugin):
        assert cls.scope_aware is False
        assert cls.create_widget is not SettingsPlugin.create_widget
        assert "load_settings" not in vars(cls) and "save_settings" not in vars(cls)
        assert _is_custom_page(cls())


# Legacy removal -------------------------------------------------------------------

OUTDATED_TEXT = "Outdated uses an older settings API and can't be shown. Update the plugin."


def test_outdated_plugin_gets_placeholder_and_error_log(qtbot, env, monkeypatch):
    errors: list[str] = []
    monkeypatch.setattr(
        "lightfall.ui.preferences.dialog.logger.error",
        lambda msg, *a, **k: errors.append(msg.format(*a) if a else msg),
    )
    dialog = make_dialog(qtbot, env)
    assert "outdated" in dialog._page_names  # the dialog opened with every page
    dialog._sidebar.setCurrentRow(page_index(dialog, "outdated"))
    page = dialog._stack.currentWidget().widget()
    labels = [lbl.text() for lbl in page.findChildren(QLabel)]
    assert OUTDATED_TEXT in labels
    assert dialog._sidebar.item(page_index(dialog, "outdated")).text() == "Outdated"
    h = header(dialog)
    assert not h._combo.isEnabled()
    assert h._state.text() == ""
    assert any("outdated" in m and "older settings API" in m for m in errors)
    click_toggle(qtbot, row(dialog, "main", FLAG), "On")
    dialog.accept()
    assert env.prefs.calls == [("set", FLAG, True, USER)]
    assert env.outdated.calls == []  # never built, loaded, validated, saved or reverted


def test_apply_never_calls_save_settings(qtbot, env):
    from lightfall.plugins.settings_plugin import SettingsPlugin

    for name in ("load_settings", "save_settings", "validate", "apply_preview"):
        assert not hasattr(SettingsPlugin, name), name
    for name in ("create_widget", "revert_preview", "on_loaded"):
        assert hasattr(SettingsPlugin, name), name
    dialog = make_dialog(qtbot, env)
    click_toggle(qtbot, row(dialog, "main", FLAG), "On")
    assert dialog._apply() is True  # the custom page's save_settings would raise
    dialog.reject()
    assert env.outdated.calls == []


def test_custom_pages_still_open(qtbot, env, monkeypatch):
    from lightfall.plugins.settings_plugin import SettingsPlugin
    from lightfall.ui.preferences.scopes_settings import ScopesSettingsPlugin
    from lightfall.ui.preferences.setting_keys_settings import SettingKeysSettingsPlugin
    from lightfall.ui.preferences.user_profile_settings import UserProfileSettingsPlugin

    monkeypatch.setattr("lightfall.ui.preferences.scopes_settings.scopes_available", lambda: False)
    monkeypatch.setattr("lightfall.ui.preferences.setting_keys_settings.scopes_available", lambda: False)
    plugins = [ScopesSettingsPlugin(), SettingKeysSettingsPlugin(), UserProfileSettingsPlugin()]

    def forbidden(*_a, **_k):  # pragma: no cover - must never be called
        raise AssertionError("old settings method called")

    for plugin in plugins:
        for name in ("load_settings", "save_settings", "validate", "apply_preview"):
            setattr(plugin, name, forbidden)
    infos = [SimpleNamespace(instance=p) for p in plugins]
    registry = SimpleNamespace(get_ready_by_type=lambda _t: list(infos))
    services = SimpleNamespace(get=lambda _cls: registry)
    monkeypatch.setattr(
        "lightfall.core.services.ServiceRegistry.get_instance", classmethod(lambda cls: services)
    )
    dialog = make_dialog(qtbot, env)
    assert sorted(dialog._page_names) == ["scopes", "setting_keys", "user_profile"]
    for plugin in plugins:
        assert type(plugin).create_widget is not SettingsPlugin.create_widget
        dialog._sidebar.setCurrentRow(page_index(dialog, plugin.name))
        assert dialog._stack.currentWidget().widget() is dialog._plugin_widgets[plugin.name]
        assert header(dialog)._state.text() == CUSTOM_TEXT
    status = dialog._plugin_widgets["scopes"].findChild(QLabel, "scopesStatus")
    assert status.text().startswith("Scoped settings unavailable")
    assert dialog._apply() is True
    dialog.reject()
