"""TheaterOverlay — fullscreen expansion overlay with dimmed backdrop."""

from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtCore import (
    Property,
    QEasingCurve,
    QEvent,
    QParallelAnimationGroup,
    QPoint,
    QPropertyAnimation,
    QRect,
    Qt,
)
from PySide6.QtGui import QColor, QKeySequence, QPainter, QShortcut
from PySide6.QtWidgets import QToolButton, QWidget

try:
    import qtawesome as qta
except ImportError:  # pragma: no cover
    qta = None

if TYPE_CHECKING:
    from lucid.ui.theater.proxy import TheaterProxy


class TheaterOverlay(QWidget):
    """Overlay that displays an expanded widget with a dimmed backdrop."""

    _MARGIN = 20
    _ANIM_DURATION_BACKDROP = 200
    _ANIM_DURATION_GEOMETRY = 300

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self.setObjectName("TheaterOverlay")

        self._active_proxy: TheaterProxy | None = None
        self._active_widget: QWidget | None = None
        self._backdrop_opacity_value: int = 0
        self._is_animating: bool = False
        self._anim_group: QParallelAnimationGroup | None = None

        # Collapse button
        self._collapse_btn = QToolButton(self)
        self._collapse_btn.setFixedSize(28, 28)
        self._collapse_btn.setCursor(Qt.CursorShape.ArrowCursor)
        self._collapse_btn.setObjectName("TheaterCollapseButton")
        self._collapse_btn.setStyleSheet(
            "QToolButton { background: rgba(0,0,0,160); border: none; "
            "border-radius: 4px; padding: 2px; }"
            "QToolButton:hover { background: rgba(0,0,0,220); }"
        )
        if qta is not None:
            try:
                self._collapse_btn.setIcon(
                    qta.icon("mdi6.arrow-collapse-all", color="#e0e0e0")
                )
            except Exception:
                self._collapse_btn.setText("\u2716")
        else:
            self._collapse_btn.setText("\u2716")
        self._collapse_btn.clicked.connect(self.deactivate)
        self._collapse_btn.setVisible(False)

        # Escape shortcut (works even when child widgets have focus)
        self._escape_shortcut = QShortcut(
            QKeySequence(Qt.Key.Key_Escape), self
        )
        self._escape_shortcut.setContext(
            Qt.ShortcutContext.WidgetWithChildrenShortcut
        )
        self._escape_shortcut.activated.connect(self.deactivate)

        self.setVisible(False)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

        # Track parent resize
        parent.installEventFilter(self)

    # -- backdrop_opacity property for QPropertyAnimation -----------------

    def _get_backdrop_opacity(self) -> int:
        return self._backdrop_opacity_value

    def _set_backdrop_opacity(self, value: int) -> None:
        self._backdrop_opacity_value = value
        self.update()

    backdrop_opacity = Property(int, _get_backdrop_opacity, _set_backdrop_opacity)

    # -- public API -------------------------------------------------------

    def activate(self, proxy: TheaterProxy) -> None:
        """Expand the proxy's widget onto the overlay."""
        if self._is_animating:
            return
        if self._active_proxy is not None:
            self._finish_deactivate()

        self._active_proxy = proxy
        widget = proxy.take_widget()
        self._active_widget = widget

        # Resize overlay to fill parent
        parent = self.parentWidget()
        self.setGeometry(0, 0, parent.width(), parent.height())

        # Capture origin rect (proxy position in overlay coords)
        origin = self.mapFromGlobal(proxy.mapToGlobal(QPoint(0, 0)))
        origin_rect = QRect(origin, proxy.size())
        target_rect = self._expanded_rect()

        # Reparent widget into overlay
        widget.setParent(self)
        widget.setGeometry(origin_rect)
        widget.show()

        # Show overlay
        self._backdrop_opacity_value = 0
        self.setVisible(True)
        self.raise_()
        self.setFocus()
        self._collapse_btn.setVisible(True)
        self._collapse_btn.raise_()
        self._update_collapse_btn_position(target_rect)

        # Animate
        self._is_animating = True
        self._animate_open(origin_rect, target_rect, widget)

    def deactivate(self) -> None:
        """Collapse the widget back to its proxy."""
        if self._is_animating or self._active_proxy is None:
            return

        proxy = self._active_proxy
        widget = self._active_widget

        # Recapture proxy position (may have changed during resize)
        target_origin = self.mapFromGlobal(proxy.mapToGlobal(QPoint(0, 0)))
        target_rect = QRect(target_origin, proxy.size())
        current_rect = widget.geometry()

        self._collapse_btn.setVisible(False)
        self._is_animating = True
        self._animate_close(current_rect, target_rect, widget)

    # -- animation --------------------------------------------------------

    def _animate_open(
        self, origin: QRect, target: QRect, widget: QWidget
    ) -> None:
        backdrop_anim = QPropertyAnimation(self, b"backdrop_opacity")
        backdrop_anim.setDuration(self._ANIM_DURATION_BACKDROP)
        backdrop_anim.setStartValue(0)
        backdrop_anim.setEndValue(150)
        backdrop_anim.setEasingCurve(QEasingCurve.Type.OutCubic)

        geo_anim = QPropertyAnimation(widget, b"geometry")
        geo_anim.setDuration(self._ANIM_DURATION_GEOMETRY)
        geo_anim.setStartValue(origin)
        geo_anim.setEndValue(target)
        geo_anim.setEasingCurve(QEasingCurve.Type.OutCubic)

        self._anim_group = QParallelAnimationGroup(self)
        self._anim_group.addAnimation(backdrop_anim)
        self._anim_group.addAnimation(geo_anim)
        self._anim_group.finished.connect(self._on_activate_finished)
        self._anim_group.start()

    def _animate_close(
        self, current: QRect, target: QRect, widget: QWidget
    ) -> None:
        backdrop_anim = QPropertyAnimation(self, b"backdrop_opacity")
        backdrop_anim.setDuration(self._ANIM_DURATION_BACKDROP)
        backdrop_anim.setStartValue(self._backdrop_opacity_value)
        backdrop_anim.setEndValue(0)
        backdrop_anim.setEasingCurve(QEasingCurve.Type.OutCubic)

        geo_anim = QPropertyAnimation(widget, b"geometry")
        geo_anim.setDuration(self._ANIM_DURATION_GEOMETRY)
        geo_anim.setStartValue(current)
        geo_anim.setEndValue(target)
        geo_anim.setEasingCurve(QEasingCurve.Type.OutCubic)

        self._anim_group = QParallelAnimationGroup(self)
        self._anim_group.addAnimation(backdrop_anim)
        self._anim_group.addAnimation(geo_anim)
        self._anim_group.finished.connect(self._finish_deactivate)
        self._anim_group.start()

    def _on_activate_finished(self) -> None:
        self._is_animating = False

    def _finish_deactivate(self) -> None:
        """Return the widget to its proxy and hide the overlay."""
        self._is_animating = False
        if self._active_proxy is None:
            return
        proxy = self._active_proxy
        widget = self._active_widget
        proxy.return_widget(widget)
        self._active_proxy = None
        self._active_widget = None
        self._collapse_btn.setVisible(False)
        self.setVisible(False)

    # -- geometry helpers -------------------------------------------------

    def _expanded_rect(self) -> QRect:
        return QRect(
            self._MARGIN,
            self._MARGIN,
            self.width() - 2 * self._MARGIN,
            self.height() - 2 * self._MARGIN,
        )

    def _update_collapse_btn_position(
        self, content_rect: QRect | None = None
    ) -> None:
        if content_rect is None and self._active_widget is not None:
            content_rect = self._active_widget.geometry()
        if content_rect is None:
            return
        self._collapse_btn.move(
            content_rect.right() - self._collapse_btn.width() - 8,
            content_rect.top() + 8,
        )

    # -- events -----------------------------------------------------------

    def paintEvent(self, event) -> None:
        if self._backdrop_opacity_value > 0:
            painter = QPainter(self)
            painter.fillRect(
                self.rect(), QColor(0, 0, 0, self._backdrop_opacity_value)
            )
            painter.end()

    def keyPressEvent(self, event) -> None:
        """Fallback Escape handler when overlay itself has focus."""
        if event.key() == Qt.Key.Key_Escape:
            self.deactivate()
        else:
            super().keyPressEvent(event)

    def mousePressEvent(self, event) -> None:
        """Click on backdrop (outside widget) dismisses the overlay."""
        if self._active_widget is not None:
            if not self._active_widget.geometry().contains(event.pos()):
                self.deactivate()
                return
        super().mousePressEvent(event)

    def eventFilter(self, obj, event) -> bool:
        if obj is self.parentWidget() and event.type() == QEvent.Type.Resize:
            new_size = event.size()
            self.setGeometry(0, 0, new_size.width(), new_size.height())
            if self._active_widget is not None and not self._is_animating:
                target_rect = self._expanded_rect()
                self._active_widget.setGeometry(target_rect)
                self._update_collapse_btn_position(target_rect)
        return super().eventFilter(obj, event)
