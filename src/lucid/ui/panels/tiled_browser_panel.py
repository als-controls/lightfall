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

from lucid.services.tiled_service import TiledConnectionState, TiledService
from lucid.ui.models.tiled_model import TiledRecord, TiledRecordFilterProxy, TiledRecordModel
from lucid.ui.panels.base import BasePanel, PanelMetadata
from lucid.ui.widgets.tiled_filter_widget import TiledFilters, TiledFilterWidget
from lucid.utils.logging import logger
from lucid.utils.threads import QThreadFuture

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
        id="lucid.panels.tiled_browser",
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
    PAGE_SIZE = 100

    def __init__(self, parent: QWidget | None = None) -> None:
        """Initialize the Tiled browser panel."""
        self._tiled_service = TiledService.get_instance()
        self._total_records = 0
        self._current_filters = TiledFilters()
        self._loading = False
        self._fetch_thread: QThreadFuture | None = None

        # Create models
        self._model = TiledRecordModel()
        self._model.set_fetch_callback(self._fetch_more)
        self._proxy_model = TiledRecordFilterProxy()
        self._proxy_model.setSourceModel(self._model)

        super().__init__(parent)

        # Connect to TiledService signals
        self._tiled_service.connection_changed.connect(self._on_connection_changed)

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

        main_layout.addWidget(self._table_view, stretch=1)

        # Record count at bottom
        count_layout = QHBoxLayout()
        count_layout.addStretch()
        self._count_label = QLabel("")
        count_layout.addWidget(self._count_label)
        main_layout.addLayout(count_layout)

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
        logger.info("Opening run {} in visualization", record.uid[:8])

        # Setup visualization using lazy ArrayClient access
        client = self._tiled_service._client
        if client is None:
            return

        self._fetch_thread = QThreadFuture(
            self._setup_visualization,
            client,
            record._client_key,
            callback_slot=self._on_visualization_ready,
            except_slot=self._on_replay_error,
            name="tiled_setup_viz",
        )
        self._fetch_thread.start()

    def _setup_visualization(self, client: Any, client_key: str) -> dict:
        """Extract metadata, ArrayClient refs, and scalar data for a run.

        For image fields: returns a lazy ArrayClient reference (no bulk fetch).
        For scalar fields: reads all data eagerly (small, one HTTP request each).
        Both paths return a unified dict consumed by ``open_tiled_run``.

        Args:
            client: Tiled client instance.
            client_key: Key for the run in the catalog.

        Returns:
            Unified dict with start_doc, descriptor, image_client,
            timestamps, frame_shape, is_live, entry, scalar_data,
            scalar_fields.
        """
        import uuid

        import numpy as np

        entry = client[client_key]
        metadata = entry.metadata

        # Start document
        start_doc = dict(metadata.get("start") or {})

        # Access primary stream
        stream_names = list(entry.keys())
        if not stream_names:
            logger.debug(
                "No streams from inlined contents for run {}; fetching from server",
                client_key[:8],
            )
            entry.item["attributes"]["structure"]["contents"] = None
            stream_names = list(entry.keys())

        if not stream_names:
            for candidate in ("primary", "baseline"):
                try:
                    entry[candidate]
                    stream_names.append(candidate)
                except (KeyError, Exception):
                    pass

        if "primary" not in stream_names:
            logger.warning("No primary stream found for run {}", client_key[:8])
            return {}

        stream = entry["primary"]
        stream_md = dict(stream.metadata)

        # Build descriptor document (lightweight, from metadata only)
        descriptor = {
            **stream_md,
            "uid": stream_md.get("uid", str(uuid.uuid4())),
            "name": "primary",
            "run_start": start_doc.get("uid", client_key),
        }

        # Classify fields by shape
        data_keys = stream_md.get("data_keys", {})
        image_field = None
        frame_shape: tuple[int, ...] = ()
        scalar_field_names: list[str] = []

        for key, info in data_keys.items():
            shape = info.get("shape", [])
            if len(shape) >= 2:
                if image_field is None:
                    image_field = key
                    frame_shape = tuple(shape)
            else:
                scalar_field_names.append(key)

        stream_keys = list(stream.keys())

        # Image field: lazy ArrayClient reference (no data fetched)
        image_client = None
        if image_field and image_field in stream_keys:
            image_client = stream[image_field]

        # Scalar fields: read eagerly (small data, one HTTP request each)
        scalar_data: dict[str, Any] = {}
        for field_name in scalar_field_names:
            if field_name in stream_keys:
                try:
                    scalar_data[field_name] = np.asarray(stream[field_name].read())
                except Exception as e:
                    logger.warning("Could not read scalar field '{}': {}", field_name, e)

        # Timestamps (small 1-D array)
        timestamps = np.array([])
        if "time" in stream_keys:
            timestamps = np.asarray(stream["time"].read(), dtype=np.float64)

        is_live = metadata.get("stop") is None

        logger.debug(
            "Setup viz for run {}: image={}, scalars={}, live={}",
            client_key[:8],
            image_field,
            len(scalar_data),
            is_live,
        )

        return {
            "start_doc": start_doc,
            "descriptor": descriptor,
            "image_client": image_client,
            "timestamps": timestamps,
            "frame_shape": frame_shape,
            "is_live": is_live,
            "entry": entry,
            "scalar_data": scalar_data or None,
            "scalar_fields": scalar_field_names,
        }

    @Slot(object)
    def _on_visualization_ready(self, result: dict | None = None) -> None:
        """Handle visualization setup result — open in VisualizationPanel."""
        if not result:
            logger.warning("No visualization data to display")
            return

        from PySide6.QtWidgets import QApplication

        from lucid.ui.panels.visualization_panel import VisualizationPanel

        for widget in QApplication.allWidgets():
            if isinstance(widget, VisualizationPanel):
                widget.open_tiled_run(
                    start_doc=result["start_doc"],
                    descriptor=result["descriptor"],
                    image_client=result.get("image_client"),
                    timestamps=result["timestamps"],
                    frame_shape=result.get("frame_shape", ()),
                    is_live=result.get("is_live", False),
                    entry=result.get("entry"),
                    scalar_data=result.get("scalar_data"),
                    scalar_fields=result.get("scalar_fields", []),
                )
                logger.info("Opened tiled run in visualization")
                return

        logger.warning("Visualization panel not found")

    @Slot(Exception)
    def _on_replay_error(self, error: Exception) -> None:
        """Handle error setting up visualization."""
        logger.error("Failed to setup visualization: {}", error)

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
        self._refresh_btn.setEnabled(False)
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
            self._refresh_btn.setEnabled(True)
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
        self._refresh_btn.setEnabled(True)
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
