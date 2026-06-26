"""Visualization panel for tiled Bluesky data.

Orchestrates visualization selection, display, and configuration
for BlueskyRun entries accessed via tiled.
"""

from __future__ import annotations

from typing import Any, ClassVar

from loguru import logger
from PySide6.QtCore import Qt, QTimer, Signal, Slot
from PySide6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSplitter,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from lightfall.ui.panels.base import BasePanel, PanelMetadata
from lightfall.utils.crash_diagnostics import gui_thread_only
from lightfall.ui.theater.manager import theater_manager
from lightfall.ui.theater.proxy import TheaterProxy
from lightfall.ui.theme import scaled_px
from lightfall.visualization.base_visualization import BaseVisualization
from lightfall.visualization.fitting.panel import FitPanel
from lightfall.visualization.stream_bridge import StreamBridge


def _widget_classes() -> list[type[BaseVisualization]]:
    """Import and return all available visualization widget classes.

    Returns the 8 built-in visualizations plus any classes contributed by
    plugins registered in VisualizationRegistry (via type_name="visualization").
    Registry entries are appended after the built-ins, deduplicated.  Any
    plugin whose get_viz_class() raises is silently skipped so a bad plugin
    never prevents the panel from opening.
    """
    from lightfall.visualization.widgets.adaptive.heatmap import (
        AdaptiveHeatmapVisualization,
    )
    from lightfall.visualization.widgets.adaptive.plot import (
        AdaptivePlotVisualization,
    )
    from lightfall.visualization.widgets.heatmap import HeatmapVisualization
    from lightfall.visualization.widgets.image_stack import ImageStackVisualization
    from lightfall.visualization.widgets.plot_1d import Plot1DVisualization
    from lightfall.visualization.widgets.scan_viewer import ScanViewerVisualization
    from lightfall.visualization.widgets.scatter import ScatterVisualization
    from lightfall.visualization.widgets.table import TableVisualization

    classes: list[type[BaseVisualization]] = [
        ImageStackVisualization,
        ScanViewerVisualization,
        Plot1DVisualization,
        HeatmapVisualization,
        ScatterVisualization,
        TableVisualization,
        AdaptiveHeatmapVisualization,
        AdaptivePlotVisualization,
    ]

    try:
        from lightfall.visualization.registry import VisualizationRegistry

        for plugin in VisualizationRegistry.get_instance().get_all_visualizations():
            try:
                cls = plugin.get_viz_class()
            except Exception:
                continue
            if cls is not None and cls not in classes:
                classes.append(cls)
    except Exception:
        pass  # registry unavailable — built-ins only

    return classes


class VisualizationPanel(BasePanel):
    """Panel for tiled data visualization.

    Receives a tiled BlueskyRun entry, scores registered widgets,
    creates the best match, and drives the set_run / set_stream /
    set_field / refresh flow.
    """

    panel_metadata: ClassVar[PanelMetadata] = PanelMetadata(
        id="lightfall.panels.visualization",
        name="Visualization",
        description="Live data visualization during scans",
        icon="chart-line",
        category="Acquisition",
        singleton=True,
        closable=True,
        keywords=["plot", "chart", "graph", "visualization", "live", "data"],
        # Docking preferences - bottom sidebar
        default_area="bottom",
        sidebar_group="bottom",
        auto_hide=True,
        sidebar_order=1,
    )

    visualization_changed = Signal(str)  # viz_name
    MAX_SYNC_RETRIES: ClassVar[int] = 8

    def __init__(self, parent: QWidget | None = None) -> None:
        self._entry: Any | None = None
        self._current_widget: BaseVisualization | None = None
        self._current_proxy: TheaterProxy | None = None
        # Single StreamBridge for the active run/viz. Created lazily; its
        # update_received signal is connected ONCE to the stable routing slot
        # _on_stream_update (which dispatches to whatever _current_widget is at
        # delivery time, so a stale connection can never reach an old viz).
        self._bridge: StreamBridge | None = None
        # Live-run follow state
        self._follow_live: bool = True       # auto-switch to the executing run
        self._live_run_uid: str | None = None  # uid of the run currently running
        self._is_live: bool = False          # displayed run is incomplete
        self._sync_retries: int = 0          # bounded retries for writer lag
        self._follow_action: Any | None = None  # title-bar toggle (Task 5)
        super().__init__(parent)

    # ---- UI setup --------------------------------------------------------

    def _setup_ui(self) -> None:
        """Build the panel UI."""
        main_layout = QVBoxLayout()
        main_layout.setContentsMargins(4, 4, 4, 4)
        main_layout.setSpacing(4)

        # Toolbar
        toolbar = self._create_toolbar()
        main_layout.addLayout(toolbar)

        # Splitter: visualization + fit panel
        splitter = QSplitter(Qt.Orientation.Horizontal)

        # Visualization stack (placeholder + active widget)
        self._viz_stack = QStackedWidget()
        self._viz_stack.setMinimumSize(400, 300)

        placeholder = QWidget()
        placeholder_layout = QVBoxLayout(placeholder)
        placeholder_layout.addStretch()
        self._placeholder_label = QLabel("No data\nStart a scan to visualize")
        self._placeholder_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._placeholder_label.setStyleSheet(f"color: gray; font-size: {scaled_px(14)}px;")
        placeholder_layout.addWidget(self._placeholder_label)
        placeholder_layout.addStretch()
        self._viz_stack.addWidget(placeholder)

        splitter.addWidget(self._viz_stack)

        # Fit panel (collapsible)
        self._fit_panel = FitPanel()
        self._fit_panel.setMaximumWidth(300)
        self._fit_panel.setVisible(False)
        splitter.addWidget(self._fit_panel)

        main_layout.addWidget(splitter)

        container = QWidget()
        container.setLayout(main_layout)
        self._layout.addWidget(container)

        # "Follow live" toggle (title bar). Default on. Disengaged when the
        # user opens a run manually; re-engaging jumps to the executing run.
        self._follow_action = self.add_title_bar_button(
            "mdi6.access-point",
            "Follow live run",
            on_triggered=self._on_follow_toggled,
            checkable=True,
            checked=True,
        )

        # Subscribe to the engine document stream for live-run follow.
        self._connect_engine()

    def _create_toolbar(self) -> QHBoxLayout:
        """Create the toolbar with stream/field/viz combos and buttons."""
        toolbar = QHBoxLayout()
        toolbar.setSpacing(8)

        # Stream selector
        self._stream_label = QLabel("Stream:")
        self._stream_combo = QComboBox()
        self._stream_combo.setMinimumWidth(100)
        self._stream_combo.currentTextChanged.connect(self._on_stream_changed)
        toolbar.addWidget(self._stream_label)
        toolbar.addWidget(self._stream_combo)
        self._stream_label.hide()
        self._stream_combo.hide()

        # Field selector
        self._field_label = QLabel("Field:")
        self._field_combo = QComboBox()
        self._field_combo.setMinimumWidth(100)
        self._field_combo.currentTextChanged.connect(self._on_field_changed)
        toolbar.addWidget(self._field_label)
        toolbar.addWidget(self._field_combo)
        self._field_label.hide()
        self._field_combo.hide()

        # Visualization type selector
        viz_label = QLabel("Visualization:")
        self._viz_combo = QComboBox()
        self._viz_combo.setMinimumWidth(120)
        self._viz_combo.addItem("Auto", None)
        for cls in _widget_classes():
            self._viz_combo.addItem(cls.viz_display_name, cls.viz_name)
        self._viz_combo.currentIndexChanged.connect(self._on_viz_selection_changed)
        toolbar.addWidget(viz_label)
        toolbar.addWidget(self._viz_combo)

        toolbar.addStretch()

        # Fit button
        self._fit_btn = QPushButton("Fit Panel")
        self._fit_btn.setCheckable(True)
        self._fit_btn.toggled.connect(self._on_fit_toggled)
        toolbar.addWidget(self._fit_btn)

        # Export button
        export_btn = QPushButton("Export...")
        export_btn.clicked.connect(self._on_export_clicked)
        toolbar.addWidget(export_btn)

        return toolbar

    # ---- Main entry point ------------------------------------------------

    def _shown_uid(self) -> str | None:
        """uid of the currently displayed run, or None."""
        if self._entry is None:
            return None
        try:
            return self._entry.metadata.get("start", {}).get("uid")
        except Exception:
            return None

    def _set_follow_live(self, value: bool) -> None:
        """Set follow state and reflect it on the toggle button if it exists."""
        self._follow_live = value
        if self._follow_action is not None:
            # setChecked emits 'toggled', not 'triggered' — no recursion into
            # _on_follow_toggled (which is wired to 'triggered').
            self._follow_action.setChecked(value)

    def open_run(self, entry: Any, *, from_user: bool = True) -> None:
        """Open a tiled BlueskyRun for visualization.

        Scores all registered widget classes, creates the winner, and drives
        the set_run / set_stream / set_field flow.

        Args:
            entry: A tiled BlueskyRun (or compatible mapping).
            from_user: True when the open is an explicit user/agent action
                (Tiled browser, MCP open_run). Such opens disengage live-follow
                so a new scan won't yank the user off the run they chose. The
                auto-follow path passes False.
        """
        if from_user:
            self._set_follow_live(False)

        import time as _time
        t0 = _time.monotonic()

        # Switching runs: drop any live subscription before we re-point at the
        # new entry. (_set_current_widget also disconnects before the swap, but
        # do it here too so the OLD node's stream stops immediately.)
        if self._bridge is not None:
            self._bridge.disconnect()
        self._entry = entry

        classes = _widget_classes()

        # Score and pick winner (or honour manual override)
        best_cls = self._pick_widget_class(classes, entry)
        t1 = _time.monotonic()
        logger.debug("open_run: scoring took {:.1f}s → {}", t1 - t0, best_cls.viz_name if best_cls else None)

        if best_cls is None:
            logger.warning("No visualization can handle this run")
            return

        self._activate_widget(best_cls, entry)
        logger.debug("open_run: total {:.1f}s", _time.monotonic() - t0)

    # ---- Widget lifecycle ------------------------------------------------

    def _pick_widget_class(
        self,
        classes: list[type[BaseVisualization]],
        entry: Any,
    ) -> type[BaseVisualization] | None:
        """Score classes and return the best, or None."""
        # If user selected a specific viz type, use that
        viz_name = self._viz_combo.currentData()
        if viz_name is not None:
            for cls in classes:
                if cls.viz_name == viz_name:
                    return cls

        # Auto mode — highest score wins
        best_cls: type[BaseVisualization] | None = None
        best_score = 0
        for cls in classes:
            try:
                score = cls.can_handle(entry)
                if score > best_score:
                    best_score = score
                    best_cls = cls
            except Exception as e:
                logger.warning("Error in {}.can_handle: {}", cls.viz_name, e)
        return best_cls

    def _activate_widget(
        self,
        cls: type[BaseVisualization],
        entry: Any,
    ) -> None:
        """Create widget, wire combos, start refresh if needed."""
        import time as _time
        t0 = _time.monotonic()

        widget = cls()
        self._set_current_widget(widget)
        widget.set_run(entry)
        t1 = _time.monotonic()

        # Populate stream combo
        streams = widget.get_streams()
        t2 = _time.monotonic()
        logger.debug("_activate: create={:.1f}s get_streams={:.1f}s", t1 - t0, t2 - t1)

        self._stream_combo.blockSignals(True)
        self._stream_combo.clear()
        self._stream_combo.addItems(streams)
        self._stream_combo.blockSignals(False)

        visible = len(streams) > 1
        self._stream_label.setVisible(visible)
        self._stream_combo.setVisible(visible)

        if streams:
            widget.set_stream(streams[0])
            self._populate_field_combo()

        # Connect fit panel if the widget exposes fit_requested
        if hasattr(widget, "fit_requested"):
            widget.fit_requested.connect(lambda: self._fit_btn.setChecked(True))
            self._fit_panel.set_plot(widget)

        self.visualization_changed.emit(cls.viz_name)

        # Live runs (no stop doc) receive Tiled streaming pushes, but only
        # while the panel is active — see _update_streaming.
        self._is_live = entry.metadata.get("stop") is None
        self._update_streaming()

        logger.info("Opened run with visualization '{}'", cls.viz_display_name)

    def _set_current_widget(self, widget: BaseVisualization) -> None:
        """Swap the active visualization widget."""
        # Tear down the live subscription BEFORE the old widget/proxy is hidden
        # or removed (theater-teardown order): no push can land on a widget that
        # is mid-removal. The routing slot also guards on _current_widget, but
        # stopping the sub here is the clean ordering.
        if self._bridge is not None:
            self._bridge.disconnect()

        # Remove old widget and proxy (hide, don't delete — avoids pyqtgraph segfaults)
        if self._current_proxy is not None:
            if (
                theater_manager._overlay is not None
                and theater_manager._overlay._active_proxy is self._current_proxy
            ):
                theater_manager._overlay._finish_deactivate()
            theater_manager.unregister(self._current_proxy)
            self._viz_stack.removeWidget(self._current_proxy)
            self._current_proxy.setParent(None)
            self._current_proxy = None
        elif self._current_widget is not None:
            self._viz_stack.removeWidget(self._current_widget)
            self._current_widget.setParent(None)
            self._current_widget = None

        # Wrap in theater proxy and display
        proxy = TheaterProxy(widget)
        self._viz_stack.addWidget(proxy)
        self._viz_stack.setCurrentWidget(proxy)

        self._current_widget = widget
        self._current_proxy = proxy

    # ---- Combo handlers --------------------------------------------------

    def _populate_field_combo(self) -> None:
        """Fill the field combo from the current widget's get_fields().

        Does NOT call set_field — set_stream already picked the best field.
        This just syncs the combo UI.
        """
        if self._current_widget is None:
            return

        fields = self._current_widget.get_fields()
        self._field_combo.blockSignals(True)
        self._field_combo.clear()
        self._field_combo.addItems(fields)
        self._field_combo.blockSignals(False)

        visible = len(fields) > 1
        self._field_label.setVisible(visible)
        self._field_combo.setVisible(visible)

    def _on_stream_changed(self, stream_name: str) -> None:
        """User changed the stream combo."""
        if not stream_name or self._current_widget is None:
            return
        self._current_widget.set_stream(stream_name)
        self._populate_field_combo()
        # Follow the newly displayed node with the live subscription.
        self._update_streaming()

    def _on_field_changed(self, field_name: str) -> None:
        """User changed the field combo."""
        if not field_name or self._current_widget is None:
            return
        self._current_widget.set_field(field_name)
        # Re-point the subscription at the new field's node mid-run.
        self._update_streaming()

    def _on_viz_selection_changed(self, index: int) -> None:
        """User changed the visualization type combo."""
        if self._entry is None:
            return

        viz_name = self._viz_combo.itemData(index)
        classes = _widget_classes()

        if viz_name is None:
            # Auto — re-score and pick winner
            best_cls = self._pick_widget_class(classes, self._entry)
        else:
            # Manual — find matching class
            best_cls = None
            for cls in classes:
                if cls.viz_name == viz_name:
                    best_cls = cls
                    break

        if best_cls is None:
            return

        # _activate_widget -> _set_current_widget disconnects the old
        # subscription, and _update_streaming re-subscribes for the new viz.
        self._activate_widget(best_cls, self._entry)

    # ---- Live-run follow --------------------------------------------------

    def _on_follow_toggled(self, checked: bool) -> None:
        """Title-bar 'Follow live' toggled by the user."""
        self._follow_live = checked
        if checked:
            self._sync_to_live_run()

    def _resolve_entry(self, uid: str) -> Any | None:
        """Resolve a run uid to a Tiled entry, or None if unavailable.

        Returns None (never raises) when Tiled is disconnected or the uid is
        not yet written — the threaded TiledWriter lags the start document.
        """
        try:
            from lightfall.services.tiled_service import TiledService

            service = TiledService.get_instance()
            client = service._client
            if client is None or not service.is_connected:
                return None
            return client[uid]
        except KeyError:
            return None
        except Exception as e:
            logger.debug("Could not resolve live run {}: {}", uid, e)
            return None

    def _schedule_sync_retry(self) -> None:
        """Retry the live-run sync shortly, to ride out TiledWriter lag."""
        if self._sync_retries >= self.MAX_SYNC_RETRIES:
            logger.debug("Live-run sync gave up after {} retries", self._sync_retries)
            return
        self._sync_retries += 1
        QTimer.singleShot(750, self._sync_to_live_run)

    def _sync_to_live_run(self) -> None:
        """Switch the panel to the executing run when conditions allow.

        No-op unless following, a run is executing, and the panel is active.
        Defers (returns) when inactive — `_on_activated` re-runs this. Retries
        when the entry is not yet resolvable in Tiled.
        """
        if not (self._follow_live and self._live_run_uid and self.is_active):
            return
        if self._live_run_uid == self._shown_uid():
            return
        entry = self._resolve_entry(self._live_run_uid)
        if entry is None:
            self._schedule_sync_retry()
            return
        self._sync_retries = 0
        self.open_run(entry, from_user=False)

    # ---- Streaming updates (live runs) -----------------------------------

    def _ensure_bridge(self) -> StreamBridge:
        """Return the single StreamBridge, creating + wiring it on first use.

        The ``update_received`` signal is connected to the stable routing slot
        ``_on_stream_update`` EXACTLY ONCE here, never per-activation, so there
        is no duplicate delivery. The slot dispatches to whatever
        ``_current_widget`` is at delivery time — a connection left over from a
        previous run can therefore never reach an old viz.
        """
        if self._bridge is None:
            self._bridge = StreamBridge(self)
            self._bridge.update_received.connect(self._on_stream_update)
        return self._bridge

    @Slot(object)
    def _on_stream_update(self, update: Any) -> None:
        """Route a Tiled streaming push to the CURRENT viz (GUI thread).

        Reads ``self._current_widget`` fresh on every delivery; guards on None
        so a push arriving after teardown is a harmless no-op.
        """
        widget = self._current_widget
        if widget is None:
            return
        try:
            widget.on_stream_update(update)
        except Exception as e:
            logger.warning("Stream update error: {}", e)

    def _active_field(self) -> str:
        """The field the active viz is currently displaying ('' if none).

        Prefer the widget's own current field (set by set_field) over the combo
        text — the combo may be hidden (single-field streams) or lag the widget.
        """
        widget = self._current_widget
        field = getattr(widget, "_field_name", "") if widget is not None else ""
        if not field:
            field = self._field_combo.currentText()
        return field or ""

    @staticmethod
    def _structure_family(node: Any) -> str | None:
        """Best-effort ``structure_family`` of a Tiled client node (None if N/A).

        Tiled exposes a ``StructureFamily`` str-enum (``"array"``, ``"table"``,
        ``"container"``, ...) on every client node. Returns it as a plain ``str``
        (the enum compares equal to its string value) or None if the node
        doesn't carry one (a bare/stubbed object).
        """
        sf = getattr(node, "structure_family", None)
        if sf is None:
            return None
        try:
            return str(sf.value)  # StructureFamily(str, Enum) -> "array"/"table"/...
        except AttributeError:
            return str(sf)

    def _resolve_active_node(self) -> Any | None:
        """Resolve a **subscribable** Tiled node for the active viz.

        Tiled's WS push is only served by catalog node adapters that carry a
        ``make_ws_handler`` — namely **array** nodes and the stream's first-class
        **``internal`` table** node. A per-event *scalar* field is merely a
        COLUMN of ``internal``; ``stream[field]`` for such a field resolves to a
        plain column-facet ``ArrayAdapter`` with **no** ``make_ws_handler``, so
        subscribing to it 500s and hangs ``start_in_thread`` forever (Task 4d
        bug). We therefore never return a scalar column facet.

        Preference order:

        1. If the active field resolves to a first-class **array** node
           (``structure_family == "array"`` — e.g. the STXM map, image_stack's
           detector array), return THAT array node, so an override viz whose
           ``on_stream_update`` blits the pushed line gets ITS ``array-data``.
        2. Else return the stream's **``internal`` table node**
           (``run[stream]["internal"]``, ``structure_family == "table"``). Its
           per-event ``table-data`` pushes drive a ``refresh()`` for
           scalar/table-displaying viz (Plot1D / Scatter / Heatmap / Table).
        3. Otherwise (no array node, no ``internal``) log + return None: the
           bridge simply isn't connected — graceful, no 500 / no hang. We never
           fall back to a column facet.

        Returns the node to subscribe, or None — never raises.
        """
        if self._entry is None or self._current_widget is None:
            return None
        stream_name = self._stream_combo.currentText() or "primary"
        try:
            stream = self._entry[stream_name]
        except Exception as e:
            logger.debug("Could not resolve active stream '{}': {}", stream_name, e)
            return None

        # 1. Active field that is a first-class ARRAY node -> subscribe it.
        #    (A scalar field's child is a column facet, structure_family != "array",
        #    so it is rejected here and falls through to the internal table.)
        active_field = self._active_field()
        if active_field:
            try:
                child = stream[active_field]
            except Exception:
                child = None
            if child is not None and self._structure_family(child) == "array":
                return child

        # 2. The stream's `internal` table node (WS-subscribable; table-data
        #    pushes drive refresh for scalar/table viz). Verify it is a table —
        #    never return a non-table child masquerading under that key.
        try:
            internal = stream["internal"]
        except Exception:
            internal = None
        if internal is not None and self._structure_family(internal) == "table":
            return internal

        # 3. Nothing subscribable. Do NOT fall back to a scalar column facet
        #    (that 500s + hangs start_in_thread). Leave the bridge unconnected.
        logger.debug(
            "No subscribable node for stream '{}' (no array node, no internal "
            "table); stream bridge not connected",
            stream_name,
        )
        return None

    def _update_streaming(self) -> None:
        """Subscribe the bridge iff a live run is shown AND the panel is active.

        Replaces the old 2s poll. When the conditions hold, point the single
        bridge at the active data node (re-subscribing is safe — connect_node
        disconnects any prior sub first). Otherwise tear the subscription down.
        """
        if self._is_live and self.is_active and self._current_widget is not None:
            node = self._resolve_active_node()
            if node is None:
                # Can't resolve the node yet (writer lag). Leave any prior sub
                # torn down; _on_activated / re-open will retry.
                if self._bridge is not None:
                    self._bridge.disconnect()
                return
            bridge = self._ensure_bridge()
            try:
                bridge.connect_node(node)
                logger.debug("Streaming subscription active on '{}'", node)
            except Exception as e:
                logger.warning("Could not subscribe stream bridge: {}", e)
        elif self._bridge is not None:
            self._bridge.disconnect()

    def _on_activated(self) -> None:
        """Panel shown: pick up the live run, catch up, (re)subscribe."""
        self._sync_to_live_run()
        if self._is_live and self._current_widget is not None:
            # Catch up on rows already written before the subscription starts.
            try:
                self._current_widget.refresh()
            except Exception as e:
                logger.warning("Catch-up refresh error: {}", e)
        self._update_streaming()

    def _on_deactivated(self) -> None:
        """Panel hidden/collapsed: tear down the subscription (keep _is_live)."""
        if self._bridge is not None:
            self._bridge.disconnect()

    # ---- Fit / Export ----------------------------------------------------

    def _on_fit_toggled(self, checked: bool) -> None:
        """Toggle fit panel visibility."""
        self._fit_panel.setVisible(checked)

    def _on_export_clicked(self) -> None:
        """Export data from the current visualization."""
        if not self._current_widget:
            return

        from PySide6.QtWidgets import QFileDialog, QMessageBox

        if not hasattr(self._current_widget, "get_supported_export_formats"):
            return

        formats = self._current_widget.get_supported_export_formats()
        if not formats:
            return

        filter_parts = [f"{fmt.upper()} Files (*.{fmt})" for fmt in formats]
        filter_str = ";;".join(filter_parts)

        filename, _ = QFileDialog.getSaveFileName(
            self, "Export Data", "", filter_str,
        )
        if not filename:
            return

        ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else formats[0]

        try:
            data = self._current_widget.export_data(ext)
            with open(filename, "wb") as f:
                f.write(data)
            logger.info("Exported data to {}", filename)
        except Exception as e:
            logger.error("Export failed: {}", e)
            QMessageBox.warning(self, "Export Error", str(e))

    # ---- Engine document stream ------------------------------------------

    def _connect_engine(self) -> None:
        """Subscribe to the engine's document stream (best-effort)."""
        self._engine = None
        try:
            from lightfall.acquire import get_engine

            engine = get_engine()
            engine.sigOutput.connect(self._on_engine_document)
            self._engine = engine
        except Exception as e:
            logger.warning("Visualization panel could not subscribe to engine: {}", e)

    @Slot(str, dict)
    @gui_thread_only
    def _on_engine_document(self, name: str, doc: dict) -> None:
        """Track the executing run from start/descriptor/stop documents."""
        if name == "start":
            self._live_run_uid = doc.get("uid")
            self._sync_retries = 0
            self._sync_to_live_run()
        elif name == "descriptor":
            # Recover the live uid if we missed the start doc (panel built late).
            if self._live_run_uid is None:
                self._live_run_uid = doc.get("run_start")
                self._sync_retries = 0
            self._sync_to_live_run()
        elif name == "stop":
            if doc.get("run_start") == self._live_run_uid:
                self._live_run_uid = None
                # If we're displaying this run, settle it: a final refresh to
                # catch the last line, then mark complete and tear down the
                # streaming subscription (this replaces the old poll-tick
                # stop-doc check). _update_streaming would also disconnect, but
                # do it explicitly so the order is final-refresh then disconnect.
                if self._is_live and self._current_widget is not None:
                    try:
                        self._current_widget.refresh()
                    except Exception as e:
                        logger.warning("Final refresh error: {}", e)
                    self._is_live = False
                    if self._bridge is not None:
                        self._bridge.disconnect()

    # ---- Cleanup ---------------------------------------------------------

    def _on_closing(self) -> None:
        """Clean up on panel close."""
        if self._bridge is not None:
            self._bridge.disconnect()
        engine = getattr(self, "_engine", None)
        if engine is not None:
            try:
                engine.sigOutput.disconnect(self._on_engine_document)
            except (RuntimeError, TypeError):
                pass

    # ---- Introspection ---------------------------------------------------

    def _get_specific_introspection_data(self) -> dict[str, Any]:
        """Panel-specific introspection data for MCP tools."""
        return {
            "current_visualization": (
                self._current_widget.viz_name
                if self._current_widget
                else None
            ),
            "available_visualizations": [
                cls.viz_name for cls in _widget_classes()
            ],
        }

    # ---- Actions ---------------------------------------------------------

    def _get_available_actions(self) -> list[dict[str, Any]]:
        actions = super()._get_available_actions()
        actions.append(
            {
                "name": "open_run",
                "description": (
                    "Display a Bluesky run by uid in this panel. "
                    "kwargs: uid (str) — start-document uid of the run."
                ),
                "method": "open_run",
                "kwargs": {"uid": "Run uid (start document uid)"},
            }
        )
        return actions

    def invoke_action(self, action_name: str, **kwargs: Any) -> Any:
        if action_name == "open_run":
            uid = kwargs.get("uid")
            if not uid:
                raise ValueError("open_run requires 'uid'")
            from lightfall.services.tiled_service import TiledService

            service = TiledService.get_instance()
            client = service._client
            if client is None or not service.is_connected:
                raise ValueError("Tiled service is not connected")
            try:
                entry = client[uid]
            except KeyError as e:
                raise ValueError(
                    f"Run uid {uid!r} not found in Tiled catalog"
                ) from e
            self.open_run(entry)
            return {"uid": uid}
        return super().invoke_action(action_name, **kwargs)
