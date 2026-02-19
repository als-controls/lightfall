"""
Fragment rendering widgets for the logbook entry system.

Provides widgets for displaying and editing individual content fragments
within logbook entries. Fragments are either user-editable text or
readonly system-generated content (plans, device changes, AI responses).
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any

from loguru import logger
from PySide6.QtCore import (
    Property,
    QEasingCurve,
    QPropertyAnimation,
    Qt,
    Signal,
    Slot,
)
from PySide6.QtGui import QCursor, QMouseEvent
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QTextEdit,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from lucid.logbook.style import (
    get_code_background_color,
    is_dark_theme,
)


# ---------------------------------------------------------------------------
# Lightweight data containers (no dependency on models.py)
# ---------------------------------------------------------------------------


class FragmentType(str, Enum):
    """Fragment content type."""

    TEXT = "text"
    READONLY = "readonly"


@dataclass
class FragmentData:
    """Lightweight fragment data container.

    Will be replaced by real model import once models.py lands.
    """

    id: str = field(default_factory=lambda: str(uuid.uuid4())[:12])
    fragment_type: FragmentType = FragmentType.TEXT
    content: str = ""
    subtype: str = ""  # bluesky_plan, device_change, claude_response, ...
    metadata: dict[str, Any] = field(default_factory=dict)
    created_at: datetime = field(default_factory=datetime.now)


# ---------------------------------------------------------------------------
# Style helpers
# ---------------------------------------------------------------------------

# Accent colours per readonly subtype
_SUBTYPE_ACCENTS: dict[str, str] = {
    "bluesky_plan": "#2196f3",      # blue
    "device_change": "#ff9800",     # orange
    "claude_response": "#9c27b0",   # purple
}
_DEFAULT_ACCENT = "#607d8b"  # blue-grey for unknown subtypes


def _accent_for(subtype: str) -> str:
    return _SUBTYPE_ACCENTS.get(subtype, _DEFAULT_ACCENT)


def _card_stylesheet(subtype: str) -> str:
    """Return QSS for a readonly card with left-border accent."""
    accent = _accent_for(subtype)
    bg = "#2a2a2a" if is_dark_theme() else "#f5f5f5"
    text = "#cccccc" if is_dark_theme() else "#333333"
    return (
        f"background-color: {bg}; "
        f"border-left: 4px solid {accent}; "
        f"border-radius: 4px; "
        f"padding: 8px 12px; "
        f"color: {text}; "
        f"font-size: 10pt; "
    )


# ---------------------------------------------------------------------------
# TextFragmentWidget
# ---------------------------------------------------------------------------


class TextFragmentWidget(QFrame):
    """Editable user text fragment.

    Shows rendered markdown preview when not focused.  Switches to an
    editable ``QTextEdit`` when clicked / focused.

    Signals:
        content_changed(fragment_id, new_content): Emitted on every edit.
    """

    content_changed = Signal(str, str)  # (fragment_id, new_content)
    editing_started = Signal(object)  # self (the widget)

    def __init__(
        self,
        fragment: FragmentData,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._fragment = fragment

        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 2, 0, 2)
        layout.setSpacing(0)

        # --- preview label (rendered markdown) ---
        self._preview = QLabel()
        self._preview.setWordWrap(True)
        self._preview.setTextFormat(Qt.TextFormat.RichText)
        self._preview.setStyleSheet("padding: 6px 8px;")
        self._preview.setCursor(QCursor(Qt.CursorShape.IBeamCursor))
        layout.addWidget(self._preview)

        # --- editor (hidden until focused) ---
        self._editor = QTextEdit()
        self._editor.setAcceptRichText(False)
        self._editor.setStyleSheet(
            "QTextEdit { "
            "  font-family: 'Cascadia Code', 'Consolas', monospace; "
            "  font-size: 10pt; "
            "  border: 1px solid palette(highlight); "
            "  border-radius: 4px; "
            "  padding: 4px; "
            "}"
        )
        self._editor.setVisible(False)
        layout.addWidget(self._editor)

        self._render_preview()
        self._editor.textChanged.connect(self._on_text_changed)

    # -- public API --

    @property
    def fragment(self) -> FragmentData:
        return self._fragment

    def get_content(self) -> str:
        return self._fragment.content

    def set_content(self, text: str) -> None:
        self._fragment.content = text
        self._editor.setPlainText(text)
        self._render_preview()

    # -- focus switching --

    def mousePressEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        self._enter_edit_mode()
        super().mousePressEvent(event)

    def _enter_edit_mode(self) -> None:
        if self._editor.isVisible():
            return
        self._editor.setPlainText(self._fragment.content)
        self._preview.setVisible(False)
        self._editor.setVisible(True)
        self._editor.setFocus()
        self.editing_started.emit(self)

    def focusOutEvent(self, event: Any) -> None:  # noqa: N802
        # QFrame doesn't normally get focus, but guard anyway
        self._exit_edit_mode()
        super().focusOutEvent(event)

    def _exit_edit_mode(self) -> None:
        if not self._editor.isVisible():
            return
        self._fragment.content = self._editor.toPlainText()
        self._render_preview()
        self._editor.setVisible(False)
        self._preview.setVisible(True)

    # -- internal --

    def _render_preview(self) -> None:
        """Very lightweight markdown→HTML (bold, italic, code)."""
        import re

        text = self._fragment.content
        if not text:
            self._preview.setStyleSheet("padding: 6px 8px; color: #888; font-style: italic;")
            self._preview.setText("Click to add a note\u2026")
            return

        self._preview.setStyleSheet("padding: 6px 8px;")
        # Minimal rendering: convert **bold**, *italic*, `code`
        html = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        html = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", html)
        html = re.sub(r"\*(.+?)\*", r"<i>\1</i>", html)
        html = re.sub(r"`(.+?)`", r"<code>\1</code>", html)
        html = html.replace("\n", "<br>")
        self._preview.setText(html)

    @Slot()
    def _on_text_changed(self) -> None:
        self._fragment.content = self._editor.toPlainText()
        self.content_changed.emit(self._fragment.id, self._fragment.content)

    # Allow the frame to lose focus when clicking elsewhere so we can
    # switch back to preview.  We watch the editor's focusOut instead.
    def showEvent(self, event: Any) -> None:  # noqa: N802
        self._editor.installEventFilter(self)
        super().showEvent(event)

    def eventFilter(self, obj: Any, event: Any) -> bool:  # noqa: N802
        from PySide6.QtCore import QEvent

        if obj is self._editor and event.type() == QEvent.Type.FocusOut:
            self._exit_edit_mode()
        return super().eventFilter(obj, event)


# ---------------------------------------------------------------------------
# ReadonlyFragmentWidget
# ---------------------------------------------------------------------------


class ReadonlyFragmentWidget(QFrame):
    """Non-editable system fragment displayed as a styled card.

    Renders differently depending on ``subtype``:
    * ``bluesky_plan`` – plan name, params summary, UID
    * ``device_change`` – device name, old→new value
    * ``claude_response`` – distinct styled text block
    * anything else – raw JSON

    Signals:
        clicked(fragment_id): Emitted on click for expand / details.
    """

    clicked = Signal(str)  # fragment_id

    def __init__(
        self,
        fragment: FragmentData,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._fragment = fragment

        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setStyleSheet(_card_stylesheet(fragment.subtype))
        self.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(2)

        self._label = QLabel()
        self._label.setWordWrap(True)
        self._label.setTextFormat(Qt.TextFormat.RichText)
        layout.addWidget(self._label)

        self._render()

    @property
    def fragment(self) -> FragmentData:
        return self._fragment

    # -- rendering per subtype --

    def _render(self) -> None:
        subtype = self._fragment.subtype
        meta = self._fragment.metadata
        if subtype == "bluesky_plan":
            self._render_plan(meta)
        elif subtype == "device_change":
            self._render_device_change(meta)
        elif subtype == "claude_response":
            self._render_claude(self._fragment.content)
        else:
            self._render_json(meta)

    def _render_plan(self, meta: dict[str, Any]) -> None:
        plan = meta.get("plan_name", "unknown")
        uid = meta.get("uid", "")[:8]
        params = meta.get("params", {})
        param_str = ", ".join(f"{k}={v}" for k, v in list(params.items())[:4])
        if len(params) > 4:
            param_str += ", …"
        html = (
            f"<b>📋 Plan:</b> <code>{plan}</code>"
            f"<br><small>{param_str}</small>"
        )
        if uid:
            html += f"<br><small style='color:#888'>UID {uid}</small>"
        self._label.setText(html)

    def _render_device_change(self, meta: dict[str, Any]) -> None:
        device = meta.get("device_name", "?")
        old = meta.get("old_value", "—")
        new = meta.get("new_value", "—")
        unit = meta.get("unit", "")
        arrow = "→"
        self._label.setText(
            f"<b>⚙ {device}</b>  {old} {arrow} {new}"
            + (f" {unit}" if unit else "")
        )

    def _render_claude(self, content: str) -> None:
        text = content[:500] + ("…" if len(content) > 500 else "")
        text_escaped = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        self._label.setText(
            f"<b>🤖 Claude:</b><br>"
            f"<span style='white-space: pre-wrap'>{text_escaped}</span>"
        )

    def _render_json(self, meta: dict[str, Any]) -> None:
        raw = json.dumps(meta, indent=2, default=str)[:600]
        code_bg = get_code_background_color()
        self._label.setText(
            f"<pre style='background:{code_bg}; padding:6px; "
            f"border-radius:4px; font-size:9pt'>{raw}</pre>"
        )

    # -- interaction --

    def mousePressEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        self.clicked.emit(self._fragment.id)
        super().mousePressEvent(event)


# ---------------------------------------------------------------------------
# CollapsibleGroup
# ---------------------------------------------------------------------------


class CollapseMode(str, Enum):
    """How consecutive readonly fragments are grouped."""

    SAME_TYPE = "same_type"
    ALL_READONLY = "all_readonly"


class CollapsibleGroup(QFrame):
    """Groups consecutive readonly fragments with expand/collapse animation.

    Two grouping modes:
    * **same_type** – only groups fragments that share the same subtype.
    * **all_readonly** – groups all consecutive readonly fragments together.

    Signals:
        mode_toggled(new_mode): Emitted when the user switches collapse mode.
    """

    mode_toggled = Signal(str)  # CollapseMode value

    # Animation duration in ms
    _ANIM_DURATION = 250

    def __init__(
        self,
        fragments: list[FragmentData],
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._fragments = fragments
        self._collapse_mode = CollapseMode.ALL_READONLY
        self._collapsed = True

        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        # --- header row ---
        header = QWidget()
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(0, 2, 0, 2)
        header_layout.setSpacing(4)

        self._toggle_btn = QToolButton()
        self._toggle_btn.setArrowType(Qt.ArrowType.RightArrow)
        self._toggle_btn.setAutoRaise(True)
        self._toggle_btn.setToolTip("Expand / collapse")
        self._toggle_btn.clicked.connect(self._toggle_collapsed)
        header_layout.addWidget(self._toggle_btn)

        self._header_label = QLabel()
        self._header_label.setStyleSheet("font-size: 9pt; color: #888;")
        header_layout.addWidget(self._header_label, 1)

        self._mode_btn = QPushButton()
        self._mode_btn.setFlat(True)
        self._mode_btn.setToolTip("Switch grouping mode")
        self._mode_btn.setStyleSheet("font-size: 8pt; color: #888; padding: 2px 6px;")
        self._mode_btn.clicked.connect(self._switch_mode)
        header_layout.addWidget(self._mode_btn)

        outer.addWidget(header)

        # --- content container (animated) ---
        self._content_widget = QWidget()
        self._content_layout = QVBoxLayout(self._content_widget)
        self._content_layout.setContentsMargins(0, 0, 0, 0)
        self._content_layout.setSpacing(2)
        outer.addWidget(self._content_widget)

        # Populate
        self._fragment_widgets: list[ReadonlyFragmentWidget] = []
        self._rebuild_widgets()
        self._update_header()
        self._apply_collapsed_state(animate=False)

        # Animation
        self._animation = QPropertyAnimation(self._content_widget, b"maximumHeight")
        self._animation.setEasingCurve(QEasingCurve.Type.InOutQuad)
        self._animation.setDuration(self._ANIM_DURATION)

    # -- public API --

    @property
    def fragments(self) -> list[FragmentData]:
        return list(self._fragments)

    @property
    def collapse_mode(self) -> CollapseMode:
        return self._collapse_mode

    @collapse_mode.setter
    def collapse_mode(self, mode: CollapseMode) -> None:
        if mode != self._collapse_mode:
            self._collapse_mode = mode
            self._update_header()

    @property
    def is_collapsed(self) -> bool:
        return self._collapsed

    # -- internal --

    def _rebuild_widgets(self) -> None:
        # Clear
        for w in self._fragment_widgets:
            self._content_layout.removeWidget(w)
            w.deleteLater()
        self._fragment_widgets.clear()

        for frag in self._fragments:
            w = ReadonlyFragmentWidget(frag)
            self._content_layout.addWidget(w)
            self._fragment_widgets.append(w)

    def _update_header(self) -> None:
        n = len(self._fragments)
        subtypes = {f.subtype for f in self._fragments}
        types_str = ", ".join(sorted(subtypes)) if subtypes else "items"
        self._header_label.setText(f"{n} {types_str}")
        mode_label = "by type" if self._collapse_mode == CollapseMode.SAME_TYPE else "all"
        self._mode_btn.setText(f"[{mode_label}]")

    @Slot()
    def _toggle_collapsed(self) -> None:
        self._collapsed = not self._collapsed
        self._apply_collapsed_state(animate=True)

    def _apply_collapsed_state(self, *, animate: bool) -> None:
        arrow = Qt.ArrowType.RightArrow if self._collapsed else Qt.ArrowType.DownArrow
        self._toggle_btn.setArrowType(arrow)

        if not animate:
            self._content_widget.setVisible(not self._collapsed)
            if self._collapsed:
                self._content_widget.setMaximumHeight(0)
            else:
                self._content_widget.setMaximumHeight(16777215)
            return

        # Animated expand / collapse
        content_height = self._content_widget.sizeHint().height()
        if self._collapsed:
            self._animation.setStartValue(content_height)
            self._animation.setEndValue(0)
            self._animation.finished.connect(self._hide_after_collapse)
        else:
            self._content_widget.setVisible(True)
            self._content_widget.setMaximumHeight(0)
            self._animation.setStartValue(0)
            self._animation.setEndValue(content_height)
            try:
                self._animation.finished.disconnect(self._hide_after_collapse)
            except RuntimeError:
                pass
        self._animation.start()

    @Slot()
    def _hide_after_collapse(self) -> None:
        if self._collapsed:
            self._content_widget.setVisible(False)

    @Slot()
    def _switch_mode(self) -> None:
        if self._collapse_mode == CollapseMode.ALL_READONLY:
            self._collapse_mode = CollapseMode.SAME_TYPE
        else:
            self._collapse_mode = CollapseMode.ALL_READONLY
        self._update_header()
        self.mode_toggled.emit(self._collapse_mode.value)
        logger.debug(f"CollapsibleGroup mode switched to {self._collapse_mode.value}")
