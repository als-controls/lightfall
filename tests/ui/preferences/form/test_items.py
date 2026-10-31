"""Form items that are not preferences: actions, status lines, embedded widgets."""
from __future__ import annotations

import threading

import pytest
import shiboken6
from qtpy.QtCore import QCoreApplication, QEvent
from qtpy.QtGui import QPalette
from qtpy.QtWidgets import (
    QApplication,
    QDialog,
    QGroupBox,
    QLabel,
    QLineEdit,
    QListWidget,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
)

from lightfall.ui.preferences.form.editors import error_color, muted_color
from lightfall.ui.preferences.form.items import ActionItem, InfoItem
from lightfall.ui.preferences.form.model import FormModel
from lightfall.ui.preferences.form.row import SettingRow
from lightfall.ui.preferences.form.schema import Action, Embed, Info, Section, SettingField, Text
from lightfall.ui.preferences.scope_api import ScopeChoice

USER, LOCAL, BEAMLINE = "user:u1", "local", "beamline:7.0.1"

NAME = "items.name"  # user + beamline text

ALLOWED = {NAME: {USER, BEAMLINE}}


class FakePrefs:
    def __init__(self, layers=None, writable=None):
        self.layers = dict(layers or {})
        self.writable = writable if writable is not None else {USER, LOCAL}

    def allowed_at(self, key, scope):
        return scope in ALLOWED.get(key, set())

    def writable_at(self, key, scope):
        return self.allowed_at(key, scope) and scope in self.writable

    def lock_at(self, key, scope):
        return None

    def layer_value(self, key, scope):
        return self.layers.get((scope, key))

    def scope_choices(self):
        return [
            ScopeChoice(USER, "user", "User", None, ""),
            ScopeChoice(LOCAL, "local", "This console", None, ""),
            ScopeChoice(BEAMLINE, "beamline", "Beamline 7.0.1", "viewer", "Read-only: you are a viewer here"),
        ]


class FakePlugin:
    def __init__(self, sections):
        self._sections = sections

    def sections(self):
        return self._sections


def make_form(qtbot, sections, prefs=None, scope=USER, resolve=None, parent=None):
    from lightfall.ui.preferences.form.view import SettingsForm

    prefs = prefs or FakePrefs()
    model = FormModel(prefs.layer_value, resolve=resolve)
    form = SettingsForm(FakePlugin(sections), model, prefs, parent=parent)
    if parent is None:
        qtbot.addWidget(form)
    form.show_scope(scope)
    return form, model


def action_item(form) -> ActionItem:
    return form.findChild(ActionItem)


def text_color(label: QLabel):
    return label.palette().color(QPalette.ColorGroup.Active, QPalette.ColorRole.WindowText)


def type_into(qtbot, form, key, text):
    row = next(r for r in form.findChildren(SettingRow) if r.key == key)
    line = row.editor.findChild(QLineEdit)
    line.setFocus()
    qtbot.keyClicks(line, text)


def test_action_runs_off_gui_thread_and_reports(qtbot):
    seen = {}

    def run(values):
        seen["thread"] = threading.current_thread()
        seen["scope"] = values.scope
        seen["name"] = values.get(NAME)
        return "Connected"

    form, _ = make_form(
        qtbot,
        [Section("Server", [SettingField(NAME, Text(), label="Name"), Action("Test connection", run)])],
        prefs=FakePrefs({(USER, NAME): "srv"}),
    )
    item = action_item(form)
    button = item.findChild(QPushButton)
    assert button.text() == "Test connection"
    button.click()
    assert not button.isEnabled()
    assert item.status.text() == "Working…"
    assert text_color(item.status) == muted_color(item.status)
    qtbot.waitUntil(lambda: item.status.text() == "Connected", timeout=5000)
    assert seen["thread"] is not threading.main_thread()
    assert seen["scope"] == USER and seen["name"] == "srv"
    assert button.isEnabled()
    assert text_color(item.status) != error_color(item.status)
    item.future.wait(5000)


def test_action_failure_shows_error(qtbot):
    def run(_values):
        raise RuntimeError("host unreachable")

    form, _ = make_form(qtbot, [Section("Server", [Action("Test", run)])])
    item = action_item(form)
    item.findChild(QPushButton).click()
    qtbot.waitUntil(lambda: item.status.text() == "host unreachable", timeout=5000)
    assert text_color(item.status) == error_color(item.status)
    assert item.findChild(QPushButton).isEnabled()
    item.future.wait(5000)


def test_action_result_after_close_is_dropped(qtbot, monkeypatch):
    boxes = []
    for name in ("warning", "critical", "information"):
        monkeypatch.setattr(QMessageBox, name, lambda *a, _n=name, **k: boxes.append(_n))
    gate = threading.Event()

    def run(_values):
        gate.wait(5)
        return "late result"

    dialog = QDialog()
    QVBoxLayout(dialog)
    form, _ = make_form(qtbot, [Section("Server", [Action("Test", run)])], parent=dialog)
    dialog.layout().addWidget(form)
    dialog.show()
    item = action_item(form)
    item.findChild(QPushButton).click()
    future = item.future
    assert future.isRunning()

    dialog.close()
    dialog.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    assert not shiboken6.isValid(item)

    gate.set()
    assert future.wait(5000)
    QApplication.processEvents()
    QCoreApplication.sendPostedEvents()
    assert boxes == []
    assert future.isFinished()


def test_action_scopes_filter(qtbot):
    sections = [Section("Tools", [
        SettingField(NAME, Text(), label="Name"),
        Action("Console only", lambda v: "", scopes=frozenset({"local"})),
        Action("Everywhere", lambda v: ""),
    ])]
    form, _ = make_form(qtbot, sections, scope=USER)
    assert [b.text() for b in form.findChildren(QPushButton)] == ["Everywhere"]
    form.show_scope(LOCAL)
    assert sorted(b.text() for b in form.findChildren(QPushButton)) == ["Console only", "Everywhere"]


def test_action_enabled_when_and_never_disabled_by_read_only_scope(qtbot):
    sections = [Section("Tools", [
        SettingField(NAME, Text(), label="Name"),
        Action("Go", lambda v: "", enabled_when=lambda v: bool(v.get(NAME))),
    ])]
    prefs = FakePrefs({(BEAMLINE, NAME): "x"})
    form, _ = make_form(qtbot, sections, prefs=prefs, scope=BEAMLINE)  # beamline is read-only here
    assert action_item(form).findChild(QPushButton).isEnabled()
    form.show_scope(USER)
    button = action_item(form).findChild(QPushButton)
    assert not button.isEnabled()
    type_into(qtbot, form, NAME, "a")
    assert button.isEnabled()


def test_info_text_updates_after_edit(qtbot):
    sections = [Section("Server", [
        SettingField(NAME, Text(), label="Name"),
        Info(lambda v: f"Will connect to {v.get(NAME) or 'nothing'}"),
        Info("Static note"),
    ])]
    form, _ = make_form(qtbot, sections)
    infos = form.findChildren(InfoItem)
    assert [i.text() for i in infos] == ["Will connect to nothing", "Static note"]
    assert text_color(infos[0]) == muted_color(infos[0])
    type_into(qtbot, form, NAME, "abc")
    assert infos[0].text() == "Will connect to abc"


def test_info_with_empty_text_is_hidden(qtbot):
    sections = [Section("Server", [
        SettingField(NAME, Text(), label="Name"),
        Info(lambda v: f"Name is {v.get(NAME)}" if v.get(NAME) else ""),
    ])]
    form, _ = make_form(qtbot, sections)
    info = form.findChild(InfoItem)
    assert not info.isVisibleTo(form)
    type_into(qtbot, form, NAME, "abc")
    assert info.isVisibleTo(form)
    assert info.text() == "Name is abc"


def test_embed_shown_only_at_its_scopes(qtbot):
    built = []

    def factory(parent):
        w = QListWidget(parent)
        built.append(w)
        return w

    sections = [Section("Lists", [Embed(factory, scopes=frozenset({"user", "beamline"}))])]
    form, _ = make_form(qtbot, sections, scope=USER)
    assert len(form.findChildren(QListWidget)) == 1
    assert built[-1].parent() is form.findChild(QGroupBox)
    form.show_scope(LOCAL)
    assert form.findChildren(QListWidget) == []
    assert form.findChildren(QGroupBox) == []
    form.show_scope(BEAMLINE)
    assert len(form.findChildren(QListWidget)) == 1


def test_effective_prefers_edit_then_resolved():
    resolved = {NAME: "from-store"}
    model = FormModel(lambda key, scope: None, resolve=lambda key, default: resolved.get(key, default))
    values = model.values(USER)
    assert values.effective(NAME) == "from-store"
    assert values.effective("other", "dflt") == "dflt"
    model.edit(USER, NAME, "typed")
    assert values.effective(NAME) == "typed"
    model.edit(USER, NAME, None)  # a Clear shows the currently resolved value
    assert values.effective(NAME) == "from-store"

    bare = FormModel(lambda key, scope: {(USER, NAME): "layer"}.get((scope, key)))
    assert bare.values(USER).effective(NAME) == "layer"
    assert bare.values(USER).effective("other", 5) == 5


def test_section_with_only_info_is_shown_but_page_has_no_fields(qtbot):
    sections = [Section("About", [Info("Nothing to set here")])]
    form, _ = make_form(qtbot, sections, scope=LOCAL)
    assert [b.title() for b in form.findChildren(QGroupBox)] == ["About"]
    assert not form.has_fields(LOCAL)


def test_action_reading_a_key_outside_its_page_fails_clearly(qtbot):
    def run(values):
        return values.get("elsewhere.key")

    form, _ = make_form(qtbot, [Section("Server", [SettingField(NAME, Text()), Action("Test", run)])])
    item = action_item(form)
    item.findChild(QPushButton).click()
    expected = "elsewhere.key is not a field on this page; actions can only read their page's fields"
    qtbot.waitUntil(lambda: item.status.text() == expected, timeout=5000)
    assert text_color(item.status) == error_color(item.status)
    item.future.wait(5000)

    snapshot = form._action_values(USER)
    with pytest.raises(KeyError, match="elsewhere.key is not a field on this page"):
        snapshot.effective("elsewhere.key", 1)


def test_action_snapshot_does_not_read_the_store(qtbot):
    reads = []
    prefs = FakePrefs({(LOCAL, NAME): "never-read"})

    def read_layer(key, scope):
        reads.append((scope, key))
        return prefs.layer_value(key, scope)

    from lightfall.ui.preferences.form.view import SettingsForm

    seen = {}

    def run(values):
        seen["get"] = values.get(NAME)
        seen["effective"] = values.effective(NAME, "dflt")
        return "ok"

    model = FormModel(read_layer, resolve=lambda key, default: "resolved" if key == NAME else default)
    form = SettingsForm(FakePlugin([Section("Tools", [SettingField(NAME, Text()), Action("Go", run)])]), model, prefs)
    qtbot.addWidget(form)
    form.show_scope(LOCAL)  # NAME is not allowed at local
    cache_before = dict(model._loaded)
    item = action_item(form)
    item.findChild(QPushButton).click()
    qtbot.waitUntil(lambda: item.status.text() == "ok", timeout=5000)
    item.future.wait(5000)
    assert seen == {"get": None, "effective": "resolved"}
    assert model._loaded == cache_before
    assert reads == []


def test_info_that_raises_shows_error_text_and_form_keeps_working(qtbot):
    def broken(_values):
        raise ValueError("no binding yet")

    sections = [Section("Server", [
        SettingField(NAME, Text(), label="Name"),
        Info(broken),
        Action("Go", lambda v: "", enabled_when=lambda v: bool(v.get(NAME))),
    ])]
    form, _ = make_form(qtbot, sections)
    info = form.findChild(InfoItem)
    assert info.text() == "no binding yet"
    assert text_color(info) == muted_color(info)
    button = action_item(form).findChild(QPushButton)
    assert not button.isEnabled()
    type_into(qtbot, form, NAME, "a")  # predicates after the broken Info still run
    assert button.isEnabled()
