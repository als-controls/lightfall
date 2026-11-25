"""Locator: refs, role+name, within, strictness."""

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDockWidget,
    QListWidget,
    QMainWindow,
    QPushButton,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from lightfall.ui.surface.locate import LocateError, locate
from lightfall.ui.surface.snapshot import snapshot
from lightfall.ui.surface.tree import StaleRef


def _dock(window, title, *widgets):
    body = QWidget()
    lay = QVBoxLayout(body)
    for w in widgets:
        lay.addWidget(w)
    dock = QDockWidget(title, window)
    dock.setWidget(body)
    window.addDockWidget(Qt.DockWidgetArea.LeftDockWidgetArea, dock)
    return dock


@pytest.fixture
def window(qtbot):
    w = QMainWindow()
    w.setWindowTitle("Lightfall")
    qtbot.addWidget(w)
    w.plans_run, w.queue_run = QPushButton("Run"), QPushButton("Run")
    w.run_plan = QPushButton("Run plan once")
    lst = QListWidget()
    lst.setAccessibleName("Plans")
    lst.addItems(["scan", "grid_scan", "count"])
    w.plans_list = lst
    _dock(w, "Plans", lst, w.plans_run, w.run_plan)
    _dock(w, "Queue", w.queue_run)
    tabs = QTabWidget()
    tabs.addTab(QWidget(), "Live")
    tabs.addTab(QWidget(), "History")
    w.tabs = tabs
    w.setCentralWidget(tabs)
    w.resize(900, 600)
    w.show()
    qtbot.waitExposed(w)
    return w


def test_two_matches_is_an_error_listing_both(window):
    with pytest.raises(LocateError) as e:
        locate({"role": "button", "name": "Run"})
    msg = str(e.value)
    assert '"Plans"' in msg and '"Queue"' in msg


def test_within_narrows(window):
    assert locate({"role": "button", "name": "Run", "within": "Queue"}).widget is window.queue_run


def test_exact_beats_prefix_and_unique_prefix_works(window):
    assert locate({"role": "button", "name": "run plan", "within": "Plans"}).widget is window.run_plan


def test_missing_lists_same_role_candidates(window):
    with pytest.raises(LocateError) as e:
        locate({"role": "button", "name": "Abort", "within": "Plans"})
    assert 'button "Run"' in str(e.value)


def test_item_by_name(window):
    node = locate({"role": "item", "name": "grid_scan", "within": "Plans"})
    assert node.widget is window.plans_list
    assert node.index.row() == 1


def test_tab_by_name(window):
    node = locate({"role": "tab", "name": "History"})
    assert node.tab == 1


def test_ref_and_stale_ref(window):
    text = snapshot(scope="Queue")
    ref = next(ln for ln in text.splitlines() if 'button "Run"' in ln).split("[")[1].split("]")[0]
    assert locate(ref).widget is window.queue_run
    snapshot(scope="Plans")
    with pytest.raises(StaleRef):
        locate(ref)


def test_role_alias(window):
    assert locate({"role": "panel", "name": "Queue"}).role == "dock"
