"""Tests for the Pipeline Jobs dock panel."""
from PySide6.QtCore import QObject, Signal

import pytest

from lightfall.ui.panels.pipeline_jobs_panel import (
    PipelineJobsDockPanel,
    PipelineJobsPanel,
)


class FakeClient(QObject):
    sigJobQueued = Signal(dict)
    sigJobProgress = Signal(dict)
    sigJobCompleted = Signal(dict)
    sigJobFailed = Signal(dict)


def test_panel_adds_row_on_queued(qtbot):
    client = FakeClient()
    panel = PipelineJobsPanel(client=client)
    qtbot.addWidget(panel)
    client.sigJobQueued.emit({"job_id": "j1", "pipeline": "reduce_saxs"})
    assert panel.row_count() == 1
    assert panel.row(0)["job_id"] == "j1"


def test_panel_updates_row_on_progress(qtbot):
    client = FakeClient()
    panel = PipelineJobsPanel(client=client)
    qtbot.addWidget(panel)
    client.sigJobQueued.emit({"job_id": "j1", "pipeline": "p"})
    client.sigJobProgress.emit({"job_id": "j1", "status": "running", "detail": "x"})
    assert panel.row(0)["status"] == "running"


def test_panel_shows_completed_outputs(qtbot):
    client = FakeClient()
    panel = PipelineJobsPanel(client=client)
    qtbot.addWidget(panel)
    client.sigJobQueued.emit({"job_id": "j1", "pipeline": "p"})
    client.sigJobCompleted.emit({
        "job_id": "j1", "status": "completed",
        "output_run_uids": ["o1", "o2"],
        "executed_notebook_path": "/d/r/j1.ipynb",
    })
    assert panel.row(0)["status"] == "completed"
    assert panel.row(0)["output_count"] == 2


def test_panel_marks_failed(qtbot):
    client = FakeClient()
    panel = PipelineJobsPanel(client=client)
    qtbot.addWidget(panel)
    client.sigJobQueued.emit({"job_id": "j1", "pipeline": "p"})
    client.sigJobFailed.emit({"job_id": "j1", "status": "failed", "error": "boom"})
    assert panel.row(0)["status"] == "failed"


def test_panel_ignores_progress_without_job_id(qtbot):
    client = FakeClient()
    panel = PipelineJobsPanel(client=client)
    qtbot.addWidget(panel)
    # Malformed event: no job_id
    client.sigJobProgress.emit({"status": "running"})
    assert panel.row_count() == 0


def test_panel_queue_label_reflects_active_jobs(qtbot):
    client = FakeClient()
    panel = PipelineJobsPanel(client=client)
    qtbot.addWidget(panel)
    client.sigJobQueued.emit({"job_id": "a", "pipeline": "p"})
    client.sigJobQueued.emit({"job_id": "b", "pipeline": "p"})
    assert panel._queue_label.text() == "Queue: 2"
    client.sigJobCompleted.emit({"job_id": "a", "status": "completed",
                                 "output_run_uids": []})
    assert panel._queue_label.text() == "Queue: 1"


def test_dock_panel_embeds_inner_when_client_registered(qtbot, monkeypatch):
    """DockPanel pulls PipelineClient from ServiceRegistry and forwards events."""
    from lightfall.core.services import ServiceRegistry
    from lightfall.pipelines import PipelineClient

    client = FakeClient()
    registry = ServiceRegistry.get_instance()
    monkeypatch.setattr(registry, "get", lambda key, default=None:
                        client if key is PipelineClient else default)

    panel = PipelineJobsDockPanel()
    qtbot.addWidget(panel)
    assert hasattr(panel, "_inner")
    client.sigJobQueued.emit({"job_id": "j1", "pipeline": "p"})
    assert panel._inner.row_count() == 1


def test_dock_panel_shows_placeholder_when_no_client(qtbot, monkeypatch):
    """No registered PipelineClient renders a placeholder label, not a crash."""
    from lightfall.core.services import ServiceRegistry

    registry = ServiceRegistry.get_instance()
    monkeypatch.setattr(registry, "get", lambda key, default=None: default)

    panel = PipelineJobsDockPanel()
    qtbot.addWidget(panel)
    assert not hasattr(panel, "_inner")
