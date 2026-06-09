"""Document stream widget for viewing Bluesky documents.

Displays Bluesky documents as they stream during a run, grouped
by document type with expandable details, or in sequential order.
"""

from __future__ import annotations

import json
from collections import deque
from datetime import datetime
from typing import TYPE_CHECKING, Any

import qtawesome as qta
from loguru import logger
from PySide6.QtCore import (
    QAbstractItemModel,
    QAbstractTableModel,
    QEvent,
    QModelIndex,
    QSize,
    Qt,
    QTimer,
    Signal,
    Slot,
)
from PySide6.QtGui import QFont, QPainter
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QStackedWidget,
    QStyle,
    QStyledItemDelegate,
    QStyleOptionViewItem,
    QTableView,
    QTreeView,
    QVBoxLayout,
    QWidget,
)

if TYPE_CHECKING:
    from lightfall.acquire.engine import Engine


class DocumentTreeItem:
    """Item in the document tree model.

    Can be a group (start, descriptor, event, stop) or an individual document.
    """

    def __init__(
        self,
        data: dict[str, Any] | None = None,
        name: str = "",
        parent: DocumentTreeItem | None = None,
    ) -> None:
        """Initialize tree item.

        Args:
            data: Document data dictionary.
            name: Display name for the item.
            parent: Parent item.
        """
        self._data = data or {}
        self._name = name
        self._parent = parent
        self._children: list[DocumentTreeItem] = []

    def append_child(self, child: DocumentTreeItem) -> None:
        """Add a child item.

        Args:
            child: Child item to add.
        """
        child._parent = self
        self._children.append(child)

    def child(self, row: int) -> DocumentTreeItem | None:
        """Get child at row.

        Args:
            row: Child row index.

        Returns:
            Child item or None.
        """
        if 0 <= row < len(self._children):
            return self._children[row]
        return None

    def child_count(self) -> int:
        """Get number of children."""
        return len(self._children)

    def row(self) -> int:
        """Get this item's row in parent."""
        if self._parent:
            return self._parent._children.index(self)
        return 0

    @property
    def parent(self) -> DocumentTreeItem | None:
        """Get parent item."""
        return self._parent

    @property
    def name(self) -> str:
        """Get display name."""
        return self._name

    @property
    def data(self) -> dict[str, Any]:
        """Get document data."""
        return self._data

    def column_data(self, column: int) -> Any:
        """Get data for a column.

        Args:
            column: Column index.

        Returns:
            Data for the column.
        """
        if column == 0:
            return self._name
        elif column == 1:
            # Show summary info
            if "uid" in self._data:
                return self._data["uid"][:8]
            elif "seq_num" in self._data:
                return f"seq #{self._data['seq_num']}"
            return ""
        elif column == 2:
            # Show timestamp or count
            if "time" in self._data:
                ts = datetime.fromtimestamp(self._data["time"])
                return ts.strftime("%H:%M:%S.%f")[:-3]
            return str(len(self._children)) if self._children else ""
        return ""


class DocumentStreamModel(QAbstractItemModel):
    """Qt model for hierarchical document display.

    Groups documents by type. Groups are created on demand as new
    document types arrive — no hardcoded list of expected types.
    """

    # Display names for known group types (pluralized).
    _GROUP_LABELS: dict[str, str] = {
        "start": "Start",
        "descriptor": "Descriptors",
        "event": "Events",
        "stop": "Stop",
        "resource": "Resources",
        "datum": "Datums",
        "stream_resource": "Stream Resources",
        "stream_datum": "Stream Datums",
        "event_page": "Event Pages",
        "datum_page": "Datum Pages",
    }

    def __init__(self, parent=None) -> None:
        """Initialize the model."""
        super().__init__(parent)
        self._root = DocumentTreeItem(name="Documents")
        self._groups: dict[str, DocumentTreeItem] = {}
        self._group_order: list[str] = []

    def _get_or_create_group(self, name: str) -> tuple[DocumentTreeItem, QModelIndex]:
        """Get existing group or create a new one for *name*.

        Returns:
            Tuple of (group item, parent QModelIndex for the group).
        """
        if name not in self._groups:
            label = self._GROUP_LABELS.get(name, name.replace("_", " ").title())
            group = DocumentTreeItem(name=label)
            row = self._root.child_count()
            self.beginInsertRows(QModelIndex(), row, row)
            self._root.append_child(group)
            self._groups[name] = group
            self._group_order.append(name)
            self.endInsertRows()
        group = self._groups[name]
        row = self._group_order.index(name)
        return group, self.index(row, 0, QModelIndex())

    def clear(self) -> None:
        """Clear all documents and groups."""
        self.beginResetModel()
        self._root._children.clear()
        self._groups.clear()
        self._group_order.clear()
        self.endResetModel()

    @staticmethod
    def _display_name(name: str, doc: dict[str, Any]) -> str:
        """Build a short display label for a document."""
        if name == "start":
            return doc.get("plan_name", "run")
        if name == "descriptor":
            return f"stream: {doc.get('name', 'primary')}"
        if name == "event":
            return f"event {doc.get('seq_num', 0)}"
        if name == "stop":
            return f"status: {doc.get('exit_status', 'unknown')}"
        if name == "resource":
            return doc.get("spec", doc.get("mimetype", "resource"))
        if name == "datum":
            return str(doc.get("datum_id", "datum"))
        if name == "stream_resource":
            return doc.get("data_key", doc.get("mimetype", "stream_resource"))
        if name == "stream_datum":
            indices = doc.get("indices", {})
            return f"[{indices.get('start', '?')}:{indices.get('stop', '?')}]"
        # Fallback for any unknown document type
        return doc.get("uid", name)[:12] if "uid" in doc else name

    def doc_consumer(self, name: str, doc: dict[str, Any]) -> None:
        """Callback for Engine document stream.

        Args:
            name: Document type.
            doc: Document data.
        """
        group, parent_index = self._get_or_create_group(name)
        item = DocumentTreeItem(
            data=doc, name=self._display_name(name, doc)
        )

        row = group.child_count()
        self.beginInsertRows(parent_index, row, row)
        group.append_child(item)
        self.endInsertRows()

    # === QAbstractItemModel implementation ===

    def index(
        self, row: int, column: int, parent: QModelIndex | None = None
    ) -> QModelIndex:
        """Get index for item.

        Args:
            row: Row.
            column: Column.
            parent: Parent index.

        Returns:
            Model index.
        """
        if parent is None:
            parent = QModelIndex()
        if not self.hasIndex(row, column, parent):
            return QModelIndex()

        if not parent.isValid():
            parent_item = self._root
        else:
            parent_item = parent.internalPointer()

        child_item = parent_item.child(row)
        if child_item:
            return self.createIndex(row, column, child_item)
        return QModelIndex()

    def parent(self, index: QModelIndex) -> QModelIndex:
        """Get parent index.

        Args:
            index: Child index.

        Returns:
            Parent index.
        """
        if not index.isValid():
            return QModelIndex()

        child_item: DocumentTreeItem = index.internalPointer()
        parent_item = child_item.parent

        if parent_item is None or parent_item is self._root:
            return QModelIndex()

        return self.createIndex(parent_item.row(), 0, parent_item)

    def rowCount(self, parent: QModelIndex | None = None) -> int:
        """Get number of rows.

        Args:
            parent: Parent index.

        Returns:
            Row count.
        """
        if parent is None:
            parent = QModelIndex()
        if parent.column() > 0:
            return 0

        if not parent.isValid():
            parent_item = self._root
        else:
            parent_item = parent.internalPointer()

        return parent_item.child_count()

    def columnCount(self, parent: QModelIndex | None = None) -> int:  # noqa: ARG002
        """Get number of columns."""
        return 3

    def data(self, index: QModelIndex, role: int = Qt.ItemDataRole.DisplayRole) -> Any:
        """Get data for index.

        Args:
            index: Model index.
            role: Data role.

        Returns:
            Data value.
        """
        if not index.isValid():
            return None

        item: DocumentTreeItem = index.internalPointer()

        if role == Qt.ItemDataRole.DisplayRole:
            return item.column_data(index.column())
        elif role == Qt.ItemDataRole.ToolTipRole:
            if item.data:
                # Show first few items from data
                preview = []
                for key, value in list(item.data.items())[:5]:
                    preview.append(f"{key}: {value!r}")
                return "\n".join(preview)
        elif role == Qt.ItemDataRole.FontRole:
            if item.parent is self._root:
                # Make group items bold
                font = QFont()
                font.setBold(True)
                return font
        elif role == Qt.ItemDataRole.UserRole:
            return item.data

        return None

    def headerData(
        self,
        section: int,
        orientation: Qt.Orientation,
        role: int = Qt.ItemDataRole.DisplayRole,
    ) -> Any:
        """Get header data.

        Args:
            section: Section (column).
            orientation: Orientation.
            role: Data role.

        Returns:
            Header value.
        """
        if orientation == Qt.Orientation.Horizontal and role == Qt.ItemDataRole.DisplayRole:
            headers = ["Type", "UID/Seq", "Time/Count"]
            if section < len(headers):
                return headers[section]
        return None

    def flags(self, index: QModelIndex) -> Qt.ItemFlag:
        """Get item flags."""
        if not index.isValid():
            return Qt.ItemFlag.NoItemFlags
        return Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable


class SequentialDocumentModel(QAbstractTableModel):
    """Table model for sequential document display.

    Shows all documents in the order they were received, without grouping.
    Uses a rolling buffer to cap memory usage during long scans.
    """

    COLUMNS = ["#", "Type", "Name", "UID", "Time", ""]  # Last column for copy button
    MAX_DOCUMENTS = 10000  # Rolling buffer limit

    def __init__(self, parent=None) -> None:
        """Initialize the model."""
        super().__init__(parent)
        self._documents: deque[tuple[str, dict[str, Any]]] = deque(maxlen=self.MAX_DOCUMENTS)
        self._base_index = 0  # Track dropped docs for row numbering

    def clear(self) -> None:
        """Clear all documents."""
        self.beginResetModel()
        self._documents.clear()
        self._base_index = 0
        self.endResetModel()

    def add_document(self, name: str, doc: dict[str, Any]) -> None:
        """Add a document to the model.

        Args:
            name: Document type.
            doc: Document data.
        """
        row = len(self._documents)
        self.beginInsertRows(QModelIndex(), row, row)
        if len(self._documents) == self.MAX_DOCUMENTS:
            self._base_index += 1
        self._documents.append((name, doc))
        self.endInsertRows()

    def add_documents_batch(self, documents: list[tuple[str, dict[str, Any]]]) -> None:
        """Add multiple documents efficiently.

        Args:
            documents: List of (doc_type, doc) tuples.
        """
        if not documents:
            return
        self.beginResetModel()
        for name, doc in documents:
            if len(self._documents) == self.MAX_DOCUMENTS:
                self._base_index += 1
            self._documents.append((name, doc))
        self.endResetModel()

    def get_document(self, row: int) -> tuple[str, dict[str, Any]] | None:
        """Get document at row.

        Args:
            row: Row index.

        Returns:
            Tuple of (doc_type, doc) or None.
        """
        if 0 <= row < len(self._documents):
            return self._documents[row]
        return None

    def rowCount(self, parent: QModelIndex | None = None) -> int:
        """Get number of rows."""
        if parent is None:
            parent = QModelIndex()
        if parent.isValid():
            return 0
        return len(self._documents)

    def columnCount(self, parent: QModelIndex | None = None) -> int:  # noqa: ARG002
        """Get number of columns."""
        return len(self.COLUMNS)

    def data(self, index: QModelIndex, role: int = Qt.ItemDataRole.DisplayRole) -> Any:
        """Get data for index."""
        if not index.isValid():
            return None

        row = index.row()
        col = index.column()

        if row >= len(self._documents):
            return None

        doc_type, doc = self._documents[row]

        if role == Qt.ItemDataRole.DisplayRole:
            if col == 0:  # Row number (accounts for dropped docs in rolling buffer)
                return str(self._base_index + row + 1)
            elif col == 1:  # Type
                return doc_type.capitalize()
            elif col == 2:  # Name
                return DocumentStreamModel._display_name(doc_type, doc)
            elif col == 3:  # UID
                uid = doc.get("uid", "")
                return uid[:8] if uid else ""
            elif col == 4:  # Time
                if "time" in doc:
                    ts = datetime.fromtimestamp(doc["time"])
                    return ts.strftime("%H:%M:%S.%f")[:-3]
                return ""
            elif col == 5:  # Copy button column - empty text
                return ""
        elif role == Qt.ItemDataRole.ToolTipRole:
            if col == 3:  # Full UID on hover
                return doc.get("uid", "")
            elif col == 5:  # Copy button tooltip
                return "Copy UID to clipboard"
        elif role == Qt.ItemDataRole.UserRole:
            return (doc_type, doc)
        elif role == Qt.ItemDataRole.FontRole:
            if doc_type in ("start", "stop"):
                font = QFont()
                font.setBold(True)
                return font

        return None

    def headerData(
        self,
        section: int,
        orientation: Qt.Orientation,
        role: int = Qt.ItemDataRole.DisplayRole,
    ) -> Any:
        """Get header data."""
        if orientation == Qt.Orientation.Horizontal and role == Qt.ItemDataRole.DisplayRole:
            if section < len(self.COLUMNS):
                return self.COLUMNS[section]
        return None

    def flags(self, index: QModelIndex) -> Qt.ItemFlag:
        """Get item flags."""
        if not index.isValid():
            return Qt.ItemFlag.NoItemFlags
        return Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable


class DocumentDetailDialog(QDialog):
    """Dialog for displaying document content in detail."""

    def __init__(
        self,
        doc_type: str,
        doc: dict[str, Any],
        parent: QWidget | None = None,
    ) -> None:
        """Initialize the dialog.

        Args:
            doc_type: Document type (start, descriptor, event, stop).
            doc: Document data dictionary.
            parent: Parent widget.
        """
        super().__init__(parent)
        self._doc_type = doc_type
        self._doc = doc
        self._setup_ui()

    def _setup_ui(self) -> None:
        """Set up the dialog UI."""
        self.setWindowTitle(f"Document: {self._doc_type.capitalize()}")
        self.setMinimumSize(600, 400)

        layout = QVBoxLayout(self)

        # Header with basic info
        header_layout = QHBoxLayout()

        uid = self._doc.get("uid", "N/A")
        header_layout.addWidget(QLabel(f"Type: {self._doc_type.capitalize()}"))
        header_layout.addWidget(QLabel(f"UID: {uid[:16]}..." if len(uid) > 16 else f"UID: {uid}"))

        if "time" in self._doc:
            ts = datetime.fromtimestamp(self._doc["time"])
            header_layout.addWidget(QLabel(f"Time: {ts.strftime('%Y-%m-%d %H:%M:%S.%f')[:-3]}"))

        header_layout.addStretch()

        copy_uid_btn = QPushButton(qta.icon("fa6.copy"), "")
        copy_uid_btn.setToolTip("Copy UID")
        copy_uid_btn.clicked.connect(self._copy_uid)
        header_layout.addWidget(copy_uid_btn)

        layout.addLayout(header_layout)

        # Document content as formatted JSON
        self._text_edit = QPlainTextEdit()
        self._text_edit.setReadOnly(True)
        self._text_edit.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)

        # Format document as JSON
        try:
            formatted = json.dumps(self._doc, indent=2, default=str)
        except Exception:
            formatted = str(self._doc)

        self._text_edit.setPlainText(formatted)
        layout.addWidget(self._text_edit)

        # Button row
        button_layout = QHBoxLayout()
        button_layout.addStretch()

        copy_all_btn = QPushButton(qta.icon("fa6.copy"), "Copy All")
        copy_all_btn.clicked.connect(self._copy_all)
        button_layout.addWidget(copy_all_btn)

        close_btn = QPushButton("Close")
        close_btn.clicked.connect(self.accept)
        button_layout.addWidget(close_btn)

        layout.addLayout(button_layout)

    @Slot()
    def _copy_uid(self) -> None:
        """Copy UID to clipboard."""
        uid = self._doc.get("uid", "")
        if uid:
            QApplication.clipboard().setText(uid)

    @Slot()
    def _copy_all(self) -> None:
        """Copy entire document to clipboard."""
        try:
            text = json.dumps(self._doc, indent=2, default=str)
        except Exception:
            text = str(self._doc)
        QApplication.clipboard().setText(text)


class CopyButtonDelegate(QStyledItemDelegate):
    """Delegate that paints copy icon - much faster than setIndexWidget().

    Instead of creating a QPushButton widget for every row, this delegate
    paints an icon directly and handles clicks via editorEvent.
    """

    copy_clicked = Signal(int)  # row

    def __init__(self, parent: QWidget | None = None) -> None:
        """Initialize the delegate."""
        super().__init__(parent)
        self._icon = qta.icon("fa6.copy")
        self._icon_size = 16

    def paint(
        self, painter: QPainter, option: QStyleOptionViewItem, index: QModelIndex
    ) -> None:
        """Paint the copy icon centered in the cell."""
        painter.save()
        if option.state & QStyle.StateFlag.State_Selected:
            painter.fillRect(option.rect, option.palette.highlight())
        icon_rect = option.rect.adjusted(
            (option.rect.width() - self._icon_size) // 2,
            (option.rect.height() - self._icon_size) // 2,
            -(option.rect.width() - self._icon_size) // 2,
            -(option.rect.height() - self._icon_size) // 2,
        )
        self._icon.paint(painter, icon_rect)
        painter.restore()

    def editorEvent(
        self,
        event: QEvent,
        model: QAbstractItemModel,
        option: QStyleOptionViewItem,
        index: QModelIndex,
    ) -> bool:
        """Handle mouse click on the copy icon."""
        if event.type() == QEvent.Type.MouseButtonRelease:
            self.copy_clicked.emit(index.row())
            return True
        return False

    def sizeHint(self, option: QStyleOptionViewItem, index: QModelIndex) -> QSize:
        """Return size hint for the cell."""
        return QSize(32, 24)


class DocumentStreamWidget(QWidget):
    """Widget for viewing streaming documents from an Engine.

    Shows documents either grouped by type (tree view) or in sequential order
    (table view). Supports auto-scrolling to latest, copying UIDs via button,
    and viewing document details in a dialog.

    Uses rate-limiting to prevent UI freezes during high-frequency scans:
    - Documents are batched and processed every 50ms (20 Hz max)
    - Copy buttons use a delegate (no per-row widget creation)
    - Documents capped at MAX_DOCUMENTS (rolling buffer)

    Signals:
        document_selected(str, dict): Emitted when a document is selected.

    Example:
        >>> from lightfall.acquire import get_engine
        >>> engine = get_engine()
        >>> widget = DocumentStreamWidget()
        >>> widget.set_engine(engine)
    """

    document_selected = Signal(str, dict)  # (doc_type, doc)

    # Rate limiting constants
    UPDATE_INTERVAL_MS = 50  # 20 Hz max update rate
    BATCH_SIZE = 100  # Max documents per update cycle

    def __init__(self, parent: QWidget | None = None) -> None:
        """Initialize the widget.

        Args:
            parent: Optional parent widget.
        """
        super().__init__(parent)
        self._engine: Engine | None = None
        self._auto_scroll = True
        self._view_mode = "tree"  # "tree" or "sequential"

        # Rate limiting: queue documents and process in batches
        self._pending_updates: list[tuple[str, dict]] = []
        self._update_timer = QTimer(self)
        self._update_timer.setInterval(self.UPDATE_INTERVAL_MS)
        self._update_timer.timeout.connect(self._process_pending_updates)

        self._setup_ui()

    def _setup_ui(self) -> None:
        """Set up the user interface."""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)

        # View / auto-scroll / clear controls live in the owning panel's
        # title bar (see DocumentsPanel), wired to the slots below.

        # Stacked widget for view modes
        self._stack = QStackedWidget()

        # Tree view (grouped by type)
        self._tree_view = QTreeView()
        self._tree_view.setSelectionBehavior(QTreeView.SelectionBehavior.SelectRows)
        self._tree_view.setAlternatingRowColors(True)
        self._tree_view.setRootIsDecorated(True)
        self._tree_view.setUniformRowHeights(True)

        self._tree_model = DocumentStreamModel()
        self._tree_view.setModel(self._tree_model)

        # Configure tree columns
        tree_header = self._tree_view.header()
        tree_header.setStretchLastSection(True)
        tree_header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        tree_header.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)

        # Connect tree signals
        self._tree_view.clicked.connect(self._on_tree_item_clicked)

        self._stack.addWidget(self._tree_view)

        # Sequential table view
        self._table_view = QTableView()
        self._table_view.setAlternatingRowColors(True)
        self._table_view.setSelectionBehavior(QTableView.SelectionBehavior.SelectRows)
        self._table_view.setSelectionMode(QTableView.SelectionMode.SingleSelection)
        self._table_view.verticalHeader().setVisible(False)

        self._sequential_model = SequentialDocumentModel()
        self._table_view.setModel(self._sequential_model)

        # Configure table columns
        table_header = self._table_view.horizontalHeader()
        table_header.setStretchLastSection(False)
        table_header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)  # #
        table_header.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)  # Type
        table_header.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)  # Name
        table_header.setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)  # UID
        table_header.setSectionResizeMode(4, QHeaderView.ResizeMode.ResizeToContents)  # Time
        table_header.setSectionResizeMode(5, QHeaderView.ResizeMode.Fixed)  # Copy button
        self._table_view.setColumnWidth(5, 40)

        # Connect table signals
        self._table_view.clicked.connect(self._on_table_item_clicked)

        # Use delegate for copy buttons (faster than setIndexWidget per row)
        self._copy_delegate = CopyButtonDelegate(self._table_view)
        self._copy_delegate.copy_clicked.connect(self._copy_uid_for_row)
        self._table_view.setItemDelegateForColumn(5, self._copy_delegate)

        self._stack.addWidget(self._table_view)

        layout.addWidget(self._stack)

        # Status bar
        self._status_label = QLabel("Ready")
        layout.addWidget(self._status_label)

    def _copy_uid_for_row(self, row: int) -> None:
        """Copy UID for a specific row to clipboard.

        Args:
            row: Row index.
        """
        doc_data = self._sequential_model.get_document(row)
        if doc_data:
            _, doc = doc_data
            uid = doc.get("uid", "")
            if uid:
                QApplication.clipboard().setText(uid)
                self._status_label.setText(f"Copied UID: {uid[:8]}...")
                logger.debug(f"Copied UID to clipboard: {uid}")

    def set_engine(self, engine: Engine) -> None:
        """Connect to an Engine instance.

        Args:
            engine: The Engine to monitor.
        """
        if self._engine is not None:
            try:
                self._engine.sigOutput.disconnect(self._on_document)
                self._engine.sigStart.disconnect(self._on_run_start)
                self._engine.sigFinish.disconnect(self._on_run_finish)
            except RuntimeError:
                pass

        self._engine = engine
        engine.sigOutput.connect(self._on_document)
        engine.sigStart.connect(self._on_run_start)
        engine.sigFinish.connect(self._on_run_finish)

    def set_run_engine(self, re: Engine) -> None:
        """Connect to an Engine instance.

        Deprecated: Use set_engine() instead.

        Args:
            re: The Engine to monitor.
        """
        self.set_engine(re)

    def clear(self) -> None:
        """Clear all documents and pending updates."""
        self._pending_updates.clear()
        self._update_timer.stop()
        self._tree_model.clear()
        self._sequential_model.clear()
        self._status_label.setText("Cleared")

    # === Slots ===

    @Slot()
    def _toggle_view_mode(self) -> None:
        """Toggle between tree and sequential view modes."""
        if self._view_mode == "tree":
            self._view_mode = "sequential"
            self._stack.setCurrentWidget(self._table_view)
        else:
            self._view_mode = "tree"
            self._stack.setCurrentWidget(self._tree_view)

    @Slot(bool)
    def _toggle_auto_scroll(self, enabled: bool) -> None:
        """Set auto-scroll mode.

        Args:
            enabled: Whether auto-scroll is on (from the title bar toggle).
        """
        self._auto_scroll = enabled

    @Slot()
    def _on_clear_clicked(self) -> None:
        """Handle clear button click."""
        self.clear()

    @Slot(str, dict)
    def _on_document(self, name: str, doc: dict) -> None:
        """Queue document for batched processing.

        Instead of updating models immediately (which causes UI freezes
        during high-frequency scans), documents are queued and processed
        in batches by the update timer.

        Args:
            name: Document type.
            doc: Document data.
        """
        self._pending_updates.append((name, doc))
        if not self._update_timer.isActive():
            self._update_timer.start()

    def _process_pending_updates(self) -> None:
        """Process pending documents in batches.

        Called by the update timer at UPDATE_INTERVAL_MS intervals.
        Processes up to BATCH_SIZE documents per call, updates UI
        elements once per batch (not per document).
        """
        if not self._pending_updates:
            self._update_timer.stop()
            return

        # Take up to BATCH_SIZE documents from the queue
        to_process = self._pending_updates[: self.BATCH_SIZE]
        self._pending_updates = self._pending_updates[self.BATCH_SIZE :]

        # Batch update tree model (still per-doc, but at throttled rate)
        for name, doc in to_process:
            self._tree_model.doc_consumer(name, doc)

        # Batch update sequential model
        self._sequential_model.add_documents_batch(to_process)

        # Update status once per batch (not per document)
        if to_process:
            last_name, last_doc = to_process[-1]
            total = len(self._sequential_model._documents)
            if last_name == "start":
                self._status_label.setText(
                    f"Running: {last_doc.get('plan_name', 'unknown')}"
                )
            elif last_name == "event":
                self._status_label.setText(
                    f"Event {last_doc.get('seq_num', 0)} ({total} total docs)"
                )
            else:
                self._status_label.setText(
                    f"{last_name} ({total} total docs)"
                )

        # Auto-scroll once per batch (not per document)
        if self._auto_scroll:
            if self._view_mode == "tree":
                self._expand_events_group()
            else:
                last_row = self._sequential_model.rowCount() - 1
                if last_row >= 0:
                    self._table_view.scrollTo(self._sequential_model.index(last_row, 0))

        # Stop timer if queue is empty
        if not self._pending_updates:
            self._update_timer.stop()

    def _expand_events_group(self) -> None:
        """Expand only the Events group and scroll to the last event.

        More efficient than expandAll() which traverses the entire tree.
        """
        if "event" not in self._tree_model._group_order:
            return
        row = self._tree_model._group_order.index("event")
        events_index = self._tree_model.index(row, 0, QModelIndex())
        if events_index.isValid():
            self._tree_view.expand(events_index)
            last_row = self._tree_model.rowCount(events_index) - 1
            if last_row >= 0:
                last_event_index = self._tree_model.index(last_row, 0, events_index)
                self._tree_view.scrollTo(last_event_index)

    @Slot()
    def _on_run_start(self) -> None:
        """Handle run start."""
        self._pending_updates.clear()
        self._update_timer.stop()
        self._tree_model.clear()
        self._sequential_model.clear()
        # Expand all groups at start (lightweight when empty)
        self._tree_view.expandAll()

    @Slot()
    def _on_run_finish(self) -> None:
        """Handle run finish."""
        # Process any remaining pending updates immediately
        while self._pending_updates:
            self._process_pending_updates()
        self._status_label.setText("Run complete")

    @Slot(QModelIndex)
    def _on_tree_item_clicked(self, index: QModelIndex) -> None:
        """Handle tree item click - show document detail dialog.

        Args:
            index: Clicked index.
        """
        doc = index.data(Qt.ItemDataRole.UserRole)
        if doc:
            parent = index.parent()
            if not parent.isValid():
                # Clicked on group header, not a document
                return

            # Look up doc type from group order by parent row
            parent_row = parent.row()
            order = self._tree_model._group_order
            doc_type = order[parent_row] if parent_row < len(order) else "unknown"

            self.document_selected.emit(doc_type, doc)
            self._show_document_dialog(doc_type, doc)

    @Slot(QModelIndex)
    def _on_table_item_clicked(self, index: QModelIndex) -> None:
        """Handle table item click - show document detail dialog.

        Args:
            index: Clicked index.
        """
        # Ignore clicks on the copy button column
        if index.column() == 5:
            return

        doc_data = index.data(Qt.ItemDataRole.UserRole)
        if doc_data:
            doc_type, doc = doc_data
            self.document_selected.emit(doc_type, doc)
            self._show_document_dialog(doc_type, doc)

    def _show_document_dialog(self, doc_type: str, doc: dict[str, Any]) -> None:
        """Show a dialog with document details.

        Args:
            doc_type: Document type.
            doc: Document data.
        """
        dialog = DocumentDetailDialog(doc_type, doc, self)
        dialog.exec()
