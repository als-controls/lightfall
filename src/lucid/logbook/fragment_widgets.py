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
from PySide6.QtGui import QCursor, QEnterEvent, QMouseEvent
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


def _highlight_code(code: str, info: str | None = None) -> str:
    """Syntax-highlight a code block using Pygments."""
    try:
        from pygments import highlight
        from pygments.formatters import HtmlFormatter
        from pygments.lexers import get_lexer_by_name, guess_lexer
        from pygments.util import ClassNotFound

        lang = info or ""
        try:
            lexer = get_lexer_by_name(lang) if lang else guess_lexer(code)
        except ClassNotFound:
            lexer = get_lexer_by_name("text")

        # Use inline styles (QLabel doesn't support <style> blocks)
        style = "monokai" if is_dark_theme() else "default"
        formatter = HtmlFormatter(noclasses=True, nowrap=False, style=style)
        return highlight(code, lexer, formatter)
    except ImportError:
        # Pygments not available — plain code block
        escaped = code.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        return f"<pre><code>{escaped}</code></pre>"


# Module-level markdown renderer (created once)
_md_renderer = None


def _get_md_renderer():
    global _md_renderer
    if _md_renderer is None:
        import mistune
        _md_renderer = mistune.create_markdown(
            plugins=["task_lists", "table", "strikethrough", "footnotes",
                     "superscript", "subscript", "abbr", "def_list"],
        )
        # Override code block rendering with Pygments highlighting
        _md_renderer.renderer.block_code = _highlight_code
    return _md_renderer


def _render_markdown(text: str) -> str:
    """Render markdown to HTML with syntax highlighting and QLabel fixups."""
    md = _get_md_renderer()
    html = md(text)
    # QLabel can't render <input> checkboxes — replace with Unicode
    html = html.replace(
        '<input class="task-list-item-checkbox" type="checkbox" disabled checked/>', "☑"
    ).replace(
        '<input class="task-list-item-checkbox" type="checkbox" disabled/>', "☐"
    )
    # Remove bullet from task-list items (class="task-list-item")
    html = html.replace(
        '<ul class="task-list">', '<ul style="list-style-type: none; padding-left: 0;">'
    )
    return html


def _accent_for(subtype: str) -> str:
    return _SUBTYPE_ACCENTS.get(subtype, _DEFAULT_ACCENT)


def _card_stylesheet(subtype: str) -> str:
    """Return QSS for a readonly card with left-border accent."""
    accent = _accent_for(subtype)
    bg = "#2a2a2a" if is_dark_theme() else "#f5f5f5"
    text = "#cccccc" if is_dark_theme() else "#333333"
    return (
        f"ReadonlyFragmentWidget {{ "
        f"  background-color: {bg}; "
        f"  border: 1px solid {bg}; "
        f"  border-left: 4px solid {accent}; "
        f"  border-radius: 4px; "
        f"  padding: 8px 12px; "
        f"  color: {text}; "
        f"  font-size: 10pt; "
        f"}}"
    )


# ---------------------------------------------------------------------------
# FragmentOverlay — hover buttons for delete / Claude
# ---------------------------------------------------------------------------


class FragmentOverlay(QWidget):
    """Floating button bar shown on hover over a fragment widget.

    Positions itself in the top-right corner of its parent fragment.

    Signals:
        delete_clicked(fragment_id): Delete button pressed.
        claude_clicked(fragment_id): Claude button pressed.
    """

    delete_clicked = Signal(str)
    claude_clicked = Signal(str)

    def __init__(self, fragment_id: str, parent: QWidget) -> None:
        super().__init__(parent)
        self._fragment_id = fragment_id
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, False)
        self.setVisible(False)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(2, 2, 2, 2)
        layout.setSpacing(2)

        btn_style = (
            "QToolButton { "
            "  border: none; border-radius: 3px; padding: 2px 6px; "
            "} "
            "QToolButton:hover { background: rgba(255,255,255,0.15); }"
        )

        # Use qtawesome icons
        try:
            import qtawesome as qta
            claude_icon = qta.icon("mdi.robot-outline", color="#9c27b0")
            delete_icon = qta.icon("mdi.delete-outline", color="#f44336")
        except ImportError:
            claude_icon = None
            delete_icon = None

        self._claude_btn = QToolButton()
        if claude_icon:
            self._claude_btn.setIcon(claude_icon)
        else:
            self._claude_btn.setText("AI")
        self._claude_btn.setToolTip("Ask Claude about this")
        self._claude_btn.setStyleSheet(btn_style)
        self._claude_btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self._claude_btn.clicked.connect(lambda: self.claude_clicked.emit(self._fragment_id))
        layout.addWidget(self._claude_btn)

        self._delete_btn = QToolButton()
        if delete_icon:
            self._delete_btn.setIcon(delete_icon)
        else:
            self._delete_btn.setText("Del")
        self._delete_btn.setToolTip("Delete fragment")
        self._delete_btn.setStyleSheet(btn_style)
        self._delete_btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self._delete_btn.clicked.connect(lambda: self.delete_clicked.emit(self._fragment_id))
        layout.addWidget(self._delete_btn)

        self.adjustSize()

    def reposition(self) -> None:
        """Move to top-right of parent."""
        p = self.parentWidget()
        if p:
            x = p.width() - self.sizeHint().width() - 4
            self.move(x, 4)


class _HoverMixin:
    """Mixin that shows/hides a FragmentOverlay on enter/leave.

    Subclass must call ``_init_overlay(fragment_id)`` after layout setup.
    """

    _overlay: FragmentOverlay | None

    def _init_overlay(self, fragment_id: str) -> None:
        self._overlay = FragmentOverlay(fragment_id, self)  # type: ignore[arg-type]
        self._overlay.delete_clicked.connect(self._on_overlay_delete)
        self._overlay.claude_clicked.connect(self._on_overlay_claude)

    # Signals that subclasses should declare
    delete_requested: Signal  # (fragment_id)
    claude_requested: Signal  # (fragment_id)

    def _on_overlay_delete(self, fid: str) -> None:
        self.delete_requested.emit(fid)  # type: ignore[attr-defined]

    def _on_overlay_claude(self, fid: str) -> None:
        self.claude_requested.emit(fid)  # type: ignore[attr-defined]

    def enterEvent(self, event: QEnterEvent) -> None:  # noqa: N802
        if self._overlay and not getattr(self, '_editor_visible', False):
            self._overlay.reposition()
            self._overlay.setVisible(True)
        super().enterEvent(event)  # type: ignore[misc]

    def leaveEvent(self, event: Any) -> None:  # noqa: N802
        if self._overlay:
            self._overlay.setVisible(False)
        super().leaveEvent(event)  # type: ignore[misc]

    def resizeEvent(self, event: Any) -> None:  # noqa: N802
        if self._overlay and self._overlay.isVisible():
            self._overlay.reposition()
        super().resizeEvent(event)  # type: ignore[misc]


# ---------------------------------------------------------------------------
# TextFragmentWidget
# ---------------------------------------------------------------------------


class TextFragmentWidget(_HoverMixin, QFrame):
    """Editable user text fragment.

    Shows rendered markdown preview when not focused.  Switches to an
    editable ``QTextEdit`` when clicked / focused.

    Signals:
        content_changed(fragment_id, new_content): Emitted on every edit.
        delete_requested(fragment_id): Delete button on overlay.
        claude_requested(fragment_id): Claude button on overlay.
    """

    content_changed = Signal(str, str)  # (fragment_id, new_content)
    editing_started = Signal(object)  # self (the widget)
    delete_requested = Signal(str)
    claude_requested = Signal(str)

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
        self._editor_visible = False
        layout.addWidget(self._editor)

        self._render_preview()
        self._editor.textChanged.connect(self._on_text_changed)

        # Hover overlay (must be after layout setup)
        self._init_overlay(fragment.id)

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
        self._editor_visible = True
        if self._overlay:
            self._overlay.setVisible(False)
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
        self._editor_visible = False
        self._fragment.content = self._editor.toPlainText()
        self._render_preview()
        self._editor.setVisible(False)
        self._preview.setVisible(True)

    # -- internal --

    def _render_preview(self) -> None:
        """Render markdown content as HTML preview using mistune."""
        text = self._fragment.content
        if not text:
            self._preview.setStyleSheet("padding: 6px 8px; color: #888; font-style: italic;")
            self._preview.setText("Click to add a note\u2026")
            return

        self._preview.setStyleSheet("padding: 6px 8px;")
        html = _render_markdown(text)
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


class ReadonlyFragmentWidget(_HoverMixin, QFrame):
    """Non-editable system fragment displayed as a styled card.

    Renders differently depending on ``subtype``:
    * ``bluesky_plan`` – plan name, params summary, UID
    * ``device_change`` – device name, old→new value
    * ``claude_response`` – distinct styled text block
    * anything else – raw JSON

    Signals:
        clicked(fragment_id): Emitted on click for expand / details.
        delete_requested(fragment_id): Delete button on overlay.
        claude_requested(fragment_id): Claude button on overlay.
    """

    clicked = Signal(str)  # fragment_id
    delete_requested = Signal(str)
    claude_requested = Signal(str)

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

        # Hover overlay
        self._init_overlay(fragment.id)

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

    pass
