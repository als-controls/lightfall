"""Document stream widget for viewing Bluesky documents.

Displays Bluesky documents as they stream during a run, grouped
by document type with expandable details.
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Any

from loguru import logger
from PySide6.QtCore import QAbstractItemModel, QModelIndex, Qt, Signal, Slot
from PySide6.QtGui import QClipboard, QFont
from PySide6.QtWidgets import (
    QApplication,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPushButton,
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


class DocumentStreamWidget(QWidget):
    """Tree view of streaming Bluesky documents.

    Shows documents grouped by type (start, descriptor, event, stop)
    with expandable details. Supports auto-scrolling to latest and
    copying UIDs to clipboard.

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
        self._setup_ui()

    def _setup_ui(self) -> None:
        """Set up the user interface."""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)

        # Header with controls
        header = QHBoxLayout()

        header.addWidget(QLabel("Document Stream"))
        header.addStretch()

        self._auto_scroll_btn = QPushButton("Auto-scroll: On")
        self._auto_scroll_btn.setCheckable(True)
        self._auto_scroll_btn.setChecked(True)
        self._auto_scroll_btn.clicked.connect(self._toggle_auto_scroll)
        header.addWidget(self._auto_scroll_btn)

        self._clear_btn = QPushButton("Clear")
        self._clear_btn.clicked.connect(self._on_clear_clicked)
        header.addWidget(self._clear_btn)

        layout.addLayout(header)

        # Tree view
        self._tree_view = QTreeView()
        self._tree_view.setAlternatingRowColors(True)
        self._tree_view.setRootIsDecorated(True)
        self._tree_view.setUniformRowHeights(True)

        self._model = DocumentStreamModel()
        self._tree_view.setModel(self._model)

        # Configure columns
        header_view = self._tree_view.header()
        header_view.setStretchLastSection(True)
        header_view.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        header_view.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)

        # Connect signals
        self._tree_view.clicked.connect(self._on_item_clicked)
        self._tree_view.doubleClicked.connect(self._on_item_double_clicked)

        layout.addWidget(self._tree_view)

        # Status bar
        self._status_label = QLabel("Ready")
        layout.addWidget(self._status_label)

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
        self._model.clear()
        self._status_label.setText("Cleared")

    # === Slots ===

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
        self._model.doc_consumer(name, doc)

        # Update status
        if name == "start":
            plan = doc.get("plan_name", "unknown")
            self._status_label.setText(f"Running: {plan}")
        elif name == "event":
            seq = doc.get("seq_num", 0)
            self._status_label.setText(f"Event {seq}")

        # Auto-scroll to latest
        if self._auto_scroll:
            # Expand event group and scroll to last item
            self._tree_view.expandAll()

    @Slot()
    def _on_run_start(self) -> None:
        """Handle run start."""
        self._model.clear()
        self._tree_view.expandAll()

    @Slot()
    def _on_run_finish(self) -> None:
        """Handle run finish."""
        self._status_label.setText("Run complete")

    @Slot(QModelIndex)
    def _on_item_clicked(self, index: QModelIndex) -> None:
        """Handle item click.

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
            else:
                item: DocumentTreeItem = index.internalPointer()
                doc_type = item.name.lower()

            self.document_selected.emit(doc_type, doc)

    @Slot(QModelIndex)
    def _on_item_double_clicked(self, index: QModelIndex) -> None:
        """Handle item double-click (copy UID).

        Args:
            index: Double-clicked index.
        """
        doc = index.data(Qt.ItemDataRole.UserRole)
        if doc and "uid" in doc:
            uid = doc["uid"]
            clipboard = QApplication.clipboard()
            clipboard.setText(uid)
            self._status_label.setText(f"Copied UID: {uid[:8]}...")
            logger.debug(f"Copied UID to clipboard: {uid}")
