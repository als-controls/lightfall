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
    assert not panel.history_btn.isEnabled() and not panel.retract_btn.isEnabled()
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
