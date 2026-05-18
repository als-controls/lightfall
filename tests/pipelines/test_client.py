"""Tests for the LUCID-side PipelineClient."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from lucid.pipelines.client import PipelineClient


@pytest.fixture
def mock_ipc():
    return MagicMock()


def test_client_mints_key_and_submits(mock_ipc, qtbot):
    with patch("lucid.pipelines.client.mint_job_key") as mint:
        mint.return_value = MagicMock(
            secret="hex" * 16,
            first_eight="hexhexhe",
            expires_at="2026-05-17T00:00:00Z",
            scopes=("read:metadata",),
        )
        client = PipelineClient(
            ipc=mock_ipc,
            host="testhost",
            tiled_url="https://t/api/v1",
            bearer_provider=lambda: "fake-bearer",
        )
        job_id = client.submit(
            pipeline="reduce_saxs",
            input_run_uid="raw",
            parameters={"k": 1},
            input_access_blob={"tags": ["x"]},
            user_id="u",
        )

    mint.assert_called_once()
    mock_ipc.request.assert_called_once()
    args, kwargs = mock_ipc.request.call_args
    subject = args[0]
    payload = args[1]
    assert subject == "lucid.pipeline.testhost"
    assert payload["pipeline"] == "reduce_saxs"
    assert payload["api_key"].startswith("hex")
    assert payload["job_id"] == job_id


def test_client_emits_signal_on_progress_event(mock_ipc, qtbot):
    client = PipelineClient(
        ipc=mock_ipc,
        host="testhost",
        tiled_url="https://t/api/v1",
        bearer_provider=lambda: "tok",
    )
    received = []
    client.sigJobProgress.connect(lambda ev: received.append(ev))

    client._on_progress(
        subject="lucid.pipeline.testhost.progress",
        data={
            "job_id": "j1", "status": "running", "detail": "x",
            "input_run_uid": "raw", "output_run_uids": [],
            "executed_notebook_path": "", "error": None, "ts": "2026-05-15T20:14:42Z",
        },
        reply=None,
    )

    assert len(received) == 1
    assert received[0]["status"] == "running"


def test_client_subscribes_to_progress_on_construction(mock_ipc, qtbot):
    PipelineClient(
        ipc=mock_ipc,
        host="testhost",
        tiled_url="https://t/api/v1",
        bearer_provider=lambda: "tok",
    )
    mock_ipc.subscribe.assert_called_once()
    args, kwargs = mock_ipc.subscribe.call_args
    assert args[0] == "lucid.pipeline.testhost.progress"
