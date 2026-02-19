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
from PySide6.QtCore import QMimeData, Qt, Signal, Slot
from PySide6.QtGui import QCursor, QDragEnterEvent, QDragMoveEvent, QDropEvent, QFont
from PySide6.QtWidgets import (
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QSizePolicy,
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


def _tag_chip(tag: str) -> QLabel:
    """Create a small coloured chip label for a tag (read-only, for sidebar)."""
    lbl = QLabel(tag)
    bg = "#3a3a5c" if is_dark_theme() else "#e0e0f0"
    fg = "#c0c0e0" if is_dark_theme() else "#333366"
    lbl.setStyleSheet(
        f"background: {bg}; color: {fg}; border-radius: 6px; "
        f"padding: 2px 8px; font-size: 8pt;"
    )
    lbl.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)
    return lbl


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
                frag = getattr(w, '_fragment', None) if not isinstance(w, CollapsibleGroup) else None
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


class EntryListWidget(QFrame):
    """Sidebar listing all logbook entries.

    Signals:
        entry_selected(entry_id): User clicked an entry.
        new_entry_requested(): User clicked 'New Entry'.
    """

    entry_selected = Signal(str)
    entry_delete_requested = Signal(str)  # entry_id
    new_entry_requested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._entries: list[EntryData] = []
        self._sort_key: str = "created_at"  # or "updated_at"
        self._selected_id: str | None = None
        self._active_tag_filter: str | None = None

        self.setFrameShape(QFrame.Shape.StyledPanel)
        self.setMinimumWidth(220)
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Expanding)

        root = QVBoxLayout(self)
        root.setContentsMargins(4, 4, 4, 4)
        root.setSpacing(4)

        # --- toolbar row ---
        toolbar = QHBoxLayout()
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

        # --- scrollable list ---
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)

        self._list_container = QWidget()
        self._list_layout = QVBoxLayout(self._list_container)
        self._list_layout.setContentsMargins(0, 0, 0, 0)
        self._list_layout.setSpacing(2)
        self._list_layout.addStretch()

        scroll.setWidget(self._list_container)
        root.addWidget(scroll, 1)

        self._row_widgets: list[QWidget] = []

    # -- public API --

    def set_entries(self, entries: list[EntryData]) -> None:
        """Replace the full entry list."""
        self._entries = list(entries)
        self._rebuild()

    def add_entry(self, entry: EntryData) -> None:
        self._entries.append(entry)
        self._rebuild()

    def select_entry(self, entry_id: str) -> None:
        self._selected_id = entry_id
        self._update_selection_highlight()

    # -- internal --

    def _rebuild(self) -> None:
        for w in self._row_widgets:
            self._list_layout.removeWidget(w)
            w.deleteLater()
        self._row_widgets.clear()

        # Rebuild tag filter bar
        self._rebuild_tag_filter()

        key = self._sort_key
        sorted_entries = sorted(
            self._entries,
            key=lambda e: getattr(e, key),
            reverse=True,
        )

        # Apply tag filter
        if self._active_tag_filter:
            sorted_entries = [e for e in sorted_entries if self._active_tag_filter in e.tags]

        for idx, entry in enumerate(sorted_entries):
            row = _EntryRow(entry, self)
            row.clicked.connect(self._on_row_clicked)
            row.delete_clicked.connect(self._on_row_delete)
            self._list_layout.insertWidget(idx, row)
            self._row_widgets.append(row)

        self._update_selection_highlight()

    def _rebuild_tag_filter(self) -> None:
        """Rebuild the tag filter chips from all entries."""
        # Clear
        while self._tag_filter_layout.count():
            item = self._tag_filter_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        # Collect all unique tags
        all_tags: set[str] = set()
        for e in self._entries:
            all_tags.update(e.tags)

        if not all_tags:
            self._tag_filter_container.setVisible(False)
            return

        self._tag_filter_container.setVisible(True)

        # "All" button
        all_btn = QToolButton()
        all_btn.setText("All")
        active = self._active_tag_filter is None
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
            active = self._active_tag_filter == tag
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
        self._active_tag_filter = tag
        self._rebuild()

    def _update_selection_highlight(self) -> None:
        sel_bg = "#3a3a5c" if is_dark_theme() else "#d0d0f0"
        for w in self._row_widgets:
            if isinstance(w, _EntryRow):
                is_sel = w.entry_id == self._selected_id
                w.setStyleSheet(
                    f"background: {sel_bg}; border-radius: 4px;" if is_sel else ""
                )

    @Slot(str)
    def _on_row_clicked(self, entry_id: str) -> None:
        self._selected_id = entry_id
        self._update_selection_highlight()
        self.entry_selected.emit(entry_id)

    @Slot(str)
    def _on_row_delete(self, entry_id: str) -> None:
        self._entries = [e for e in self._entries if e.id != entry_id]
        if self._selected_id == entry_id:
            self._selected_id = self._entries[0].id if self._entries else None
        self._rebuild()
        self.entry_delete_requested.emit(entry_id)
        # Auto-select next entry
        if self._selected_id:
            self.entry_selected.emit(self._selected_id)

    @Slot(int)
    def _on_sort_changed(self, index: int) -> None:
        self._sort_key = "created_at" if index == 0 else "updated_at"
        self._rebuild()


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------


class _EntryRow(QFrame):
    """Single row in the entry list sidebar."""

    clicked = Signal(str)
    delete_clicked = Signal(str)  # entry_id

    def __init__(self, entry: EntryData, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.entry_id = entry.id
        self.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Maximum)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 4, 6, 4)
        layout.setSpacing(1)

        title = entry.title or _first_line(entry)
        title_lbl = QLabel(title)
        title_lbl.setStyleSheet("font-weight: bold; font-size: 9pt;")
        title_lbl.setWordWrap(True)
        layout.addWidget(title_lbl)

        meta_row = QHBoxLayout()
        date_lbl = QLabel(entry.created_at.strftime("%Y-%m-%d %H:%M"))
        date_lbl.setStyleSheet("font-size: 8pt; color: #888;")
        meta_row.addWidget(date_lbl)

        for tag in entry.tags[:3]:
            meta_row.addWidget(_tag_chip(tag))
        meta_row.addStretch()
        layout.addLayout(meta_row)

        # Delete button (overlay, shown on hover)
        from PySide6.QtCore import QSize
        self._delete_btn = QToolButton(self)
        try:
            import qtawesome as qta
            self._delete_btn.setIcon(qta.icon("mdi.trash-can-outline", color="#f44336"))
            self._delete_btn.setIconSize(QSize(16, 16))
        except Exception:
            self._delete_btn.setText("✕")
        self._delete_btn.setToolTip("Delete entry")
        self._delete_btn.setFixedSize(22, 22)
        self._delete_btn.setStyleSheet(
            "QToolButton { border: none; border-radius: 3px; } "
            "QToolButton:hover { background: rgba(244,67,54,0.2); }"
        )
        self._delete_btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self._delete_btn.clicked.connect(lambda: self.delete_clicked.emit(self.entry_id))
        self._delete_btn.setVisible(False)

    def enterEvent(self, event: Any) -> None:  # noqa: N802
        self._delete_btn.move(self.width() - self._delete_btn.sizeHint().width() - 4, 4)
        self._delete_btn.setVisible(True)
        super().enterEvent(event)

    def leaveEvent(self, event: Any) -> None:  # noqa: N802
        self._delete_btn.setVisible(False)
        super().leaveEvent(event)

    def resizeEvent(self, event: Any) -> None:  # noqa: N802
        if self._delete_btn.isVisible():
            self._delete_btn.move(self.width() - self._delete_btn.sizeHint().width() - 4, 4)
        super().resizeEvent(event)

    def mousePressEvent(self, event: Any) -> None:  # noqa: N802
        self.clicked.emit(self.entry_id)
        super().mousePressEvent(event)


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
