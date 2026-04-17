"""Tutorial overlay system for guided user onboarding.

Provides an interactive tutorial mode that highlights widgets in sequence,
guiding users through the LUCID interface with:
- Dimmed overlay with spotlight cutout around target widgets
- Callout popups with step instructions and navigation
- Smooth animated transitions between steps
- Theme-aware styling
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import TYPE_CHECKING, Any

from PySide6.QtCore import (
    Property,
    QEasingCurve,
    QEvent,
    QObject,
    QPoint,
    QPropertyAnimation,
    QRect,
    QRectF,
    QSize,
    Qt,
    QTimer,
    Signal,
    Slot,
)
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import (
    QApplication,
    QFrame,
    QGraphicsDropShadowEffect,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from lucid.utils.logging import logger

if TYPE_CHECKING:
    from lucid.ui.mainwindow import NCSMainWindow


class CalloutPosition(Enum):
    """Where to place the callout relative to the target widget."""

    AUTO = auto()
    ABOVE = auto()
    BELOW = auto()
    LEFT = auto()
    RIGHT = auto()


@dataclass
class TutorialStep:
    """A single step in a tutorial sequence.

    Attributes:
        target: Callable returning the widget to highlight, or None for
            a centered modal step (e.g. welcome/finish screens).
        title: Step title displayed in the callout.
        message: Instructional text for this step.
        position: Preferred callout position relative to target.
        padding: Extra pixels around the target widget for the cutout.
        target_description: Fallback text if target widget can't be found.
    """

    target: Callable[[], QWidget | None] | None = None
    title: str = ""
    message: str = ""
    position: CalloutPosition = CalloutPosition.AUTO
    padding: int = 8
    target_description: str = ""


@dataclass
class Tutorial:
    """A named tutorial consisting of ordered steps.

    Attributes:
        id: Unique tutorial identifier.
        name: Human-readable tutorial name.
        description: What this tutorial covers.
        steps: Ordered list of tutorial steps.
    """

    id: str
    name: str
    description: str = ""
    steps: list[TutorialStep] = field(default_factory=list)


class TutorialCallout(QFrame):
    """Popup callout widget showing step instructions.

    Positioned near the highlighted target widget with title, message,
    navigation buttons, and step progress indicator.

    Signals:
        next_clicked: User clicked Next / Finish.
        back_clicked: User clicked Back.
        skip_clicked: User clicked Skip to end the tutorial.
    """

    next_clicked = Signal()
    back_clicked = Signal()
    skip_clicked = Signal()

    # Constraints
    MAX_WIDTH = 380
    MIN_WIDTH = 280

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("TutorialCallout")
        self.setFixedWidth(self.MAX_WIDTH)

        self._setup_ui()
        self._setup_style()
        self._setup_shadow()

    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(8)

        # Title
        self._title_label = QLabel()
        self._title_label.setObjectName("TutorialCalloutTitle")
        self._title_label.setWordWrap(True)
        layout.addWidget(self._title_label)

        # Message
        self._message_label = QLabel()
        self._message_label.setObjectName("TutorialCalloutMessage")
        self._message_label.setWordWrap(True)
        layout.addWidget(self._message_label)

        # Bottom row: step counter + buttons
        bottom_layout = QHBoxLayout()
        bottom_layout.setContentsMargins(0, 6, 0, 0)
        bottom_layout.setSpacing(8)

        self._step_label = QLabel()
        self._step_label.setObjectName("TutorialCalloutStep")
        bottom_layout.addWidget(self._step_label)

        bottom_layout.addStretch()

        self._skip_btn = QPushButton("Skip")
        self._skip_btn.setObjectName("TutorialSkipBtn")
        self._skip_btn.setFixedWidth(50)
        self._skip_btn.clicked.connect(self.skip_clicked)
        bottom_layout.addWidget(self._skip_btn)

        self._back_btn = QPushButton("Back")
        self._back_btn.setObjectName("TutorialBackBtn")
        self._back_btn.setFixedWidth(50)
        self._back_btn.clicked.connect(self.back_clicked)
        bottom_layout.addWidget(self._back_btn)

        self._next_btn = QPushButton("Next")
        self._next_btn.setObjectName("TutorialNextBtn")
        self._next_btn.setFixedWidth(70)
        self._next_btn.setProperty("primary", True)
        self._next_btn.clicked.connect(self.next_clicked)
        bottom_layout.addWidget(self._next_btn)

        layout.addLayout(bottom_layout)

    def _setup_style(self) -> None:
        """Apply theme-aware styling."""
        from lucid.ui.theme import ThemeManager

        tm = ThemeManager.get_instance()
        c = tm.colors

        self.setStyleSheet(f"""
            #TutorialCallout {{
                background-color: {c.surface};
                border: 1px solid {c.border};
                border-radius: 8px;
            }}
            #TutorialCalloutTitle {{
                font-size: 14px;
                font-weight: bold;
                color: {c.text};
            }}
            #TutorialCalloutMessage {{
                font-size: 12px;
                color: {c.text_secondary};
                line-height: 1.4;
            }}
            #TutorialCalloutStep {{
                font-size: 11px;
                color: {c.text_secondary};
            }}
            #TutorialSkipBtn {{
                background: transparent;
                border: none;
                color: {c.text_secondary};
                font-size: 11px;
                padding: 4px;
            }}
            #TutorialSkipBtn:hover {{
                color: {c.text};
            }}
            #TutorialBackBtn {{
                background: transparent;
                border: 1px solid {c.border};
                border-radius: 4px;
                color: {c.text};
                font-size: 11px;
                padding: 4px;
            }}
            #TutorialBackBtn:hover {{
                background: {c.border};
            }}
            #TutorialNextBtn {{
                background: {c.primary};
                border: none;
                border-radius: 4px;
                color: white;
                font-size: 12px;
                font-weight: bold;
                padding: 6px;
            }}
            #TutorialNextBtn:hover {{
                background: {ThemeManager._adjust_color(c.primary, -20)};
            }}
        """)

    def _setup_shadow(self) -> None:
        shadow = QGraphicsDropShadowEffect(self)
        shadow.setBlurRadius(20)
        shadow.setColor(QColor(0, 0, 0, 80))
        shadow.setOffset(0, 4)
        self.setGraphicsEffect(shadow)

    def set_step(
        self,
        title: str,
        message: str,
        step_num: int,
        total_steps: int,
        *,
        is_first: bool = False,
        is_last: bool = False,
    ) -> None:
        """Update the callout content for a new step.

        Args:
            title: Step title.
            message: Step message.
            step_num: Current step number (1-based).
            total_steps: Total number of steps.
            is_first: Whether this is the first step.
            is_last: Whether this is the last step.
        """
        self._title_label.setText(title)
        self._message_label.setText(message)
        self._step_label.setText(f"{step_num} / {total_steps}")

        self._back_btn.setVisible(not is_first)
        self._next_btn.setText("Finish" if is_last else "Next")

        # Re-layout to get correct size hint
        self.adjustSize()


class TutorialOverlay(QWidget):
    """Full-window overlay with spotlight cutout and callout.

    Covers the parent window with a semi-transparent dimming layer,
    cuts out a spotlight around the target widget, and positions a
    callout with instructions nearby.

    Signals:
        tutorial_finished: Emitted when the tutorial ends (completed or skipped).
        step_changed: Emitted with the new step index when advancing.
    """

    tutorial_finished = Signal()
    step_changed = Signal(int)

    # Overlay appearance
    OVERLAY_COLOR = QColor(0, 0, 0, 150)
    SPOTLIGHT_BORDER_COLOR = QColor(255, 255, 255, 60)
    SPOTLIGHT_BORDER_WIDTH = 2
    SPOTLIGHT_RADIUS = 6
    CALLOUT_MARGIN = 12  # Gap between spotlight and callout
    EDGE_PADDING = 24  # Inset from overlay edge (must exceed shadow blur radius)

    def __init__(self, parent: QWidget) -> None:
        """Initialize the overlay.

        Args:
            parent: The main window to overlay.
        """
        super().__init__(parent)
        self.setObjectName("TutorialOverlay")

        # Must cover the entire parent
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, False)
        self.setMouseTracking(True)

        self._tutorial: Tutorial | None = None
        self._current_step: int = 0

        # Current spotlight rectangle (animated)
        self._spotlight_rect: QRectF = QRectF()
        # Animation for smooth spotlight transitions
        self._spotlight_anim: QPropertyAnimation | None = None

        # Callout widget
        self._callout = TutorialCallout(self)
        self._callout.next_clicked.connect(self._on_next)
        self._callout.back_clicked.connect(self._on_back)
        self._callout.skip_clicked.connect(self._on_skip)
        self._callout.hide()

        # Watch parent for resize events so overlay stays full-window
        parent.installEventFilter(self)

    def _get_spotlight_rect(self) -> QRectF:
        return self._spotlight_rect

    def _set_spotlight_rect(self, rect: QRectF) -> None:
        self._spotlight_rect = rect
        self.update()
        self._position_callout()

    spotlightRect = Property(QRectF, _get_spotlight_rect, _set_spotlight_rect)

    def start(self, tutorial: Tutorial) -> None:
        """Start a tutorial sequence.

        Args:
            tutorial: The tutorial to run.
        """
        if not tutorial.steps:
            logger.warning("Tutorial '{}' has no steps", tutorial.id)
            return

        self._tutorial = tutorial
        self._current_step = 0

        # Resize to cover parent
        self._resize_to_parent()

        self.show()
        self.raise_()
        self._show_step(0)

        logger.info("Started tutorial: {} ({} steps)", tutorial.name, len(tutorial.steps))

    def stop(self) -> None:
        """Stop the current tutorial."""
        parent = self.parentWidget()
        if parent is not None:
            parent.removeEventFilter(self)
        self._callout.hide()
        self.hide()
        self._tutorial = None
        self._current_step = 0
        self.tutorial_finished.emit()

    def _show_step(self, index: int) -> None:
        """Show a specific tutorial step.

        Args:
            index: Step index.
        """
        if self._tutorial is None or index < 0 or index >= len(self._tutorial.steps):
            return

        self._current_step = index
        step = self._tutorial.steps[index]

        # Resolve target widget
        target_rect = self._resolve_target_rect(step)

        # Animate spotlight to new position
        self._animate_spotlight(target_rect)

        # Update callout content
        self._callout.set_step(
            title=step.title,
            message=step.message,
            step_num=index + 1,
            total_steps=len(self._tutorial.steps),
            is_first=(index == 0),
            is_last=(index == len(self._tutorial.steps) - 1),
        )
        self._callout.show()
        self._callout.raise_()

        self.step_changed.emit(index)

    def _resolve_target_rect(self, step: TutorialStep) -> QRectF:
        """Get the rectangle for the target widget in overlay coordinates.

        Args:
            step: The tutorial step.

        Returns:
            Target rectangle in overlay-local coordinates, or a centered
            rectangle if the target can't be found.
        """
        if step.target is None:
            # No target - center a virtual rect for modal steps
            center = self.rect().center()
            return QRectF(center.x() - 1, center.y() - 100, 2, 2)

        widget = step.target()
        if widget is None or not widget.isVisible():
            # Widget not found or hidden - use center
            logger.warning(
                "Tutorial target not found for step '{}' ({})",
                step.title,
                step.target_description,
            )
            center = self.rect().center()
            return QRectF(center.x() - 1, center.y() - 100, 2, 2)

        # Map widget rect to overlay coordinates
        top_left = widget.mapToGlobal(QPoint(0, 0))
        top_left = self.mapFromGlobal(top_left)
        size = widget.size()

        rect = QRectF(
            top_left.x() - step.padding,
            top_left.y() - step.padding,
            size.width() + step.padding * 2,
            size.height() + step.padding * 2,
        )

        return rect

    def _animate_spotlight(self, target_rect: QRectF) -> None:
        """Animate the spotlight to a new position.

        Args:
            target_rect: New spotlight rectangle.
        """
        if self._spotlight_anim is not None:
            self._spotlight_anim.stop()

        # First step or no current rect: snap immediately
        if self._spotlight_rect.isNull() or self._spotlight_rect.isEmpty():
            self._spotlight_rect = target_rect
            self.update()
            # Position callout after a tiny delay so layout is settled
            QTimer.singleShot(10, self._position_callout)
            return

        self._spotlight_anim = QPropertyAnimation(self, b"spotlightRect")
        self._spotlight_anim.setDuration(300)
        self._spotlight_anim.setEasingCurve(QEasingCurve.Type.InOutCubic)
        self._spotlight_anim.setStartValue(self._spotlight_rect)
        self._spotlight_anim.setEndValue(target_rect)
        self._spotlight_anim.start()

    def _position_callout(self) -> None:
        """Position the callout relative to the current spotlight."""
        if self._tutorial is None:
            return

        step = self._tutorial.steps[self._current_step]
        spot = self._spotlight_rect
        # Use actual widget size, not sizeHint — sizeHint ignores
        # setFixedWidth and returns the layout's natural preferred width,
        # which can be narrower than 380, making the clamp too loose.
        cw = self._callout.width()
        ch = self._callout.height()
        callout_size = QSize(cw, ch)
        overlay_rect = self.rect()

        # Determine best position
        position = step.position
        if position == CalloutPosition.AUTO:
            position = self._auto_position(spot, callout_size, overlay_rect)

        # Calculate callout origin
        x, y = self._calculate_callout_xy(position, spot, callout_size, overlay_rect)

        # Clamp to overlay bounds (EDGE_PADDING accounts for drop shadow bleed)
        pad = self.EDGE_PADDING
        x = max(pad, min(x, overlay_rect.width() - cw - pad))
        y = max(pad, min(y, overlay_rect.height() - ch - pad))

        self._callout.move(int(x), int(y))

    def _auto_position(
        self, spot: QRectF, callout_size: QSize, overlay_rect: QRect
    ) -> CalloutPosition:
        """Determine the best callout position automatically.

        Tries below, then right, then above, then left - preferring
        positions with the most available space.
        """
        margin = self.CALLOUT_MARGIN
        cw, ch = callout_size.width(), callout_size.height()

        space_below = overlay_rect.height() - spot.bottom() - margin
        space_above = spot.top() - margin
        space_right = overlay_rect.width() - spot.right() - margin
        space_left = spot.left() - margin

        # Prefer below, then right, then above, then left
        if space_below >= ch + margin:
            return CalloutPosition.BELOW
        if space_right >= cw + margin:
            return CalloutPosition.RIGHT
        if space_above >= ch + margin:
            return CalloutPosition.ABOVE
        if space_left >= cw + margin:
            return CalloutPosition.LEFT

        # Fallback: wherever has most space vertically
        return CalloutPosition.BELOW if space_below >= space_above else CalloutPosition.ABOVE

    def _calculate_callout_xy(
        self,
        position: CalloutPosition,
        spot: QRectF,
        callout_size: QSize,
        overlay_rect: QRect,
    ) -> tuple[float, float]:
        """Calculate callout x, y for a given position."""
        margin = self.CALLOUT_MARGIN
        cw, ch = callout_size.width(), callout_size.height()

        if position == CalloutPosition.BELOW:
            x = spot.center().x() - cw / 2
            y = spot.bottom() + margin
        elif position == CalloutPosition.ABOVE:
            x = spot.center().x() - cw / 2
            y = spot.top() - ch - margin
        elif position == CalloutPosition.RIGHT:
            x = spot.right() + margin
            y = spot.center().y() - ch / 2
        elif position == CalloutPosition.LEFT:
            x = spot.left() - cw - margin
            y = spot.center().y() - ch / 2
        else:
            # Centered fallback
            x = (overlay_rect.width() - cw) / 2
            y = (overlay_rect.height() - ch) / 2

        return x, y

    # Painting

    def paintEvent(self, event: Any) -> None:
        """Paint the dimmed overlay with spotlight cutout."""
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        # Full overlay path
        overlay_path = QPainterPath()
        overlay_path.addRect(QRectF(self.rect()))

        # Subtract the spotlight cutout
        if not self._spotlight_rect.isNull() and not self._spotlight_rect.isEmpty():
            cutout = QPainterPath()
            cutout.addRoundedRect(
                self._spotlight_rect,
                self.SPOTLIGHT_RADIUS,
                self.SPOTLIGHT_RADIUS,
            )
            overlay_path = overlay_path.subtracted(cutout)

        # Draw dimmed background
        painter.fillPath(overlay_path, self.OVERLAY_COLOR)

        # Draw spotlight border
        if not self._spotlight_rect.isNull() and not self._spotlight_rect.isEmpty():
            pen = QPen(self.SPOTLIGHT_BORDER_COLOR, self.SPOTLIGHT_BORDER_WIDTH)
            painter.setPen(pen)
            painter.drawRoundedRect(
                self._spotlight_rect,
                self.SPOTLIGHT_RADIUS,
                self.SPOTLIGHT_RADIUS,
            )

        painter.end()

    # Event handling

    def mousePressEvent(self, event: Any) -> None:
        """Handle clicks - advance if clicking on spotlight area."""
        if self._spotlight_rect.contains(event.position()):
            # Click on the highlighted widget - advance
            self._on_next()
        else:
            # Click outside - consume the event (don't pass through)
            event.accept()

    def keyPressEvent(self, event: Any) -> None:
        """Handle keyboard navigation."""
        key = event.key()
        if key == Qt.Key.Key_Escape:
            self._on_skip()
        elif key in (Qt.Key.Key_Right, Qt.Key.Key_Return, Qt.Key.Key_Space):
            self._on_next()
        elif key == Qt.Key.Key_Left:
            self._on_back()
        else:
            super().keyPressEvent(event)

    def eventFilter(self, obj: QObject, event: QEvent) -> bool:
        """Track parent resize so overlay stays full-window."""
        if obj is self.parentWidget() and event.type() == QEvent.Type.Resize:
            self._resize_to_parent()
            if self._tutorial is not None:
                # Refresh spotlight position (widget may have moved)
                step = self._tutorial.steps[self._current_step]
                self._spotlight_rect = self._resolve_target_rect(step)
                self.update()
                self._position_callout()
        return False

    def showEvent(self, event: Any) -> None:
        super().showEvent(event)
        self.setFocus()
        self._resize_to_parent()

    def _resize_to_parent(self) -> None:
        """Resize to cover the entire parent widget."""
        parent = self.parentWidget()
        if parent:
            self.setGeometry(parent.rect())

    # Navigation

    @Slot()
    def _on_next(self) -> None:
        if self._tutorial is None:
            return

        next_index = self._current_step + 1
        if next_index >= len(self._tutorial.steps):
            # Tutorial complete
            logger.info("Tutorial completed: {}", self._tutorial.name)
            self.stop()
        else:
            self._show_step(next_index)

    @Slot()
    def _on_back(self) -> None:
        if self._current_step > 0:
            self._show_step(self._current_step - 1)

    @Slot()
    def _on_skip(self) -> None:
        if self._tutorial:
            logger.info("Tutorial skipped: {}", self._tutorial.name)
        self.stop()


class TutorialManager(QObject):
    """Singleton manager for registering and running tutorials.

    Manages the overlay lifecycle and stores registered tutorials.

    Signals:
        tutorial_started: Emitted with tutorial ID when one begins.
        tutorial_ended: Emitted with tutorial ID when one ends.
    """

    tutorial_started = Signal(str)
    tutorial_ended = Signal(str)

    _instance: TutorialManager | None = None
    _lock = threading.RLock()

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._tutorials: dict[str, Tutorial] = {}
        self._overlay: TutorialOverlay | None = None
        self._active_tutorial_id: str | None = None

    @classmethod
    def get_instance(cls) -> TutorialManager:
        """Get the singleton TutorialManager instance."""
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    @classmethod
    def reset(cls) -> None:
        """Reset the singleton instance (for testing)."""
        with cls._lock:
            if cls._instance is not None:
                cls._instance.deleteLater()
            cls._instance = None

    def register(self, tutorial: Tutorial) -> None:
        """Register a tutorial.

        Args:
            tutorial: Tutorial to register.
        """
        self._tutorials[tutorial.id] = tutorial
        logger.debug("Registered tutorial: {} ({} steps)", tutorial.id, len(tutorial.steps))

    def unregister(self, tutorial_id: str) -> None:
        """Remove a registered tutorial.

        Args:
            tutorial_id: Tutorial to remove.
        """
        self._tutorials.pop(tutorial_id, None)

    def list_tutorials(self) -> list[Tutorial]:
        """Get all registered tutorials."""
        return list(self._tutorials.values())

    def start(self, tutorial_id: str, window: NCSMainWindow) -> bool:
        """Start a tutorial by ID.

        Args:
            tutorial_id: ID of the registered tutorial.
            window: The main window to overlay.

        Returns:
            True if the tutorial was started successfully.
        """
        tutorial = self._tutorials.get(tutorial_id)
        if tutorial is None:
            logger.warning("Unknown tutorial: {}", tutorial_id)
            return False

        # Stop any running tutorial
        if self._overlay is not None:
            self._overlay.stop()

        # Create overlay on the main window
        self._overlay = TutorialOverlay(window)
        self._overlay.tutorial_finished.connect(self._on_tutorial_finished)

        self._active_tutorial_id = tutorial_id
        self._overlay.start(tutorial)

        self.tutorial_started.emit(tutorial_id)
        return True

    def stop(self) -> None:
        """Stop the currently running tutorial."""
        if self._overlay is not None:
            self._overlay.stop()

    @property
    def is_running(self) -> bool:
        """Whether a tutorial is currently active."""
        return self._overlay is not None and self._overlay.isVisible()

    @Slot()
    def _on_tutorial_finished(self) -> None:
        tid = self._active_tutorial_id
        self._active_tutorial_id = None

        if self._overlay is not None:
            self._overlay.deleteLater()
            self._overlay = None

        if tid:
            self.tutorial_ended.emit(tid)


# ── Built-in tutorials ────────────────────────────────────────────────────


def _build_welcome_tutorial() -> Tutorial:
    """Build the default 'Welcome to LUCID' tutorial."""

    def _find_sidebar() -> QWidget | None:
        window = _find_main_window()
        if window is None or window._docking_manager is None:
            return None
        return window._docking_manager.icon_sidebar

    def _find_sidebar_button(panel_id: str) -> Callable[[], QWidget | None]:
        """Return a callable that finds a specific sidebar icon button."""

        def finder() -> QWidget | None:
            window = _find_main_window()
            if window is None or window._docking_manager is None:
                return None
            sidebar = window._docking_manager.icon_sidebar
            if sidebar is None:
                return None
            return sidebar._buttons.get(panel_id)

        return finder

    def _find_menubar() -> QWidget | None:
        window = _find_main_window()
        return window.menuBar() if window else None

    def _find_statusbar() -> QWidget | None:
        window = _find_main_window()
        return window.statusBar() if window else None

    def _find_re_control() -> QWidget | None:
        window = _find_main_window()
        return window._re_control if window else None

    def _find_panel_dock(panel_id: str) -> Callable[[], QWidget | None]:
        """Return a callable that finds a panel's dock widget."""

        def finder() -> QWidget | None:
            window = _find_main_window()
            if window is None or window._docking_manager is None:
                return None
            return window._docking_manager.get_dock_widget(panel_id)

        return finder

    return Tutorial(
        id="welcome",
        name="Welcome to LUCID",
        description="A quick tour of the LUCID interface.",
        steps=[
            # ── Intro ──────────────────────────────────────────────
            TutorialStep(
                target=None,
                title="Welcome to LUCID",
                message=(
                    "LUCID is the Lightsource Unified Control Interface "
                    "Dashboard for beamline controls.\n\n"
                    "This quick tour will show you around the interface. "
                    "Use the arrow keys, click Next, or click the "
                    "highlighted area to advance."
                ),
            ),
            # ── Top-level UI ───────────────────────────────────────
            TutorialStep(
                target=_find_menubar,
                title="Menu Bar",
                message=(
                    "The menu bar provides access to File operations, "
                    "View options (including panel management), Tools "
                    "(Preferences), User login, and Help."
                ),
                position=CalloutPosition.BELOW,
                target_description="Menu bar",
            ),
            TutorialStep(
                target=_find_re_control,
                title="Run Engine Status",
                message=(
                    "This indicator shows the current state of the "
                    "Bluesky Run Engine. It turns green when running a "
                    "scan and shows pause/stop controls."
                ),
                position=CalloutPosition.AUTO,
                target_description="RunEngine control widget",
            ),
            TutorialStep(
                target=_find_statusbar,
                title="Status Bar",
                message=(
                    "The status bar shows connection state, authentication "
                    "info, and other indicators. Plugins can add their "
                    "own status widgets here."
                ),
                position=CalloutPosition.ABOVE,
                target_description="Status bar",
            ),
            # ── Sidebar overview ───────────────────────────────────
            TutorialStep(
                target=_find_sidebar,
                title="Sidebar",
                message=(
                    "The icon sidebar gives you quick access to all panels. "
                    "Click an icon to toggle its panel. "
                    "You can drag icons to reorder them.\n\n"
                    "Let's walk through the key panels."
                ),
                position=CalloutPosition.RIGHT,
                target_description="Icon strip sidebar",
            ),
            # ── Individual panel icons ─────────────────────────────
            TutorialStep(
                target=_find_sidebar_button("lucid.panels.logbook_entries"),
                title="Logbook Entries",
                message=(
                    "The logbook is where you record experiment notes, "
                    "observations, and results. Entries are saved per-project "
                    "and can include rich text and attachments."
                ),
                position=CalloutPosition.RIGHT,
                padding=4,
                target_description="Logbook Entries sidebar button",
            ),
            TutorialStep(
                target=_find_sidebar_button("lucid.panels.bluesky"),
                title="Bluesky Plans",
                message=(
                    "The Bluesky panel is your primary scan interface. "
                    "Configure and launch scan plans, monitor progress "
                    "in real time, and review results when complete."
                ),
                position=CalloutPosition.RIGHT,
                padding=4,
                target_description="Bluesky sidebar button",
            ),
            TutorialStep(
                target=_find_sidebar_button("lucid.panels.tiled_browser"),
                title="Data Browser (Tiled)",
                message=(
                    "Browse and search past experiment data stored in "
                    "Tiled. Filter by date, plan type, or metadata. "
                    "Open runs for visualization and analysis."
                ),
                position=CalloutPosition.RIGHT,
                padding=4,
                target_description="Tiled Browser sidebar button",
            ),
            TutorialStep(
                target=_find_sidebar_button("lucid.panels.devices"),
                title="Devices",
                message=(
                    "View and control all beamline devices: motors, "
                    "detectors, signals, and more. Devices are organized "
                    "in a tree you can search and filter."
                ),
                position=CalloutPosition.RIGHT,
                padding=4,
                target_description="Devices sidebar button",
            ),
            TutorialStep(
                target=_find_sidebar_button("lucid.panels.claude"),
                title="Claude Assistant",
                message=(
                    "An AI assistant that understands your beamline. "
                    "Ask questions about devices, get help writing scan "
                    "plans, or troubleshoot issues."
                ),
                position=CalloutPosition.RIGHT,
                padding=4,
                target_description="Claude sidebar button",
            ),
            TutorialStep(
                target=_find_sidebar_button("lucid.panels.visualization"),
                title="Visualization",
                message=(
                    "Live and post-hoc data visualization. Plots update "
                    "in real time during scans and support 1D line plots, "
                    "2D images, and more."
                ),
                position=CalloutPosition.RIGHT,
                padding=4,
                target_description="Visualization sidebar button",
            ),
            TutorialStep(
                target=_find_sidebar_button("lucid.panels.synoptic"),
                title="Synoptic",
                message=(
                    "A 2D schematic view of the beamline hardware layout. "
                    "See device status at a glance and click components "
                    "to open their controls."
                ),
                position=CalloutPosition.RIGHT,
                padding=4,
                target_description="Synoptic sidebar button",
            ),
            # ── Logbook panel ──────────────────────────────────────
            TutorialStep(
                target=_find_panel_dock("lucid.panels.logbook"),
                title="Logbook Panel",
                message=(
                    "The Logbook panel shows your current project notebook. "
                    "Write Markdown notes, tag entries, and keep a running "
                    "record of your experiment session."
                ),
                position=CalloutPosition.AUTO,
                target_description="Logbook dock widget",
            ),
            # ── Outro ─────────────────────────────────────────────
            TutorialStep(
                target=None,
                title="You're all set!",
                message=(
                    "That covers the basics. Click any sidebar icon to "
                    "open its panel and start exploring.\n\n"
                    "You can restart this tour anytime from\n"
                    "Help > Welcome Tutorial."
                ),
            ),
        ],
    )


def _find_main_window() -> NCSMainWindow | None:
    """Find the NCSMainWindow instance."""
    app = QApplication.instance()
    if app is None:
        return None
    for widget in app.topLevelWidgets():
        if widget.__class__.__name__ == "NCSMainWindow":
            return widget  # type: ignore[return-value]
    return None


def register_builtin_tutorials() -> None:
    """Register all built-in tutorials with the TutorialManager."""
    manager = TutorialManager.get_instance()
    manager.register(_build_welcome_tutorial())
    logger.debug("Registered built-in tutorials")
