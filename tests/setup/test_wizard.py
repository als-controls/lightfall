"""SetupWizard with fake steps: landing, navigation, progress, finish, cleanup."""
from __future__ import annotations

import threading
from unittest.mock import MagicMock

import pytest
import shiboken6
from PySide6.QtCore import QPropertyAnimation, Qt
from PySide6.QtWidgets import QDialog, QFrame, QLabel, QPushButton, QStackedWidget, QWidget

from lightfall.setup.page import StepPage
from lightfall.setup.progress import is_setup_completed, load_progress
from lightfall.setup.registry import SetupRegistry
from lightfall.setup.step import StepError, StepState, StepStatus

SAT = StepStatus.SATISFIED


class FakeStep:
    def __init__(self, step_id, status=StepStatus.NEEDS_INPUT, value="", gate=None, error=None,
                 apply_error=None):
        self.id = step_id
        self.title = step_id.title()
        self.result = StepState(status, value)
        self.gate = gate
        self.error = error
        self.apply_error = apply_error
        self.applied = 0
        self.closed = 0

    def detect(self, ctx):
        if self.gate is not None:
            self.gate.wait(5)
        return self.result

    def widget(self, ctx):
        step = self

        class Page(StepPage):
            def heading(self):
                return f"{step.title} heading", f"{step.title} lead"

            def summary(self):
                return f"{step.id} done"

            def close_page(self):
                step.closed += 1

        return Page(ctx)

    def validate(self, ctx):
        return self.error

    def apply(self, ctx):
        if self.apply_error:
            raise StepError(self.apply_error)
        self.applied += 1


@pytest.fixture
def make_wizard(qtbot, cfg):
    from lightfall.setup.context import SetupContext
    from lightfall.setup.wizard import SetupWizard

    def make(*steps, parent=None):
        registry = SetupRegistry()
        for step in steps:
            registry.register(step)
        ctx = SetupContext(config=cfg, session=MagicMock(), prefs=MagicMock())
        wizard = SetupWizard(parent, ctx, registry)
        if parent is None:  # a parent owns (and deletes) its wizard
            qtbot.addWidget(wizard)
        wizard.show()
        return wizard

    return make


def _click(wizard, name):
    wizard.findChild(QPushButton, name).click()


def _at(qtbot, wizard, step_id):
    qtbot.waitUntil(lambda: wizard.current_step_id() == step_id, timeout=3000)


def test_opens_at_the_first_unsatisfied_step(qtbot, make_wizard):
    from lightfall.setup.wizard import FADE_MS, RAIL_WIDTH

    wizard = make_wizard(FakeStep("a", SAT, "A ok"), FakeStep("b"), FakeStep("c"))
    _at(qtbot, wizard, "b")
    assert wizard.rail_item("a").state() == "done"
    assert wizard.rail_item("a").value_label.text() == "A ok"
    assert wizard.rail_item("b").is_current()
    assert wizard.findChild(QLabel, "setupKicker").text() == "STEP 2 OF 3"
    assert wizard.findChild(QLabel, "setupTitle").text() == "B heading"
    assert wizard.findChild(QLabel, "setupLead").text() == "B lead"
    surface = wizard.findChild(QFrame, "setupSurface")
    assert surface.minimumWidth() >= 720 and surface.minimumHeight() >= 400
    assert wizard.findChild(QFrame, "setupRail").width() == RAIL_WIDTH
    assert FADE_MS == 150


def test_fade_effect_is_removed_after_the_fade(qtbot, make_wizard):
    # A QGraphicsOpacityEffect left on the page stack mispaints its children
    # (facility tiles drawn over the footer), so it must only live during the fade.
    from lightfall.setup.wizard import FADE_MS

    wizard = make_wizard(FakeStep("a"), FakeStep("b"))
    _at(qtbot, wizard, "a")
    effect = wizard.findChild(QStackedWidget).graphicsEffect()
    qtbot.waitUntil(lambda: not effect.isEnabled(), timeout=FADE_MS * 10)
    assert wizard.findChild(QPropertyAnimation).duration() == FADE_MS


def test_rail_dot_is_centred_on_the_step_title(qtbot, make_wizard):
    wizard = make_wizard(FakeStep("a", SAT, "A ok"), FakeStep("b"))
    _at(qtbot, wizard, "b")
    for step_id in ("a", "b"):
        item = wizard.rail_item(step_id)
        dot, title = item.dot, item.title_label
        assert abs(dot.geometry().top() + dot._center_y()
                   - (title.geometry().center().y() + 0.5)) < 1
    assert wizard.rail_item("a").dot.state() == "done"
    assert wizard.rail_item("b").dot.state() == "current"


def test_all_satisfied_opens_the_ready_page(qtbot, make_wizard):
    wizard = make_wizard(FakeStep("a", SAT), FakeStep("b", SAT))
    _at(qtbot, wizard, "ready")
    assert wizard.findChild(QLabel, "setupTitle").text() == "Lightfall is ready"
    assert wizard.findChild(QPushButton, "setupPrimary").text() == "Finish"


def test_slow_later_detect_does_not_delay_landing(qtbot, make_wizard):
    gate = threading.Event()
    try:
        wizard = make_wizard(FakeStep("a", SAT), FakeStep("b"), FakeStep("c", gate=gate))
        _at(qtbot, wizard, "b")
        assert wizard.rail_item("c").state() == "checking"
    finally:
        gate.set()
    qtbot.waitUntil(lambda: wizard.rail_item("c").state() == "todo", timeout=3000)


def test_continue_validates_applies_saves_and_advances(qtbot, make_wizard, cfg):
    b = FakeStep("b")
    wizard = make_wizard(FakeStep("a", SAT), b, FakeStep("c"))
    _at(qtbot, wizard, "b")
    _click(wizard, "setupPrimary")
    assert b.applied == 1
    assert wizard.current_step_id() == "c"
    assert wizard.rail_item("b").state() == "done"
    assert wizard.rail_item("b").value_label.text() == "b done"
    assert load_progress(cfg).completed == ["b"]


def test_validation_error_stays_on_the_step(qtbot, make_wizard):
    b = FakeStep("b", error="Pick one first.")
    wizard = make_wizard(b, FakeStep("c"))
    _at(qtbot, wizard, "b")
    _click(wizard, "setupPrimary")
    assert b.applied == 0
    assert wizard.current_step_id() == "b"
    note = wizard.page("b").note
    assert (note.kind(), note.text()) == ("error", "Pick one first.")


def test_apply_error_marks_the_step_warn(qtbot, make_wizard):
    b = FakeStep("b", apply_error="Tiled: boom")
    wizard = make_wizard(b, FakeStep("c"))
    _at(qtbot, wizard, "b")
    _click(wizard, "setupPrimary")
    assert wizard.current_step_id() == "b"
    assert wizard.rail_item("b").state() == "warn"
    assert wizard.page("b").note.text() == "Tiled: boom"


def test_skip_back_and_rail_clicks(qtbot, make_wizard, cfg):
    wizard = make_wizard(FakeStep("a"), FakeStep("b"), FakeStep("c"))
    _at(qtbot, wizard, "a")
    assert not wizard.findChild(QPushButton, "setupBack").isVisible()
    _click(wizard, "setupSkip")
    assert wizard.current_step_id() == "b"
    assert wizard.rail_item("a").state() == "skipped"
    assert load_progress(cfg).skipped == ["a"]
    _click(wizard, "setupBack")
    assert wizard.current_step_id() == "a"
    qtbot.mouseClick(wizard.rail_item("c"), Qt.MouseButton.LeftButton)
    assert wizard.current_step_id() == "c"
    qtbot.mouseClick(wizard.rail_item("b"), Qt.MouseButton.LeftButton)
    assert wizard.current_step_id() == "b"


def test_progress_survives_close_and_reopen(qtbot, make_wizard, cfg):
    wizard = make_wizard(FakeStep("a", SAT), FakeStep("b"), FakeStep("c"))
    _at(qtbot, wizard, "b")
    _click(wizard, "setupSkip")
    wizard.reject()
    assert wizard.result() == QDialog.DialogCode.Rejected
    assert is_setup_completed(cfg) is False
    again = make_wizard(FakeStep("a", SAT), FakeStep("b"), FakeStep("c"))
    _at(qtbot, again, "c")
    assert again.rail_item("b").state() == "skipped"


def test_finish_sets_setup_completed(qtbot, make_wizard, cfg):
    finished = []
    wizard = make_wizard(FakeStep("a", SAT), FakeStep("b"))
    wizard.finished_setup.connect(lambda: finished.append(1))
    _at(qtbot, wizard, "b")
    _click(wizard, "setupPrimary")
    assert wizard.current_step_id() == "ready"
    _click(wizard, "setupPrimary")
    assert finished == [1]
    assert is_setup_completed(cfg) is True
    assert wizard.result() == QDialog.DialogCode.Accepted


def test_close_drops_late_detect_results(qtbot, make_wizard):
    gate = threading.Event()
    a = FakeStep("a")
    try:
        wizard = make_wizard(a, FakeStep("b", SAT, gate=gate))
        _at(qtbot, wizard, "a")
        _click(wizard, "setupClose")
        assert a.closed == 1
    finally:
        gate.set()
    qtbot.waitUntil(lambda: all(not f.isRunning() for f in wizard._futures), timeout=3000)
    qtbot.wait(50)  # let any queued result slot run
    assert wizard.state("b") is None


def test_continue_auto_skips_a_satisfied_later_step(qtbot, make_wizard):
    wizard = make_wizard(FakeStep("a"), FakeStep("b", SAT, "B ok"), FakeStep("c"))
    _at(qtbot, wizard, "a")
    qtbot.waitUntil(lambda: wizard.state("b") is not None, timeout=3000)
    _click(wizard, "setupPrimary")
    _at(qtbot, wizard, "c")
    assert wizard.rail_item("b").state() == "done"


def test_continue_chains_to_ready_when_the_rest_is_satisfied(qtbot, make_wizard):
    wizard = make_wizard(FakeStep("a"), FakeStep("b", SAT), FakeStep("c", SAT))
    _at(qtbot, wizard, "a")
    _click(wizard, "setupPrimary")
    _at(qtbot, wizard, "ready")


@pytest.mark.parametrize("navigate", ["back", "rail"])
def test_navigation_cancels_the_pending_auto_skip(qtbot, make_wizard, navigate):
    gate = threading.Event()
    try:
        wizard = make_wizard(FakeStep("a"), FakeStep("b", SAT, gate=gate), FakeStep("c"))
        _at(qtbot, wizard, "a")
        _click(wizard, "setupPrimary")
        assert wizard.current_step_id() == "b"
        if navigate == "back":
            _click(wizard, "setupBack")
        else:
            qtbot.mouseClick(wizard.rail_item("a"), Qt.MouseButton.LeftButton)
        assert wizard.current_step_id() == "a"
    finally:
        gate.set()
    qtbot.waitUntil(lambda: wizard.state("b") is not None, timeout=3000)
    qtbot.wait(50)
    assert wizard.current_step_id() == "a"


def test_cleanup_failure_still_finishes(qtbot, make_wizard, monkeypatch):
    wizard = make_wizard(FakeStep("a"))
    _at(qtbot, wizard, "a")

    def boom():
        raise RuntimeError("overlay")

    monkeypatch.setattr(wizard, "_remove_overlay", boom)
    with qtbot.waitSignal(wizard.finished, timeout=1000):
        wizard.reject()


def test_dim_overlay_covers_the_parent(qtbot, make_wizard):
    from lightfall.setup.wizard import DimOverlay

    parent = QWidget()
    parent.resize(800, 600)
    qtbot.addWidget(parent)
    parent.show()
    qtbot.waitExposed(parent)
    wizard = make_wizard(FakeStep("a"), parent=parent)
    overlay = parent.findChild(DimOverlay)
    assert overlay is not None and overlay.geometry() == parent.rect()
    wizard.reject()
    assert not shiboken6.isValid(overlay) or not overlay.isVisible()
