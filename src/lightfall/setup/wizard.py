"""SetupWizard: the step-rail dialog (spec sections 3 and 5)."""
from __future__ import annotations

from collections.abc import Callable

import shiboken6
from PySide6.QtCore import QEvent, QObject, QPropertyAnimation, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QMouseEvent, QPainter, QPaintEvent, QShowEvent
from PySide6.QtWidgets import (
    QDialog,
    QFrame,
    QGraphicsDropShadowEffect,
    QGraphicsOpacityEffect,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from lightfall.setup.context import SetupContext
from lightfall.setup.page import Spinner, StepPage
from lightfall.setup.progress import load_progress, mark_setup_completed, save_progress
from lightfall.setup.registry import SetupRegistry
from lightfall.setup.step import StepError, StepState, StepStatus
from lightfall.ui.style import modern
from lightfall.utils.logging import logger
from lightfall.utils.threads import QThreadFuture

FADE_MS = 150
RAIL_WIDTH = 208
SURFACE_MIN_WIDTH = 720
SURFACE_MIN_HEIGHT = 400
SHADOW_MARGIN = 28
RAIL_ICONS = {"done": "✓", "current": "●", "warn": "!", "todo": "", "skipped": "–",
              "checking": "…"}


class DimOverlay(QWidget):
    """Translucent scrim over the main window while the wizard is open."""

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self.setObjectName("setupDimOverlay")
        self.setGeometry(parent.rect())
        parent.installEventFilter(self)

    def eventFilter(self, obj: QObject, event: QEvent) -> bool:  # noqa: N802 - Qt API
        parent = self.parentWidget()
        if obj is parent and event.type() == QEvent.Type.Resize:
            self.setGeometry(parent.rect())
        return False

    def paintEvent(self, event: QPaintEvent) -> None:  # noqa: N802 - Qt API
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor(6, 8, 12, 153))
        painter.end()


class RailItem(QFrame):
    """One step in the rail: status icon, title, muted value line."""

    clicked = Signal(int)

    def __init__(self, index: int, step_id: str, title: str,
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._index = index
        self.setObjectName(f"rail-{step_id}")
        self.setProperty("modern", "rail-item")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        row = QHBoxLayout(self)
        row.setContentsMargins(8, 8, 8, 8)
        row.setSpacing(10)
        self.icon_label = QLabel(self)
        self.icon_label.setProperty("railRole", "icon")
        self.icon_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        column = QVBoxLayout()
        column.setSpacing(1)
        self.title_label = QLabel(title, self)
        self.title_label.setProperty("railRole", "title")
        self.value_label = QLabel(self)
        self.value_label.setProperty("railRole", "value")
        column.addWidget(self.title_label)
        column.addWidget(self.value_label)
        row.addWidget(self.icon_label, 0, Qt.AlignmentFlag.AlignTop)
        row.addLayout(column, 1)
        self.set_state("checking", "Checking…", current=False)

    def set_state(self, state: str, value: str, *, current: bool) -> None:
        self.setProperty("railState", state)
        self.setProperty("railCurrent", "true" if current else "false")
        self.icon_label.setText(RAIL_ICONS[state])
        self.value_label.setText(value)
        self.value_label.setVisible(bool(value))
        modern.repolish(self)

    def state(self) -> str:
        return str(self.property("railState"))

    def is_current(self) -> bool:
        return self.property("railCurrent") == "true"

    def mousePressEvent(self, event: QMouseEvent) -> None:  # noqa: N802 - Qt API
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit(self._index)
        super().mousePressEvent(event)


class SetupWizard(QDialog):
    """Modal setup dialog over the main window.

    On open every step's ``detect()`` runs in the background; the wizard opens at the
    first step that is neither satisfied nor skipped (the rail shows ✓ for the rest).
    Progress is saved after each step. Closing leaves the app as it is, and setup
    resumes next launch; Finish sets ``setup.completed``.
    """

    finished_setup = Signal()

    def __init__(self, parent: QWidget | None, ctx: SetupContext,
                 registry: SetupRegistry) -> None:
        super().__init__(parent)
        self.ctx = ctx
        self._steps = registry.steps()
        self._states: list[StepState | None] = [None] * len(self._steps)
        self._detect_gen = [0] * len(self._steps)
        self._futures: list[QThreadFuture] = []
        self._pages: dict[int, StepPage] = {}
        self._current: int | None = None
        self._landed = False
        self._pending: int | None = None  # step moved to whose re-detect may auto-skip it
        self._closed = False
        self._progress = load_progress(ctx.config)
        self._overlay: DimOverlay | None = None

        self.setObjectName("setupWizard")
        self.setWindowTitle("Set up Lightfall")
        self.setModal(True)
        self.setWindowFlags(Qt.WindowType.Dialog | Qt.WindowType.FramelessWindowHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setMinimumSize(SURFACE_MIN_WIDTH + 2 * SHADOW_MARGIN,
                            SURFACE_MIN_HEIGHT + 2 * SHADOW_MARGIN)
        self._build()
        self.setStyleSheet(modern.stylesheet(modern.current_tokens()))
        self._show_checking()
        self._start_detect(0)

    # --- construction --------------------------------------------------------

    def _build(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(SHADOW_MARGIN, SHADOW_MARGIN, SHADOW_MARGIN, SHADOW_MARGIN)
        self._surface = QFrame(self)
        self._surface.setObjectName("setupSurface")
        self._surface.setProperty("modern", "surface")
        self._surface.setMinimumSize(SURFACE_MIN_WIDTH, SURFACE_MIN_HEIGHT)
        shadow = QGraphicsDropShadowEffect(self._surface)
        shadow.setBlurRadius(70)
        shadow.setOffset(0, 24)
        shadow.setColor(QColor(0, 0, 0, 140))
        self._surface.setGraphicsEffect(shadow)
        outer.addWidget(self._surface)

        row = QHBoxLayout(self._surface)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(0)

        rail = QFrame(self._surface)
        rail.setObjectName("setupRail")
        rail.setFixedWidth(RAIL_WIDTH)
        rail_layout = QVBoxLayout(rail)
        rail_layout.setContentsMargins(14, 20, 14, 20)
        rail_layout.setSpacing(2)
        brand = QLabel("Set up Lightfall", rail)
        brand.setProperty("modern", "brand")
        rail_layout.addWidget(brand)
        rail_layout.addSpacing(12)
        self._rail_items: list[RailItem] = []
        for index, step in enumerate(self._steps):
            item = RailItem(index, step.id, step.title, rail)
            item.clicked.connect(self._on_rail_clicked)
            rail_layout.addWidget(item)
            self._rail_items.append(item)
        rail_layout.addStretch(1)
        row.addWidget(rail)

        main = QFrame(self._surface)
        main.setObjectName("setupMain")
        main_layout = QVBoxLayout(main)
        main_layout.setContentsMargins(30, 26, 30, 20)
        main_layout.setSpacing(6)
        top = QHBoxLayout()
        self._kicker = QLabel(main)
        self._kicker.setObjectName("setupKicker")
        self._kicker.setProperty("modern", "kicker")
        top.addWidget(self._kicker, 1)
        close = self._link("✕", "setupClose")
        close.setToolTip("Close. Setup resumes next time you start Lightfall.")
        close.clicked.connect(self.reject)
        top.addWidget(close)
        main_layout.addLayout(top)
        self._title = QLabel(main)
        self._title.setObjectName("setupTitle")
        self._title.setProperty("modern", "title")
        self._lead = QLabel(main)
        self._lead.setObjectName("setupLead")
        self._lead.setProperty("modern", "lead")
        self._lead.setWordWrap(True)
        self._lead.setMaximumWidth(430)
        main_layout.addWidget(self._title)
        main_layout.addWidget(self._lead)
        main_layout.addSpacing(12)

        self._stack = QStackedWidget(main)
        self._opacity = QGraphicsOpacityEffect(self._stack)
        self._opacity.setOpacity(1.0)
        self._stack.setGraphicsEffect(self._opacity)
        self._fade = QPropertyAnimation(self._opacity, b"opacity", self)
        self._fade.setDuration(FADE_MS)
        self._fade.setStartValue(0.0)
        self._fade.setEndValue(1.0)
        self._checking = self._make_checking_page()
        self._ready = self._make_ready_page()
        self._stack.addWidget(self._checking)
        self._stack.addWidget(self._ready)
        main_layout.addWidget(self._stack, 1)

        footer = QHBoxLayout()
        footer.setSpacing(18)
        self._back = self._link("Back", "setupBack")
        self._back.clicked.connect(self._on_back)
        self._skip = self._link("Skip", "setupSkip")
        self._skip.clicked.connect(self._on_skip_clicked)
        self._primary = QPushButton("Continue", main)
        self._primary.setObjectName("setupPrimary")
        self._primary.setProperty("modern", "primary")
        self._primary.setDefault(True)
        self._primary.clicked.connect(self._on_primary)
        footer.addWidget(self._back)
        footer.addWidget(self._skip)
        footer.addStretch(1)
        footer.addWidget(self._primary)
        main_layout.addLayout(footer)
        row.addWidget(main, 1)

    def _link(self, text: str, name: str) -> QPushButton:
        button = QPushButton(text, self._surface)
        button.setObjectName(name)
        button.setProperty("modern", "link")
        button.setFlat(True)
        button.setCursor(Qt.CursorShape.PointingHandCursor)
        return button

    def _make_checking_page(self) -> QWidget:
        page = QWidget(self._stack)
        row = QHBoxLayout(page)
        row.setContentsMargins(0, 0, 0, 0)
        self._checking_spinner = Spinner(page)
        row.addWidget(self._checking_spinner, 0, Qt.AlignmentFlag.AlignTop)
        label = QLabel("Checking what's already set up…", page)
        label.setProperty("modern", "muted")
        row.addWidget(label, 1)
        return page

    def _make_ready_page(self) -> QWidget:
        page = QWidget(self._stack)
        page.setObjectName("setupReadyPage")
        column = QVBoxLayout(page)
        column.setContentsMargins(0, 0, 0, 0)
        hint = QLabel("You can run this setup again any time from Help → Setup wizard…", page)
        hint.setProperty("modern", "muted")
        hint.setWordWrap(True)
        column.addWidget(hint)
        column.addStretch(1)
        return page

    # --- accessors (also used by tests) ---------------------------------------

    def current_index(self) -> int | None:
        return self._current

    def current_step_id(self) -> str | None:
        if self._current is None:
            return None
        if self._current >= len(self._steps):
            return "ready"
        return self._steps[self._current].id

    def _index_of(self, step_id: str) -> int:
        return next(i for i, step in enumerate(self._steps) if step.id == step_id)

    def rail_item(self, step_id: str) -> RailItem:
        return self._rail_items[self._index_of(step_id)]

    def page(self, step_id: str) -> StepPage | None:
        return self._pages.get(self._index_of(step_id))

    def state(self, step_id: str) -> StepState | None:
        return self._states[self._index_of(step_id)]

    # --- detection -------------------------------------------------------------

    def _start_detect(self, start: int) -> None:
        """(Re)run detect() for steps ``start..`` off the GUI thread."""
        self._futures = [f for f in self._futures if f.isRunning()]
        for index in range(start, len(self._steps)):
            self._detect_gen[index] += 1
            gen = self._detect_gen[index]
            self._states[index] = None
            self._paint_rail(index)
            step = self._steps[index]
            future = QThreadFuture(
                step.detect, self.ctx,
                callback_slot=lambda state, i=index, g=gen: self._on_detected(i, g, state),
                except_slot=lambda exc, i=index, g=gen: self._on_detected(
                    i, g, StepState(StepStatus.FAILED, f"Couldn't check: {exc}")),
                name=f"setup-detect-{step.id}", log_exceptions=False,
            )
            self._futures.append(future)
            future.start()

    def _on_detected(self, index: int, gen: int, state: object) -> None:
        if self._closed or not shiboken6.isValid(self) or gen != self._detect_gen[index]:
            return  # superseded, or the wizard is gone
        if not isinstance(state, StepState):
            state = StepState(StepStatus.FAILED, "Couldn't check")
        self._states[index] = state
        self._paint_rail(index)
        if index == self._pending:
            self._pending = None
            if state.satisfied and self._current == index:
                self._advance_from(index)  # still there, and already set up: skip it
                return
        if not self._landed:
            target = self._landing_index()
            if target is not None:
                self._landed = True
                self._go(target, animate=False)

    def _landing_index(self) -> int | None:
        """First step neither satisfied nor skipped, once every step before it is known."""
        for index, step in enumerate(self._steps):
            state = self._states[index]
            if state is None:
                return None
            if state.satisfied or step.id in self._progress.skipped:
                continue
            return index
        return len(self._steps)

    def _advance_from(self, index: int) -> None:
        """Go to the step after ``index``; if its re-detect is still running and it turns
        out satisfied, ``_on_detected`` moves on again unless the user navigates first."""
        target = self._next_index(index)
        in_flight = target < len(self._steps) and self._states[target] is None
        self._go(target)
        self._pending = target if in_flight else None

    def _next_index(self, index: int) -> int:
        for later in range(index + 1, len(self._steps)):
            state = self._states[later]
            if state is None or not state.satisfied:
                return later
        return len(self._steps)

    # --- painting ----------------------------------------------------------------

    def _paint_rail(self, index: int) -> None:
        item = self._rail_items[index]
        state = self._states[index]
        current = index == self._current
        if state is None:
            item.set_state("current" if current else "checking", "Checking…", current=current)
        elif state.status is StepStatus.FAILED:
            item.set_state("warn", state.value, current=current)
        elif current:
            item.set_state("current", state.value, current=True)
        elif state.satisfied:
            item.set_state("done", state.value, current=False)
        elif (state.status is StepStatus.SKIPPED
              or self._steps[index].id in self._progress.skipped):
            item.set_state("skipped", "Skipped", current=False)
        else:
            item.set_state("todo", state.value, current=False)

    def _refresh_header(self) -> None:
        total = len(self._steps)
        if self._current is None:
            kicker, title, lead = "", "Checking your setup…", ""
        elif self._current >= total:
            kicker, title, lead = "ALL SET", "Lightfall is ready", ""
        else:
            kicker = f"STEP {self._current + 1} OF {total}"
            title, lead = self._pages[self._current].heading()
        self._kicker.setText(kicker)
        self._title.setText(title)
        self._lead.setText(lead)
        self._lead.setVisible(bool(lead))

    def _refresh_footer(self) -> None:
        if not shiboken6.isValid(self):
            return
        index = self._current
        if index is None:
            for widget in (self._primary, self._back, self._skip):
                widget.hide()
            return
        self._primary.show()
        self._back.setVisible(index > 0)
        if index >= len(self._steps):
            self._primary.setText("Finish")
            self._primary.setEnabled(True)
            self._skip.hide()
            return
        page = self._pages[index]
        self._primary.setText(page.primary_label())
        self._primary.setEnabled(page.primary_enabled())
        self._skip.setText(page.skip_label())
        self._skip.show()

    def _show_checking(self) -> None:
        self._stack.setCurrentWidget(self._checking)
        self._checking_spinner.start()
        self._refresh_header()
        self._refresh_footer()

    # --- navigation ---------------------------------------------------------------

    def _page_for(self, index: int) -> StepPage:
        page = self._pages.get(index)
        if page is None:
            page = self._steps[index].widget(self.ctx)
            page.completed.connect(lambda i=index: self._on_completed(i))
            page.skip_requested.connect(lambda i=index: self._on_skip(i))
            page.primary_changed.connect(self._refresh_footer)
            self._stack.addWidget(page)
            self._pages[index] = page
        return page

    def _go(self, index: int, *, animate: bool = True) -> None:
        previous = self._current
        if previous is not None and previous != index and previous in self._pages:
            self._pages[previous].on_leave()
        self._current = index
        self._checking_spinner.stop()
        if index >= len(self._steps):
            self._stack.setCurrentWidget(self._ready)
        else:
            self._stack.setCurrentWidget(self._page_for(index))
        self._refresh_header()
        self._refresh_footer()
        for i in range(len(self._steps)):
            self._paint_rail(i)
        if index < len(self._steps) and previous != index:
            self._pages[index].on_enter()
        if animate:
            self._fade.stop()
            self._fade.start()

    def _on_rail_clicked(self, index: int) -> None:
        self._pending = None
        self._landed = True
        if index != self._current:
            self._go(index)

    def _on_back(self) -> None:
        self._pending = None
        if self._current is not None and self._current > 0:
            self._go(self._current - 1)

    def _on_primary(self) -> None:
        if self._current is None:
            return
        if self._current >= len(self._steps):
            self._finish()
            return
        self._pages[self._current].on_primary()

    def _on_skip_clicked(self) -> None:
        if self._current is not None:
            self._on_skip(self._current)

    def _on_skip(self, index: int) -> None:
        if index != self._current or index >= len(self._steps):
            return
        self._progress.mark_skipped(self._steps[index].id)
        self._save_progress()
        self._states[index] = StepState(StepStatus.SKIPPED, "Skipped")
        self._pending = None
        self._go(self._next_index(index))

    def _on_completed(self, index: int) -> None:
        if index != self._current:
            return
        step, page = self._steps[index], self._pages[index]
        error = step.validate(self.ctx)
        if error:
            page.note.show_note("error", error)
            return
        try:
            step.apply(self.ctx)
        except StepError as exc:
            page.note.show_note("error", str(exc))
            self._states[index] = StepState(StepStatus.FAILED, "Needs attention")
            self._paint_rail(index)
            return
        except Exception as exc:  # noqa: BLE001 - a bug must not wedge the wizard
            logger.exception("Setup step {!r} failed to apply", step.id)
            page.note.show_note("error", f"Something went wrong: {exc}")
            self._states[index] = StepState(StepStatus.FAILED, "Needs attention")
            self._paint_rail(index)
            return
        self._states[index] = StepState(StepStatus.SATISFIED, page.summary())
        self._progress.mark_completed(step.id)
        self._save_progress()
        self._start_detect(index + 1)  # later steps may depend on this one
        self._advance_from(index)

    def _save_progress(self) -> None:
        try:
            save_progress(self.ctx.config, self._progress)
        except Exception:  # noqa: BLE001 - losing progress must not break the wizard
            logger.exception("Couldn't save setup progress")

    def _finish(self) -> None:
        try:
            mark_setup_completed(self.ctx.config)
        except Exception:  # noqa: BLE001
            logger.exception("Couldn't save setup.completed")
        self.finished_setup.emit()
        self.accept()

    # --- lifecycle ---------------------------------------------------------------

    def showEvent(self, event: QShowEvent) -> None:  # noqa: N802 - Qt API
        super().showEvent(event)
        parent = self.parentWidget()
        if self._overlay is None and parent is not None and parent.isVisible():
            self._overlay = DimOverlay(parent.window())
            self._overlay.show()
            self._overlay.raise_()

    def _remove_overlay(self) -> None:
        overlay, self._overlay = self._overlay, None
        if overlay is not None and shiboken6.isValid(overlay):
            overlay.hide()
            overlay.deleteLater()

    def done(self, result: int) -> None:
        try:
            if not self._closed:
                self._closed = True
                self._pending = None
                self._detect_gen = [gen + 1 for gen in self._detect_gen]
                self._checking_spinner.stop()
                for page in self._pages.values():
                    try:
                        page.on_leave()
                        page.close_page()
                    except Exception:  # noqa: BLE001
                        logger.exception("Setup page cleanup failed")
                self._remove_overlay()
        except Exception:  # noqa: BLE001 - the dialog must still close and emit finished
            logger.exception("Setup wizard cleanup failed")
        finally:
            super().done(result)


def open_setup_wizard(
    parent: QWidget | None,
    *,
    on_closed: Callable[[], None] | None = None,
    ctx: SetupContext | None = None,
    registry: SetupRegistry | None = None,
) -> SetupWizard:
    """Open the wizard modally over ``parent`` (non-blocking); ``on_closed`` runs on close."""
    from lightfall.setup.registry import default_registry

    wizard = SetupWizard(parent, ctx or SetupContext.from_services(),
                         registry or default_registry())
    wizard.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
    if on_closed is not None:
        wizard.finished.connect(lambda _result: on_closed())
    wizard.open()
    return wizard


def schedule_first_run_wizard(window: QWidget, *, on_closed: Callable[[], None]) -> None:
    """Open the wizard on the first event-loop turn, once app.run() has shown ``window``.

    ``on_closed`` (the post-login plugin wave) always runs: when the wizard closes, or
    at once if it cannot be opened, so a broken wizard never leaves an empty window.
    """
    def _open() -> None:
        try:
            open_setup_wizard(window, on_closed=on_closed)
        except Exception:  # noqa: BLE001 - startup must go on without the wizard
            logger.exception("Couldn't open the setup wizard")
            on_closed()

    QTimer.singleShot(0, _open)
