"""Tests for the LUCID-side PipelineClient (auth-v2)."""
from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest

from lightfall.pipelines.client import PipelineClient


def _fake_key(secret: str = "s" * 48, first_eight: str = "ssssssss",
              expires_at: datetime | None = None):
    """Build a fake MintedKey-shaped mock."""
    m = MagicMock()
    m.secret = secret
    m.first_eight = first_eight
    m.expires_at = expires_at
    return m


@pytest.fixture
def mock_ipc():
    return MagicMock()


@pytest.fixture
def key_provider():
    """Returns a key_provider that yields a fresh MintedKey for any service."""
    expires = datetime(2026, 5, 25, tzinfo=timezone.utc)
    return MagicMock(return_value=_fake_key(expires_at=expires))


def test_client_submits_using_session_key(mock_ipc, key_provider, qtbot):
    mock_ipc.request.return_value = {"status": "queued"}
    client = PipelineClient(
        ipc=mock_ipc, host="testhost",
        tiled_url="https://t/api/v1",
        key_provider=key_provider,
    )
    job_id = client.submit(
        pipeline="reduce_saxs",
        input_run_uid="raw-abc",
        parameters={"k": 1},
        input_access_blob={"tags": ["x"]},
        user_id="u",
    )

    key_provider.assert_called_with("tiled")
    mock_ipc.request.assert_called_once()
    args, kwargs = mock_ipc.request.call_args
    assert args[0] == "lightfall.pipeline.testhost"
    payload = args[1]
    assert payload["pipeline"] == "reduce_saxs"
    assert payload["api_key"] == "s" * 48
    assert payload["api_key_expires_at"] == "2026-05-25T00:00:00+00:00"
    assert payload["job_id"] == job_id


def test_client_raises_when_no_tiled_key_cached(mock_ipc, qtbot):
    key_provider = MagicMock(return_value=None)
    client = PipelineClient(
        ipc=mock_ipc, host="testhost",
        tiled_url="https://t/api/v1",
        key_provider=key_provider,
    )
    with pytest.raises(RuntimeError, match="No Tiled API key"):
        client.submit(
            pipeline="p", input_run_uid="r",
            parameters={}, input_access_blob={}, user_id="u",
        )
    mock_ipc.request.assert_not_called()


def test_client_emits_signal_on_progress_event(mock_ipc, key_provider, qtbot):
    client = PipelineClient(
        ipc=mock_ipc, host="testhost",
        tiled_url="https://t/api/v1",
        key_provider=key_provider,
    )
    received = []
    client.sigJobProgress.connect(lambda ev: received.append(ev))

    client._on_progress(
        subject="lightfall.pipeline.testhost.progress",
        data={
            "job_id": "j1", "status": "running", "detail": "x",
            "input_run_uid": "raw", "output_run_uids": [],
            "executed_notebook_path": "", "error": None, "ts": "2026-05-15T20:14:42Z",
        },
        reply=None,
    )

    assert len(received) == 1
    assert received[0]["status"] == "running"


def test_client_subscribes_to_progress_on_construction(mock_ipc, key_provider, qtbot):
    PipelineClient(
        ipc=mock_ipc, host="testhost",
        tiled_url="https://t/api/v1",
        key_provider=key_provider,
    )
    mock_ipc.subscribe.assert_called_once()
    args, kwargs = mock_ipc.subscribe.call_args
    assert args[0] == "lightfall.pipeline.testhost.progress"


def test_client_raises_when_executor_times_out(mock_ipc, key_provider, qtbot):
    mock_ipc.request.return_value = None  # IPCService returns None on timeout
    client = PipelineClient(
        ipc=mock_ipc, host="h",
        tiled_url="https://t/api/v1",
        key_provider=key_provider,
    )
    with pytest.raises(RuntimeError, match="did not respond"):
        client.submit(
            pipeline="p", input_run_uid="r",
            parameters={}, input_access_blob={}, user_id="u",
        )


def test_client_drops_malformed_progress_event(mock_ipc, key_provider, qtbot):
    client = PipelineClient(
        ipc=mock_ipc, host="h",
        tiled_url="https://t/api/v1",
        key_provider=key_provider,
    )
    received = []
    client.sigJobProgress.connect(lambda ev: received.append(ev))

    client._on_progress(subject="lightfall.pipeline.h.progress", data={}, reply=None)
    client._on_progress(
        subject="lightfall.pipeline.h.progress",
        data={"status": "running"},
        reply=None,
    )

    assert received == []


def test_client_emits_queued_signal_with_input_run_uid(mock_ipc, key_provider, qtbot):
    mock_ipc.request.return_value = {"status": "queued"}
    client = PipelineClient(
        ipc=mock_ipc, host="h",
        tiled_url="https://t/api/v1",
        key_provider=key_provider,
    )
    captured = []
    client.sigJobQueued.connect(lambda ev: captured.append(ev))
    client.submit(
        pipeline="p", input_run_uid="RAW123",
        parameters={}, input_access_blob={}, user_id="u",
    )

    assert len(captured) == 1
    assert captured[0]["input_run_uid"] == "RAW123"
    assert captured[0]["pipeline"] == "p"
