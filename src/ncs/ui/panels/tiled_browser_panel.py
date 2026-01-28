"""Tiled data browser panel for NCS.

Provides a panel for browsing and searching data stored in a Tiled server,
with filtering, pagination, and record selection.
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Any, ClassVar

from PySide6.QtCore import Qt, Signal, Slot
from PySide6.QtWidgets import (
    QAbstractItemView,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPushButton,
    QTableView,
    QVBoxLayout,
    QWidget,
)

from ncs.services.tiled_service import TiledConnectionState, TiledService
from ncs.ui.models.tiled_model import TiledRecord, TiledRecordFilterProxy, TiledRecordModel
from ncs.ui.panels.base import BasePanel, PanelMetadata
from ncs.ui.widgets.tiled_filter_widget import TiledFilterWidget, TiledFilters
from ncs.utils.logging import logger
from ncs.utils.threads import QThreadFuture

if TYPE_CHECKING:
    pass


class TiledBrowserPanel(BasePanel):
    """Panel for browsing data stored in a Tiled server.

    TiledBrowserPanel provides:
    - Connection status display
    - Filter UI for date range, text search, plan, and status
    - Table view of records with sorting
    - Pagination for large datasets
    - Signals for record selection

    Signals:
        record_clicked: Emitted when a record is single-clicked (TiledRecord).
        record_double_clicked: Emitted when a record is double-clicked (TiledRecord).
    """

    panel_metadata: ClassVar[PanelMetadata] = PanelMetadata(
        id="ncs.panels.tiled_browser",
        name="Data Browser",
        description="Browse and search data stored in Tiled server",
        icon="database",
        category="Data",
        required_permission=None,
        singleton=True,
        closable=True,
        keywords=["tiled", "data", "browser", "catalog", "runs", "scans"],
    )

    # Signals
    record_clicked = Signal(object)  # TiledRecord on single-click
    record_double_clicked = Signal(object)  # TiledRecord on double-click

    # Page size for pagination
    PAGE_SIZE = 100

    def __init__(self, parent: QWidget | None = None) -> None:
        """Initialize the Tiled browser panel."""
        self._tiled_service = TiledService.get_instance()
        self._current_page = 0
        self._total_records = 0
        self._current_filters = TiledFilters()
        self._loading = False
        self._fetch_thread: QThreadFuture | None = None

        # Create models
        self._model = TiledRecordModel()
        self._proxy_model = TiledRecordFilterProxy()
        self._proxy_model.setSourceModel(self._model)

        super().__init__(parent)

        # Connect to TiledService signals
        self._tiled_service.connection_changed.connect(self._on_connection_changed)

        # Initial status update
        self._update_status()

    def _setup_ui(self) -> None:
        """Setup the panel UI."""
        # Main layout
        main_widget = QWidget()
        main_layout = QVBoxLayout(main_widget)
        main_layout.setContentsMargins(8, 8, 8, 8)
        main_layout.setSpacing(8)

        # Status bar at top
        status_layout = QHBoxLayout()

        self._status_label = QLabel("Status: Disconnected")
        self._status_label.setStyleSheet("font-weight: bold;")
        status_layout.addWidget(self._status_label)

        status_layout.addStretch()

        self._refresh_btn = QPushButton("Refresh")
        self._refresh_btn.clicked.connect(self._on_refresh_clicked)
        status_layout.addWidget(self._refresh_btn)

        main_layout.addLayout(status_layout)

        # Filter widget
        self._filter_widget = TiledFilterWidget()
        self._filter_widget.filters_changed.connect(self._on_filters_changed)
        main_layout.addWidget(self._filter_widget)

        # Table view
        self._table_view = QTableView()
        self._table_view.setModel(self._proxy_model)
        self._table_view.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._table_view.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self._table_view.setAlternatingRowColors(True)
        self._table_view.setSortingEnabled(True)
        self._table_view.sortByColumn(2, Qt.SortOrder.DescendingOrder)  # Sort by timestamp desc
        self._table_view.setShowGrid(False)
        self._table_view.verticalHeader().setVisible(False)

        # Configure header
        header = self._table_view.horizontalHeader()
        header.setStretchLastSection(True)
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)  # Scan ID
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Interactive)  # Plan
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)  # Timestamp
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)  # Status
        header.setSectionResizeMode(4, QHeaderView.ResizeMode.ResizeToContents)  # Points
        header.setSectionResizeMode(5, QHeaderView.ResizeMode.ResizeToContents)  # Duration
        # Sample column will stretch

        # Set default column widths
        self._table_view.setColumnWidth(1, 120)  # Plan

        # Connect click signals
        self._table_view.clicked.connect(self._on_table_clicked)
        self._table_view.doubleClicked.connect(self._on_table_double_clicked)

        main_layout.addWidget(self._table_view, stretch=1)

        # Pagination bar at bottom
        pagination_layout = QHBoxLayout()

        self._prev_btn = QPushButton("< Prev")
        self._prev_btn.clicked.connect(self._on_prev_page)
        self._prev_btn.setEnabled(False)
        pagination_layout.addWidget(self._prev_btn)

        self._page_label = QLabel("Page 1 of 1")
        pagination_layout.addWidget(self._page_label)

        self._next_btn = QPushButton("Next >")
        self._next_btn.clicked.connect(self._on_next_page)
        self._next_btn.setEnabled(False)
        pagination_layout.addWidget(self._next_btn)

        pagination_layout.addStretch()

        self._count_label = QLabel("Loaded 0 records")
        pagination_layout.addWidget(self._count_label)

        main_layout.addLayout(pagination_layout)

        self._layout.addWidget(main_widget)

    def _update_status(self) -> None:
        """Update the status display based on TiledService state."""
        state = self._tiled_service.state
        status_info = self._tiled_service.get_status_info()

        if state == TiledConnectionState.CONNECTED:
            self._status_label.setText(f"Status: Connected to {status_info['url']}")
            self._status_label.setStyleSheet("font-weight: bold; color: green;")
            self._filter_widget.set_enabled(True)
            self._refresh_btn.setEnabled(True)
        elif state == TiledConnectionState.CONNECTING:
            self._status_label.setText("Status: Connecting...")
            self._status_label.setStyleSheet("font-weight: bold; color: orange;")
            self._filter_widget.set_enabled(False)
            self._refresh_btn.setEnabled(False)
        elif state == TiledConnectionState.ERROR:
            error_msg = status_info.get("error", "Unknown error")
            self._status_label.setText(f"Status: Error - {error_msg}")
            self._status_label.setStyleSheet("font-weight: bold; color: red;")
            self._filter_widget.set_enabled(False)
            self._refresh_btn.setEnabled(True)  # Allow retry
        else:  # DISCONNECTED
            self._status_label.setText("Status: Disconnected")
            self._status_label.setStyleSheet("font-weight: bold;")
            self._filter_widget.set_enabled(False)
            self._refresh_btn.setEnabled(False)

    def _update_pagination(self) -> None:
        """Update pagination controls based on current state."""
        if self._total_records == 0:
            self._page_label.setText("Page 0 of 0")
            self._prev_btn.setEnabled(False)
            self._next_btn.setEnabled(False)
            self._count_label.setText("Loaded 0 records")
            return

        total_pages = max(1, (self._total_records + self.PAGE_SIZE - 1) // self.PAGE_SIZE)
        current_page_display = self._current_page + 1

        self._page_label.setText(f"Page {current_page_display} of {total_pages}")
        self._prev_btn.setEnabled(self._current_page > 0)
        self._next_btn.setEnabled(current_page_display < total_pages)

        loaded = self._model.rowCount()
        self._count_label.setText(f"Loaded {loaded} of {self._total_records} records")

    @Slot(object, str)
    def _on_connection_changed(self, state: TiledConnectionState, message: str) -> None:
        """Handle TiledService connection state change."""
        logger.debug("Tiled connection changed: {} - {}", state.value, message)
        self._update_status()

        # Auto-load data when connected
        if state == TiledConnectionState.CONNECTED:
            self._load_data()

    @Slot(object)
    def _on_filters_changed(self, filters: TiledFilters) -> None:
        """Handle filter changes from filter widget."""
        self._current_filters = filters
        self._current_page = 0
        self._load_data()

    @Slot()
    def _on_refresh_clicked(self) -> None:
        """Handle refresh button click."""
        if self._tiled_service.state == TiledConnectionState.ERROR:
            # Try to reconnect
            self._tiled_service.connect()
        else:
            self._load_data()

    @Slot()
    def _on_prev_page(self) -> None:
        """Handle previous page button click."""
        if self._current_page > 0:
            self._current_page -= 1
            self._load_data()

    @Slot()
    def _on_next_page(self) -> None:
        """Handle next page button click."""
        total_pages = (self._total_records + self.PAGE_SIZE - 1) // self.PAGE_SIZE
        if self._current_page + 1 < total_pages:
            self._current_page += 1
            self._load_data()

    @Slot()
    def _on_table_clicked(self) -> None:
        """Handle table row click."""
        selection = self._table_view.selectionModel().selectedRows()
        if selection:
            proxy_index = selection[0]
            source_index = self._proxy_model.mapToSource(proxy_index)
            record = self._model.get_record(source_index.row())
            if record:
                self.record_clicked.emit(record)
                logger.debug("Record clicked: uid={}", record.uid)

    @Slot()
    def _on_table_double_clicked(self) -> None:
        """Handle table row double-click."""
        selection = self._table_view.selectionModel().selectedRows()
        if selection:
            proxy_index = selection[0]
            source_index = self._proxy_model.mapToSource(proxy_index)
            record = self._model.get_record(source_index.row())
            if record:
                self.record_double_clicked.emit(record)
                logger.debug("Record double-clicked: uid={}", record.uid)

    def _load_data(self) -> None:
        """Load data from Tiled server with current filters."""
        if not self._tiled_service.is_connected:
            logger.debug("Cannot load data: not connected to Tiled")
            return

        if self._loading:
            logger.debug("Load already in progress, skipping")
            return

        # Get client reference in main thread (TiledService is a QObject)
        client = self._tiled_service._client
        if client is None:
            logger.debug("Cannot load data: no Tiled client")
            return

        self._loading = True
        self._refresh_btn.setEnabled(False)
        self._status_label.setText("Loading...")

        # Capture current filter state to avoid race conditions
        filters = self._current_filters
        page = self._current_page
        page_size = self.PAGE_SIZE

        # Create and start background thread using QThreadFuture directly
        self._fetch_thread = QThreadFuture(
            self._do_fetch,
            client,
            filters,
            page,
            page_size,
            callback_slot=self._on_records_loaded,
            except_slot=self._on_load_error,
            name="tiled_fetch",
        )
        self._fetch_thread.start()

    def _do_fetch(
        self,
        client: Any,
        filters: TiledFilters,
        page: int,
        page_size: int,
    ) -> tuple[list[TiledRecord], int, list[str]]:
        """Fetch records from Tiled server (called from background thread).

        This method only uses pure Python logic and doesn't access Qt objects,
        making it safe to call from a background thread.

        Args:
            client: Tiled client instance.
            filters: Filter settings to apply.
            page: Page number (0-indexed).
            page_size: Number of records per page.

        Returns:
            Tuple of (records, total_count, plan_names).
        """
        # Build query with filters
        result = self._build_query(client, filters)

        # Get total count before pagination
        total_count = len(result)

        # Collect unique plan names for filter dropdown
        plan_names: set[str] = set()

        # Apply pagination
        start = page * page_size
        end = start + page_size

        records: list[TiledRecord] = []

        # Iterate over results with pagination
        for i, key in enumerate(result):
            if i < start:
                continue
            if i >= end:
                break

            try:
                entry = result[key]
                record = self._entry_to_record(key, entry)
                records.append(record)
                if record.plan_name:
                    plan_names.add(record.plan_name)
            except Exception as e:
                logger.warning("Failed to parse record {}: {}", key, e)
                continue

        # Also collect plan names from a broader sample for the filter dropdown
        for i, key in enumerate(result):
            if i >= 500:  # Sample first 500 for plan names
                break
            try:
                entry = result[key]
                metadata = entry.metadata
                start_doc = metadata.get("start", {})
                plan = start_doc.get("plan_name", "")
                if plan:
                    plan_names.add(plan)
            except Exception:
                pass

        return records, total_count, list(plan_names)

    def _build_query(self, client: Any, filters: TiledFilters) -> Any:
        """Build Tiled query from filters.

        Args:
            client: Tiled client instance.
            filters: Filter settings.

        Returns:
            Filtered Tiled container.
        """
        try:
            from tiled.queries import FullText, Key
        except ImportError:
            logger.warning("tiled.queries not available, returning unfiltered results")
            return client

        result = client

        # Apply time filters
        if filters.start_date:
            try:
                result = result.search(Key("time") >= filters.start_date.timestamp())
            except Exception as e:
                logger.debug("Failed to apply start_date filter: {}", e)

        if filters.end_date:
            try:
                result = result.search(Key("time") <= filters.end_date.timestamp())
            except Exception as e:
                logger.debug("Failed to apply end_date filter: {}", e)

        # Apply text search
        if filters.text_query:
            try:
                result = result.search(FullText(filters.text_query))
            except Exception as e:
                logger.debug("Failed to apply text search: {}", e)

        # Apply plan name filter
        if filters.plan_name:
            try:
                result = result.search(Key("plan_name") == filters.plan_name)
            except Exception as e:
                logger.debug("Failed to apply plan_name filter: {}", e)

        # Apply exit status filter
        if filters.exit_status:
            try:
                result = result.search(Key("exit_status") == filters.exit_status)
            except Exception as e:
                logger.debug("Failed to apply exit_status filter: {}", e)

        return result

    def _entry_to_record(self, key: str, entry: Any) -> TiledRecord:
        """Convert a Tiled entry to a TiledRecord.

        Args:
            key: Entry key in the catalog.
            entry: Tiled entry object.

        Returns:
            TiledRecord instance.
        """
        metadata = entry.metadata

        # Get start document
        start_doc = metadata.get("start", {})
        stop_doc = metadata.get("stop", {})

        # Extract fields
        uid = start_doc.get("uid", key)
        scan_id = start_doc.get("scan_id")
        plan_name = start_doc.get("plan_name", "")
        time_val = start_doc.get("time", 0)
        timestamp = datetime.fromtimestamp(time_val) if time_val else datetime.now()

        # Exit status from stop document
        exit_status = stop_doc.get("exit_status", "unknown") if stop_doc else "running"

        # Calculate duration
        duration = None
        if stop_doc and "time" in stop_doc:
            stop_time = stop_doc["time"]
            duration = stop_time - time_val

        # Number of points from stop document
        num_points = stop_doc.get("num_events", {}).get("primary", 0) if stop_doc else 0

        # Sample name from start document
        sample_name = start_doc.get("sample", {}).get("name", "") if isinstance(
            start_doc.get("sample"), dict
        ) else start_doc.get("sample_name", "")

        return TiledRecord(
            uid=uid,
            scan_id=scan_id,
            plan_name=plan_name,
            timestamp=timestamp,
            exit_status=exit_status,
            num_points=num_points,
            duration=duration,
            sample_name=sample_name,
            metadata=dict(metadata),
            _client_key=key,
        )

    @Slot(object)
    def _on_records_loaded(
        self,
        records: list[TiledRecord],
        total_count: int,
        plan_names: list[str],
    ) -> None:
        """Handle records loaded from background thread.

        Note: @threads.method() unpacks tuple return values, so this
        receives 3 separate arguments instead of a single tuple.
        """
        self._loading = False
        self._total_records = total_count
        self._model.set_records(records)
        self._filter_widget.set_plan_names(plan_names)
        self._update_pagination()
        self._update_status()
        self._refresh_btn.setEnabled(True)

        logger.debug(
            "Loaded {} records (page {} of {})",
            len(records),
            self._current_page + 1,
            max(1, (total_count + self.PAGE_SIZE - 1) // self.PAGE_SIZE),
        )

    @Slot(Exception)
    def _on_load_error(self, error: Exception) -> None:
        """Handle error loading records."""
        self._loading = False
        self._refresh_btn.setEnabled(True)
        self._status_label.setText(f"Error: {error}")
        self._status_label.setStyleSheet("font-weight: bold; color: red;")
        logger.error("Failed to load records: {}", error)

    # === Introspection ===

    def _get_specific_introspection_data(self) -> dict[str, Any]:
        """Get panel-specific introspection data for MCP tools."""
        selected_record = None
        selection = self._table_view.selectionModel().selectedRows()
        if selection:
            proxy_index = selection[0]
            source_index = self._proxy_model.mapToSource(proxy_index)
            record = self._model.get_record(source_index.row())
            if record:
                selected_record = {
                    "uid": record.uid,
                    "scan_id": record.scan_id,
                    "plan_name": record.plan_name,
                    "timestamp": record.timestamp.isoformat(),
                    "exit_status": record.exit_status,
                    "num_points": record.num_points,
                    "sample_name": record.sample_name,
                }

        return {
            "connected": self._tiled_service.is_connected,
            "connection_state": self._tiled_service.state.value,
            "loaded_records": self._model.rowCount(),
            "total_records": self._total_records,
            "current_page": self._current_page,
            "page_size": self.PAGE_SIZE,
            "filters": self._current_filters.to_dict(),
            "selected_record": selected_record,
        }

    def _get_available_actions(self) -> list[dict[str, Any]]:
        """Get available actions for this panel."""
        actions = super()._get_available_actions()
        actions.extend([
            {
                "name": "refresh",
                "description": "Refresh data from Tiled server",
                "method": "action_refresh",
            },
            {
                "name": "set_text_filter",
                "description": "Set text search filter",
                "method": "action_set_text_filter",
                "parameters": {"query": "string"},
            },
            {
                "name": "set_status_filter",
                "description": "Filter by exit status",
                "method": "action_set_status_filter",
                "parameters": {"status": "success|fail|abort|null"},
            },
            {
                "name": "next_page",
                "description": "Go to next page",
                "method": "action_next_page",
            },
            {
                "name": "prev_page",
                "description": "Go to previous page",
                "method": "action_prev_page",
            },
            {
                "name": "select_record",
                "description": "Select a record by UID",
                "method": "action_select_record",
                "parameters": {"uid": "string"},
            },
        ])
        return actions

    def action_refresh(self) -> bool:
        """Action: Refresh data from Tiled server."""
        self._on_refresh_clicked()
        return True

    def action_set_text_filter(self, query: str) -> bool:
        """Action: Set text search filter.

        Args:
            query: Search query string.

        Returns:
            True if filter was applied.
        """
        self._proxy_model.set_text_filter(query)
        return True

    def action_set_status_filter(self, status: str | None) -> bool:
        """Action: Filter by exit status.

        Args:
            status: Exit status or None for all.

        Returns:
            True if filter was applied.
        """
        self._proxy_model.set_status_filter(status)
        return True

    def action_next_page(self) -> bool:
        """Action: Go to next page."""
        self._on_next_page()
        return True

    def action_prev_page(self) -> bool:
        """Action: Go to previous page."""
        self._on_prev_page()
        return True

    def action_select_record(self, uid: str) -> bool:
        """Action: Select a record by UID.

        Args:
            uid: Record UID to select.

        Returns:
            True if record was found and selected.
        """
        record = self._model.get_record_by_uid(uid)
        if record is None:
            return False

        # Find the record in the model and select it
        for row in range(self._model.rowCount()):
            if self._model.get_record(row).uid == uid:
                # Map to proxy index
                source_index = self._model.index(row, 0)
                proxy_index = self._proxy_model.mapFromSource(source_index)
                if proxy_index.isValid():
                    self._table_view.selectRow(proxy_index.row())
                    return True
        return False
