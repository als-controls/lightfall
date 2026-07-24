"""Tests for LeaseRequestDialog (caproxy lease UX, task 2)."""
from __future__ import annotations

import pytest

from lightfall.services.caproxy_lease_service import CaproxyLeaseService
from lightfall.ui.dialogs.lease_request_dialog import LeaseRequestDialog


@pytest.fixture
def service():
    CaproxyLeaseService.reset()
    svc = CaproxyLeaseService.get_instance()
    yield svc
    CaproxyLeaseService.reset()


def test_submit_blocked_with_no_patterns(qapp, service, monkeypatch):
    dialog = LeaseRequestDialog()
    dialog.show()
    called = []
    monkeypatch.setattr(service, "request_lease", lambda *a, **k: called.append((a, k)))

    dialog._on_submit()

    assert not called
    assert dialog._error_label.isVisible()
    assert "pattern" in dialog._error_label.text().lower()
    dialog.close()


def test_submit_blocked_with_invalid_bounds(qapp, service, monkeypatch):
    dialog = LeaseRequestDialog()
    dialog.show()
    dialog._patterns_edit.setPlainText("es:motor:z*")
    dialog._bounds_min_edit.setText("5")
    dialog._bounds_max_edit.setText("1")
    called = []
    monkeypatch.setattr(service, "request_lease", lambda *a, **k: called.append((a, k)))

    dialog._on_submit()

    assert not called
    assert dialog._error_label.isVisible()
    dialog.close()


def test_submit_blocked_with_non_numeric_bounds(qapp, service, monkeypatch):
    dialog = LeaseRequestDialog()
    dialog.show()
    dialog._patterns_edit.setPlainText("es:motor:z*")
    dialog._bounds_min_edit.setText("abc")
    called = []
    monkeypatch.setattr(service, "request_lease", lambda *a, **k: called.append((a, k)))

    dialog._on_submit()

    assert not called
    assert dialog._error_label.isVisible()
    dialog.close()


def test_successful_submit_shows_pending_state(qapp, service):
    dialog = LeaseRequestDialog()
    dialog.show()
    dialog._patterns_edit.setPlainText("es:motor:z*\nes:motor:x*")
    dialog._duration_spin.setValue(30)

    requests = []

    def fake_request_lease(patterns, duration_s, bounds_min=None, bounds_max=None, note=""):
        requests.append((patterns, duration_s, bounds_min, bounds_max, note))

    dialog._service.request_lease = fake_request_lease
    dialog._on_submit()

    assert requests == [(["es:motor:z*", "es:motor:x*"], 1800.0, None, None, "")]
    assert not dialog._submit_btn.isEnabled() or dialog._request_pending

    service.request_finished.emit({"lease_id": "abc123"})

    assert dialog._status_label.isVisible()
    assert "pending" in dialog._status_label.text().lower()
    assert "abc123" in dialog._status_label.text()
    assert dialog._cancel_btn.text() == "Close"
    dialog.close()


def test_request_failed_shows_error_verbatim(qapp, service):
    dialog = LeaseRequestDialog()
    dialog.show()
    dialog._patterns_edit.setPlainText("es:motor:z*")
    dialog._service.request_lease = lambda *a, **k: None
    dialog._on_submit()

    service.request_failed.emit("bounds_min exceeds facility limit")

    assert dialog._error_label.isVisible()
    assert dialog._error_label.text() == "bounds_min exceeds facility limit"
    assert dialog._submit_btn.isEnabled()
    dialog.close()


def test_signals_disconnected_on_close_no_crash(qapp, service):
    dialog = LeaseRequestDialog()
    dialog.show()
    dialog.close()

    # Service outlives the dialog; emitting afterwards must not raise or
    # touch deleted widgets (known segfault class in this repo).
    service.request_finished.emit({"lease_id": "xyz"})
    service.request_failed.emit("late error")

    qapp.processEvents()


def test_signals_disconnected_on_reject_no_crash(qapp, service):
    dialog = LeaseRequestDialog()
    dialog.show()
    dialog._patterns_edit.setPlainText("es:motor:z*")

    # Reject (Escape key) bypasses closeEvent, relying on finished signal
    # and WA_DeleteOnClose to clean up properly.
    dialog.reject()

    # Service outlives the dialog; emitting afterwards must not raise or
    # touch deleted widgets or trigger handlers.
    service.request_finished.emit({"lease_id": "xyz"})
    service.request_failed.emit("late error")

    qapp.processEvents()
