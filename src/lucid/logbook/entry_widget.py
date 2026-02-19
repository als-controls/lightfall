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
from PySide6.QtCore import Qt, Signal, Slot
from PySide6.QtGui import QCursor
from PySide6.QtWidgets import (
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QSizePolicy,
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


def _tag_chip(tag: str) -> QLabel:
    """Create a small coloured chip label for a tag."""
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
    title_changed = Signal(str, str)  # (entry_id, new_title)

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
            self._tags_layout.addWidget(_tag_chip(tag))
        self._tags_layout.addStretch()

    # -- fragment building --

    def _on_title_edited(self) -> None:
        new_title = self._title_edit.text().strip()
        if new_title != self._entry.title:
            self._entry.title = new_title
            self.title_changed.emit(self._entry.id, new_title)

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
            elif all(f.fragment_type == FragmentType.READONLY for f in group) and len(group) > 1:
                widget = CollapsibleGroup(group)
                widget.collapse_mode = self._collapse_mode
            elif len(group) == 1 and group[0].fragment_type == FragmentType.READONLY:
                widget = ReadonlyFragmentWidget(group[0])
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
    new_entry_requested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._entries: list[EntryData] = []
        self._sort_key: str = "created_at"  # or "updated_at"
        self._selected_id: str | None = None

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

        key = self._sort_key
        sorted_entries = sorted(
            self._entries,
            key=lambda e: getattr(e, key),
            reverse=True,
        )

        for idx, entry in enumerate(sorted_entries):
            row = _EntryRow(entry, self)
            row.clicked.connect(self._on_row_clicked)
            self._list_layout.insertWidget(idx, row)
            self._row_widgets.append(row)

        self._update_selection_highlight()

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
