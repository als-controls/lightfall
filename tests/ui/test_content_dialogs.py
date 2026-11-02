"""Publish and History dialogs against a fake client, with a synchronous runner."""
from __future__ import annotations

import pytest
from PySide6.QtWidgets import QMessageBox

from lightfall.content.actions import ContentRow
from lightfall.content.publish import LocalItem
from lightfall.scopes.client import ContentPolicy, ScopesError
from lightfall.ui.dialogs import content_publish_dialog as pub_mod
from lightfall.ui.dialogs.content_history_dialog import ContentHistoryDialog
from lightfall.ui.dialogs.content_publish_dialog import ContentPublishDialog

BRANCH = "branch:7.0.1.1"


def sync_run(fn, ok, err):
    try:
        result = fn()
    except Exception as e:  # noqa: BLE001 - mirrors QThreadFuture's except_slot
        err(e)
    else:
        ok(result)


class FakeClient:
    def __init__(self, error=None, history=None):
        self.error = error
        self.published = []
        self.calls = []
        self._history = history or []

    def publish_content(self, scope, type_, name, manifest, bundle, *, agent=False):
        if self.error:
            raise self.error
        self.published.append((scope, type_, name, manifest, bundle))
        return {"revision": 7}

    def content_history(self, scope, type_, name):
        return self._history

    def retract_content(self, scope, type_, name, *, agent=False):
        self.calls.append(("retract", scope, type_, name))
        return {}

    def restore_content(self, scope, type_, name, revision, *, agent=False):
        self.calls.append(("restore", scope, type_, name, revision))
        return {"revision": revision}


@pytest.fixture(autouse=True)
def _quiet(monkeypatch):
    toasts = []
    monkeypatch.setattr(pub_mod, "toast_success", lambda t, x: toasts.append((t, x)))
    monkeypatch.setattr("lightfall.content.actions.refresh_sync", lambda: None)
    monkeypatch.setattr("lightfall.content.publish.GitTracker.get_instance",
                        lambda: type("G", (), {"head_commit": lambda self, p: None})())
    import lightfall.ui.dialogs.content_history_dialog as hist

    monkeypatch.setattr(hist, "toast_success", lambda t, x: toasts.append((t, x)))
    return toasts


@pytest.fixture
def toasts(_quiet):
    return _quiet


@pytest.fixture
def item(tmp_path):
    f = tmp_path / "tools.py"
    f.write_text("x = 1\n" * 10)
    return LocalItem("plugin", "tools", f)


def dialog(qtbot, item, client, *, roles=None, policy=None, scopes=(BRANCH,)):
    d = ContentPublishDialog(item, list(scopes), roles or {BRANCH: "maintainer"},
                             policy or ContentPolicy(), client=client, run=sync_run)
    qtbot.addWidget(d)
    return d


def test_publish_dialog_shows_bundle_facts(qtbot, item):
    d = dialog(qtbot, item, FakeClient())
    assert "plugin: tools" in d._type_label.text()
    facts = d._facts_label.text()
    assert "1 file" in facts and "B compressed" in facts and f"{d._bundle.uncompressed_size} B uncompressed" in facts
    assert d._bundle.sha256[:12] in facts
    assert d._scope_combo.itemText(0) == "Branch 7.0.1.1"


def test_publish_disabled_when_over_policy_unless_admin(qtbot, item):
    tiny = ContentPolicy(max_bundle_bytes=10, max_uncompressed_bytes=10, max_files=2000)
    d = dialog(qtbot, item, FakeClient(), policy=tiny)
    d._message_edit.setText("hello")
    assert not d._publish_btn.isEnabled()
    assert "limit is 10 B" in d._problems_label.text()
    assert "admin: limit waived" not in d._problems_label.text()

    a = dialog(qtbot, item, FakeClient(), policy=tiny, roles={BRANCH: "admin"})
    a._message_edit.setText("hello")
    assert a._publish_btn.isEnabled()
    assert "admin: limit waived" in a._problems_label.text()


def test_publish_requires_message(qtbot, item):
    d = dialog(qtbot, item, FakeClient())
    assert not d._publish_btn.isEnabled()
    d._message_edit.setText("   ")
    assert not d._publish_btn.isEnabled()
    d._message_edit.setText("fix")
    assert d._publish_btn.isEnabled()
    d._other_check.setChecked(True)
    assert not d._publish_btn.isEnabled()  # author needed
    d._author_edit.setText("Sam")
    assert d._publish_btn.isEnabled()


def test_publish_uploads_and_reports_revision(qtbot, item, toasts):
    client = FakeClient()
    d = dialog(qtbot, item, client)
    d._message_edit.setText("first")
    d._other_check.setChecked(True)
    d._author_edit.setText("Sam")
    bundle_path = d._bundle.path
    d._publish_btn.click()
    (scope, type_, name, manifest, path), = client.published
    assert (scope, type_, name) == (BRANCH, "plugin", "tools")
    assert path == bundle_path
    assert manifest["message"] == "first" and manifest["author"] == "Sam"
    assert manifest["sha256"] == d._bundle.sha256
    assert toasts == [("Published", "Published tools to Branch 7.0.1.1 (revision 7)")]
    assert not d.isVisible()
    assert d.result() == d.DialogCode.Accepted


def test_publish_error_shown_in_dialog(qtbot, item, toasts):
    client = FakeClient(error=ScopesError("nope", status=403))
    d = dialog(qtbot, item, client)
    d._message_edit.setText("x")
    d._publish_btn.click()
    assert "not a maintainer of Branch 7.0.1.1" in d._error_label.text()
    assert not d._error_label.isHidden()
    assert toasts == []
    assert d._publish_btn.isEnabled()  # can retry


def test_publish_result_after_close_is_ignored(qtbot, item, toasts):
    pending = []
    d = ContentPublishDialog(item, [BRANCH], {BRANCH: "maintainer"}, ContentPolicy(),
                             client=FakeClient(), run=lambda fn, ok, err: pending.append((fn, ok)))
    qtbot.addWidget(d)
    fn, ok = pending.pop()
    ok(fn())  # build finishes
    d._message_edit.setText("x")
    d._publish_btn.click()
    d.reject()
    fn, ok = pending.pop()
    ok(fn())  # upload finishes after the dialog closed
    assert toasts == []


def history_records():
    return [{"revision": 3, "published_at": "2026-11-02T10:00:00Z", "published_by": "ann",
             "author": None, "message": "newer", "agent": False, "current": True},
            {"revision": 2, "published_at": "2026-11-01T10:00:00Z", "published_by": "bob",
             "author": "Cy", "message": "older", "agent": True, "current": False}]


def row():
    return ContentRow("plugin", "tools", BRANCH, 3, None, (), None)


def history_dialog(qtbot, client, role="maintainer"):
    d = ContentHistoryDialog(row(), {BRANCH: role}, client=client, run=sync_run)
    qtbot.addWidget(d)
    return d


def test_history_lists_revisions_and_restores(qtbot, monkeypatch):
    client = FakeClient(history=history_records())
    d = history_dialog(qtbot, client)
    assert d._table.rowCount() == 2
    assert d._table.item(0, 0).text() == "3" and d._table.item(0, 6).text() == "current"
    assert d._table.item(1, 5).text() == "yes" and d._table.item(1, 3).text() == "Cy"
    d._table.selectRow(0)
    assert not d._restore_btn.isEnabled()  # current revision
    d._table.selectRow(1)
    assert d._restore_btn.isEnabled()
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.No)
    d._restore_btn.click()
    assert client.calls == []
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Yes)
    d._restore_btn.click()
    assert client.calls == [("restore", BRANCH, "plugin", "tools", 2)]


def test_history_retract_confirms(qtbot, monkeypatch):
    client = FakeClient(history=history_records())
    d = history_dialog(qtbot, client)
    asked = []

    def question(*a, **k):
        asked.append(a)
        return QMessageBox.StandardButton.No

    monkeypatch.setattr(QMessageBox, "question", question)
    d._retract_btn.click()
    assert len(asked) == 1 and client.calls == []
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Yes)
    d._retract_btn.click()
    assert client.calls == [("retract", BRANCH, "plugin", "tools")]


def test_history_read_only_for_non_maintainers(qtbot):
    d = history_dialog(qtbot, FakeClient(history=history_records()), role="operator")
    d._table.selectRow(1)
    assert not d._restore_btn.isEnabled() and not d._retract_btn.isEnabled()
