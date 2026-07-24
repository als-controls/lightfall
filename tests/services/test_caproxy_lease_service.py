"""Unit tests for CaproxyLeaseService (request + polling)."""

from __future__ import annotations

import threading
import time
from unittest.mock import MagicMock, patch

import httpx
import pytest

from lightfall.services.caproxy_lease_service import CaproxyLeaseService


@pytest.fixture(autouse=True)
def _reset_service():
    CaproxyLeaseService.reset()
    yield
    CaproxyLeaseService.reset()


def _mock_response(status_code: int, json_data=None, text: str = ""):
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = json_data if json_data is not None else {}
    resp.text = text
    return resp


def test_request_lease_success_emits_request_finished(qtbot):
    service = CaproxyLeaseService.get_instance()
    success_payload = {"status": "pending", "lease_id": "abc123"}

    fake_client = MagicMock()
    fake_client.__enter__.return_value = fake_client
    fake_client.post.return_value = _mock_response(200, success_payload)

    with patch("httpx.Client", return_value=fake_client), \
            patch(
                "lightfall.services.caproxy_lease_service._auth_headers",
                return_value={},
            ):
        with qtbot.waitSignal(service.request_finished, timeout=5000) as blocker:
            service.request_lease(["motor:x"], duration_s=60.0)

    assert blocker.args == [success_payload]


def test_request_lease_400_emits_request_failed_with_server_text(qtbot):
    service = CaproxyLeaseService.get_instance()

    fake_client = MagicMock()
    fake_client.__enter__.return_value = fake_client
    fake_client.post.return_value = _mock_response(
        400, {"error": "pv_patterns not allowed for this beamline"}
    )

    with patch("httpx.Client", return_value=fake_client), \
            patch(
                "lightfall.services.caproxy_lease_service._auth_headers",
                return_value={},
            ):
        with qtbot.waitSignal(service.request_failed, timeout=5000) as blocker:
            service.request_lease(["motor:x"], duration_s=60.0)

    assert blocker.args == ["pv_patterns not allowed for this beamline"]


def test_request_lease_network_failure_emits_request_failed(qtbot):
    service = CaproxyLeaseService.get_instance()

    fake_client = MagicMock()
    fake_client.__enter__.return_value = fake_client
    fake_client.post.side_effect = ConnectionError("boom")

    with patch("httpx.Client", return_value=fake_client), \
            patch(
                "lightfall.services.caproxy_lease_service._auth_headers",
                return_value={},
            ):
        with qtbot.waitSignal(service.request_failed, timeout=5000) as blocker:
            service.request_lease(["motor:x"], duration_s=60.0)

    assert "boom" in blocker.args[0]


def test_request_lease_includes_bearer_header_when_token_present(qtbot):
    service = CaproxyLeaseService.get_instance()

    fake_client = MagicMock()
    fake_client.__enter__.return_value = fake_client
    fake_client.post.return_value = _mock_response(200, {"status": "ok"})

    with patch("httpx.Client", return_value=fake_client), \
            patch(
                "lightfall.services.caproxy_lease_service._resolve_token",
                return_value="tok-123",
            ):
        with qtbot.waitSignal(service.request_finished, timeout=5000):
            service.request_lease(["motor:x"], duration_s=60.0)

    _, kwargs = fake_client.post.call_args
    assert kwargs["headers"] == {"Authorization": "Bearer tok-123"}


def test_request_lease_no_auth_header_when_no_token(qtbot):
    service = CaproxyLeaseService.get_instance()

    fake_client = MagicMock()
    fake_client.__enter__.return_value = fake_client
    fake_client.post.return_value = _mock_response(200, {"status": "ok"})

    with patch("httpx.Client", return_value=fake_client), \
            patch(
                "lightfall.services.caproxy_lease_service._resolve_token",
                return_value=None,
            ):
        with qtbot.waitSignal(service.request_finished, timeout=5000):
            service.request_lease(["motor:x"], duration_s=60.0)

    _, kwargs = fake_client.post.call_args
    assert kwargs["headers"] == {}


def test_polling_emits_leases_updated_only_on_change(qtbot):
    service = CaproxyLeaseService.get_instance()

    leases_seq = [
        [{"id": "1", "state": "pending"}],
        [{"id": "1", "state": "pending"}],  # unchanged -> no signal
        [{"id": "1", "state": "active"}],  # changed -> signal
    ]
    call_count = {"n": 0}

    def fake_get(*args, **kwargs):
        idx = min(call_count["n"], len(leases_seq) - 1)
        call_count["n"] += 1
        return _mock_response(200, leases_seq[idx])

    fake_client = MagicMock()
    fake_client.__enter__.return_value = fake_client
    fake_client.get.side_effect = fake_get

    with patch("httpx.Client", return_value=fake_client), \
            patch(
                "lightfall.services.caproxy_lease_service._auth_headers",
                return_value={},
            ), patch(
                "lightfall.services.caproxy_lease_service.POLL_INTERVAL_S", 0.01
            ):
        received = []
        service.leases_updated.connect(received.append)

        service.start_polling()
        try:
            qtbot.waitUntil(lambda: len(received) >= 2, timeout=5000)
        finally:
            service.stop_polling()

    assert received[0] == [{"id": "1", "state": "pending"}]
    assert received[1] == [{"id": "1", "state": "active"}]


def test_polling_error_emits_once_per_failure_streak(qtbot):
    service = CaproxyLeaseService.get_instance()

    fake_client = MagicMock()
    fake_client.__enter__.return_value = fake_client
    fake_client.get.side_effect = ConnectionError("server unreachable")

    with patch("httpx.Client", return_value=fake_client), \
            patch(
                "lightfall.services.caproxy_lease_service._auth_headers",
                return_value={},
            ), patch(
                "lightfall.services.caproxy_lease_service.POLL_INTERVAL_S", 0.01
            ):
        errors = []
        service.poll_error.connect(errors.append)

        service.start_polling()
        try:
            qtbot.wait(300)
        finally:
            service.stop_polling()

    assert len(errors) == 1
    assert "server unreachable" in errors[0]


def test_stop_polling_unblocks_promptly(qtbot):
    """interrupt_callable must actually unblock the sleep — cancellation
    should not have to wait out a full POLL_INTERVAL_S cycle."""
    service = CaproxyLeaseService.get_instance()

    fake_client = MagicMock()
    fake_client.__enter__.return_value = fake_client
    fake_client.get.return_value = _mock_response(200, [])

    with patch("httpx.Client", return_value=fake_client), \
            patch(
                "lightfall.services.caproxy_lease_service._auth_headers",
                return_value={},
            ), patch(
                "lightfall.services.caproxy_lease_service.POLL_INTERVAL_S", 30.0
            ):
        service.start_polling()
        qtbot.wait(50)  # let the loop enter its first sleep
        service.stop_polling()
        # The poll thread should have honored the stop_event quickly rather
        # than waiting the full 30s POLL_INTERVAL_S.
        assert service._poll_thread is None


def test_stop_polling_aborts_inflight_get_promptly(qtbot):
    """A hung GET (server slow/unreachable) must not block stop_polling().

    interrupt_callable must close the in-flight httpx.Client (aborting the
    pending GET), not just set stop_event — otherwise QThreadFuture.cancel()
    blocks on its wait() for up to timeout_ms, freezing the GUI thread.
    """
    service = CaproxyLeaseService.get_instance()

    block_event = threading.Event()
    closed = threading.Event()

    fake_client = MagicMock()
    fake_client.__enter__.return_value = fake_client

    def hung_get(*args, **kwargs):
        # Simulate an in-flight request that only returns once the client
        # is closed (mimicking httpx aborting the socket on close()).
        block_event.wait(timeout=5.0)
        raise httpx.ReadError("client closed")

    def fake_close():
        closed.set()
        block_event.set()

    fake_client.get.side_effect = hung_get
    fake_client.close.side_effect = fake_close

    with patch("httpx.Client", return_value=fake_client), \
            patch(
                "lightfall.services.caproxy_lease_service._auth_headers",
                return_value={},
            ), patch(
                "lightfall.services.caproxy_lease_service.POLL_INTERVAL_S", 30.0
            ):
        service.start_polling()
        qtbot.waitUntil(lambda: fake_client.get.called, timeout=2000)

        start = time.monotonic()
        service.stop_polling()
        elapsed = time.monotonic() - start

    assert elapsed < 1.0, f"stop_polling() took {elapsed:.2f}s — GUI-thread stall"
    assert closed.is_set()
    assert service._poll_thread is None
