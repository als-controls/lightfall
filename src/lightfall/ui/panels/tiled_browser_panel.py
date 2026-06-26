"""Tiled data browser panel for NCS.

Provides a panel for browsing and searching data stored in a Tiled server,
with filtering, pagination, and record selection.
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Any, ClassVar

import httpx
from PySide6.QtCore import Qt, Signal, Slot
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMenu,
    QTableView,
    QVBoxLayout,
    QWidget,
)

from lightfall.services.tiled_service import TiledConnectionState, TiledService
from lightfall.ui.models.tiled_model import TiledRecord, TiledRecordFilterProxy, TiledRecordModel
from lightfall.ui.panels.base import BasePanel, PanelMetadata
from lightfall.ui.theme import ThemeManager
from lightfall.ui.widgets.tiled_filter_widget import TiledFilters, TiledFilterWidget
from lightfall.utils.logging import logger
from lightfall.utils.threads import QThreadFuture

if TYPE_CHECKING:
    pass

# Sentinel to distinguish "stop key absent" from "stop is None"
_STOP_ABSENT = object()


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
        id="lightfall.panels.tiled_browser",
        name="Data Browser",
        description="Browse and search data stored in Tiled server",
        icon="database",
        category="Data",
        required_permission=None,
        singleton=True,
        closable=True,
        keywords=["tiled", "data", "browser", "catalog", "runs", "scans"],
        # Docking preferences - left sidebar (top icons)
        default_area="left",
        sidebar_group="top",
        auto_hide=True,
        sidebar_order=0,
    )

    # Signals
    record_clicked = Signal(object)  # TiledRecord on single-click
    record_double_clicked = Signal(object)  # TiledRecord on double-click

    # Page size for pagination
    PAGE_SIZE = 20

    def __init__(self, parent: QWidget | None = None) -> None:
        """Initialize the Tiled browser panel."""
        self._tiled_service = TiledService.get_instance()
        self._theme_manager = ThemeManager.get_instance()
        self._total_records = 0
        self._current_filters = TiledFilters()
        self._loading = False
        self._fetch_thread: QThreadFuture | None = None
        # Run-sort key, detected from the catalog backend on first fetch
        # (see _detect_sort_key / _do_fetch) and cached. None until detected.
        self._sort_key: str | None = None

        # Create models
        self._model = TiledRecordModel()
        self._model.set_fetch_callback(self._fetch_more)
        self._proxy_model = TiledRecordFilterProxy()
        self._proxy_model.setSourceModel(self._model)

        super().__init__(parent)

        # Connect to TiledService signals
        self._tiled_service.connection_changed.connect(self._on_connection_changed)

        # Re-style the status label when the theme changes
        self._theme_manager.colors_changed.connect(self._update_status)

        # Initial status update and data load
        self._update_status()
        if self._tiled_service.is_connected:
            self._load_data()

    def _setup_ui(self) -> None:
        """Setup the panel UI."""
        # Main layout
        main_widget = QWidget()
        main_layout = QVBoxLayout(main_widget)
        main_layout.setContentsMargins(8, 8, 8, 8)
        main_layout.setSpacing(8)

        # Refresh action lives in the panel title bar (icon-only button).
        self._refresh_action = self.add_title_bar_button(
            "mdi6.refresh", "Refresh", self._on_refresh_clicked
        )

        # Filter widget
        self._filter_widget = TiledFilterWidget()
        self._filter_widget.filters_changed.connect(self._on_filters_changed)
        main_layout.addWidget(self._filter_widget)

        # Table view
        self._table_view = QTableView()
        self._table_view.setModel(self._proxy_model)
        self._table_view.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._table_view.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self._table_view.setAlternatingRowColors(True)
        self._table_view.setSortingEnabled(True)
        self._table_view.sortByColumn(2, Qt.SortOrder.DescendingOrder)  # Sort by timestamp desc
        self._table_view.setShowGrid(False)
        self._table_view.verticalHeader().setVisible(False)

        # Configure header
        header = self._table_view.horizontalHeader()
        header.setStretchLastSection(False)
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)           # Sample - stretch
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Interactive)       # Plan
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)  # Timestamp
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)  # Status
        header.setSectionResizeMode(4, QHeaderView.ResizeMode.ResizeToContents)  # Scan ID

        # Set default widths for interactive columns
        self._table_view.setColumnWidth(1, 120)  # Plan

        # Connect click signals
        self._table_view.clicked.connect(self._on_table_clicked)
        self._table_view.doubleClicked.connect(self._on_table_double_clicked)

        # Context menu
        self._table_view.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._table_view.customContextMenuRequested.connect(self._show_context_menu)

        main_layout.addWidget(self._table_view, stretch=1)

        # Bottom row: status indicator on the left, record count on the right.
        bottom_layout = QHBoxLayout()

        self._status_label = QLabel("Status: Disconnected")
        self._status_label.setStyleSheet("font-weight: bold;")
        bottom_layout.addWidget(self._status_label)

        bottom_layout.addStretch()

        self._count_label = QLabel("")
        bottom_layout.addWidget(self._count_label)
        main_layout.addLayout(bottom_layout)

        self._layout.addWidget(main_widget)

    def _update_status(self) -> None:
        """Update the status display based on TiledService state."""
        state = self._tiled_service.state
        status_info = self._tiled_service.get_status_info()
        colors = self._theme_manager.colors

        if state == TiledConnectionState.CONNECTED:
            self._status_label.setText(f"Status: Connected to {status_info['url']}")
            self._status_label.setStyleSheet(f"font-weight: bold; color: {colors.success};")
            self._filter_widget.set_enabled(True)
            self._refresh_action.setEnabled(True)
        elif state == TiledConnectionState.CONNECTING:
            self._status_label.setText("Status: Connecting...")
            self._status_label.setStyleSheet(f"font-weight: bold; color: {colors.warning};")
            self._filter_widget.set_enabled(False)
            self._refresh_action.setEnabled(False)
        elif state == TiledConnectionState.ERROR:
            error_msg = status_info.get("error", "Unknown error")
            self._status_label.setText(f"Status: Error - {error_msg}")
            self._status_label.setStyleSheet(f"font-weight: bold; color: {colors.error};")
            self._filter_widget.set_enabled(False)
            self._refresh_action.setEnabled(True)  # Allow retry
        else:  # DISCONNECTED
            self._status_label.setText("Status: Disconnected")
            self._status_label.setStyleSheet("font-weight: bold;")
            self._filter_widget.set_enabled(False)
            self._refresh_action.setEnabled(False)

    def _update_count_label(self) -> None:
        """Update the record count label."""
        loaded = self._model.rowCount()
        if self._total_records > loaded:
            self._count_label.setText(f"{loaded} of {self._total_records} records")
        elif self._total_records > 0:
            self._count_label.setText(f"{self._total_records} records")
        else:
            self._count_label.setText("")

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
        """Handle filter changes from filter widget.

        Server-side filters (date range, plan name, exit status) trigger
        a reload. Text search is applied client-side via the proxy model.
        """
        # Text search is always client-side (fast, no round-trip)
        self._proxy_model.set_text_filter(filters.text_query)

        # Check if server-side filters changed
        server_filters_changed = (
            filters.start_date != self._current_filters.start_date
            or filters.end_date != self._current_filters.end_date
            or filters.plan_name != self._current_filters.plan_name
            or filters.exit_status != self._current_filters.exit_status
        )

        self._current_filters = filters

        if server_filters_changed:
            self._load_data()

    @Slot()
    def _on_refresh_clicked(self) -> None:
        """Handle refresh button click."""
        if self._tiled_service.state == TiledConnectionState.ERROR:
            # Try to reconnect asynchronously
            self._tiled_service.connect_async()
        else:
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
        """Handle table row double-click - open run in Visualization panel."""
        selection = self._table_view.selectionModel().selectedRows()
        if not selection:
            return

        proxy_index = selection[0]
        source_index = self._proxy_model.mapToSource(proxy_index)
        record = self._model.get_record(source_index.row())
        if not record:
            return

        self.record_double_clicked.emit(record)
        self._open_run_in_visualization(record)

    @Slot("QPoint")
    def _show_context_menu(self, pos) -> None:
        """Show context menu for the table view."""
        index = self._table_view.indexAt(pos)
        if not index.isValid():
            return

        source_index = self._proxy_model.mapToSource(index)
        record = self._model.get_record(source_index.row())
        if not record:
            return

        menu = QMenu(self._table_view)

        copy_uid_action = menu.addAction("Copy UUID")
        copy_scan_id_action = menu.addAction("Copy Scan ID")
        copy_scan_id_action.setEnabled(record.scan_id is not None)
        menu.addSeparator()
        show_viz_action = menu.addAction("Show Visualization")
        show_docs_action = menu.addAction("Show Documents")
        run_pipeline_action = menu.addAction("Run pipeline...")
        run_pipeline_action.setEnabled(self._pipeline_client() is not None)
        menu.addSeparator()
        export_action = menu.addAction("Export")

        action = menu.exec(self._table_view.viewport().mapToGlobal(pos))
        if action is None:
            return

        if action is copy_uid_action:
            QApplication.clipboard().setText(record.uid)
        elif action is copy_scan_id_action:
            QApplication.clipboard().setText(str(record.scan_id))
        elif action is show_viz_action:
            self._open_run_in_visualization(record)
        elif action is show_docs_action:
            self._open_run_in_documents(record)
        elif action is run_pipeline_action:
            self._open_run_pipeline_dialog(record)
        elif action is export_action:
            self._on_export_clicked()

    def _get_tiled_entry(self, record: TiledRecord):
        """Get the Tiled entry for a record, or None on failure."""
        client = self._tiled_service._client
        if client is None:
            return None
        try:
            return client[record._client_key]
        except Exception as e:
            logger.error("Failed to access run {}: {}", record.uid[:8], e)
            return None

    def _pipeline_client(self):
        """Look up the PipelineClient via the service registry (lazy)."""
        from lightfall.core.services import ServiceRegistry
        from lightfall.pipelines import PipelineClient
        return ServiceRegistry.get_instance().get(PipelineClient, None)

    def _open_run_pipeline_dialog(self, record: TiledRecord) -> None:
        """Open the Run Pipeline dialog for the selected run."""
        client = self._pipeline_client()
        if client is None:
            return
        from lightfall.auth.session import SessionManager
        from lightfall.ui.dialogs.run_pipeline_dialog import RunPipelineDialog
        session_manager = SessionManager.get_instance()
        user_id = session_manager.current_user.attributes.get(
            "preferred_username", ""
        )
        dialog = RunPipelineDialog(
            client=client,
            run_uid=record.uid,
            input_access_blob=getattr(record, "access_blob", {}) or {},
            user_id=user_id,
            parent=self,
        )
        dialog.exec()

    def _open_run_in_visualization(self, record: TiledRecord) -> None:
        """Open a run in the Visualization panel."""
        entry = self._get_tiled_entry(record)
        if entry is None:
            return

        logger.info("Opening run {} in visualization", record.uid[:8])

        from lightfall.core.services import ServiceRegistry
        from lightfall.ui.docking import DockingManager
        from lightfall.ui.panels.visualization_panel import VisualizationPanel

        dm = ServiceRegistry.get_instance().get(DockingManager, None)
        if dm is None:
            return

        viz_panel_id = "lightfall.panels.visualization"
        dm.show_panel(viz_panel_id)
        panel = dm.get_panel(viz_panel_id)
        if isinstance(panel, VisualizationPanel):
            panel.open_run(entry)

    def _open_run_in_documents(self, record: TiledRecord) -> None:
        """Open a run in the Documents panel."""
        entry = self._get_tiled_entry(record)
        if entry is None:
            return

        logger.info("Opening run {} in documents", record.uid[:8])

        from lightfall.core.services import ServiceRegistry
        from lightfall.ui.docking import DockingManager
        from lightfall.ui.panels.documents_panel import DocumentsPanel

        dm = ServiceRegistry.get_instance().get(DockingManager, None)
        if dm is None:
            return

        docs_panel_id = "lightfall.panels.documents"
        dm.show_panel(docs_panel_id)
        panel = dm.get_panel(docs_panel_id)
        if isinstance(panel, DocumentsPanel):
            panel.open_run(entry)

    def _get_selected_records(self) -> list[TiledRecord]:
        """Get all currently selected TiledRecord objects."""
        records = []
        selection = self._table_view.selectionModel().selectedRows()
        for proxy_index in selection:
            source_index = self._proxy_model.mapToSource(proxy_index)
            record = self._model.get_record(source_index.row())
            if record:
                records.append(record)
        return records

    @Slot()
    def _on_export_clicked(self) -> None:
        """Open the export dialog for the selected run(s)."""
        records = self._get_selected_records()
        if not records:
            return

        from lightfall.ui.dialogs.export_dialog import ExportDialog

        dialog = ExportDialog(
            records=records,
            tiled_service=self._tiled_service,
            parent=self,
        )
        dialog.exec()

    def _load_data(self) -> None:
        """Load initial batch of data from Tiled server with current filters."""
        if not self._tiled_service.is_connected:
            logger.debug("Cannot load data: not connected to Tiled")
            return

        if self._loading:
            logger.debug("Load already in progress, skipping")
            return

        client = self._tiled_service._client
        if client is None:
            logger.debug("Cannot load data: no Tiled client")
            return

        self._loading = True
        self._model.clear()
        self._refresh_action.setEnabled(False)
        self._status_label.setText("Loading...")

        self._fetch_thread = QThreadFuture(
            self._do_fetch,
            client,
            self._current_filters,
            0,
            self.PAGE_SIZE,
            callback_slot=self._on_initial_load,
            except_slot=self._on_load_error,
            name="tiled_fetch",
        )
        self._fetch_thread.start()

    def _fetch_more(self) -> None:
        """Fetch next batch of records (called by model's fetchMore)."""
        if not self._tiled_service.is_connected or self._loading:
            self._model.set_fetching(False)
            return

        client = self._tiled_service._client
        if client is None:
            self._model.set_fetching(False)
            return

        self._loading = True
        offset = self._model.rowCount()

        self._fetch_thread = QThreadFuture(
            self._do_fetch,
            client,
            self._current_filters,
            offset // self.PAGE_SIZE,
            self.PAGE_SIZE,
            callback_slot=self._on_more_loaded,
            except_slot=self._on_load_error,
            name="tiled_fetch_more",
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
        # Refresh client to pick up new entries written since connection
        if hasattr(client, "refresh"):
            client.refresh()

        # Build query with filters
        result = self._build_query(client, filters)

        # Get total count before pagination
        total_count = len(result)

        # Apply pagination. Use items()[start:end] so the server returns keys
        # AND full metadata in one paginated request — avoids an N+1 pattern
        # where each `result[key]` would issue its own KeyLookup roundtrip.
        start = page * page_size
        end = start + page_size

        # Sort newest-first. The correct key is backend-dependent and the two
        # bluesky catalogs need different, incompatible keys (a single key would
        # silently misorder one of them), so detect the backend ONCE from the
        # catalog spec version -- deterministic, not by interpreting a 500 (which
        # can occur for unrelated/transient reasons):
        #   * CatalogOfBlueskyRuns v1  -> databroker mongo_normalized -> "time"
        #   * CatalogOfBlueskyRuns v2+ -> tiled SQL catalog           -> "start.time"
        if self._sort_key is None:
            self._sort_key = self._detect_sort_key(client)

        # .sort() is lazy, so a server-side failure surfaces HERE when .items()
        # is materialized. The key is already chosen by backend, so a 500 here is
        # a genuine/transient error (not a key mismatch) -- fall back to an
        # unsorted listing for this page so the browser still loads, without
        # changing the cached key.
        try:
            page_items = list(result.sort((self._sort_key, -1)).items()[start:end])
        except httpx.HTTPStatusError as e:
            logger.warning(
                "Tiled sort on {!r} failed ({}); listing without sort",
                self._sort_key,
                e,
            )
            page_items = list(result.items()[start:end])

        records: list[TiledRecord] = []
        plan_names: set[str] = set()

        for key, entry in page_items:
            try:
                record = self._entry_to_record(key, entry)
                records.append(record)
                if record.plan_name:
                    plan_names.add(record.plan_name)
            except Exception as e:
                logger.warning("Failed to parse record {}: {}", key, e)
                continue

        return records, total_count, list(plan_names)

    @staticmethod
    def _detect_sort_key(client: Any) -> str:
        """Pick the run-time sort key from the catalog backend, by spec version.

        The two bluesky catalog backends sort runs on different, incompatible
        keys, and the spec version distinguishes them deterministically:

        * ``CatalogOfBlueskyRuns`` v1  -> databroker ``mongo_normalized``: sorts
          the top-level ``time`` field (nested ``start.time`` 500s).
        * ``CatalogOfBlueskyRuns`` v2+ -> tiled SQL catalog: sorts nested
          ``start.time`` (``time`` resolves to a nonexistent key, silently wrong).

        Unknown/missing spec defaults to ``start.time``: a wrong guess there
        fails loudly (500 -> unsorted fallback) rather than silently misordering.
        """
        try:
            for spec in getattr(client, "specs", None) or []:
                if getattr(spec, "name", None) == "CatalogOfBlueskyRuns":
                    version = str(getattr(spec, "version", "") or "")
                    return "time" if version.startswith("1") else "start.time"
        except Exception as e:
            logger.debug("Could not detect Tiled backend for sort key: {}", e)
        return "start.time"

    def _build_query(self, client: Any, filters: TiledFilters) -> Any:
        """Build Tiled query from filters.

        Uses dot-separated key paths to traverse nested Bluesky metadata.
        The catalog stores metadata as {"start": {...}, "stop": {...}},
        so fields are accessed as e.g. "start.time", "start.plan_name".

        Args:
            client: Tiled client instance.
            filters: Filter settings.

        Returns:
            Filtered Tiled container.
        """
        try:
            from tiled.queries import Key
        except ImportError:
            logger.warning("tiled.queries not available, returning unfiltered results")
            return client

        result = client

        # Apply time filters (nested under start document)
        if filters.start_date:
            try:
                result = result.search(Key("start.time") >= filters.start_date.timestamp())
            except Exception as e:
                logger.debug("Failed to apply start_date filter: {}", e)

        if filters.end_date:
            try:
                result = result.search(Key("start.time") <= filters.end_date.timestamp())
            except Exception as e:
                logger.debug("Failed to apply end_date filter: {}", e)

        # Apply plan name filter (nested under start document)
        if filters.plan_name:
            try:
                result = result.search(Key("start.plan_name") == filters.plan_name)
            except Exception as e:
                logger.debug("Failed to apply plan_name filter: {}", e)

        # Apply exit status filter (nested under stop document)
        if filters.exit_status:
            try:
                result = result.search(Key("stop.exit_status") == filters.exit_status)
            except Exception as e:
                logger.debug("Failed to apply exit_status filter: {}", e)

        # NOTE: sorting is applied by the caller (_do_fetch), not here. .sort()
        # is lazy, so its server-side failure must be caught where the query is
        # materialized (.items()) to fall back to an unsorted listing.
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
        start_doc = metadata.get("start") or {}

        # Get stop document - distinguish None (running) from absent (unknown)
        # from present (completed). metadata.get("stop", {}) returns {} for
        # absent keys (falsy!) and None for explicitly-None values (also falsy!),
        # so we use a sentinel to tell them apart.
        _stop_raw = metadata.get("stop", _STOP_ABSENT)
        if _stop_raw is _STOP_ABSENT:
            stop_doc = None
            exit_status = "unknown"
        elif _stop_raw is None:
            stop_doc = None
            exit_status = "running"
        else:
            stop_doc = _stop_raw
            exit_status = stop_doc.get("exit_status", "unknown")

        # Extract fields
        uid = start_doc.get("uid", key)
        scan_id = start_doc.get("scan_id")
        plan_name = start_doc.get("plan_name", "")
        time_val = start_doc.get("time", 0)
        timestamp = datetime.fromtimestamp(time_val) if time_val else datetime.now()

        # Calculate duration
        duration = None
        if stop_doc and "time" in stop_doc:
            stop_time = stop_doc["time"]
            duration = stop_time - time_val

        # Number of points from stop document
        num_points = 0
        if stop_doc:
            num_points = stop_doc.get("num_events", {}).get("primary", 0)

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
    def _on_initial_load(self, result: tuple | None = None) -> None:
        """Handle initial data load from background thread."""
        self._loading = False

        if result is None:
            self._update_status()
            self._refresh_action.setEnabled(True)
            return

        try:
            records, total_count, plan_names = result
            self._total_records = total_count
            self._model.set_total_available(total_count)
            self._model.set_records(records)
            self._filter_widget.set_plan_names(plan_names or [])
            self._update_count_label()
        except Exception as e:
            logger.error("Error processing loaded records: {}", e)

        self._update_status()
        self._refresh_action.setEnabled(True)
        logger.debug("Loaded {} of {} records", self._model.rowCount(), self._total_records)

    @Slot(object)
    def _on_more_loaded(self, result: tuple | None = None) -> None:
        """Handle incremental data load (lazy loading)."""
        self._loading = False
        self._model.set_fetching(False)

        if result is None:
            return

        try:
            records, total_count, plan_names = result
            self._total_records = total_count
            self._model.set_total_available(total_count)
            self._model.append_records(records)
            self._update_count_label()
        except Exception as e:
            logger.error("Error appending records: {}", e)

    @Slot(Exception)
    def _on_load_error(self, error: Exception) -> None:
        """Handle error loading records."""
        self._loading = False
        self._refresh_action.setEnabled(True)
        self._status_label.setText(f"Error: {error}")
        self._status_label.setStyleSheet(
            f"font-weight: bold; color: {self._theme_manager.colors.error};"
        )
        logger.error("Failed to load records: {}", error)

    # === Introspection ===

    def _get_specific_introspection_data(self) -> dict[str, Any]:
        """Get panel-specific introspection data for MCP tools."""
        selected_records = []
        selection = self._table_view.selectionModel().selectedRows()
        for proxy_index in selection:
            source_index = self._proxy_model.mapToSource(proxy_index)
            record = self._model.get_record(source_index.row())
            if record:
                selected_records.append({
                    "uid": record.uid,
                    "scan_id": record.scan_id,
                    "plan_name": record.plan_name,
                    "timestamp": record.timestamp.isoformat(),
                    "exit_status": record.exit_status,
                    "num_points": record.num_points,
                    "sample_name": record.sample_name,
                })

        return {
            "connected": self._tiled_service.is_connected,
            "connection_state": self._tiled_service.state.value,
            "loaded_records": self._model.rowCount(),
            "total_records": self._total_records,
            "filters": self._current_filters.to_dict(),
            "selected_records": selected_records,
            "selected_count": len(selected_records),
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
                "name": "select_record",
                "description": "Select a record by UID",
                "method": "action_select_record",
                "parameters": {"uid": "string"},
            },
            {
                "name": "export",
                "description": "Export selected runs",
                "method": "action_export",
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

    def action_export(self) -> bool:
        """Action: Open export dialog for selected runs."""
        self._on_export_clicked()
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
