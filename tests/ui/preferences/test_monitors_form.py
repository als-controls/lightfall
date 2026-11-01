"""Monitors page: scope-aware catalogs and mappings writing the scoped ``monitors.*`` keys.

The old page wrote seven keys; see commit 6e22b486 for its characterization. The two
scalars (``monitor_advisor_enabled``, ``monitor_tick_interval``) stay as they were, at User.
The two plugin lists become ``monitors.enabled`` ({name: False} for a disabled default-on
monitor, {name: True} for a default-off one switched on), ``disabled_monitor_feeds`` becomes
``monitor_feeds.enabled`` and the two per-feed dicts become ``monitor_feeds.interval`` and
``monitor_feeds.severity``. Feeds of a disabled monitor plugin are drawn grey, as before.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from PySide6.QtCore import QObject, Signal
from PySide6.QtWidgets import QComboBox, QLineEdit, QMessageBox, QTableWidget

from lightfall.monitor.feed import MonitorFeed
from lightfall.monitor.monitor_plugin import MonitorPlugin
from lightfall.monitor.registry import MonitorRegistry
from lightfall.ui.preferences.dialog import PreferencesDialog
from lightfall.ui.preferences.form.editors import muted_color
from lightfall.ui.preferences.form.row import SettingRow
from lightfall.ui.preferences.form.view import SettingsForm
from lightfall.ui.preferences.scope_api import ScopeChoice

MONITORS, FEEDS = "monitors.enabled", "monitor_feeds.enabled"
INTERVALS, SEVERITY = "monitor_feeds.interval", "monitor_feeds.severity"
ADVISOR, TICK = "monitor_advisor_enabled", "monitor_tick_interval"
USER, BEAMLINE, FACILITY = "user:u1", "beamline:7.0.1", "facility"
OFF_NOTE = "monitor plugin is off"


def make_feed(name, interval=30.0):
    return type(f"Feed_{name}", (MonitorFeed,), {
        "name": name,
        "default_interval_s": interval,
        "evaluate": lambda self, ctx, window, prior: None,
    })()


def make_monitor(name, feeds=(), category="general", default=True):
    return type(f"Monitor_{name}", (MonitorPlugin,), {
        "name": property(lambda self: name),
        "description": property(lambda self: f"{name} monitor"),
        "category": property(lambda self: category),
        "enabled_by_default": property(lambda self: default),
        "create_feeds": lambda self: [make_feed(*f) for f in feeds],
    })()


def monitors():
    return [
        make_monitor("health", [("acq_health", 15.0), ("beam_status", 60.0)]),
        make_monitor("xpcs", [("xpcs_quality", 30.0)], "analysis", default=False),
    ]


@pytest.fixture
def registry():
    MonitorRegistry.reset_instance()
    reg = MonitorRegistry.get_instance()
    for m in monitors():
        reg.register(m)
    yield reg
    MonitorRegistry.reset_instance()


class FakePrefs(QObject):
    scopes_changed = Signal(object)

    def __init__(self):
        super().__init__()
        self.layers: dict = {}
        self.resolved: dict = {}
        self.inherited: dict = {}
        self.calls: list[tuple] = []

    def scope_choices(self):
        return [
            ScopeChoice(USER, "user", "User", None, ""),
            ScopeChoice(BEAMLINE, "beamline", "Beamline", "operator", ""),
            ScopeChoice(FACILITY, "facility", "Facility", "viewer", "Read-only"),
        ]

    def default_scope(self):
        return USER

    def allowed_at(self, key, scope):
        from lightfall.scopes.keys import client_registry
        from lightfall.ui.preferences.form.schema import scope_kind

        return client_registry().get(key).allows(scope_kind(scope))

    def writable_at(self, key, scope):
        return scope in (USER, BEAMLINE) and self.allowed_at(key, scope)

    def lock_at(self, key, scope):
        return None

    def layer_value(self, key, scope):
        return self.layers.get((scope, key))

    def inherited_items(self, key, scope):
        return self.inherited.get(key, {}) if scope == USER else {}

    def resolved_items(self, key):
        return dict(self.resolved.get(key, {}))

    def get(self, key, default=None):
        return self.resolved.get(key, default)

    def set(self, key, value, *, scope=None):
        self.calls.append((key, value, scope))

    def remove(self, key, *, scope=None):
        self.calls.append((key, None, scope))

    def subscribe(self, key, slot):
        pass

    def unsubscribe(self, key, slot):
        pass


@pytest.fixture
def env(monkeypatch, registry):
    from lightfall.ui.preferences.monitor_settings import MonitorSettingsPlugin

    pages = [MonitorSettingsPlugin()]
    prefs = FakePrefs()
    plugins = SimpleNamespace(
        get_ready_by_type=lambda _t: [SimpleNamespace(instance=p) for p in pages],
        get_all=lambda: [],
    )
    services = SimpleNamespace(get=lambda _cls: plugins)
    monkeypatch.setattr(
        "lightfall.core.services.ServiceRegistry.get_instance", classmethod(lambda cls: services)
    )
    monkeypatch.setattr(
        "lightfall.ui.preferences.manager.PreferencesManager.get_instance", classmethod(lambda cls: prefs)
    )
    boxes: list[str] = []
    monkeypatch.setattr(QMessageBox, "warning", lambda _p, _t, text, *a, **k: boxes.append(text))
    return SimpleNamespace(prefs=prefs, pages=pages, boxes=boxes)


def _dialog(qtbot):
    dialog = PreferencesDialog()
    qtbot.addWidget(dialog)

    def no_restart():
        raise AssertionError("the Monitors page must not ask for a restart")

    dialog._prompt_restart = no_restart
    return dialog


def _form(dialog) -> SettingsForm:
    return next(f for f in dialog.findChildren(SettingsForm) if f._plugin.name == "monitor")


def _row(dialog, key) -> SettingRow:
    return next(r for r in _form(dialog).findChildren(SettingRow) if r.key == key)


def _table(dialog, key) -> QTableWidget:
    return _row(dialog, key).editor.findChild(QTableWidget)


def _pick(dialog, key, item, text):
    box: QComboBox = _row(dialog, key).editor.item_combo(item)
    box.setCurrentIndex(box.findText(text))


def _type(dialog, key, item, text):
    edit = _row(dialog, key).editor.item_editor(item).findChild(QLineEdit)
    edit.setText(text)
    edit.textEdited.emit(text)


def _inherited(dialog, key):
    t = _table(dialog, key)
    last = t.columnCount() - 1
    return {t.item(r, 0).text(): t.item(r, last).text() for r in range(t.rowCount())}


def test_page_declares_union_specs_and_keeps_the_scalars_user_only():
    from lightfall.ui.preferences.monitor_settings import MonitorSettingsPlugin

    page = MonitorSettingsPlugin()
    assert (page.name, page.display_name, page.category, page.priority) == ("monitor", "Monitor", "advanced", 95)
    specs = {s.key: s for s in page.specs()}
    assert set(specs) == {MONITORS, FEEDS, INTERVALS, SEVERITY, ADVISOR, TICK}
    for key, tp in ((MONITORS, bool), (FEEDS, bool), (INTERVALS, int), (SEVERITY, str)):
        assert (specs[key].policy, specs[key].type) == ("union", tp), key
        assert specs[key].scopes == {"user", "branch", "beamline", "facility"}
    for key in (ADVISOR, TICK):
        assert specs[key].scopes == {"user"} and specs[key].policy == "override"


def test_page_sections_and_tables(qtbot, env):
    from lightfall.ui.preferences.monitor_settings import MonitorSettingsPlugin

    titles = [s.title for s in MonitorSettingsPlugin().sections()]
    assert titles == ["Monitor", "Monitor plugins", "Feeds", "Feed intervals", "Advisor severity"]
    dialog = _dialog(qtbot)
    plugins = _table(dialog, MONITORS)
    assert [[plugins.item(r, c).text() for c in range(3)] for r in range(plugins.rowCount())] == [
        ["Xpcs", "Analysis", "xpcs monitor"],  # sorted by category, then display name, as before
        ["Health", "General", "health monitor"],
    ]
    feeds = _table(dialog, FEEDS)
    assert [[feeds.item(r, c).text() for c in range(2)] for r in range(feeds.rowCount())] == [
        ["acq_health", "Health"], ["beam_status", "Health"], ["xpcs_quality", "Xpcs"],
    ]
    intervals = _table(dialog, INTERVALS)
    headers = [intervals.horizontalHeaderItem(c).text() for c in range(intervals.columnCount())]
    assert headers == ["Name", "Monitor", "Here (s)", "Inherited"]
    assert _inherited(dialog, INTERVALS) == {
        "acq_health": "15 s (default)", "beam_status": "60 s (default)", "xpcs_quality": "30 s (default)"}
    assert set(_inherited(dialog, SEVERITY).values()) == {"Info (default)"}
    assert _inherited(dialog, MONITORS) == {"Xpcs": "Off (default)", "Health": "On (default)"}


def test_severity_choices(qtbot, env):
    dialog = _dialog(qtbot)
    box = _row(dialog, SEVERITY).editor.item_combo("acq_health")
    assert [box.itemText(i) for i in range(box.count())] == ["Not set", "Info", "Warn", "Critical"]


def test_page_writes_each_key_at_user(qtbot, env):
    dialog = _dialog(qtbot)
    _pick(dialog, MONITORS, "health", "Off")
    _pick(dialog, MONITORS, "xpcs", "On")
    _pick(dialog, FEEDS, "beam_status", "Off")
    _type(dialog, INTERVALS, "acq_health", "5")
    _pick(dialog, SEVERITY, "acq_health", "Warn")
    advisor = _row(dialog, ADVISOR).editor.findChild(QComboBox)
    advisor.setCurrentIndex(advisor.findText("On"))
    tick = _row(dialog, TICK).editor.findChild(QLineEdit)
    tick.setText("45")
    tick.textEdited.emit("45")
    assert dialog._apply() is True
    assert sorted(env.prefs.calls) == sorted([
        (MONITORS, {"health": False, "xpcs": True}, USER),
        (FEEDS, {"beam_status": False}, USER),
        (INTERVALS, {"acq_health": 5}, USER),
        (SEVERITY, {"acq_health": "warn"}, USER),
        (ADVISOR, True, USER),
        (TICK, 45, USER),
    ], key=str)


def test_union_keys_at_beamline_scalars_not_there(qtbot, env):
    dialog = _dialog(qtbot)
    dialog.select_scope(BEAMLINE)
    _type(dialog, INTERVALS, "beam_status", "120")
    _pick(dialog, SEVERITY, "beam_status", "Critical")
    assert dialog._apply() is True
    assert sorted(env.prefs.calls, key=str) == sorted([
        (SEVERITY, {"beam_status": "critical"}, BEAMLINE),
        (INTERVALS, {"beam_status": 120}, BEAMLINE),
    ], key=str)
    form = _form(dialog)
    shown = {r.key for r in form.findChildren(SettingRow) if r.isVisibleTo(form)}
    assert {MONITORS, FEEDS, INTERVALS, SEVERITY} <= shown
    assert ADVISOR not in shown and TICK not in shown


def test_interval_must_be_positive(qtbot, env):
    dialog = _dialog(qtbot)
    _type(dialog, INTERVALS, "acq_health", "0")
    assert dialog._apply() is False
    assert env.prefs.calls == []
    assert env.boxes
    _type(dialog, INTERVALS, "acq_health", "1")
    assert dialog._apply() is True
    assert env.prefs.calls == [(INTERVALS, {"acq_health": 1}, USER)]


def test_feeds_of_a_disabled_monitor_are_greyed(qtbot, env):
    env.prefs.resolved[MONITORS] = {"health": False}  # xpcs is off by default
    dialog = _dialog(qtbot)
    for key in (FEEDS, INTERVALS, SEVERITY):
        t = _table(dialog, key)
        muted = muted_color(t)
        cells = {t.item(r, 0).text(): t.item(r, 0) for r in range(t.rowCount())}
        assert set(cells) == {"acq_health", "beam_status", "xpcs_quality"}
        for cell in cells.values():
            assert cell.foreground().color() == muted, key
            assert OFF_NOTE in cell.toolTip(), key
    # greyed rows stay editable, as the old page's were
    assert _row(dialog, FEEDS).editor.item_combo("xpcs_quality").isEnabled()

    env.prefs.resolved[MONITORS] = {"xpcs": True}
    t = _table(_dialog(qtbot), FEEDS)
    cells = {t.item(r, 0).text(): t.item(r, 0) for r in range(t.rowCount())}
    assert OFF_NOTE not in cells["acq_health"].toolTip()
    assert OFF_NOTE not in cells["xpcs_quality"].toolTip()


def test_user_layer_values_show(qtbot, env):
    env.prefs.layers[(USER, INTERVALS)] = {"acq_health": 9}
    env.prefs.layers[(USER, SEVERITY)] = {"beam_status": "critical"}
    env.prefs.layers[(USER, TICK)] = 45
    env.prefs.inherited[INTERVALS] = {"beam_status": (90, "Beamline 7.0.1")}
    dialog = _dialog(qtbot)
    assert _row(dialog, INTERVALS).editor.item_editor("acq_health").findChild(QLineEdit).text() == "9"
    assert _row(dialog, SEVERITY).editor.item_combo("beam_status").currentText() == "Critical"
    assert _row(dialog, TICK).editor.value() == 45
    assert _inherited(dialog, INTERVALS)["beam_status"] == "90 s (Beamline 7.0.1)"
    assert dialog._apply() is True
    assert env.prefs.calls == []


def test_stored_invalid_interval_is_kept_when_untouched(qtbot, env):
    env.prefs.layers[(USER, INTERVALS)] = {"acq_health": 0, "beam_status": 20}
    dialog = _dialog(qtbot)
    assert _row(dialog, INTERVALS).editor.value() == {"acq_health": 0, "beam_status": 20}
    assert dialog._apply() is True
    assert env.prefs.calls == []
