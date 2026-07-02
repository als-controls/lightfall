# tests/visualization/test_stream_bridge.py
from PySide6.QtCore import QObject
from lightfall.visualization.stream_bridge import StreamBridge


class _FakeSub:
    """Stand-in for a Tiled subscription: lets the test fire a callback."""
    def __init__(self):
        self._cb = None
        self.disconnected = False

    def add_callback(self, cb):
        self._cb = cb

    def start_in_thread(self, **kw):
        pass

    def disconnect(self):
        self.disconnected = True

    def fire(self, update):
        self._cb(update)


class _FakeNode:
    def __init__(self, sub):
        self._sub = sub

    def subscribe(self):
        return self._sub


def test_bridge_emits_signal_on_update(qtbot):
    sub = _FakeSub()
    bridge = StreamBridge()
    received = []
    bridge.update_received.connect(received.append)
    bridge.connect_node(_FakeNode(sub))
    sub.fire({"type": "array-data", "row": 0})
    # signal delivery may be queued; process events
    qtbot.wait(50)
    assert received == [{"type": "array-data", "row": 0}]


class _FakeSubNewData:
    """Stand-in for the PRODUCTION Tiled subscription shape (sub.new_data.add_callback)."""
    class _NewData:
        def __init__(self):
            self._cb = None

        def add_callback(self, cb):
            self._cb = cb

    def __init__(self):
        self.new_data = self._NewData()
        self.disconnected = False

    def start_in_thread(self, **kw):
        pass

    def disconnect(self):
        self.disconnected = True

    def fire(self, update):
        self.new_data._cb(update)


class _FakeNodeNewData:
    def __init__(self, sub):
        self._sub = sub

    def subscribe(self):
        return self._sub


def test_bridge_emits_via_new_data_path(qtbot):
    sub = _FakeSubNewData()
    bridge = StreamBridge()
    received = []
    bridge.update_received.connect(received.append)
    bridge.connect_node(_FakeNodeNewData(sub))
    sub.fire({"type": "array-data", "row": 3})
    qtbot.wait(50)
    assert received == [{"type": "array-data", "row": 3}]


def test_bridge_disconnect_is_idempotent(qtbot):
    sub = _FakeSub()
    bridge = StreamBridge()
    bridge.connect_node(_FakeNode(sub))
    bridge.disconnect()
    bridge.disconnect()  # second call must not raise
    assert sub.disconnected is True
