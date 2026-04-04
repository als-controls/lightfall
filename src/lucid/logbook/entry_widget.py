"""
Entry-level widgets for the fragment-based logbook.

Provides:
* **EntryWidget** – displays a single logbook entry as a vertical list of
  fragment widgets, with automatic grouping of consecutive readonly fragments.
* **EntryListWidget** – sidebar listing all entries with selection, sorting,
  and creation controls.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from loguru import logger
from PySide6.QtCore import (
    QAbstractListModel,
    QModelIndex,
    QRect,
    QSize,
    QSortFilterProxyModel,
    Qt,
    Signal,
    Slot,
)
from PySide6.QtGui import (
    QBrush,
    QColor,
    QCursor,
    QDragEnterEvent,
    QDragMoveEvent,
    QDropEvent,
    QFont,
    QFontMetrics,
    QMouseEvent,
    QPainter,
    QPen,
)
from PySide6.QtWidgets import (
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListView,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QStyledItemDelegate,
    QStyleOptionViewItem,
    QToolBar,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from lucid.logbook.fragment_widgets import (
    CollapseMode,
    CollapsibleGroup,
    FragmentData,
    FragmentType,
    ReadonlyFragmentWidget,
    TextFragmentWidget,
)
from lucid.logbook.style import is_dark_theme


# ---------------------------------------------------------------------------
# Lightweight entry data container
# ---------------------------------------------------------------------------


@dataclass
class EntryData:
    """Minimal logbook entry container (will be replaced by real model)."""

    id: str = field(default_factory=lambda: str(uuid.uuid4())[:12])
    title: str = ""
    fragments: list[FragmentData] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    created_at: datetime = field(default_factory=datetime.now)
    updated_at: datetime = field(default_factory=datetime.now)


# ---------------------------------------------------------------------------
# Tag chip helper
# ---------------------------------------------------------------------------


class _TagChip(QFrame):
    """Small coloured chip with optional remove button."""

    removed = Signal(str)  # tag text

    def __init__(self, tag: str, *, removable: bool = True, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._tag = tag
        bg = "#3a3a5c" if is_dark_theme() else "#e0e0f0"
        fg = "#c0c0e0" if is_dark_theme() else "#333366"
        self.setStyleSheet(
            f"_TagChip {{ background: {bg}; color: {fg}; border-radius: 6px; "
            f"padding: 1px 4px; font-size: 8pt; }}"
        )
        self.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(4, 0, 2, 0)
        layout.setSpacing(2)

        lbl = QLabel(tag)
        lbl.setStyleSheet(f"color: {fg}; font-size: 8pt; background: transparent;")
        layout.addWidget(lbl)

        if removable:
            close_btn = QToolButton()
            close_btn.setText("✕")
            close_btn.setStyleSheet(
                f"QToolButton {{ border: none; color: {fg}; font-size: 7pt; padding: 0 2px; background: transparent; }} "
                f"QToolButton:hover {{ color: #f44336; }}"
            )
            close_btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
            close_btn.clicked.connect(lambda: self.removed.emit(self._tag))
            layout.addWidget(close_btn)


# _tag_chip removed — delegate paints chips directly


# ---------------------------------------------------------------------------
# EntryWidget
# ---------------------------------------------------------------------------


class EntryWidget(QFrame):
    """Displays one logbook entry as a vertical list of fragments.

    Automatically groups consecutive readonly fragments into
    :class:`CollapsibleGroup` widgets.

    Signals:
        fragment_added(entry_id, fragment_id): New text fragment created.
        fragment_changed(entry_id, fragment_id, content): Fragment edited.
        fragment_deleted(entry_id, fragment_id): Fragment removed.
    """

    fragment_added = Signal(str, str)
    fragment_changed = Signal(str, str, str)
    fragment_deleted = Signal(str, str)
    fragment_reordered = Signal(str, list)  # (entry_id, [fragment_ids])
    title_changed = Signal(str, str)  # (entry_id, new_title)
    tags_changed = Signal(str, list)  # (entry_id, new_tags)
    claude_requested = Signal(str, str)  # (entry_id, fragment_id)

    def __init__(
        self,
        entry: EntryData,
        collapse_mode: CollapseMode = CollapseMode.ALL_READONLY,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._entry = entry
        self._collapse_mode = collapse_mode

        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

        root = QVBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 8)
        root.setSpacing(6)

        # --- header ---
        self._header = self._build_header()
        root.addWidget(self._header)

        # --- scrollable fragment area ---
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)

        self._fragment_container = QWidget()
        self._fragment_container.setAcceptDrops(True)
        self._fragment_container.dragEnterEvent = self._frag_drag_enter
        self._fragment_container.dragMoveEvent = self._frag_drag_move
        self._fragment_container.dropEvent = self._frag_drop
        self._fragment_layout = QVBoxLayout(self._fragment_container)
        self._fragment_layout.setContentsMargins(0, 0, 0, 0)
        self._fragment_layout.setSpacing(4)

        # --- add-note button (inside fragment list, acts like a ghost fragment) ---
        self._add_btn = QPushButton("＋ Add note")
        self._add_btn.setFlat(True)
        self._add_btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self._add_btn.setStyleSheet(
            "color: #888; font-size: 9pt; padding: 8px 8px; text-align: left; "
            "border: 1px dashed #555; border-radius: 4px;"
        )
        self._add_btn.clicked.connect(self._add_text_fragment)
        self._fragment_layout.addWidget(self._add_btn)

        self._fragment_layout.addStretch()

        scroll.setWidget(self._fragment_container)
        root.addWidget(scroll, 1)

        # --- bottom toolbar ---
        self._toolbar = self._build_toolbar()
        root.addWidget(self._toolbar)

        # Build fragment widgets
        self._widget_items: list[QWidget] = []
        self._rebuild_fragments()

    # -- public --

    @property
    def entry(self) -> EntryData:
        return self._entry

    def set_entry(self, entry: EntryData) -> None:
        """Replace the displayed entry."""
        self._entry = entry
        self._update_header()
        self._rebuild_fragments()

    def set_collapse_mode(self, mode: CollapseMode) -> None:
        if mode != self._collapse_mode:
            self._collapse_mode = mode
            self._rebuild_fragments()

    # -- header --

    def _build_header(self) -> QWidget:
        header = QWidget()
        layout = QVBoxLayout(header)
        layout.setContentsMargins(0, 0, 0, 4)
        layout.setSpacing(2)

        # Title / date row
        top = QHBoxLayout()
        self._title_edit = QLineEdit()
        self._title_edit.setPlaceholderText("Untitled entry")
        self._title_edit.setStyleSheet(
            "font-size: 12pt; font-weight: bold; border: none; "
            "background: transparent; padding: 2px 0;"
        )
        self._title_edit.editingFinished.connect(self._on_title_edited)
        top.addWidget(self._title_edit, 1)

        self._date_label = QLabel()
        self._date_label.setStyleSheet("font-size: 8pt; color: #888;")
        top.addWidget(self._date_label)
        layout.addLayout(top)

        # Tags row
        self._tags_layout = QHBoxLayout()
        self._tags_layout.setContentsMargins(0, 0, 0, 0)
        self._tags_layout.setSpacing(4)
        layout.addLayout(self._tags_layout)

        self._update_header()
        return header

    def _update_header(self) -> None:
        self._title_edit.setText(self._entry.title or "")
        self._date_label.setText(self._entry.created_at.strftime("%Y-%m-%d %H:%M"))

        # Clear old tag chips
        while self._tags_layout.count():
            item = self._tags_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        for tag in self._entry.tags:
            chip = _TagChip(tag, removable=True)
            chip.removed.connect(self._on_tag_removed)
            self._tags_layout.addWidget(chip)

        # Add-tag button
        add_tag_btn = QToolButton()
        add_tag_btn.setText("+ tag")
        add_tag_btn.setStyleSheet(
            "QToolButton { border: 1px dashed #666; border-radius: 6px; "
            "padding: 1px 6px; font-size: 8pt; color: #888; } "
            "QToolButton:hover { border-color: #aaa; color: #aaa; }"
        )
        add_tag_btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        add_tag_btn.clicked.connect(self._on_add_tag_clicked)
        self._tags_layout.addWidget(add_tag_btn)
        self._tags_layout.addStretch()

    # -- fragment building --

    # -- toolbar --

    def _build_toolbar(self) -> QToolBar:
        """Build the bottom toolbar with collapse controls."""
        toolbar = QToolBar()
        toolbar.setMovable(False)
        toolbar.setFloatable(False)
        toolbar.setStyleSheet(
            "QToolBar { border-top: 1px solid #444; padding: 2px 4px; spacing: 2px; }"
        )

        spacer = QWidget()
        spacer.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        toolbar.addWidget(spacer)

        # Collapse mode buttons
        self._expand_all_btn = QToolButton()
        self._expand_all_btn.setText("Expand All")
        self._expand_all_btn.setToolTip("Expand all collapsed groups")
        self._expand_all_btn.clicked.connect(self._expand_all_groups)
        toolbar.addWidget(self._expand_all_btn)

        self._collapse_all_btn = QToolButton()
        self._collapse_all_btn.setText("Collapse All")
        self._collapse_all_btn.setToolTip("Group all consecutive readonly fragments")
        self._collapse_all_btn.setCheckable(True)
        self._collapse_all_btn.setChecked(self._collapse_mode == CollapseMode.ALL_READONLY)
        self._collapse_all_btn.clicked.connect(
            lambda: self._set_collapse_mode(CollapseMode.ALL_READONLY)
        )
        toolbar.addWidget(self._collapse_all_btn)

        self._collapse_type_btn = QToolButton()
        self._collapse_type_btn.setText("By Type")
        self._collapse_type_btn.setToolTip("Group consecutive readonly fragments by subtype")
        self._collapse_type_btn.setCheckable(True)
        self._collapse_type_btn.setChecked(self._collapse_mode == CollapseMode.SAME_TYPE)
        self._collapse_type_btn.clicked.connect(
            lambda: self._set_collapse_mode(CollapseMode.SAME_TYPE)
        )
        toolbar.addWidget(self._collapse_type_btn)

        return toolbar

    def _expand_all_groups(self) -> None:
        for w in self._widget_items:
            if isinstance(w, CollapsibleGroup) and w.is_collapsed:
                w._toggle_collapsed()

    def _set_collapse_mode(self, mode: CollapseMode) -> None:
        self._collapse_mode = mode
        self._collapse_all_btn.setChecked(mode == CollapseMode.ALL_READONLY)
        self._collapse_type_btn.setChecked(mode == CollapseMode.SAME_TYPE)
        self._rebuild_fragments()

    def _on_title_edited(self) -> None:
        new_title = self._title_edit.text().strip()
        if new_title != self._entry.title:
            self._entry.title = new_title
            self.title_changed.emit(self._entry.id, new_title)

    def _on_tag_removed(self, tag: str) -> None:
        if tag in self._entry.tags:
            self._entry.tags.remove(tag)
            self._update_header()
            self.tags_changed.emit(self._entry.id, self._entry.tags)

    def _on_add_tag_clicked(self) -> None:
        """Replace the '+ tag' button with an inline QLineEdit."""
        # Find and hide the add button
        for i in range(self._tags_layout.count()):
            w = self._tags_layout.itemAt(i).widget()
            if isinstance(w, QToolButton) and w.text() == "+ tag":
                w.setVisible(False)
                break

        tag_input = QLineEdit()
        tag_input.setPlaceholderText("new tag")
        tag_input.setFixedWidth(80)
        tag_input.setStyleSheet(
            "font-size: 8pt; border: 1px solid #666; border-radius: 6px; "
            "padding: 1px 6px; background: transparent;"
        )
        # Insert before the stretch
        self._tags_layout.insertWidget(self._tags_layout.count() - 1, tag_input)
        tag_input.setFocus()

        def commit() -> None:
            text = tag_input.text().strip()
            tag_input.deleteLater()
            if text and text not in self._entry.tags:
                self._entry.tags.append(text)
                self.tags_changed.emit(self._entry.id, self._entry.tags)
            self._update_header()

        tag_input.returnPressed.connect(commit)
        tag_input.editingFinished.connect(commit)

    def _rebuild_fragments(self) -> None:
        """Rebuild all fragment widgets, grouping readonly runs."""
        # Remove old widgets (keep add button and trailing stretch)
        for w in self._widget_items:
            self._fragment_layout.removeWidget(w)
            w.deleteLater()
        self._widget_items.clear()

        groups = _group_fragments(self._entry.fragments, self._collapse_mode)
        # Find the index of the add button so we insert before it
        add_btn_idx = self._fragment_layout.indexOf(self._add_btn)
        insert_idx = 0
        for group in groups:
            widget: QWidget
            if len(group) == 1 and group[0].fragment_type == FragmentType.TEXT:
                widget = TextFragmentWidget(group[0])
                widget.content_changed.connect(self._on_fragment_changed)
                widget.delete_requested.connect(self._on_fragment_delete)
                widget.claude_requested.connect(self._on_fragment_claude)
            elif all(f.fragment_type == FragmentType.READONLY for f in group) and len(group) > 1:
                widget = CollapsibleGroup(group)
                widget.collapse_mode = self._collapse_mode
            elif len(group) == 1 and group[0].fragment_type == FragmentType.READONLY:
                widget = ReadonlyFragmentWidget(group[0])
                widget.delete_requested.connect(self._on_fragment_delete)
                widget.claude_requested.connect(self._on_fragment_claude)
            else:
                # Mixed single item fallback
                widget = ReadonlyFragmentWidget(group[0]) if group else QWidget()

            self._fragment_layout.insertWidget(insert_idx, widget)
            self._widget_items.append(widget)
            insert_idx += 1

    # -- slots --

    @Slot(str, str)
    def _on_fragment_changed(self, frag_id: str, content: str) -> None:
        self._entry.updated_at = datetime.now()
        self.fragment_changed.emit(self._entry.id, frag_id, content)

    @Slot(str)
    def _on_fragment_delete(self, frag_id: str) -> None:
        """Remove a fragment from the entry."""
        self._entry.fragments = [f for f in self._entry.fragments if f.id != frag_id]
        self._entry.updated_at = datetime.now()
        self._rebuild_fragments()
        self.fragment_deleted.emit(self._entry.id, frag_id)
        logger.debug("Deleted fragment {} from entry {}", frag_id, self._entry.id)

    @Slot(str)
    def _on_fragment_claude(self, frag_id: str) -> None:
        """Forward Claude request to the panel."""
        self.claude_requested.emit(self._entry.id, frag_id)

    # -- drag & drop reorder --

    def _frag_drag_enter(self, event: QDragEnterEvent) -> None:
        if event.mimeData().hasFormat("application/x-logbook-fragment-id"):
            event.acceptProposedAction()

    def _frag_drag_move(self, event: QDragMoveEvent) -> None:
        if event.mimeData().hasFormat("application/x-logbook-fragment-id"):
            event.acceptProposedAction()

    def _frag_drop(self, event: QDropEvent) -> None:
        mime = event.mimeData()
        if not mime.hasFormat("application/x-logbook-fragment-id"):
            return
        dragged_id = bytes(mime.data("application/x-logbook-fragment-id")).decode()

        # Find the drop position by checking which widget we're over
        drop_y = event.position().y()
        target_idx = len(self._entry.fragments)  # default: end

        for i, w in enumerate(self._widget_items):
            widget_y = w.y() + w.height() / 2
            if drop_y < widget_y:
                # Map widget back to fragment index
                frag = (
                    getattr(w, "_fragment", None) if not isinstance(w, CollapsibleGroup) else None
                )
                if frag:
                    target_idx = next(
                        (j for j, f in enumerate(self._entry.fragments) if f.id == frag.id),
                        target_idx,
                    )
                else:
                    target_idx = i
                break

        # Reorder
        frags = self._entry.fragments
        old_idx = next((j for j, f in enumerate(frags) if f.id == dragged_id), None)
        if old_idx is None:
            return

        frag = frags.pop(old_idx)
        if target_idx > old_idx:
            target_idx -= 1
        frags.insert(target_idx, frag)

        self._rebuild_fragments()
        self.fragment_reordered.emit(self._entry.id, [f.id for f in frags])
        event.acceptProposedAction()

    @Slot()
    def _add_text_fragment(self) -> None:
        frag = FragmentData(fragment_type=FragmentType.TEXT, content="")
        self._entry.fragments.append(frag)
        self._entry.updated_at = datetime.now()
        self._rebuild_fragments()
        self.fragment_added.emit(self._entry.id, frag.id)
        logger.debug(f"Added text fragment {frag.id} to entry {self._entry.id}")

        # Focus the newly created fragment's editor
        for w in self._widget_items:
            if isinstance(w, TextFragmentWidget) and w.fragment.id == frag.id:
                w._enter_edit_mode()
                break


# ---------------------------------------------------------------------------
# EntryListWidget
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Model / View components for EntryListWidget
# ---------------------------------------------------------------------------

# Custom data roles
_TitleRole = Qt.ItemDataRole.UserRole + 1
_DateRole = Qt.ItemDataRole.UserRole + 2
_TagsRole = Qt.ItemDataRole.UserRole + 3
_EntryIdRole = Qt.ItemDataRole.UserRole + 4
_UpdatedAtRole = Qt.ItemDataRole.UserRole + 5
_CreatedAtRole = Qt.ItemDataRole.UserRole + 6


class EntryListModel(QAbstractListModel):
    """Stores ``EntryData`` objects for a ``QListView``."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._entries: list[EntryData] = []

    # -- QAbstractListModel interface --

    def rowCount(self, parent: QModelIndex | None = None) -> int:  # noqa: N802
        return len(self._entries)

    def data(self, index: QModelIndex, role: int = Qt.ItemDataRole.DisplayRole) -> Any:
        if not index.isValid() or not (0 <= index.row() < len(self._entries)):
            return None
        entry = self._entries[index.row()]
        if role == Qt.ItemDataRole.DisplayRole or role == _TitleRole:
            return entry.title or _first_line(entry)
        if role == _DateRole:
            return entry.created_at.strftime("%Y-%m-%d %H:%M")
        if role == _TagsRole:
            return entry.tags
        if role == _EntryIdRole:
            return entry.id
        if role == _UpdatedAtRole:
            return entry.updated_at
        if role == _CreatedAtRole:
            return entry.created_at
        return None

    # -- public helpers --

    def set_entries(self, entries: list[EntryData]) -> None:
        self.beginResetModel()
        self._entries = list(entries)
        self.endResetModel()

    def add_entry(self, entry: EntryData) -> None:
        row = len(self._entries)
        self.beginInsertRows(QModelIndex(), row, row)
        self._entries.append(entry)
        self.endInsertRows()

    def remove_entry(self, entry_id: str) -> None:
        for row, e in enumerate(self._entries):
            if e.id == entry_id:
                self.beginRemoveRows(QModelIndex(), row, row)
                self._entries.pop(row)
                self.endRemoveRows()
                return

    def entry_at(self, index: QModelIndex) -> EntryData | None:
        if index.isValid() and 0 <= index.row() < len(self._entries):
            return self._entries[index.row()]
        return None

    def all_entries(self) -> list[EntryData]:
        return list(self._entries)


class EntrySortFilterProxy(QSortFilterProxyModel):
    """Sort by *created_at* or *updated_at* (descending) and filter by tag."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._sort_role_key: str = "created_at"  # or "updated_at"
        self._tag_filter: str | None = None
        self.setSortRole(_CreatedAtRole)
        self.setDynamicSortFilter(True)
        self.sort(0, Qt.SortOrder.DescendingOrder)

    # -- public --

    def set_sort_key(self, key: str) -> None:
        self._sort_role_key = key
        role = _CreatedAtRole if key == "created_at" else _UpdatedAtRole
        self.setSortRole(role)
        self.invalidate()
        self.sort(0, Qt.SortOrder.DescendingOrder)

    def set_tag_filter(self, tag: str | None) -> None:
        self._tag_filter = tag
        self.invalidateFilter()

    @property
    def tag_filter(self) -> str | None:
        return self._tag_filter

    # -- overrides --

    def lessThan(self, left: QModelIndex, right: QModelIndex) -> bool:  # noqa: N802
        lv = left.data(self.sortRole())
        rv = right.data(self.sortRole())
        if lv is None or rv is None:
            return False
        # Normalize to offset-naive for comparison
        if hasattr(lv, "tzinfo") and lv.tzinfo is not None:
            lv = lv.replace(tzinfo=None)
        if hasattr(rv, "tzinfo") and rv.tzinfo is not None:
            rv = rv.replace(tzinfo=None)
        return lv < rv

    def filterAcceptsRow(  # noqa: N802
        self, source_row: int, source_parent: QModelIndex
    ) -> bool:
        if self._tag_filter is None:
            return True
        idx = self.sourceModel().index(source_row, 0, source_parent)
        tags = idx.data(_TagsRole) or []
        return self._tag_filter in tags


class EntryDelegate(QStyledItemDelegate):
    """Custom delegate that replicates the old ``_EntryRow`` look."""

    delete_clicked = Signal(str)  # entry_id

    _ROW_PADDING = 6
    _LINE_SPACING = 2
    _CHIP_H_PAD = 6
    _CHIP_V_PAD = 2
    _CHIP_RADIUS = 6
    _CHIP_SPACING = 4
    _DELETE_SIZE = 18

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._hovered_index: QModelIndex = QModelIndex()

    def set_hovered_index(self, index: QModelIndex) -> None:
        self._hovered_index = index

    # -- painting --

    def paint(
        self,
        painter: QPainter,
        option: QStyleOptionViewItem,
        index: QModelIndex,
    ) -> None:
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)

        rect = option.rect
        is_selected = bool(option.state & option.state.State_Selected)
        is_hovered = self._hovered_index.isValid() and self._hovered_index.row() == index.row()
        dark = is_dark_theme()

        # Background
        if is_selected:
            bg = QColor("#3a3a5c") if dark else QColor("#d0d0f0")
            painter.fillRect(rect, bg)
        elif is_hovered:
            hover_bg = QColor("#2a2a4c") if dark else QColor("#e8e8f8")
            painter.fillRect(rect, hover_bg)

        pad = self._ROW_PADDING
        x0 = rect.left() + pad
        y0 = rect.top() + pad
        avail_w = rect.width() - 2 * pad

        # Title (bold, 9pt)
        title_font = QFont()
        title_font.setPointSize(9)
        title_font.setBold(True)
        painter.setFont(title_font)
        painter.setPen(QPen(QColor("#e0e0e0" if dark else "#222222")))
        title_text = index.data(_TitleRole) or "Untitled entry"
        fm_title = QFontMetrics(title_font)
        elided = fm_title.elidedText(title_text, Qt.TextElideMode.ElideRight, avail_w)
        painter.drawText(x0, y0 + fm_title.ascent(), elided)

        # Second line: date + tag chips
        y1 = y0 + fm_title.height() + self._LINE_SPACING

        date_font = QFont()
        date_font.setPointSize(8)
        painter.setFont(date_font)
        painter.setPen(QPen(QColor("#888888")))
        date_text = index.data(_DateRole) or ""
        fm_date = QFontMetrics(date_font)
        painter.drawText(x0, y1 + fm_date.ascent(), date_text)

        chip_x = x0 + fm_date.horizontalAdvance(date_text) + 8
        tags = (index.data(_TagsRole) or [])[:3]
        chip_bg = QColor("#3a3a5c" if dark else "#e0e0f0")
        chip_fg = QColor("#c0c0e0" if dark else "#333366")
        chip_font = QFont()
        chip_font.setPointSize(8)
        fm_chip = QFontMetrics(chip_font)

        for tag in tags:
            tw = fm_chip.horizontalAdvance(tag)
            chip_w = tw + 2 * self._CHIP_H_PAD
            chip_h = fm_chip.height() + 2 * self._CHIP_V_PAD
            chip_rect = QRect(int(chip_x), int(y1), int(chip_w), int(chip_h))
            painter.setBrush(QBrush(chip_bg))
            painter.setPen(Qt.PenStyle.NoPen)
            painter.drawRoundedRect(chip_rect, self._CHIP_RADIUS, self._CHIP_RADIUS)
            painter.setPen(QPen(chip_fg))
            painter.setFont(chip_font)
            painter.drawText(chip_rect, Qt.AlignmentFlag.AlignCenter, tag)
            chip_x += chip_w + self._CHIP_SPACING

        # Delete button on hover
        if is_hovered:
            ds = self._DELETE_SIZE
            dx = rect.right() - ds - pad
            dy = rect.top() + pad
            del_rect = QRect(dx, dy, ds, ds)

            painter.setBrush(QBrush(QColor(244, 67, 54, 50)))
            painter.setPen(Qt.PenStyle.NoPen)
            painter.drawRoundedRect(del_rect, 3, 3)

            del_font = QFont()
            del_font.setPointSize(9)
            del_font.setBold(True)
            painter.setFont(del_font)
            painter.setPen(QPen(QColor("#f44336")))
            painter.drawText(del_rect, Qt.AlignmentFlag.AlignCenter, "✕")

        painter.restore()

    def sizeHint(  # noqa: N802
        self, option: QStyleOptionViewItem, index: QModelIndex
    ) -> QSize:
        title_font = QFont()
        title_font.setPointSize(9)
        title_font.setBold(True)
        date_font = QFont()
        date_font.setPointSize(8)
        h = (
            self._ROW_PADDING
            + QFontMetrics(title_font).height()
            + self._LINE_SPACING
            + QFontMetrics(date_font).height()
            + 2 * self._CHIP_V_PAD
            + self._ROW_PADDING
        )
        return QSize(option.rect.width(), max(h, 44))

    # -- delete click detection --

    def editorEvent(self, event, model, option, index) -> bool:  # noqa: N802
        if (
            isinstance(event, QMouseEvent)
            and event.type() == event.Type.MouseButtonRelease
            and event.button() == Qt.MouseButton.LeftButton
        ):
            rect = option.rect
            pad = self._ROW_PADDING
            ds = self._DELETE_SIZE
            dx = rect.right() - ds - pad
            dy = rect.top() + pad
            del_rect = QRect(dx, dy, ds, ds)
            if del_rect.contains(event.position().toPoint()):
                entry_id = index.data(_EntryIdRole)
                if entry_id:
                    self.delete_clicked.emit(entry_id)
                    return True
        return super().editorEvent(event, model, option, index)


class EntryListWidget(QFrame):
    """Sidebar listing all logbook entries.

    Signals:
        entry_selected(entry_id): User clicked an entry.
        entry_delete_requested(entry_id): User requested deletion.
        new_entry_requested(): User clicked 'New Entry'.
    """

    entry_selected = Signal(str)
    entry_delete_requested = Signal(str)  # entry_id
    new_entry_requested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._selected_id: str | None = None

        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setMinimumWidth(220)
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Expanding)

        root = QVBoxLayout(self)
        root.setContentsMargins(4, 4, 4, 4)
        root.setSpacing(4)

        # --- toolbar row ---
        toolbar = QHBoxLayout()
        toolbar.setSpacing(4)
        new_btn = QPushButton("＋ New Entry")
        new_btn.clicked.connect(self.new_entry_requested)
        toolbar.addWidget(new_btn)

        self._sort_combo = QComboBox()
        self._sort_combo.addItems(["Created", "Updated"])
        self._sort_combo.currentIndexChanged.connect(self._on_sort_changed)
        toolbar.addWidget(self._sort_combo)
        root.addLayout(toolbar)

        # --- tag filter row ---
        self._tag_filter_container = QWidget()
        self._tag_filter_layout = QHBoxLayout(self._tag_filter_container)
        self._tag_filter_layout.setContentsMargins(0, 0, 0, 0)
        self._tag_filter_layout.setSpacing(3)
        self._tag_filter_container.setVisible(False)
        root.addWidget(self._tag_filter_container)

        # --- model / view ---
        self._model = EntryListModel(self)
        self._proxy = EntrySortFilterProxy(self)
        self._proxy.setSourceModel(self._model)

        self._delegate = EntryDelegate(self)
        self._delegate.delete_clicked.connect(self._on_row_delete)

        self._view = QListView()
        self._view.setModel(self._proxy)
        self._view.setItemDelegate(self._delegate)
        self._view.setFrameShape(QFrame.Shape.NoFrame)
        self._view.setSelectionMode(QListView.SelectionMode.SingleSelection)
        self._view.setMouseTracking(True)
        self._view.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._view.setVerticalScrollMode(QListView.ScrollMode.ScrollPerPixel)
        self._view.entered.connect(self._on_view_entered)
        self._view.selectionModel().currentChanged.connect(self._on_current_changed)
        self._view.setStyleSheet(
            "QListView { background: transparent; }"
            "QListView::item { border: none; }"
            "QListView::item:selected { background: transparent; }"
            "QListView::item:hover { background: transparent; }"
        )

        root.addWidget(self._view, 1)

    # -- public API --

    def set_entries(self, entries: list[EntryData]) -> None:
        """Replace the full entry list."""
        self._model.set_entries(entries)
        self._rebuild_tag_filter()
        self._restore_selection()

    def add_entry(self, entry: EntryData) -> None:
        self._model.add_entry(entry)
        self._rebuild_tag_filter()

    def select_entry(self, entry_id: str) -> None:
        self._selected_id = entry_id
        self._restore_selection()

    # -- internal --

    def _restore_selection(self) -> None:
        """Select the row matching ``_selected_id`` in the proxy view."""
        if not self._selected_id:
            return
        for row in range(self._proxy.rowCount()):
            idx = self._proxy.index(row, 0)
            if idx.data(_EntryIdRole) == self._selected_id:
                self._view.setCurrentIndex(idx)
                return

    def _rebuild_tag_filter(self) -> None:
        """Rebuild the tag filter chips from all entries."""
        while self._tag_filter_layout.count():
            item = self._tag_filter_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        all_tags: set[str] = set()
        for e in self._model.all_entries():
            all_tags.update(e.tags)

        if not all_tags:
            self._tag_filter_container.setVisible(False)
            return

        self._tag_filter_container.setVisible(True)

        # "All" button
        all_btn = QToolButton()
        all_btn.setText("All")
        active = self._proxy.tag_filter is None
        bg = "#4a4a6c" if active else "transparent"
        all_btn.setStyleSheet(
            f"QToolButton {{ border: 1px solid #555; border-radius: 6px; "
            f"padding: 1px 6px; font-size: 8pt; background: {bg}; }} "
            f"QToolButton:hover {{ background: #4a4a6c; }}"
        )
        all_btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        all_btn.clicked.connect(lambda: self._on_tag_filter(None))
        self._tag_filter_layout.addWidget(all_btn)

        for tag in sorted(all_tags):
            btn = QToolButton()
            btn.setText(tag)
            active = self._proxy.tag_filter == tag
            bg = "#4a4a6c" if active else "transparent"
            btn.setStyleSheet(
                f"QToolButton {{ border: 1px solid #555; border-radius: 6px; "
                f"padding: 1px 6px; font-size: 8pt; background: {bg}; }} "
                f"QToolButton:hover {{ background: #4a4a6c; }}"
            )
            btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
            btn.clicked.connect(lambda checked=False, t=tag: self._on_tag_filter(t))
            self._tag_filter_layout.addWidget(btn)

        self._tag_filter_layout.addStretch()

    def _on_tag_filter(self, tag: str | None) -> None:
        self._proxy.set_tag_filter(tag)
        self._rebuild_tag_filter()

    @Slot(QModelIndex)
    def _on_view_entered(self, index: QModelIndex) -> None:
        self._delegate.set_hovered_index(index)
        self._view.viewport().update()

    @Slot(QModelIndex, QModelIndex)
    def _on_current_changed(self, current: QModelIndex, _previous: QModelIndex) -> None:
        if not current.isValid():
            return
        entry_id = current.data(_EntryIdRole)
        if entry_id and entry_id != self._selected_id:
            self._selected_id = entry_id
            self.entry_selected.emit(entry_id)

    @Slot(str)
    def _on_row_delete(self, entry_id: str) -> None:
        self._model.remove_entry(entry_id)
        entries = self._model.all_entries()
        if self._selected_id == entry_id:
            self._selected_id = entries[0].id if entries else None
        self._rebuild_tag_filter()
        self.entry_delete_requested.emit(entry_id)
        if self._selected_id:
            self._restore_selection()
            self.entry_selected.emit(self._selected_id)

    @Slot(int)
    def _on_sort_changed(self, index: int) -> None:
        key = "created_at" if index == 0 else "updated_at"
        self._proxy.set_sort_key(key)


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------


# _EntryRow removed — replaced by EntryDelegate + QListView


def _first_line(entry: EntryData) -> str:
    """Extract a display title from the first text fragment."""
    for f in entry.fragments:
        if f.fragment_type == FragmentType.TEXT and f.content.strip():
            line = f.content.strip().split("\n")[0][:60]
            return line
    return "Untitled entry"


def _group_fragments(
    fragments: list[FragmentData],
    mode: CollapseMode,
) -> list[list[FragmentData]]:
    """Group fragments for display.

    Text fragments are always standalone. Consecutive readonly fragments
    are grouped according to *mode*.

    Returns a list of groups, where each group is a list of fragments.
    """
    groups: list[list[FragmentData]] = []
    current_run: list[FragmentData] = []

    def flush() -> None:
        nonlocal current_run
        if current_run:
            if mode == CollapseMode.ALL_READONLY:
                groups.append(current_run)
            else:  # SAME_TYPE
                # Sub-split by subtype
                sub: list[FragmentData] = []
                for f in current_run:
                    if sub and sub[-1].subtype != f.subtype:
                        groups.append(sub)
                        sub = []
                    sub.append(f)
                if sub:
                    groups.append(sub)
            current_run = []

    for frag in fragments:
        if frag.fragment_type == FragmentType.TEXT:
            flush()
            groups.append([frag])
        else:
            current_run.append(frag)

    flush()
    return groups
