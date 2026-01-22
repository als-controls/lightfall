"""Document stream widget for viewing Bluesky documents.

Displays Bluesky documents as they stream during a run, grouped
by document type with expandable details, or in sequential order.
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import TYPE_CHECKING, Any

from loguru import logger
from PySide6.QtCore import (
    QAbstractItemModel,
    QAbstractTableModel,
    QModelIndex,
    Qt,
    Signal,
    Slot,
)
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QStackedWidget,
    QTableView,
    QTreeView,
    QVBoxLayout,
    QWidget,
)

if TYPE_CHECKING:
    from ncs.acquire import QRunEngine


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

    Groups documents by type (start, descriptor, event, stop).
    """

    def __init__(self, parent=None) -> None:
        """Initialize the model."""
        super().__init__(parent)
        self._root = DocumentTreeItem(name="Documents")

        # Create group items
        self._groups = {
            "start": DocumentTreeItem(name="Start"),
            "descriptor": DocumentTreeItem(name="Descriptors"),
            "event": DocumentTreeItem(name="Events"),
            "stop": DocumentTreeItem(name="Stop"),
        }

        for group in self._groups.values():
            self._root.append_child(group)

    def clear(self) -> None:
        """Clear all documents."""
        self.beginResetModel()
        for group in self._groups.values():
            group._children.clear()
        self.endResetModel()

    def doc_consumer(self, name: str, doc: dict[str, Any]) -> None:
        """Callback for QRunEngine document stream.

        Args:
            name: Document type.
            doc: Document data.
        """
        group = self._groups.get(name)
        if group is None:
            return

        # Create display name for the document
        if name == "start":
            plan_name = doc.get("plan_name", "run")
            display_name = f"{plan_name}"
        elif name == "descriptor":
            stream = doc.get("name", "primary")
            display_name = f"stream: {stream}"
        elif name == "event":
            seq_num = doc.get("seq_num", 0)
            display_name = f"event {seq_num}"
        elif name == "stop":
            status = doc.get("exit_status", "unknown")
            display_name = f"status: {status}"
        else:
            display_name = name

        item = DocumentTreeItem(data=doc, name=display_name)

        # Insert the new item
        parent_index = self.index(list(self._groups.keys()).index(name), 0, QModelIndex())
        row = group.child_count()

        self.beginInsertRows(parent_index, row, row)
        group.append_child(item)
        self.endInsertRows()

    # === QAbstractItemModel implementation ===

    def index(
        self, row: int, column: int, parent: QModelIndex = QModelIndex()
    ) -> QModelIndex:
        """Get index for item.

        Args:
            row: Row.
            column: Column.
            parent: Parent index.

        Returns:
            Model index.
        """
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

    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:
        """Get number of rows.

        Args:
            parent: Parent index.

        Returns:
            Row count.
        """
        if parent.column() > 0:
            return 0

        if not parent.isValid():
            parent_item = self._root
        else:
            parent_item = parent.internalPointer()

        return parent_item.child_count()

    def columnCount(self, parent: QModelIndex = QModelIndex()) -> int:
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
    """

    COLUMNS = ["#", "Type", "Name", "UID", "Time", ""]  # Last column for copy button

    def __init__(self, parent=None) -> None:
        """Initialize the model."""
        super().__init__(parent)
        self._documents: list[tuple[str, dict[str, Any]]] = []

    def clear(self) -> None:
        """Clear all documents."""
        self.beginResetModel()
        self._documents.clear()
        self.endResetModel()

    def add_document(self, name: str, doc: dict[str, Any]) -> None:
        """Add a document to the model.

        Args:
            name: Document type.
            doc: Document data.
        """
        row = len(self._documents)
        self.beginInsertRows(QModelIndex(), row, row)
        self._documents.append((name, doc))
        self.endInsertRows()

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

    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:
        """Get number of rows."""
        if parent.isValid():
            return 0
        return len(self._documents)

    def columnCount(self, parent: QModelIndex = QModelIndex()) -> int:
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
            if col == 0:  # Row number
                return str(row + 1)
            elif col == 1:  # Type
                return doc_type.capitalize()
            elif col == 2:  # Name
                if doc_type == "start":
                    return doc.get("plan_name", "")
                elif doc_type == "descriptor":
                    return doc.get("name", "primary")
                elif doc_type == "event":
                    return f"seq #{doc.get('seq_num', 0)}"
                elif doc_type == "stop":
                    return doc.get("exit_status", "")
                return ""
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

        copy_uid_btn = QPushButton("Copy UID")
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

        copy_all_btn = QPushButton("Copy All")
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


class CopyButtonDelegate(QWidget):
    """Widget delegate for copy button in table view."""

    copy_clicked = Signal(int)  # row

    def __init__(self, parent: QWidget | None = None) -> None:
        """Initialize the delegate."""
        super().__init__(parent)


class DocumentStreamWidget(QWidget):
    """Widget for viewing streaming Bluesky documents.

    Shows documents either grouped by type (tree view) or in sequential order
    (table view). Supports auto-scrolling to latest, copying UIDs via button,
    and viewing document details in a dialog.

    Signals:
        document_selected(str, dict): Emitted when a document is selected.

    Example:
        >>> from ncs.acquire import get_run_engine
        >>> RE = get_run_engine()
        >>> widget = DocumentStreamWidget()
        >>> widget.set_run_engine(RE)
    """

    document_selected = Signal(str, dict)  # (doc_type, doc)

    def __init__(self, parent: QWidget | None = None) -> None:
        """Initialize the widget.

        Args:
            parent: Optional parent widget.
        """
        super().__init__(parent)
        self._re: QRunEngine | None = None
        self._auto_scroll = True
        self._view_mode = "tree"  # "tree" or "sequential"
        self._setup_ui()

    def _setup_ui(self) -> None:
        """Set up the user interface."""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)

        # Header with controls
        header = QHBoxLayout()

        header.addWidget(QLabel("Document Stream"))
        header.addStretch()

        # View mode toggle
        self._view_mode_btn = QPushButton("View: Tree")
        self._view_mode_btn.setCheckable(True)
        self._view_mode_btn.clicked.connect(self._toggle_view_mode)
        header.addWidget(self._view_mode_btn)

        self._auto_scroll_btn = QPushButton("Auto-scroll: On")
        self._auto_scroll_btn.setCheckable(True)
        self._auto_scroll_btn.setChecked(True)
        self._auto_scroll_btn.clicked.connect(self._toggle_auto_scroll)
        header.addWidget(self._auto_scroll_btn)

        self._clear_btn = QPushButton("Clear")
        self._clear_btn.clicked.connect(self._on_clear_clicked)
        header.addWidget(self._clear_btn)

        layout.addLayout(header)

        # Stacked widget for view modes
        self._stack = QStackedWidget()

        # Tree view (grouped by type)
        self._tree_view = QTreeView()
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
        self._table_view.setColumnWidth(5, 60)

        # Connect table signals
        self._table_view.clicked.connect(self._on_table_item_clicked)

        self._stack.addWidget(self._table_view)

        layout.addWidget(self._stack)

        # Status bar
        self._status_label = QLabel("Ready")
        layout.addWidget(self._status_label)

        # Set up copy buttons for existing rows
        self._setup_copy_buttons()

    def _setup_copy_buttons(self) -> None:
        """Set up copy buttons for existing rows in the table view."""
        # Connect model signals to add copy buttons for new rows
        self._sequential_model.rowsInserted.connect(self._on_rows_inserted)

    @Slot(QModelIndex, int, int)
    def _on_rows_inserted(self, parent: QModelIndex, first: int, last: int) -> None:
        """Add copy buttons for newly inserted rows."""
        for row in range(first, last + 1):
            self._add_copy_button_to_row(row)

    def _add_copy_button_to_row(self, row: int) -> None:
        """Add a copy button to a specific row.

        Args:
            row: Row index.
        """
        copy_btn = QPushButton("Copy")
        copy_btn.setMaximumWidth(50)
        copy_btn.clicked.connect(lambda checked, r=row: self._copy_uid_for_row(r))
        index = self._sequential_model.index(row, 5)
        self._table_view.setIndexWidget(index, copy_btn)

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

    def set_run_engine(self, re: QRunEngine) -> None:
        """Connect to a QRunEngine instance.

        Args:
            re: The QRunEngine to monitor.
        """
        if self._re is not None:
            try:
                self._re.sigDocumentYield.disconnect(self._on_document)
                self._re.sigStart.disconnect(self._on_run_start)
                self._re.sigFinish.disconnect(self._on_run_finish)
            except RuntimeError:
                pass

        self._re = re
        re.sigDocumentYield.connect(self._on_document)
        re.sigStart.connect(self._on_run_start)
        re.sigFinish.connect(self._on_run_finish)

    def clear(self) -> None:
        """Clear all documents."""
        self._tree_model.clear()
        self._sequential_model.clear()
        self._status_label.setText("Cleared")

    # === Slots ===

    @Slot()
    def _toggle_view_mode(self) -> None:
        """Toggle between tree and sequential view modes."""
        if self._view_mode == "tree":
            self._view_mode = "sequential"
            self._view_mode_btn.setText("View: Sequential")
            self._stack.setCurrentWidget(self._table_view)
        else:
            self._view_mode = "tree"
            self._view_mode_btn.setText("View: Tree")
            self._stack.setCurrentWidget(self._tree_view)

    @Slot()
    def _toggle_auto_scroll(self) -> None:
        """Toggle auto-scroll mode."""
        self._auto_scroll = self._auto_scroll_btn.isChecked()
        text = "Auto-scroll: On" if self._auto_scroll else "Auto-scroll: Off"
        self._auto_scroll_btn.setText(text)

    @Slot()
    def _on_clear_clicked(self) -> None:
        """Handle clear button click."""
        self.clear()

    @Slot(str, dict)
    def _on_document(self, name: str, doc: dict) -> None:
        """Handle document from RunEngine.

        Args:
            name: Document type.
            doc: Document data.
        """
        # Add to both models
        self._tree_model.doc_consumer(name, doc)
        self._sequential_model.add_document(name, doc)

        # Update status
        if name == "start":
            plan = doc.get("plan_name", "unknown")
            self._status_label.setText(f"Running: {plan}")
        elif name == "event":
            seq = doc.get("seq_num", 0)
            self._status_label.setText(f"Event {seq}")

        # Auto-scroll to latest
        if self._auto_scroll:
            if self._view_mode == "tree":
                self._tree_view.expandAll()
            else:
                # Scroll to last row in table view
                last_row = self._sequential_model.rowCount() - 1
                if last_row >= 0:
                    index = self._sequential_model.index(last_row, 0)
                    self._table_view.scrollTo(index)

    @Slot()
    def _on_run_start(self) -> None:
        """Handle run start."""
        self._tree_model.clear()
        self._sequential_model.clear()
        self._tree_view.expandAll()

    @Slot()
    def _on_run_finish(self) -> None:
        """Handle run finish."""
        self._status_label.setText("Run complete")

    @Slot(QModelIndex)
    def _on_tree_item_clicked(self, index: QModelIndex) -> None:
        """Handle tree item click - show document detail dialog.

        Args:
            index: Clicked index.
        """
        doc = index.data(Qt.ItemDataRole.UserRole)
        if doc:
            # Determine doc type from parent
            parent = index.parent()
            if parent.isValid():
                parent_item: DocumentTreeItem = parent.internalPointer()
                doc_type = parent_item.name.lower()
                # Remove trailing 's' from plural group names
                if doc_type.endswith("s"):
                    doc_type = doc_type[:-1]
                if doc_type == "descriptor":
                    doc_type = "descriptor"
            else:
                # Clicked on group header, not a document
                return

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
