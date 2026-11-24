"""ui_snapshot: outline, refs, scope, summary mode."""

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QDockWidget,
    QDoubleSpinBox,
    QFormLayout,
    QLabel,
    QListWidget,
    QMainWindow,
    QPushButton,
    QSpinBox,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from lightfall.ui.surface.snapshot import snapshot
from lightfall.ui.surface.tree import REFS, StaleRef, SurfaceError


def _dock(window: QMainWindow, title: str, body: QWidget) -> QDockWidget:
    dock = QDockWidget(title, window)
    dock.setWidget(body)
    window.addDockWidget(Qt.DockWidgetArea.LeftDockWidgetArea, dock)
    return dock


@pytest.fixture
def window(qtbot):
    w = QMainWindow()
    w.setWindowTitle("Lightfall")
    qtbot.addWidget(w)

    plans = QWidget()
    lay = QVBoxLayout(plans)
    lst = QListWidget()
    lst.setAccessibleName("Plans")
    lst.addItems([f"plan_{i:02d}" for i in range(30)])
    lay.addWidget(lst)
    form_host = QWidget()
    form = QFormLayout(form_host)
    form.addRow("Start", QDoubleSpinBox())
    form.addRow("Points", QSpinBox())
    lay.addWidget(form_host)
    run = QPushButton("Run")
    lay.addWidget(run)
    stop = QPushButton("Stop")
    stop.setEnabled(False)
    lay.addWidget(stop)
    _dock(w, "Plans", plans)

    devices = QWidget()
    QVBoxLayout(devices).addWidget(QPushButton("Connect"))
    _dock(w, "Devices", devices)

    central = QTabWidget()
    central.addTab(QLabel("plot area"), "Live")
    central.addTab(QLabel("other"), "History")
    w.setCentralWidget(central)
    w.resize(1000, 700)
    w.show()
    qtbot.waitExposed(w)
    w.run_button = run
    return w


def test_scoped_dock_outline(window):
    text = snapshot(scope="Plans")
    assert text.splitlines()[0].startswith('dock "Plans" [p1]')
    assert 'list "Plans"' in text and "30 items, showing 1-" in text
    assert 'item "plan_00"' in text
    assert 'field "Start"' in text and 'field "Points"' in text
    assert 'button "Run"' in text
    assert 'button "Stop"' in text and "disabled" in text.split('button "Stop"')[1].splitlines()[0]
    # The form labels name the fields; they are not repeated as labels.
    assert 'label "Start"' not in text


def test_summary_mode_collapses_docks_without_focus(window):
    text = snapshot()
    assert 'window "Lightfall"' in text
    assert 'dock "Plans"' in text and "collapsed" in text
    assert 'tab "Live"' in text and "selected" in text
    assert 'label "other"' not in text  # hidden tab page


def test_focused_dock_stays_expanded(window, qtbot):
    window.run_button.setFocus()
    qtbot.waitUntil(lambda: window.run_button.hasFocus())
    text = snapshot()
    assert 'button "Run"' in text
    assert 'button "Connect"' not in text


def test_refs_resolve_and_go_stale(window):
    text = snapshot(scope="Plans")
    run_line = next(ln for ln in text.splitlines() if 'button "Run"' in ln)
    ref = run_line.split("[")[1].split("]")[0]
    assert REFS.resolve(ref).widget is window.run_button
    snapshot(scope="Devices")
    with pytest.raises(StaleRef):
        REFS.resolve(ref)


def test_modal_dialog_is_a_root(window, qtbot):
    dlg = QDialog(window)
    dlg.setWindowTitle("Confirm plan")
    QVBoxLayout(dlg).addWidget(QPushButton("Submit"))
    dlg.setModal(True)
    dlg.open()
    qtbot.waitExposed(dlg)
    text = snapshot()
    assert 'dialog "Confirm plan" [d1] modal' in text
    assert 'button "Submit"' in text
    dlg.close()


def test_unknown_scope_lists_open_panels(window):
    with pytest.raises(SurfaceError) as e:
        snapshot(scope="Logbook")
    assert 'dock "Plans"' in str(e.value)


def test_unnamed_widget_shows_object_name_hint(window):
    b = QPushButton()
    b.setObjectName("mysteryButton")
    window.findChild(QDockWidget).widget().layout().addWidget(b)
    b.show()
    text = snapshot(scope="Plans")
    assert "button (unnamed, objectName=mysteryButton)" in text


def test_toast_appears(window, qtbot):
    from lightfall.ui.toast import ToastManager

    mgr = ToastManager.get_instance()
    mgr.set_parent_widget(window)
    toast = mgr.info("Saved", "entry 12")
    qtbot.waitUntil(lambda: toast.isVisible(), timeout=3000)
    text = snapshot()
    assert 'toast "Saved"' in text and '"entry 12"' in text
    toast.hide()

