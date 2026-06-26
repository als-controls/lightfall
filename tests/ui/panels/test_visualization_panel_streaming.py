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
# 3b. Node resolution follows the ACTIVE FIELD (override-viz correctness)
# ---------------------------------------------------------------------------


class _StubStream:
    """Stand-in for a Tiled stream container with per-field child nodes."""

    def __init__(self, data_keys, hints_fields=None, children=None):
        self.metadata = {
            "data_keys": {k: {} for k in data_keys},
            "hints": {"fields": list(hints_fields or [])},
        }
        # child nodes keyed by field name; default to a unique sentinel each.
        self._children = children or {k: f"node:{k}" for k in data_keys}

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


def test_resolve_returns_active_field_node_not_first_child(qtbot, monkeypatch):
    """With a field selected, the bridge node is THAT field's child, not the first."""
    # data_keys order + hints would otherwise pick SampleX first.
    stream = _StubStream(
        data_keys=["SampleX", "Counter1"],
        hints_fields=["SampleX"],
    )
    panel = _panel_with_stream(qtbot, monkeypatch, stream, active_field="Counter1")
    node = panel._resolve_active_node()
    assert node == "node:Counter1"  # the ACTIVE FIELD's node, not "node:SampleX"


def test_resolve_falls_back_to_hinted_when_no_active_field(qtbot, monkeypatch):
    """No active field -> first hinted/data_keys child (prior behavior)."""
    stream = _StubStream(
        data_keys=["SampleX", "Counter1"],
        hints_fields=["SampleX"],
    )
    panel = _panel_with_stream(qtbot, monkeypatch, stream, active_field="")
    node = panel._resolve_active_node()
    assert node == "node:SampleX"  # first hinted child


def test_resolve_uses_combo_field_when_widget_field_blank(qtbot, monkeypatch):
    """When the widget exposes no field, fall back to the field combo text."""
    stream = _StubStream(data_keys=["SampleX", "Counter1"], hints_fields=["SampleX"])
    panel = _panel_with_stream(
        qtbot, monkeypatch, stream, active_field="", combo_field="Counter1"
    )
    node = panel._resolve_active_node()
    assert node == "node:Counter1"


def test_field_change_resubscribes_to_new_field_node(qtbot, monkeypatch):
    """_on_field_changed re-points the bridge at the new field's node."""
    stream = _StubStream(data_keys=["SampleX", "Counter1"], hints_fields=["SampleX"])
    panel = _panel_with_stream(qtbot, monkeypatch, stream, active_field="SampleX")
    panel._is_live = True
    panel.activate()  # is_active True
    bridge = MagicMock()
    monkeypatch.setattr(panel, "_ensure_bridge", lambda: bridge)

    # User picks Counter1: the widget updates its field, panel re-subscribes.
    def _set_field(name):
        panel._current_widget._field_name = name

    panel._current_widget.set_field.side_effect = _set_field
    panel._on_field_changed("Counter1")

    panel._current_widget.set_field.assert_called_once_with("Counter1")
    bridge.connect_node.assert_called_once_with("node:Counter1")


def test_stream_change_resubscribes(qtbot, monkeypatch):
    """_on_stream_changed re-points the bridge after switching streams."""
    stream = _StubStream(data_keys=["Counter1"], hints_fields=["Counter1"])
    panel = _panel_with_stream(qtbot, monkeypatch, stream, active_field="Counter1")
    panel._is_live = True
    panel.activate()
    bridge = MagicMock()
    monkeypatch.setattr(panel, "_ensure_bridge", lambda: bridge)
    monkeypatch.setattr(panel, "_populate_field_combo", lambda: None)

    panel._on_stream_changed("primary")
    bridge.connect_node.assert_called_once_with("node:Counter1")


def test_resolve_container_fallback_when_no_data_child(qtbot, monkeypatch):
    """No resolvable data child -> subscribe the stream container."""
    stream = _StubStream(data_keys=[], hints_fields=[])
    panel = _panel_with_stream(qtbot, monkeypatch, stream, active_field="")
    node = panel._resolve_active_node()
    assert node is stream  # the container itself


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
