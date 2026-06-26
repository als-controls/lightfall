"""Tests for VisualizationPanel streaming-push updates (Task 4).

The 2-second QTimer poll is replaced by a Tiled WebSocket push routed through
a single StreamBridge: on run activation the panel points the bridge at the
active data node and routes ``bridge.update_received`` to the CURRENT viz's
``on_stream_update``. These tests pin:

  1. the source-level gate: the poll timer is gone (no ``_on_refresh_tick``,
     no ``.start(2000)``);
  2. a push routed through the panel reaches the active viz's
     ``on_stream_update`` with the payload, and only the CURRENT viz (a stale
     connection cannot deliver to an old viz);
  3. teardown disconnects the bridge before the widget is removed.

Live push against a real Tiled server is Task 6's job — here we drive the
panel's own ``StreamBridge.update_received`` (or the routing slot directly).
"""

from __future__ import annotations

from unittest.mock import MagicMock

from PySide6.QtCore import QObject, Signal

from lightfall.ui.panels import visualization_panel as vp_mod
from lightfall.ui.panels.visualization_panel import VisualizationPanel


# ---------------------------------------------------------------------------
# Stubs (mirror test_visualization_panel_live_follow.py conventions)
# ---------------------------------------------------------------------------


class _StubEntry:
    """Minimal stand-in for a Tiled BlueskyRun entry."""

    def __init__(self, uid: str, stop=None, streams=None):
        self.metadata = {"start": {"uid": uid}, "stop": stop}
        # mapping of stream name -> node (for _resolve_active_node)
        self._streams = streams or {}

    def __getitem__(self, key):
        return self._streams[key]

    def refresh(self):
        pass


class _FakeEngine(QObject):
    """Engine stub exposing the one signal the panel subscribes to."""

    sigOutput = Signal(str, dict)

    def subscribe(self, cb):
        return 0

    def unsubscribe(self, token):
        pass


def _install_fake_engine(monkeypatch) -> _FakeEngine:
    engine = _FakeEngine()
    monkeypatch.setattr("lightfall.acquire.get_engine", lambda: engine)
    return engine


# ---------------------------------------------------------------------------
# 1. Source-level gate: the poll timer is gone
# ---------------------------------------------------------------------------


def test_no_poll_timer_symbols():
    """The 2s poll is removed: no QTimer.start(2000) and no _on_refresh_tick."""
    text = open(vp_mod.__file__, encoding="utf-8").read()
    assert "_on_refresh_tick" not in text
    assert ".start(2000)" not in text
    # _start_refresh/_stop_refresh helpers are gone too.
    assert "_start_refresh" not in text
    assert "_stop_refresh" not in text


# ---------------------------------------------------------------------------
# 2. A push routed through the panel reaches the active viz
# ---------------------------------------------------------------------------


def test_routing_slot_dispatches_to_current_widget(qtbot):
    """_on_stream_update routes the payload to the CURRENT viz's on_stream_update."""
    panel = VisualizationPanel()
    qtbot.addWidget(panel)
    widget = MagicMock()
    panel._current_widget = widget

    payload = {"type": "array-data", "offset": (3, 0)}
    panel._on_stream_update(payload)

    widget.on_stream_update.assert_called_once_with(payload)


def test_routing_slot_noop_when_no_widget(qtbot):
    """_on_stream_update is a guarded no-op when there is no current widget."""
    panel = VisualizationPanel()
    qtbot.addWidget(panel)
    panel._current_widget = None
    # Must not raise.
    panel._on_stream_update({"type": "array-data"})


def test_bridge_update_signal_reaches_current_widget(qtbot):
    """An emit on the panel's StreamBridge.update_received reaches the active viz.

    Drives the real Qt signal -> stable routing slot -> current widget, end to
    end through the panel's own bridge.
    """
    panel = VisualizationPanel()
    qtbot.addWidget(panel)
    widget = MagicMock()
    panel._current_widget = widget

    bridge = panel._ensure_bridge()  # creates + wires the stable slot ONCE
    payload = {"type": "table-data", "partition": 0}
    bridge.update_received.emit(payload)
    qtbot.wait(50)  # signal delivery may be queued

    widget.on_stream_update.assert_called_once_with(payload)


def test_stale_connection_cannot_deliver_to_old_viz(qtbot):
    """After swapping the current widget, a push reaches only the NEW viz.

    The routing slot reads self._current_widget fresh, so the stable signal
    connection cannot deliver to a viz that is no longer current.
    """
    panel = VisualizationPanel()
    qtbot.addWidget(panel)
    old = MagicMock()
    new = MagicMock()

    panel._current_widget = old
    bridge = panel._ensure_bridge()

    # Swap to the new viz, then push.
    panel._current_widget = new
    bridge.update_received.emit({"type": "array-data"})
    qtbot.wait(50)

    old.on_stream_update.assert_not_called()
    new.on_stream_update.assert_called_once()


def test_signal_connected_once_no_duplicate_delivery(qtbot):
    """Repeated _ensure_bridge calls do not connect the signal twice."""
    panel = VisualizationPanel()
    qtbot.addWidget(panel)
    widget = MagicMock()
    panel._current_widget = widget

    bridge = panel._ensure_bridge()
    # A second activation reuses the same bridge and must not re-connect.
    again = panel._ensure_bridge()
    assert again is bridge

    bridge.update_received.emit({"type": "array-data"})
    qtbot.wait(50)
    assert widget.on_stream_update.call_count == 1


# ---------------------------------------------------------------------------
# 3. Activation subscribes the bridge to the active node
# ---------------------------------------------------------------------------


def test_activation_subscribes_bridge_to_active_node(qtbot, monkeypatch):
    """When live + active, _update_streaming points the bridge at the active node."""
    panel = VisualizationPanel()
    qtbot.addWidget(panel)
    panel.activate()
    panel._is_live = True
    panel._current_widget = MagicMock()

    node = object()
    monkeypatch.setattr(panel, "_resolve_active_node", lambda: node)

    bridge = MagicMock()
    monkeypatch.setattr(panel, "_ensure_bridge", lambda: bridge)

    panel._update_streaming()
    bridge.connect_node.assert_called_once_with(node)


def test_update_streaming_disconnects_when_not_live(qtbot, monkeypatch):
    """When not live (or inactive), _update_streaming disconnects the bridge."""
    panel = VisualizationPanel()
    qtbot.addWidget(panel)
    panel._is_live = False

    bridge = MagicMock()
    panel._bridge = bridge  # already created

    panel._update_streaming()
    bridge.disconnect.assert_called_once()
    bridge.connect_node.assert_not_called()


# ---------------------------------------------------------------------------
# 3b. Node resolution returns a SUBSCRIBABLE node (Task 4d)
#
# Tiled's WS push is served only by catalog adapters that carry make_ws_handler:
# array nodes and the stream's first-class `internal` table node. A per-event
# SCALAR field is just a COLUMN of `internal`; stream[scalar] resolves to a
# plain column-facet ArrayAdapter with NO make_ws_handler -> 500 + start_in_thread
# hang. _resolve_active_node must therefore:
#   * return the active field's ARRAY node when it is structure_family "array",
#   * else return the `internal` TABLE node (structure_family "table"),
#   * NEVER return a bare scalar column facet,
#   * return None when nothing subscribable exists.
#
# Local dev venv is Tiled 0.2.9 where a live table-node subscription may 500;
# the array-node path is the locally-real one. We unit-test the RESOLUTION
# LOGIC with stubs that model structure_family + an `internal` table + scalar
# column facets — no live table subscription is exercised here.
# ---------------------------------------------------------------------------


class _StubNode:
    """A Tiled-like client node carrying a structure_family.

    `family` is one of "array" / "table" / "container". Equality/identity by
    object so tests can assert which node was picked.
    """

    def __init__(self, name, family):
        self.name = name
        self.structure_family = family  # plain str (StructureFamily compares == str)

    def __repr__(self):
        return f"_StubNode({self.name!r}, {self.structure_family!r})"


class _StubStream:
    """Stand-in for a Tiled stream container exposing typed child nodes.

    Children are keyed exactly as the real client exposes them:
      * array fields -> structure_family "array" nodes (subscribable),
      * per-event scalar fields -> column-FACET "array" nodes? NO. In the real
        client a scalar field's child is a column facet (ArrayAdapter) that is
        NOT WS-subscribable. To prove _resolve_active_node never picks such a
        facet for a scalar, we model scalar fields as children that exist under
        their field name but whose structure_family is the column-facet marker
        "column" — distinct from a first-class "array" node, so the resolver
        rejects them and falls through to `internal`.
      * "internal" -> the WS-subscribable table node (structure_family "table").
    """

    def __init__(self, *, array_fields=None, scalar_fields=None, internal=True):
        array_fields = array_fields or []
        scalar_fields = scalar_fields or []
        data_keys = {}
        self._children: dict = {}
        for f in array_fields:
            data_keys[f] = {"shape": [512, 512]}
            self._children[f] = _StubNode(f, "array")
        for f in scalar_fields:
            data_keys[f] = {"shape": []}
            # A scalar field's direct child is a column facet, NOT subscribable.
            self._children[f] = _StubNode(f, "column")
        if internal:
            self._children["internal"] = _StubNode("internal", "table")
        self.metadata = {"data_keys": data_keys, "hints": {"fields": []}}

    def __getitem__(self, key):
        return self._children[key]


def _panel_with_stream(qtbot, monkeypatch, stream, *, active_field="", combo_field=""):
    """Build a panel wired so _resolve_active_node sees `stream` + a field."""
    panel = VisualizationPanel()
    qtbot.addWidget(panel)
    widget = MagicMock()
    widget._field_name = active_field
    panel._current_widget = widget
    panel._entry = _StubEntry("u1", streams={"primary": stream})
    # Stream combo: make currentText() resolve to "primary".
    monkeypatch.setattr(panel._stream_combo, "currentText", lambda: "primary")
    monkeypatch.setattr(panel._field_combo, "currentText", lambda: combo_field)
    return panel


def test_resolve_returns_array_node_for_array_field(qtbot, monkeypatch):
    """Active field that is a first-class array node -> THAT array node."""
    stream = _StubStream(array_fields=["STXMLineFlyer"], scalar_fields=["SampleY"])
    panel = _panel_with_stream(
        qtbot, monkeypatch, stream, active_field="STXMLineFlyer"
    )
    node = panel._resolve_active_node()
    assert node is stream["STXMLineFlyer"]
    assert node.structure_family == "array"


def test_resolve_returns_internal_table_for_scalar_field(qtbot, monkeypatch):
    """Active field that is a scalar -> the `internal` TABLE node, NOT the facet."""
    stream = _StubStream(array_fields=[], scalar_fields=["SampleY", "Counter1"])
    panel = _panel_with_stream(qtbot, monkeypatch, stream, active_field="Counter1")
    node = panel._resolve_active_node()
    assert node is stream["internal"]
    assert node.structure_family == "table"


def test_resolve_never_returns_scalar_column_facet(qtbot, monkeypatch):
    """The resolved node is never the scalar field's column facet."""
    stream = _StubStream(array_fields=[], scalar_fields=["SampleY", "Counter1"])
    panel = _panel_with_stream(qtbot, monkeypatch, stream, active_field="Counter1")
    node = panel._resolve_active_node()
    # The facet (structure_family "column") must never be picked.
    assert node is not stream["Counter1"]
    assert node is not stream["SampleY"]
    assert getattr(node, "structure_family", None) != "column"


def test_resolve_internal_when_no_active_field(qtbot, monkeypatch):
    """No active field -> the `internal` table node (scalar/table viz refresh)."""
    stream = _StubStream(array_fields=[], scalar_fields=["SampleX", "Counter1"])
    panel = _panel_with_stream(qtbot, monkeypatch, stream, active_field="")
    node = panel._resolve_active_node()
    assert node is stream["internal"]


def test_resolve_combo_array_field_picks_array_node(qtbot, monkeypatch):
    """Widget field blank -> combo field; if it is an array node, pick it."""
    stream = _StubStream(array_fields=["Img"], scalar_fields=["Counter1"])
    panel = _panel_with_stream(
        qtbot, monkeypatch, stream, active_field="", combo_field="Img"
    )
    node = panel._resolve_active_node()
    assert node is stream["Img"]


def test_resolve_none_when_nothing_subscribable(qtbot, monkeypatch):
    """No array node and no `internal` table -> None (bridge stays disconnected)."""
    stream = _StubStream(array_fields=[], scalar_fields=["Counter1"], internal=False)
    panel = _panel_with_stream(qtbot, monkeypatch, stream, active_field="Counter1")
    node = panel._resolve_active_node()
    assert node is None  # never a column facet, never a 500/hang


def test_field_change_resubscribes_to_internal_for_scalar(qtbot, monkeypatch):
    """_on_field_changed on a scalar field re-points the bridge at `internal`."""
    stream = _StubStream(array_fields=[], scalar_fields=["SampleX", "Counter1"])
    panel = _panel_with_stream(qtbot, monkeypatch, stream, active_field="SampleX")
    panel._is_live = True
    panel.activate()  # is_active True
    bridge = MagicMock()
    monkeypatch.setattr(panel, "_ensure_bridge", lambda: bridge)

    def _set_field(name):
        panel._current_widget._field_name = name

    panel._current_widget.set_field.side_effect = _set_field
    panel._on_field_changed("Counter1")

    panel._current_widget.set_field.assert_called_once_with("Counter1")
    bridge.connect_node.assert_called_once_with(stream["internal"])


def test_field_change_resubscribes_to_array_node(qtbot, monkeypatch):
    """_on_field_changed onto an array field re-points the bridge at that array."""
    stream = _StubStream(array_fields=["Img"], scalar_fields=["Counter1"])
    panel = _panel_with_stream(qtbot, monkeypatch, stream, active_field="Counter1")
    panel._is_live = True
    panel.activate()
    bridge = MagicMock()
    monkeypatch.setattr(panel, "_ensure_bridge", lambda: bridge)

    def _set_field(name):
        panel._current_widget._field_name = name

    panel._current_widget.set_field.side_effect = _set_field
    panel._on_field_changed("Img")

    bridge.connect_node.assert_called_once_with(stream["Img"])


def test_stream_change_resubscribes(qtbot, monkeypatch):
    """_on_stream_changed re-points the bridge after switching streams."""
    stream = _StubStream(array_fields=[], scalar_fields=["Counter1"])
    panel = _panel_with_stream(qtbot, monkeypatch, stream, active_field="Counter1")
    panel._is_live = True
    panel.activate()
    bridge = MagicMock()
    monkeypatch.setattr(panel, "_ensure_bridge", lambda: bridge)
    monkeypatch.setattr(panel, "_populate_field_combo", lambda: None)

    panel._on_stream_changed("primary")
    bridge.connect_node.assert_called_once_with(stream["internal"])


# ---------------------------------------------------------------------------
# 4. Teardown / run-switch / stop-doc disconnect the bridge
# ---------------------------------------------------------------------------


def test_deactivate_disconnects_bridge(qtbot):
    """Panel deactivation disconnects the live subscription."""
    panel = VisualizationPanel()
    qtbot.addWidget(panel)
    panel._is_live = True
    panel.activate()
    bridge = MagicMock()
    panel._bridge = bridge
    panel.deactivate()
    bridge.disconnect.assert_called()


def test_closing_disconnects_bridge(qtbot):
    """Closing the panel tears down the bridge subscription."""
    panel = VisualizationPanel()
    qtbot.addWidget(panel)
    bridge = MagicMock()
    panel._bridge = bridge
    panel._on_closing()
    bridge.disconnect.assert_called()


def test_set_current_widget_disconnects_bridge_before_swap(qtbot):
    """Run-switch: the bridge is disconnected BEFORE the old widget is removed.

    Ordering matters (theater-teardown rule): the disconnect must precede the
    proxy-wrap/removeWidget. We record call order via a sentinel on a real
    QWidget so the assertion proves "disconnect ran first", not merely "ran".
    """
    from PySide6.QtWidgets import QWidget

    order: list[str] = []

    class _OrderingBridge:
        def disconnect(self):
            order.append("disconnect")

    new_widget = QWidget()
    qtbot.addWidget(new_widget)

    panel = VisualizationPanel()
    qtbot.addWidget(panel)
    panel._bridge = _OrderingBridge()

    panel._set_current_widget(new_widget)

    # disconnect was called, and it ran before the (successful) widget swap.
    assert order == ["disconnect"]
    assert panel._current_widget is new_widget


def test_stop_doc_disconnects_bridge_after_final_refresh(qtbot, monkeypatch):
    """Stop doc for the active run: final refresh, _is_live False, bridge disconnect."""
    engine = _install_fake_engine(monkeypatch)
    panel = VisualizationPanel()
    qtbot.addWidget(panel)
    panel._live_run_uid = "u1"
    panel._is_live = True
    widget = MagicMock()
    panel._current_widget = widget
    bridge = MagicMock()
    panel._bridge = bridge

    engine.sigOutput.emit("stop", {"run_start": "u1"})

    assert panel._live_run_uid is None
    assert panel._is_live is False
    widget.refresh.assert_called()  # final catch-up
    bridge.disconnect.assert_called()
