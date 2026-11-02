"""Shared content panel against fake rows, scope state, client and runner."""
from __future__ import annotations

from pathlib import Path

import pytest
from PySide6.QtCore import QObject, Signal
from PySide6.QtWidgets import QMessageBox

from lightfall.content.actions import ContentRow
from lightfall.content.publish import LocalItem
from lightfall.scopes.client import ContentPolicy
from lightfall.ui.panels import content_panel as mod
from lightfall.ui.panels.content_panel import ContentPanel

BRANCH = "branch:7.0.1.1"


def sync_run(fn, ok, err):
    try:
        result = fn()
    except Exception as e:  # noqa: BLE001
        err(e)
    else:
        ok(result)


class FakeClient:
    def __init__(self):
        self.calls = []

    def retract_content(self, scope, type_, name, *, agent=False):
        self.calls.append(("retract", scope, type_, name))
        return {}


class Source(QObject):
    synced = Signal(object)


def make_rows(tmp_path: Path):
    mine = tmp_path / "mine.md"
    mine.write_text("x")
    shared = tmp_path / "shared.md"
    shared.write_text("x")
    pkg = tmp_path / "pkg"
    pkg.mkdir()
    return [
        ContentRow("agent", "mine", "local", None, mine, (), None),
        ContentRow("agent", "shared", BRANCH, 3, shared, ("package", "core"), None),
        ContentRow("skill", "pkgskill", "package", None, pkg, (), None),
        ContentRow("plan", "corescan", "core", None, None, (), None),
    ]


@pytest.fixture
def state():
    return {"roles": {BRANCH: "maintainer"}, "chain": ("facility", BRANCH)}


@pytest.fixture
def panel(qtbot, tmp_path, state):
    rows = make_rows(tmp_path)
    holder = {"rows": rows}
    src = Source()
    p = ContentPanel(
        rows=lambda: list(holder["rows"]),
        scope_state=lambda: (state["chain"], state["roles"], ContentPolicy()),
        local_items=lambda: [LocalItem("agent", "mine", rows[0].path)],
        client_factory=FakeClient,
        run=sync_run,
        refresh_signals=[src.synced],
    )
    qtbot.addWidget(p)
    p._holder, p._src = holder, src
    return p


def select(p, name):
    for r in range(p.table.rowCount()):
        if p.table.item(r, 1).text() == name:
            p.table.selectRow(r)
            return
    raise AssertionError(name)


def test_panel_metadata():
    md = ContentPanel.panel_metadata
    assert md.id == "shared_content" and md.name == "Shared content"


def test_panel_lists_rows_with_labels(panel):
    t = panel.table
    assert t.rowCount() == 4
    cells = {t.item(r, 1).text(): [t.item(r, c).text() for c in range(5)]
             for r in range(t.rowCount())}
    assert cells["mine"][:4] == ["agent", "mine", "Mine", ""]
    assert cells["shared"][2:4] == ["Branch 7.0.1.1", "3"]
    assert "Package" in cells["shared"][4] and "Built-in" in cells["shared"][4]
    assert cells["pkgskill"][2] == "Package"
    assert cells["corescan"][2] == "Built-in"


def test_type_filter_and_search(panel):
    panel.type_filter.setCurrentIndex(panel.type_filter.findData("skill"))
    assert panel.table.rowCount() == 1
    panel.type_filter.setCurrentIndex(0)
    panel.search.setText("sha")
    assert panel.table.rowCount() == 1


def test_buttons_follow_row_kind_and_role(panel, state):
    select(panel, "mine")
    assert panel.publish_btn.isEnabled()
    assert panel.history_btn.isEnabled()  # the chain has shared scopes to look at
    assert not panel.retract_btn.isEnabled()
    assert not panel.copy_btn.isEnabled() and panel.open_btn.isEnabled()

    select(panel, "shared")
    assert panel.history_btn.isEnabled() and panel.retract_btn.isEnabled()
    assert panel.copy_btn.isEnabled() and not panel.publish_btn.isEnabled()

    select(panel, "pkgskill")
    assert panel.copy_btn.isEnabled() and not panel.history_btn.isEnabled()
    assert not panel.retract_btn.isEnabled()

    select(panel, "corescan")  # no file to copy or open
    assert not panel.copy_btn.isEnabled() and not panel.open_btn.isEnabled()

    state["roles"] = {BRANCH: "viewer"}
    panel.refresh()
    select(panel, "shared")
    assert panel.history_btn.isEnabled() and not panel.retract_btn.isEnabled()
    select(panel, "mine")
    assert not panel.publish_btn.isEnabled()
    assert "No scope where you can publish" in panel.publish_btn.toolTip()

    state["chain"], state["roles"] = (), {}
    panel.refresh()
    select(panel, "mine")
    assert "Scoped settings are off" in panel.publish_btn.toolTip()
    assert not panel.history_btn.isEnabled()


def test_publish_passes_publishable_scopes_not_the_chain(panel, monkeypatch, state):
    seen = {}

    class Dlg(QObject):
        finished = Signal(int)

        def __init__(self, item, scopes, roles, policy, *, client, run=None, parent=None):
            super().__init__()
            seen.update(item=item, scopes=scopes, roles=roles, policy=policy)

        def open(self):
            seen["opened"] = True

    monkeypatch.setattr(mod, "ContentPublishDialog", Dlg)
    select(panel, "mine")
    panel.publish_btn.click()
    assert seen["scopes"] == [BRANCH] and seen["item"].name == "mine"
    assert seen["roles"] == state["roles"] and seen["opened"]


def test_history_passes_roles(panel, monkeypatch, state):
    seen = {}

    class Dlg(QObject):
        finished = Signal(int)

        def __init__(self, row, roles, *, client, run=None, parent=None):
            super().__init__()
            seen.update(row=row, roles=roles)

        def open(self):
            pass

    monkeypatch.setattr(mod, "ContentHistoryDialog", Dlg)
    select(panel, "shared")
    panel.history_btn.click()
    assert seen["row"].name == "shared" and seen["roles"] == state["roles"]


def test_retract_confirms_then_calls_client(panel, monkeypatch):
    calls = []
    monkeypatch.setattr(mod.actions, "retract",
                        lambda client, scope, t, n, **kw: calls.append((scope, t, n)))
    select(panel, "shared")
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.No)
    panel.retract_btn.click()
    assert calls == []
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Yes)
    panel.retract_btn.click()
    assert calls == [(BRANCH, "agent", "shared")]


def test_copy_to_mine_asks_before_overwrite(panel, monkeypatch):
    calls = []

    def fake_copy(row, *, overwrite=False):
        calls.append(overwrite)
        if not overwrite:
            raise FileExistsError("exists")

    asked = []
    monkeypatch.setattr(mod, "copy_to_mine", fake_copy)
    select(panel, "shared")

    def no(parent, title, text, *a, **k):
        asked.append(text)
        return QMessageBox.StandardButton.No

    monkeypatch.setattr(QMessageBox, "question", no)
    panel.copy_btn.click()
    assert calls == [False] and "Replace your agent 'shared'?" in asked[0]

    calls.clear()
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Yes)
    panel.copy_btn.click()
    assert calls == [False, True]


def test_refreshes_on_sync(panel, tmp_path):
    assert panel.table.rowCount() == 4
    panel._holder["rows"] = panel._holder["rows"][:2]
    panel._src.synced.emit(None)
    assert panel.table.rowCount() == 2


# -- History from your own copy, retract wording and a closed panel ------------------------

class HistoryDlg(QObject):
    finished = Signal(int)
    seen: dict = {}

    def __init__(self, row, roles, *, client, run=None, parent=None):
        super().__init__()
        HistoryDlg.seen = {"row": row, "roles": roles}

    def open(self):
        HistoryDlg.seen["opened"] = True


def test_history_from_mine_asks_which_scope(panel, monkeypatch, state, tmp_path):
    from PySide6.QtWidgets import QInputDialog

    state["chain"] = ("facility", "beamline:7.0.1", BRANCH, "user:u1")
    state["roles"] = {"beamline:7.0.1": "maintainer"}
    mine = tmp_path / "xafs.py"
    mine.write_text("x")
    # Published from Mine to Beamline 7.0.1: only the Mine row shows, "Replaces Beamline".
    panel._holder["rows"] = [
        ContentRow("plan", "xafs", "local", None, mine, ("beamline:7.0.1",), None)]
    panel.refresh()
    monkeypatch.setattr(mod, "ContentHistoryDialog", HistoryDlg)
    asked = []

    def get_item(parent, title, label, items, current=0, editable=True, *a, **k):
        asked.append((list(items), current, editable))
        return items[2], True

    monkeypatch.setattr(QInputDialog, "getItem", get_item)
    select(panel, "xafs")
    assert panel.history_btn.isEnabled()
    panel.history_btn.click()
    # Shared scopes of the chain, most specific first; the shadowed one preselected.
    assert asked == [(["Branch 7.0.1.1", "Beamline 7.0.1", "Facility"], 1, False)]
    assert HistoryDlg.seen["row"] == ContentRow("plan", "xafs", "facility", None, None, (),
                                                None)
    assert HistoryDlg.seen["roles"] == state["roles"] and HistoryDlg.seen["opened"]

    HistoryDlg.seen = {}
    monkeypatch.setattr(QInputDialog, "getItem", lambda *a, **k: ("", False))
    panel.history_btn.click()
    assert HistoryDlg.seen == {}  # cancelled: no dialog


def test_history_from_mine_with_one_shared_scope_opens_it(panel, monkeypatch):
    from PySide6.QtWidgets import QInputDialog

    monkeypatch.setattr(mod, "ContentHistoryDialog", HistoryDlg)
    monkeypatch.setattr(QInputDialog, "getItem",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("asked")))
    panel._scope_state = lambda: ((BRANCH, "user:u1"), {}, ContentPolicy())
    select(panel, "mine")
    panel.history_btn.click()
    assert HistoryDlg.seen["row"].source == BRANCH


def test_retract_wording(panel, monkeypatch):
    asked = []
    monkeypatch.setattr(mod.actions, "retract", lambda *a, **k: {})

    def question(parent, title, text, *a, **k):
        asked.append(text)
        return QMessageBox.StandardButton.No

    monkeypatch.setattr(QMessageBox, "question", question)
    select(panel, "shared")
    panel.retract_btn.click()
    assert asked == ["Retract agent 'shared' from Branch 7.0.1.1?\n"
                     "Everyone there stops receiving it; history keeps it."]


class RetractedClient(FakeClient):
    """Branch 7.0.1.1 has plan 'xafs' retracted (last revision 4); nobody has it locally."""

    retracted = True
    old_server = False

    def __init__(self):
        super().__init__()
        self.listed = []

    def retracted_content(self, scope):
        from lightfall.scopes.client import ContentItem

        self.listed.append(scope)
        if self.old_server:
            return None
        if scope != BRANCH or not RetractedClient.retracted:
            return []
        return [ContentItem("plan", "xafs", 4, "ab" * 32, 10)]

    def content_history(self, scope, type_, name):
        return [{"revision": 4, "current": not RetractedClient.retracted},
                {"revision": 3, "current": False}]

    def restore_content(self, scope, type_, name, revision, *, agent=False,
                        expected_revision=None):
        self.calls.append(("restore", scope, type_, name, revision, expected_revision))
        RetractedClient.retracted = False
        return {"revision": revision}


def test_retracted_view_lists_and_restores(qtbot, tmp_path, monkeypatch):
    import lightfall.ui.dialogs.content_history_dialog as hist

    RetractedClient.retracted, RetractedClient.old_server = True, False
    monkeypatch.setattr(hist, "toast_success", lambda *a: None)
    monkeypatch.setattr(mod.actions, "refresh_sync", lambda: None)
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Yes)
    client = RetractedClient()
    holder = {"rows": make_rows(tmp_path)}
    src = Source()
    p = ContentPanel(rows=lambda: list(holder["rows"]),
                     scope_state=lambda: (("facility", "beamline:7.0.1", BRANCH, "user:u1"),
                                          {BRANCH: "maintainer"}, ContentPolicy()),
                     local_items=lambda: [], client_factory=lambda: client, run=sync_run,
                     refresh_signals=[src.synced])
    qtbot.addWidget(p)
    assert p.table.rowCount() == 4 and client.listed == []  # off: nothing fetched
    assert p.retracted_scope.isHidden()

    p.retracted_check.setChecked(True)
    assert not p.retracted_scope.isHidden()
    # The shared scopes of the chain, most specific first.
    assert [p.retracted_scope.itemText(i) for i in range(p.retracted_scope.count())] == \
        ["Branch 7.0.1.1", "Beamline 7.0.1", "Facility"]
    assert client.listed == [BRANCH]
    assert p.table.rowCount() == 5
    cells = {p.table.item(r, 1).text(): [p.table.item(r, c).text() for c in range(5)]
             for r in range(p.table.rowCount())}
    assert cells["xafs"] == ["plan", "xafs", "Branch 7.0.1.1", "4", "Retracted"]

    select(p, "xafs")
    assert p.history_btn.isEnabled()
    assert not p.retract_btn.isEnabled() and not p.copy_btn.isEnabled()
    p.history_btn.click()
    dlg = p._dialogs[-1]
    assert isinstance(dlg, mod.ContentHistoryDialog)
    assert not dlg._retract_btn.isEnabled()  # nothing current
    dlg._table.selectRow(0)
    assert dlg._restore_btn.isEnabled()  # maintainer+
    dlg._restore_btn.click()
    assert client.calls == [("restore", BRANCH, "plan", "xafs", 4, "none")]
    dlg.done(0)  # closing the dialog refreshes the panel: no longer retracted
    assert "xafs" not in [p.table.item(r, 1).text() for r in range(p.table.rowCount())]

    # The next sync brings it back as a synced row.
    holder["rows"] = holder["rows"] + [ContentRow("plan", "xafs", BRANCH, 4, None, (), None)]
    src.synced.emit(None)
    cells = {p.table.item(r, 1).text(): [p.table.item(r, c).text() for c in range(5)]
             for r in range(p.table.rowCount())}
    assert cells["xafs"] == ["plan", "xafs", "Branch 7.0.1.1", "4", ""]

    # Another scope: fetched for that scope.
    p.retracted_scope.setCurrentIndex(1)
    assert client.listed[-1] == "beamline:7.0.1"

    # Off again: the retracted rows go.
    RetractedClient.retracted = True
    p.retracted_scope.setCurrentIndex(0)
    assert p.table.rowCount() == 6
    p.retracted_check.setChecked(False)
    assert p.table.rowCount() == 5 and p.retracted_scope.isHidden()


def test_retracted_view_on_an_old_server_says_so(qtbot, tmp_path):
    RetractedClient.old_server = True
    try:
        client = RetractedClient()
        p = ContentPanel(rows=lambda: make_rows(tmp_path),
                         scope_state=lambda: (("facility", BRANCH), {}, ContentPolicy()),
                         local_items=lambda: [], client_factory=lambda: client, run=sync_run,
                         refresh_signals=[])
        qtbot.addWidget(p)
        p.retracted_check.setChecked(True)
        assert p.table.rowCount() == 4
        assert p.retracted_note.text() == "This server doesn't list retracted items."
        assert not p.retracted_note.isHidden()
    finally:
        RetractedClient.old_server = False


def test_retracted_result_after_the_panel_closed(qtbot, tmp_path):
    import shiboken6

    pending = []
    p = ContentPanel(rows=lambda: make_rows(tmp_path),
                     scope_state=lambda: (("facility", BRANCH), {}, ContentPolicy()),
                     local_items=lambda: [], client_factory=RetractedClient,
                     run=lambda fn, ok, err: pending.append((fn, ok, err)), refresh_signals=[])
    p.retracted_check.setChecked(True)
    shiboken6.delete(p)
    fn, ok, err = pending.pop()
    ok(fn())  # must not touch the deleted table
    err(RuntimeError("boom"))


def test_retract_result_after_the_panel_closed(qtbot, tmp_path, monkeypatch):
    import shiboken6

    toasts = []
    monkeypatch.setattr(mod, "toast_success", lambda t, x: toasts.append((t, x)))
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Yes)
    warned = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: warned.append(a))
    pending = []
    rows = make_rows(tmp_path)

    def build():
        p = ContentPanel(rows=lambda: list(rows),
                         scope_state=lambda: (("facility", BRANCH), {BRANCH: "maintainer"},
                                              ContentPolicy()),
                         local_items=lambda: [], client_factory=FakeClient,
                         run=lambda fn, ok, err: pending.append((fn, ok, err)),
                         refresh_signals=[])
        select(p, "shared")
        p.retract_btn.click()
        return p

    p = build()
    shiboken6.delete(p)
    fn, ok, _ = pending.pop()
    ok(fn())  # must not touch the deleted status label
    assert toasts == [("Retracted", "agent shared retracted from Branch 7.0.1.1.")]

    p = build()
    shiboken6.delete(p)
    _, _, err = pending.pop()
    err(RuntimeError("boom"))
    assert warned == []
