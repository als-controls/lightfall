"""Setting keys review page: facility admins approve / reject / revoke proposed keys."""
from __future__ import annotations

import pytest
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QTableWidget,
)

from lightfall.ui.preferences.setting_keys_settings import (
    KeysState,
    SettingKeysSettingsPlugin,
    review_base,
    rule_edits,
)

PROPOSED = {"key": "tiled_enabled", "status": "pending", "spec": None,
            "proposed_spec": {"key": "tiled_enabled", "type": "bool",
                              "scopes": ["beamline", "branch", "user"],
                              "write_role": "maintainer"},
            "proposed_by": "mia", "proposed_scope": "beamline:7.0.1", "note": ""}
APPROVED = {"key": "voice.wake_word", "status": "approved", "proposed_spec": None,
            "spec": {"key": "voice.wake_word", "type": "str", "scopes": ["branch", "user"],
                     "write_role": "maintainer", "operator_writable": True},
            "note": "seeded"}
REJECTED = {"key": "x", "status": "rejected", "spec": None,
            "proposed_spec": {"key": "x", "scopes": ["facility"]}, "note": "no"}


def test_review_base_prefers_the_approved_spec():
    assert review_base(APPROVED)["scopes"] == ["branch", "user"]
    assert review_base(PROPOSED)["scopes"] == ["beamline", "branch", "user"]
    assert review_base({"status": "pending"}) == {}


def test_rule_edits_sends_only_what_changed():
    base = review_base(PROPOSED)
    same = rule_edits(base, scopes={"beamline", "branch", "user"}, write_role="maintainer",
                      operator_writable=False, lockable=True)
    assert same == {}
    edits = rule_edits(base, scopes={"facility", "beamline", "branch"}, write_role="admin",
                       operator_writable=False, lockable=True)
    assert edits == {"scopes": ["beamline", "branch", "facility"], "write_role": "admin"}


class Actions:
    def __init__(self):
        self.calls = []

    def approve_key(self, key, *, edits=None, note=""):
        self.calls.append(("approve", key, edits, note))
        return {"key": key, "status": "approved"}

    def reject_key(self, key, *, note=""):
        self.calls.append(("reject", key, note))
        return {"key": key, "status": "rejected"}

    def revoke_key(self, key):
        self.calls.append(("revoke", key))


@pytest.fixture
def page(qtbot):
    def _make(admin=True, records=(PROPOSED, APPROVED, REJECTED)):
        plugin = SettingKeysSettingsPlugin()
        actions = Actions()
        plugin.loader = lambda: KeysState(tuple(records), admin, False)
        plugin.actions = lambda: actions
        widget = plugin.create_widget()
        qtbot.addWidget(widget)
        table = widget.findChild(QTableWidget, "keysTable")
        qtbot.waitUntil(lambda: "admin" in widget.findChild(QLabel, "keysStatus").text(),
                        timeout=3000)
        return plugin, widget, table, actions
    return _make


def _show_all(widget):
    combo = widget.findChild(QComboBox, "keysFilter")
    combo.setCurrentIndex(combo.findData(None))


def test_pending_filter_by_default_then_all(page):
    _, widget, table, _ = page()
    assert table.rowCount() == 1 and table.item(0, 0).text() == "tiled_enabled"
    assert table.item(0, 2).text() == "mia" and table.item(0, 3).text() == "beamline:7.0.1"
    _show_all(widget)
    assert table.rowCount() == 3


def test_non_admin_sees_keys_but_cannot_decide(page):
    _, widget, table, _ = page(admin=False)
    table.selectRow(0)
    assert not widget.findChild(QPushButton, "keysApprove").isEnabled()
    assert "Only facility admins" in widget.findChild(QLabel, "keysStatus").text()


def test_approve_with_changed_rules_and_note(page, qtbot):
    _, widget, table, actions = page()
    table.selectRow(0)
    widget.findChild(QCheckBox, "keysLevel_user").setChecked(False)
    widget.findChild(QCheckBox, "keysLevel_facility").setChecked(True)
    widget.findChild(QLineEdit, "keysNote").setText("site-wide")
    widget.findChild(QPushButton, "keysApprove").click()
    qtbot.waitUntil(lambda: bool(actions.calls), timeout=3000)
    assert actions.calls == [("approve", "tiled_enabled",
                              {"scopes": ["beamline", "branch", "facility"]}, "site-wide")]


def test_reject_only_for_pending_and_revoke_only_for_approved(page, qtbot, monkeypatch):
    _, widget, table, actions = page()
    reject = widget.findChild(QPushButton, "keysReject")
    revoke = widget.findChild(QPushButton, "keysRevoke")
    table.selectRow(0)
    assert reject.isEnabled() and not revoke.isEnabled()
    reject.click()
    qtbot.waitUntil(lambda: bool(actions.calls), timeout=3000)
    assert actions.calls == [("reject", "tiled_enabled", "")]

    _show_all(widget)
    table.selectRow(1)                                    # the approved key
    assert widget.findChild(QPushButton, "keysApprove").text() == "Save rules"
    assert revoke.isEnabled() and not reject.isEnabled()
    monkeypatch.setattr(QMessageBox, "question",
                        lambda *a, **k: QMessageBox.StandardButton.Yes)
    revoke.click()
    qtbot.waitUntil(lambda: len(actions.calls) == 2, timeout=3000)
    assert actions.calls[-1] == ("revoke", "voice.wake_word")


def test_approve_needs_at_least_one_level(page, monkeypatch):
    _, widget, table, actions = page()
    warned = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: warned.append(a[2]))
    table.selectRow(0)
    for level in ("facility", "beamline", "branch", "user"):
        widget.findChild(QCheckBox, f"keysLevel_{level}").setChecked(False)
    widget.findChild(QPushButton, "keysApprove").click()
    assert warned and actions.calls == []
