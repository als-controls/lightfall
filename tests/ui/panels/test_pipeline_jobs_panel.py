"""Tests for the Pipeline Jobs dock panel."""
from PySide6.QtCore import QObject, Signal

import pytest

from lucid.ui.panels.pipeline_jobs_panel import PipelineJobsPanel


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
