"""Tests for NATSPlanBridge using a mock IPCService."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from lightfall.acquire.nats_bridge import NATSPlanBridge


@pytest.fixture
def mock_ipc():
    ipc = MagicMock()
    def make_sub(*args, **kwargs):
        sub = MagicMock()
        sub.unsubscribe = MagicMock()
        return sub
    ipc.subscribe.side_effect = make_sub
    ipc.publish = MagicMock()
    return ipc


class TestNATSPlanBridge:
    def test_subscribe_creates_queue(self, mock_ipc):
        bridge = NATSPlanBridge(mock_ipc)
        bridge.subscribe("tsuchinoko.targets")
        assert "tsuchinoko.targets" in bridge._queues
        mock_ipc.subscribe.assert_called_once()

    def test_subscribe_idempotent(self, mock_ipc):
        bridge = NATSPlanBridge(mock_ipc)
        bridge.subscribe("tsuchinoko.targets")
        bridge.subscribe("tsuchinoko.targets")
        assert mock_ipc.subscribe.call_count == 1

    def test_try_get_empty_returns_none(self, mock_ipc):
        bridge = NATSPlanBridge(mock_ipc)
        bridge.subscribe("tsuchinoko.targets")
        assert bridge.try_get("tsuchinoko.targets") is None

    def test_try_get_unknown_subject_returns_none(self, mock_ipc):
        bridge = NATSPlanBridge(mock_ipc)
        assert bridge.try_get("nonexistent") is None

    def test_callback_puts_into_queue(self, mock_ipc):
        bridge = NATSPlanBridge(mock_ipc)
        bridge.subscribe("tsuchinoko.targets")
        call_kwargs = mock_ipc.subscribe.call_args
        callback = call_kwargs.kwargs.get("callback") or call_kwargs.args[1]

        callback("tsuchinoko.targets", {"iteration": 1}, None)
        msg = bridge.try_get("tsuchinoko.targets")
        assert msg == {"iteration": 1}
        assert bridge.try_get("tsuchinoko.targets") is None

    def test_publish_forwards_to_ipc(self, mock_ipc):
        bridge = NATSPlanBridge(mock_ipc)
        bridge.publish("lightfall.adaptive.measured", {"iteration": 1})
        mock_ipc.publish.assert_called_once_with(
            "lightfall.adaptive.measured", {"iteration": 1}
        )

    def test_cleanup_unsubscribes(self, mock_ipc):
        bridge = NATSPlanBridge(mock_ipc)
        bridge.subscribe("a")
        bridge.subscribe("b")
        bridge.cleanup()
        assert len(bridge._subscriptions) == 0
        assert len(bridge._queues) == 0

    def test_cleanup_tolerates_unsubscribe_error(self, mock_ipc):
        bridge = NATSPlanBridge(mock_ipc)
        bridge.subscribe("a")
        bridge._subscriptions[0].unsubscribe.side_effect = Exception("boom")
        bridge.cleanup()  # should not raise
