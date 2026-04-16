"""Documents panel for viewing Bluesky document streams.

Provides a standalone panel for viewing documents emitted during
RunEngine execution, with filtering and search capabilities.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, ClassVar

from loguru import logger
from PySide6.QtWidgets import QLabel, QWidget

from lucid.ui.panels.base import BasePanel, PanelMetadata
from lucid.ui.widgets.document_stream import DocumentStreamWidget

if TYPE_CHECKING:
    from lucid.acquire import QRunEngine


class DocumentsPanel(BasePanel):
    """Panel for viewing Bluesky document streams.

    Displays documents (start, descriptor, event, stop) emitted
    during RunEngine execution in a hierarchical tree view.

    Example:
        >>> from lucid.acquire import get_run_engine
        >>> panel = DocumentsPanel()
        >>> panel.set_run_engine(get_run_engine())
    """

    panel_metadata: ClassVar[PanelMetadata] = PanelMetadata(
        id="lucid.panels.documents",
        name="Documents",
        description="View Bluesky document streams during acquisition",
        icon="receipt",
        category="Acquisition",
        singleton=True,
        closable=True,
        keywords=["documents", "bluesky", "stream", "events", "data"],
        # Docking preferences - left sidebar (top icons)
        default_area="left",
        sidebar_group="top",
        auto_hide=True,
        sidebar_order=2,
    )

    def __init__(self, parent: QWidget | None = None) -> None:
        """Initialize the Documents panel.

        Args:
            parent: Parent widget.
        """
        self._re: QRunEngine | None = None
        super().__init__(parent)

    def _setup_ui(self) -> None:
        """Set up the panel UI."""
        # Header label showing the source of the current documents
        self._source_label = QLabel("")
        self._source_label.setStyleSheet("font-weight: bold;")
        self._source_label.setVisible(False)
        self._layout.addWidget(self._source_label)

        # Document stream widget fills the panel
        self._doc_stream = DocumentStreamWidget()
        self._layout.addWidget(self._doc_stream)

        # Auto-configure with RunEngine singleton
        self._auto_configure()

    def _auto_configure(self) -> None:
        """Auto-configure with RunEngine singleton."""
        try:
            from lucid.acquire import get_run_engine
            re = get_run_engine()
            self.set_run_engine(re)
        except Exception as e:
            logger.debug("Could not auto-configure RunEngine: {}", e)

    def set_run_engine(self, re: QRunEngine) -> None:
        """Connect to a QRunEngine instance.

        Args:
            re: The QRunEngine to monitor.
        """
        self._re = re
        self._doc_stream.set_run_engine(re)
        logger.info("DocumentsPanel connected to RunEngine")

    def open_run(self, entry: Any) -> None:
        """Load documents from a Tiled run entry.

        Extracts all available documents (start, descriptors, events, stop)
        from the entry and feeds them into the document stream widget.

        Args:
            entry: Tiled BlueskyRun entry.
        """
        metadata = entry.metadata
        start_doc = metadata.get("start") or {}
        stop_doc = metadata.get("stop")

        uid = start_doc.get("uid", "")
        plan = start_doc.get("plan_name", "unknown")
        scan_id = start_doc.get("scan_id")
        label = f"Run: {plan}"
        if scan_id is not None:
            label += f" (scan {scan_id})"
        label += f" — {uid[:8]}"
        self._source_label.setText(label)
        self._source_label.setVisible(True)

        self._doc_stream.clear()

        def _emit(name: str, doc: dict) -> None:
            self._doc_stream._tree_model.doc_consumer(name, doc)
            self._doc_stream._sequential_model.add_document(name, doc)

        # Feed start document
        if start_doc:
            _emit("start", start_doc)

        # Feed descriptor + events for each stream
        for stream_name in entry:
            try:
                stream = entry[stream_name]
            except Exception as e:
                logger.debug("Could not access stream {}: {}", stream_name, e)
                continue

            # Descriptor
            try:
                desc = dict(stream.metadata)
                desc.setdefault("name", stream_name)
                _emit("descriptor", desc)
            except Exception as e:
                logger.debug("Could not read descriptor for stream {}: {}", stream_name, e)

            # Events from internal events table
            try:
                stream_keys = list(stream.keys())
            except Exception:
                stream_keys = []

            if "internal" in stream_keys:
                try:
                    internal = stream["internal"]
                    if "events" in internal:
                        events_df = internal["events"].read()
                        data_keys = set(stream.metadata.get("data_keys", {}))
                        for _, row in events_df.iterrows():
                            row_dict = row.to_dict()
                            event_doc: dict[str, Any] = {
                                "seq_num": row_dict.pop("seq_num", None),
                                "time": row_dict.pop("time", None),
                                "uid": row_dict.pop("uid", None),
                            }
                            # Split into data and timestamps
                            data = {}
                            timestamps = {}
                            for key, val in row_dict.items():
                                if key.startswith("ts_"):
                                    timestamps[key[3:]] = val
                                elif key in data_keys:
                                    data[key] = val
                            if data:
                                event_doc["data"] = data
                            if timestamps:
                                event_doc["timestamps"] = timestamps
                            _emit("event", event_doc)
                except Exception as e:
                    logger.debug("Could not read events for stream {}: {}", stream_name, e)

        # Feed stop document
        if stop_doc:
            _emit("stop", stop_doc)

        total = (
            self._doc_stream._sequential_model.rowCount()
        )
        self._doc_stream._status_label.setText(
            f"Loaded {total} documents from run {uid[:8]}"
        )
        logger.info("Loaded {} documents for run {}", total, uid[:8])

    # === Introspection API for MCP tools ===

    def get_introspection_data(self) -> dict[str, Any]:
        """Get introspection data for Claude MCP tools.

        Returns:
            Dictionary with panel state.
        """
        return {
            "panel_id": self.panel_metadata.id,
            "panel_name": self.panel_metadata.name,
            "has_run_engine": self._re is not None,
        }

    def get_available_actions(self) -> list[dict[str, str]]:
        """Get list of actions that can be performed on this panel.

        Returns:
            List of action descriptions for MCP tools.
        """
        return [
            {
                "action": "clear",
                "description": "Clear the document display",
                "params": "None",
            },
        ]
