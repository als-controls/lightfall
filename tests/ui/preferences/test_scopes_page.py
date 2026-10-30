"""Read-only Scopes Preferences page."""
from __future__ import annotations

import threading

import pytest
from PySide6.QtWidgets import QComboBox, QLabel, QTableWidget

from lightfall.scopes.client import ScopesError
from lightfall.scopes.view import KeyRow, ScopesView
from lightfall.ui.preferences.scopes_settings import ScopesSettingsPlugin, format_value

VIEW = ScopesView(
    bind="branch:7.0.1.1",
    chain=("facility", "beamline:7.0.1", "branch:7.0.1.1", "user:u1"),
    roles={"facility": "admin", "beamline:7.0.1": "admin", "branch:7.0.1.1": "admin",
           "user:u1": "owner"},
    entitled=("beamline:7.0.1", "branch:7.0.1.1", "branch:7.0.1.2"),
    facility_label="ALS",
    keys=(KeyRow("voice.wake_word", "approved", ("branch", "user"), "hey", "branch:7.0.1.1",
                 "branch:7.0.1.1", "demo", ()),),
    keys_restricted=False, offline=False, cached_at=None,
)


@pytest.fixture(autouse=True)
def _bound_machine(monkeypatch):
    monkeypatch.setattr("lightfall.scopes.site.get_site_bind", lambda: "branch:7.0.1.1")


def _page(qtbot, loader):
    plugin = ScopesSettingsPlugin()
    plugin.loader = loader
    widget = plugin.create_widget()
    qtbot.addWidget(widget)
    return plugin, widget


def _status(widget):
    return widget.findChild(QLabel, "scopesStatus").text()


def test_renders_chain_roles_and_keys(qtbot):
    _, w = _page(qtbot, lambda bind: VIEW)
    chain = w.findChild(QTableWidget, "scopesChain")
    qtbot.waitUntil(lambda: chain.rowCount() == 4, timeout=3000)
    assert chain.item(0, 0).text() == "Facility (ALS)"
    assert chain.item(0, 1).text() == "admin"
    assert chain.item(3, 0).text() == "Me (user:u1)"
    keys = w.findChild(QTableWidget, "scopesKeys")
    assert [keys.item(0, c).text() for c in range(6)] == [
        "voice.wake_word", "approved", "branch, user", "'hey'", "branch:7.0.1.1",
        "🔒 branch:7.0.1.1"]
    assert keys.item(0, 5).toolTip() == "demo"


def test_combo_lists_bind_and_entitled_once(qtbot):
    _, w = _page(qtbot, lambda bind: VIEW)
    combo = w.findChild(QComboBox, "scopesBind")
    qtbot.waitUntil(lambda: combo.count() == 3, timeout=3000)
    assert [combo.itemText(i) for i in range(3)] == [
        "branch:7.0.1.1", "beamline:7.0.1", "branch:7.0.1.2"]


def test_choosing_another_scope_reloads_with_it(qtbot):
    seen = []

    def loader(bind):
        seen.append(bind)
        return VIEW

    _, w = _page(qtbot, loader)
    combo = w.findChild(QComboBox, "scopesBind")
    qtbot.waitUntil(lambda: combo.count() == 3, timeout=3000)
    combo.setCurrentIndex(2)
    qtbot.waitUntil(lambda: "branch:7.0.1.2" in seen, timeout=3000)


def test_not_logged_in_message(qtbot):
    def loader(bind):
        raise ScopesError("Not authenticated", status=401)

    _, w = _page(qtbot, loader)
    qtbot.waitUntil(lambda: _status(w) == "Log in to see scoped settings", timeout=3000)


def test_offline_shows_cache_age(qtbot, monkeypatch):
    import lightfall.ui.preferences.scopes_settings as mod

    monkeypatch.setattr(mod, "_now", lambda: 1000.0 + 12 * 60)
    offline = ScopesView(**{**VIEW.__dict__, "offline": True, "cached_at": 1000.0})
    _, w = _page(qtbot, lambda bind: offline)
    qtbot.waitUntil(lambda: _status(w) == "Offline — cached 12 min ago", timeout=3000)


def test_restricted_note_visible_only_when_restricted(qtbot):
    restricted = ScopesView(**{**VIEW.__dict__, "keys_restricted": True})
    _, w = _page(qtbot, lambda bind: restricted)
    note = w.findChild(QLabel, "scopesKeysNote")
    qtbot.waitUntil(lambda: not note.isHidden(), timeout=3000)


def test_result_after_widget_deleted_is_ignored(qtbot):
    gate = threading.Event()

    def slow(bind):
        gate.wait(3)
        return VIEW

    plugin = ScopesSettingsPlugin()
    plugin.loader = slow
    widget = plugin.create_widget()
    with qtbot.waitSignal(widget.destroyed, timeout=2000):
        widget.deleteLater()
    errors = []
    import sys
    old = sys.excepthook
    sys.excepthook = lambda *a: errors.append(a[1])
    try:
        gate.set()
        qtbot.wait(300)
    finally:
        sys.excepthook = old
    assert errors == []


@pytest.mark.parametrize("value,text", [(None, "—"), ("x", "'x'"), (True, "True"),
                                        ({"a": True}, "{'a': True}")])
def test_format_value(value, text):
    assert format_value(value) == text


def test_unavailable_package_message(qtbot, monkeypatch):
    import lightfall.ui.preferences.scopes_settings as mod

    monkeypatch.setattr(mod, "scopes_available", lambda: False)
    plugin = ScopesSettingsPlugin()
    w = plugin.create_widget()
    qtbot.addWidget(w)
    assert "lightfall-settings" in _status(w)


def test_rendering_does_not_create_preferences_manager(qtbot):
    # The view carries the server URL; the page must not bootstrap the
    # PreferencesManager singleton itself (it leaked into later tests).
    from lightfall.ui.preferences.manager import PreferencesManager

    PreferencesManager._instance = None
    _, w = _page(qtbot, lambda bind: VIEW)
    qtbot.waitUntil(lambda: _status(w).startswith("Live"), timeout=3000)
    assert PreferencesManager._instance is None


def test_stale_result_does_not_overwrite_newer_one(qtbot):
    # Review I1: a slow earlier load must not repaint over a later one.
    gate = threading.Event()
    beam = ScopesView(**{**VIEW.__dict__, "chain": ("facility", "beamline:7.0.1", "user:u1")})

    def loader(bind):
        if bind == "branch:7.0.1.1":
            gate.wait(3)
            return VIEW
        return beam

    plugin, w = _page(qtbot, loader)
    plugin._refresh("beamline:7.0.1")
    chain = w.findChild(QTableWidget, "scopesChain")
    qtbot.waitUntil(lambda: chain.rowCount() == 3, timeout=3000)
    gate.set()
    qtbot.wait(300)
    assert chain.rowCount() == 3


def test_offline_without_cache_is_not_called_live(qtbot):
    # Review I2.
    partial = ScopesView(**{**VIEW.__dict__, "offline": True, "cached_at": None})
    _, w = _page(qtbot, lambda bind: partial)
    qtbot.waitUntil(lambda: _status(w) == "Offline — no cached copy", timeout=3000)


# -- binding this console ------------------------------------------------------

from lightfall.scopes.client import TreeEntry  # noqa: E402
from lightfall.ui.preferences.scopes_settings import can_bind  # noqa: E402

TREE = (TreeEntry("beamline:7.0.1", None, "maintainer"),
        TreeEntry("beamline:12.0.2", None, None),
        TreeEntry("branch:7.0.1.1", "beamline:7.0.1", "maintainer"),
        TreeEntry("branch:12.0.2.1", "beamline:12.0.2", "maintainer"))


def test_can_bind_needs_maintainer_on_the_target_or_the_current_scope():
    assert can_bind(TREE, "facility", "branch:12.0.2.1")
    assert not can_bind(TREE, "facility", "beamline:12.0.2")        # no role there
    assert can_bind(TREE, "branch:7.0.1.1", "facility")            # unbind: role on current
    assert not can_bind(TREE, "beamline:12.0.2", "facility")
    assert not can_bind(TREE, "branch:7.0.1.1", "branch:7.0.1.1")  # already bound
    assert not can_bind((), "facility", "branch:7.0.1.1")          # no tree: old server


def test_combo_lists_the_whole_tree_with_branches_under_beamlines(qtbot):
    view = ScopesView(**{**VIEW.__dict__, "tree": TREE})
    _, w = _page(qtbot, lambda bind: view)
    combo = w.findChild(QComboBox, "scopesBind")
    qtbot.waitUntil(lambda: combo.count() == 5, timeout=3000)
    assert [combo.itemText(i) for i in range(5)] == [
        "branch:7.0.1.1", "facility", "beamline:7.0.1", "beamline:12.0.2", "branch:12.0.2.1"]


def test_bind_writes_site_bind_and_restarts(qtbot, monkeypatch):
    from PySide6.QtWidgets import QMessageBox, QPushButton

    calls = []
    monkeypatch.setattr("lightfall.scopes.site.set_site_bind", lambda b: calls.append(("bind", b)))
    monkeypatch.setattr("lightfall.utils.restart.request_restart", lambda: calls.append("restart"))
    monkeypatch.setattr(QMessageBox, "question",
                        lambda *a, **k: QMessageBox.StandardButton.Yes)
    view = ScopesView(**{**VIEW.__dict__, "tree": TREE})
    _, w = _page(qtbot, lambda bind: view)
    combo = w.findChild(QComboBox, "scopesBind")
    button = w.findChild(QPushButton, "scopesBindButton")
    qtbot.waitUntil(lambda: combo.count() == 5, timeout=3000)
    assert not button.isEnabled()                      # already bound to the selection
    combo.setCurrentIndex(combo.findText("beamline:12.0.2"))
    assert not button.isEnabled()                      # no role there
    assert "maintainer" in w.findChild(QLabel, "scopesBindHint").text()
    combo.setCurrentIndex(combo.findText("branch:12.0.2.1"))
    assert button.isEnabled()
    button.click()
    assert calls == [("bind", "branch:12.0.2.1"), "restart"]
