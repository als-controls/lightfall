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
from enum import StrEnum
from typing import Any

from PySide6.QtCore import (
    QEasingCurve,
    QMimeData,
    QPropertyAnimation,
    Qt,
    Signal,
    Slot,
)
from PySide6.QtGui import QCursor, QDrag, QEnterEvent, QMouseEvent, QPixmap
from PySide6.QtWidgets import (
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QScrollArea,
    QSizePolicy,
    QTextEdit,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from lightfall.logbook.style import (
    get_code_background_color,
    is_dark_theme,
)
from lightfall.logbook.style import (
    scaled_pt as _spt,
)

# ---------------------------------------------------------------------------
# Lightweight data containers (no dependency on models.py)
# ---------------------------------------------------------------------------


class FragmentType(StrEnum):
    """Fragment content type."""

    TEXT = "text"
    READONLY = "readonly"
    IMAGE = "image"


@dataclass
class FragmentData:
    """Lightweight fragment data container.

    Will be replaced by real model import once models.py lands.
    """

    id: str = field(default_factory=lambda: str(uuid.uuid4()))
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
        f"  font-size: {_spt(10)}pt; "
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

    edit_clicked = Signal(str)
    copy_clicked = Signal(str)
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
            edit_icon = qta.icon("mdi.pencil-outline", color="#2196F3")
            copy_icon = qta.icon("mdi.content-copy", color="#9E9E9E")
            claude_icon = qta.icon("mdi.robot-outline", color="#9c27b0")
            delete_icon = qta.icon("mdi.delete-outline", color="#f44336")
        except ImportError:
            edit_icon = None
            copy_icon = None
            claude_icon = None
            delete_icon = None

        self._edit_btn = QToolButton()
        if edit_icon:
            self._edit_btn.setIcon(edit_icon)
        else:
            self._edit_btn.setText("Edit")
        self._edit_btn.setToolTip("Edit fragment")
        self._edit_btn.setStyleSheet(btn_style)
        self._edit_btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self._edit_btn.clicked.connect(lambda: self.edit_clicked.emit(self._fragment_id))
        layout.addWidget(self._edit_btn)

        self._copy_btn = QToolButton()
        if copy_icon:
            self._copy_btn.setIcon(copy_icon)
        else:
            self._copy_btn.setText("Copy")
        self._copy_btn.setToolTip("Copy content")
        self._copy_btn.setStyleSheet(btn_style)
        self._copy_btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self._copy_btn.clicked.connect(lambda: self.copy_clicked.emit(self._fragment_id))
        layout.addWidget(self._copy_btn)

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
        self._overlay.edit_clicked.connect(self._on_overlay_edit)
        self._overlay.copy_clicked.connect(self._on_overlay_copy)
        self._overlay.delete_clicked.connect(self._on_overlay_delete)
        self._overlay.claude_clicked.connect(self._on_overlay_claude)

    # Signals that subclasses should declare
    delete_requested: Signal  # (fragment_id)
    claude_requested: Signal  # (fragment_id)

    def _on_overlay_edit(self, fid: str) -> None:
        """Handle edit button — enter edit mode if available."""
        if hasattr(self, '_enter_edit_mode'):
            self._enter_edit_mode()

    def _on_overlay_copy(self, fid: str) -> None:
        """Copy fragment content to clipboard."""
        from PySide6.QtWidgets import QApplication

        content = ""
        fragment = getattr(self, '_fragment', None)
        if fragment and hasattr(fragment, 'content'):
            content = fragment.content
        elif hasattr(self, 'get_content'):
            content = self.get_content()

        if content:
            clipboard = QApplication.clipboard()
            clipboard.setText(content)

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

    def mouseMoveEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        """Initiate drag when mouse moves with left button held."""
        if event.buttons() & Qt.MouseButton.LeftButton:
            frag = getattr(self, '_fragment', None)
            if frag is None:
                super().mouseMoveEvent(event)  # type: ignore[misc]
                return
            drag = QDrag(self)  # type: ignore[arg-type]
            mime = QMimeData()
            mime.setData("application/x-logbook-fragment-id", frag.id.encode())
            drag.setMimeData(mime)
            drag.exec(Qt.DropAction.MoveAction)
        else:
            super().mouseMoveEvent(event)  # type: ignore[misc]


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
            f"  font-size: {_spt(10)}pt; "
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

    def mouseDoubleClickEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        self._enter_edit_mode()
        super().mouseDoubleClickEvent(event)

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
        exit_status = meta.get("exit_status")
        num_events = meta.get("num_events", {})

        param_str = ", ".join(f"{k}={v}" for k, v in list(params.items())[:4])
        if len(params) > 4:
            param_str += ", …"
        html = (
            f"<b>📋 Plan:</b> <code>{plan}</code>"
            f"<br><small>{param_str}</small>"
        )
        if exit_status:
            status_color = "#4CAF50" if exit_status == "success" else "#F44336"
            total_events = sum(num_events.values()) if num_events else 0
            html += (
                f"<br><small style='color:{status_color}'>"
                f"Status: {exit_status}"
            )
            if total_events:
                html += f" · {total_events} event{'s' if total_events != 1 else ''}"
            html += "</small>"
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
        rendered = _render_markdown(content)
        self._label.setText(f"<b>🤖 Claude:</b><br>{rendered}")

    def _render_json(self, meta: dict[str, Any]) -> None:
        raw = json.dumps(meta, indent=2, default=str)[:600]
        code_bg = get_code_background_color()
        self._label.setText(
            f"<pre style='background:{code_bg}; padding:6px; "
            f"border-radius:4px; font-size:{_spt(9)}pt'>{raw}</pre>"
        )

    # -- interaction --

    def mousePressEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        self.clicked.emit(self._fragment.id)
        super().mousePressEvent(event)


# ---------------------------------------------------------------------------
# ImageFragmentWidget — thumbnail + caption + full-size viewer
# ---------------------------------------------------------------------------


class ImageFragmentWidget(_HoverMixin, QFrame):
    """Displays an image fragment with thumbnail, caption, and click-to-expand.

    Signals:
        delete_requested(fragment_id): Delete button pressed.
        caption_changed(fragment_id, new_caption): Caption edited.
        claude_requested(fragment_id): Claude button pressed.
    """

    delete_requested = Signal(str)
    caption_changed = Signal(str, str)
    claude_requested = Signal(str)

    THUMBNAIL_MAX_WIDTH = 400

    def __init__(self, data: FragmentData, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._data = data
        self._image_id = data.metadata.get("image_id", "")
        self._full_pixmap: QPixmap | None = None
        self._setup_ui()
        self._load_image()

    def _setup_ui(self) -> None:
        self.setObjectName("imageFragment")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 8, 12, 8)

        # Card styling with accent bar
        accent = _accent_for(self._data.subtype)
        bg = "#2a2a2a" if is_dark_theme() else "#f5f5f5"
        text_color = "#cccccc" if is_dark_theme() else "#333333"
        self.setStyleSheet(
            f"ImageFragmentWidget {{ "
            f"  background-color: {bg}; "
            f"  border: 1px solid {bg}; "
            f"  border-left: 4px solid {accent}; "
            f"  border-radius: 4px; "
            f"  padding: 8px 12px; "
            f"  color: {text_color}; "
            f"}}"
        )

        # Thumbnail label
        self._thumbnail = QLabel()
        self._thumbnail.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._thumbnail.setCursor(Qt.CursorShape.PointingHandCursor)
        self._thumbnail.mousePressEvent = self._on_thumbnail_clicked
        layout.addWidget(self._thumbnail)

        # Placeholder for syncing state
        self._placeholder = QLabel("Image syncing...")
        self._placeholder.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._placeholder.setStyleSheet("color: #888; padding: 20px;")
        self._placeholder.setVisible(False)
        layout.addWidget(self._placeholder)

        # Caption (editable)
        self._caption = QLineEdit(self._data.content)
        self._caption.setPlaceholderText("Add a caption...")
        self._caption.setStyleSheet(
            "QLineEdit { background: transparent; border: none; "
            f"color: {text_color}; font-style: italic; }}"
        )
        self._caption.editingFinished.connect(self._on_caption_edited)
        layout.addWidget(self._caption)

        # Hover overlay
        self._overlay = FragmentOverlay(self._data.id, self)
        self._overlay.delete_clicked.connect(lambda: self.delete_requested.emit(self._data.id))
        self._overlay.claude_clicked.connect(lambda: self.claude_requested.emit(self._data.id))

    def _load_image(self) -> None:
        """Load image from local storage."""
        from lightfall.logbook.client import LogbookClient

        client = LogbookClient.get_instance()
        path = client._get_local_image_path(self._image_id)

        if path is None:
            self._thumbnail.setVisible(False)
            self._placeholder.setVisible(True)
            return

        pixmap = QPixmap(str(path))
        if pixmap.isNull():
            self._thumbnail.setText("Failed to load image")
            return

        self._full_pixmap = pixmap
        scaled = pixmap.scaledToWidth(
            min(self.THUMBNAIL_MAX_WIDTH, pixmap.width()),
            Qt.TransformationMode.SmoothTransformation,
        )
        self._thumbnail.setPixmap(scaled)
        self._thumbnail.setVisible(True)
        self._placeholder.setVisible(False)

    def _on_thumbnail_clicked(self, event) -> None:
        """Open full-size image in a dialog."""
        if self._full_pixmap is None:
            return
        dialog = QDialog(self)
        dialog.setWindowTitle(self._data.content or "Image")
        dialog.setMinimumSize(400, 300)
        dlg_layout = QVBoxLayout(dialog)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        label = QLabel()
        label.setPixmap(self._full_pixmap)
        label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        scroll.setWidget(label)
        dlg_layout.addWidget(scroll)

        dialog.resize(
            min(self._full_pixmap.width() + 40, 1200),
            min(self._full_pixmap.height() + 40, 800),
        )
        dialog.exec()

    def _on_caption_edited(self) -> None:
        new_caption = self._caption.text()
        if new_caption != self._data.content:
            self._data.content = new_caption
            self.caption_changed.emit(self._data.id, new_caption)

    def refresh_image(self) -> None:
        """Reload the image (e.g., after sync downloads it)."""
        self._load_image()

    @property
    def fragment_data(self) -> FragmentData:
        return self._data


# ---------------------------------------------------------------------------
# CollapsibleGroup
# ---------------------------------------------------------------------------


class CollapseMode(StrEnum):
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
        self._header_label.setStyleSheet(f"font-size: {_spt(9)}pt; color: #888;")
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
        self._hide_connected = False  # Track finished→_hide_after_collapse connection

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
            if not self._hide_connected:
                self._animation.finished.connect(self._hide_after_collapse)
                self._hide_connected = True
        else:
            self._content_widget.setVisible(True)
            self._content_widget.setMaximumHeight(0)
            self._animation.setStartValue(0)
            self._animation.setEndValue(content_height)
            if self._hide_connected:
                self._animation.finished.disconnect(self._hide_after_collapse)
                self._hide_connected = False
        self._animation.start()

    @Slot()
    def _hide_after_collapse(self) -> None:
        if self._collapsed:
            self._content_widget.setVisible(False)

    pass
