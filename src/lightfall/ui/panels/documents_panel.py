"""Documents panel for viewing Bluesky document streams.

Provides a standalone panel for viewing documents emitted during
RunEngine execution, with filtering and search capabilities.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, ClassVar

from loguru import logger
from PySide6.QtWidgets import QLabel, QWidget

from lightfall.ui.panels.base import BasePanel, PanelMetadata
from lightfall.ui.widgets.document_stream import DocumentStreamWidget

if TYPE_CHECKING:
    from lightfall.acquire import QRunEngine


class DocumentsPanel(BasePanel):
    """Panel for viewing Bluesky document streams.

    Displays documents (start, descriptor, event, stop) emitted
    during RunEngine execution in a hierarchical tree view.

    Example:
        >>> from lightfall.acquire import get_run_engine
        >>> panel = DocumentsPanel()
        >>> panel.set_run_engine(get_run_engine())
    """

    panel_metadata: ClassVar[PanelMetadata] = PanelMetadata(
        id="lightfall.panels.documents",
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

        # Stream controls live in the panel title bar, wired to the
        # document stream widget's existing handlers.
        self._view_action = self.add_title_bar_button(
            "mdi6.file-tree", "View", self._on_toggle_view
        )
        self._update_view_icon()
        self.add_title_bar_button(
            "mdi6.auto-download",
            "Auto-scroll",
            self._doc_stream._toggle_auto_scroll,
            checkable=True,
            checked=self._doc_stream._auto_scroll,
        )
        self.add_title_bar_button(
            "mdi6.trash-can", "Clear", self._doc_stream._on_clear_clicked
        )

        # Auto-configure with RunEngine singleton
        self._auto_configure()

    def _on_toggle_view(self, *_args) -> None:
        """Toggle the stream view mode and reflect it in the button icon."""
        self._doc_stream._toggle_view_mode()
        self._update_view_icon()

    def _update_view_icon(self) -> None:
        """Show the icon for the current view mode (tree vs sequential)."""
        import qtawesome as qta

        try:
            from lightfall.ui.theme import ThemeManager

            color = ThemeManager.get_instance().colors.text_secondary
        except Exception:
            color = "#808080"
        name = (
            "mdi6.file-tree"
            if self._doc_stream._view_mode == "tree"
            else "mdi6.view-sequential"
        )
        self._view_action.setIcon(qta.icon(name, color=color))

    def _auto_configure(self) -> None:
        """Auto-configure with RunEngine singleton."""
        try:
            from lightfall.acquire import get_run_engine
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

        Uses entry.documents() to get all documents (start, descriptors,
        events, stream_resource, stream_datum, stop). Falls back to
        showing start/stop from metadata if documents() is unavailable.

        Args:
            entry: Tiled BlueskyRun entry.
        """
        metadata = entry.metadata
        start_doc = metadata.get("start") or {}

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

        # Try the proper documents() API first
        try:
            for name, doc in entry.documents():
                _emit(name, dict(doc))
        except Exception as e:
            logger.warning("entry.documents() failed, falling back to metadata: {}", e)
            # Fallback: show what we can from metadata
            if start_doc:
                _emit("start", start_doc)
            stop_doc = metadata.get("stop")
            if stop_doc:
                _emit("stop", stop_doc)

        total = self._doc_stream._sequential_model.rowCount()
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
