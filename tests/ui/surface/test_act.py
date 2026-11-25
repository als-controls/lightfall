"""ui_act: real input from a worker thread, posted so modal dialogs cannot block it."""

import threading

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDockWidget,
    QDoubleSpinBox,
    QFormLayout,
    QLineEdit,
    QListWidget,
    QMainWindow,
    QPushButton,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from lightfall.ui.surface.act import ActError, act, act_sync


def in_worker(qtbot, fn, timeout=20000):
    """Run *fn* on a worker thread while the GUI thread keeps processing events."""
    box = {}

    def target():
        try:
            box["value"] = fn()
        except BaseException as e:  # noqa: BLE001 - re-raised on the test thread
            box["error"] = e

    t = threading.Thread(target=target, daemon=True)
    t.start()
    qtbot.waitUntil(lambda: not t.is_alive(), timeout=timeout)
    if "error" in box:
        raise box["error"]
    return box["value"]


class Calls:
    def __init__(self):
        self.log = []

    def __call__(self, *args):
        self.log.append(args)


@pytest.fixture
def ui(qtbot):
    w = QMainWindow()
    w.setWindowTitle("Lightfall")
    qtbot.addWidget(w)
    body = QWidget()
    lay = QVBoxLayout(body)
    form_host = QWidget()
    form = QFormLayout(form_host)
    w.title = QLineEdit()
    w.start = QDoubleSpinBox()
    w.start.setRange(-100, 100)
    w.mode = QComboBox()
    w.mode.addItems(["step", "fly", "continuous"])
    form.addRow("Title", w.title)
    form.addRow("Start", w.start)
    form.addRow("Mode", w.mode)
    lay.addWidget(form_host)
    w.lst = QListWidget()
    w.lst.setAccessibleName("Plans")
    w.lst.addItems(["scan", "grid_scan", "count"])
    lay.addWidget(w.lst)
    w.check = QCheckBox("Live plot")
    w.check.setChecked(True)
    lay.addWidget(w.check)
    w.run = QPushButton("Run")
    w.ask = QPushButton("Submit…")
    w.off = QPushButton("Abort")
    w.off.setEnabled(False)
    for b in (w.run, w.ask, w.off):
        lay.addWidget(b)
    dock = QDockWidget("Plans", w)
    dock.setWidget(body)
    w.addDockWidget(Qt.DockWidgetArea.LeftDockWidgetArea, dock)
    tabs = QTabWidget()
    tabs.addTab(QWidget(), "Live")
    tabs.addTab(QWidget(), "History")
    w.tabs = tabs
    w.setCentralWidget(tabs)
    w.resize(900, 700)
    w.show()
    qtbot.waitExposed(w)
    return w


def test_click_runs_slot_once(ui, qtbot):
    calls = Calls()
    ui.run.clicked.connect(calls)
    result = in_worker(qtbot, lambda: act("click", {"role": "button", "name": "Run"}))
    assert result["ok"] and len(calls.log) == 1


def test_fill_line_edit_fires_editing_finished(ui, qtbot):
    finished = Calls()
    ui.title.editingFinished.connect(finished)
    ui.title.setText("old")
    r = in_worker(qtbot, lambda: act("fill", {"role": "field", "name": "Title"}, "Surface trial"))
    assert ui.title.text() == "Surface trial"
    assert len(finished.log) >= 1
    assert r["changes"]["target"]["value"] == "Surface trial"


def test_fill_spin_box(ui, qtbot):
    finished = Calls()
    ui.start.editingFinished.connect(finished)
    in_worker(qtbot, lambda: act("fill", {"name": "Start"}, "-1.5"))
    assert ui.start.value() == pytest.approx(-1.5)
    assert len(finished.log) >= 1


def test_select_combo_emits_activated(ui, qtbot):
    activated = Calls()
    ui.mode.activated.connect(activated)
    in_worker(qtbot, lambda: act("select", {"role": "combo", "name": "Mode"}, "continuous"))
    assert ui.mode.currentText() == "continuous"
    assert activated.log and activated.log[-1] == (2,)


def test_select_unknown_option_lists_options(ui, qtbot):
    with pytest.raises(ActError) as e:
        in_worker(qtbot, lambda: act("select", {"name": "Mode"}, "spiral"))
    assert "fly" in str(e.value)


def test_click_list_item_selects_it(ui, qtbot):
    in_worker(qtbot, lambda: act("click", {"role": "item", "name": "grid_scan"}))
    assert ui.lst.currentRow() == 1


def test_select_item_in_list_by_value(ui, qtbot):
    in_worker(qtbot, lambda: act("select", {"role": "list", "name": "Plans"}, "count"))
    assert ui.lst.currentRow() == 2


def test_select_tab(ui, qtbot):
    in_worker(qtbot, lambda: act("click", {"role": "tab", "name": "History"}))
    assert ui.tabs.currentIndex() == 1


def test_check_already_checked_is_a_no_op(ui, qtbot):
    toggled = Calls()
    ui.check.toggled.connect(toggled)
    r = in_worker(qtbot, lambda: act("check", {"role": "checkbox", "name": "Live plot"}))
    assert r["ok"] and not toggled.log
    in_worker(qtbot, lambda: act("uncheck", {"role": "checkbox", "name": "Live plot"}))
    assert not ui.check.isChecked()


def test_disabled_target_is_refused(ui, qtbot):
    with pytest.raises(ActError, match="disabled"):
        in_worker(qtbot, lambda: act("click", {"role": "button", "name": "Abort"}))


def test_modal_exec_does_not_block_act(ui, qtbot):
    done = []

    def ask():
        dlg = QDialog(ui)
        dlg.setWindowTitle("Confirm")
        ok = QPushButton("OK", dlg)
        ok.clicked.connect(dlg.accept)
        QVBoxLayout(dlg).addWidget(ok)
        done.append(("result", dlg.exec()))

    ui.ask.clicked.connect(ask)

    # Both acts run on one worker, as an agent's would: while the dialog is open the GUI
    # thread sits inside exec(), so the test thread cannot return between them.
    def agent():
        first = act("click", {"role": "button", "name": "Submit…"})
        still_open = not done
        second = act("click", {"role": "button", "name": "OK", "within": "Confirm"})
        return first, still_open, second

    first, still_open, second = in_worker(qtbot, agent)
    assert first["changes"]["opened"] == ['dialog "Confirm"']
    assert still_open  # act returned while the slot was still inside exec()
    qtbot.waitUntil(lambda: bool(done), timeout=5000)
    assert done[0][1] == 1  # Accepted
    assert second["changes"].get("closed") == ['dialog "Confirm"']


def test_act_refuses_gui_thread(ui):
    with pytest.raises(RuntimeError, match="off the GUI thread"):
        act("click", {"name": "Run"})


def test_act_sync_on_gui_thread(ui):
    calls = Calls()
    ui.run.clicked.connect(calls)
    assert act_sync("click", {"role": "button", "name": "Run"})["ok"]
    assert len(calls.log) == 1


def test_press_keys_on_target(ui, qtbot):
    ui.title.setText("abc")
    in_worker(qtbot, lambda: act("press", {"name": "Title"}, "End"))
    in_worker(qtbot, lambda: act("press", {"name": "Title"}, "Backspace"))
    assert ui.title.text() == "ab"
