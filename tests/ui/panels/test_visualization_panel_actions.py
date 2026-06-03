"""Tests for VisualizationPanel.invoke_action exposure of open_run."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from lightfall.ui.panels.visualization_panel import VisualizationPanel


def _patch_tiled_service(monkeypatch, *, client, is_connected=True):
    service = MagicMock()
    service._client = client
    service.is_connected = is_connected
    monkeypatch.setattr(
        "lightfall.services.tiled_service.TiledService.get_instance",
        lambda: service,
    )
    return service


def test_open_run_listed_in_actions(qtbot):
    panel = VisualizationPanel()
    qtbot.addWidget(panel)

    names = [a["name"] for a in panel._get_available_actions()]
    assert "open_run" in names


def test_invoke_open_run_resolves_uid_and_calls_open_run(qtbot, monkeypatch):
    panel = VisualizationPanel()
    qtbot.addWidget(panel)

    entry = MagicMock(name="entry")
    client = MagicMock()
    client.__getitem__.return_value = entry
    _patch_tiled_service(monkeypatch, client=client)

    open_run_mock = MagicMock()
    monkeypatch.setattr(panel, "open_run", open_run_mock)

    result = panel.invoke_action("open_run", uid="abc-123")

    client.__getitem__.assert_called_once_with("abc-123")
    open_run_mock.assert_called_once_with(entry)
    assert result == {"uid": "abc-123"}


def test_invoke_open_run_missing_uid_raises(qtbot):
    panel = VisualizationPanel()
    qtbot.addWidget(panel)

    with pytest.raises(ValueError, match="uid"):
        panel.invoke_action("open_run")


def test_invoke_open_run_not_connected_raises(qtbot, monkeypatch):
    panel = VisualizationPanel()
    qtbot.addWidget(panel)

    _patch_tiled_service(monkeypatch, client=None, is_connected=False)

    with pytest.raises(ValueError, match="not connected"):
        panel.invoke_action("open_run", uid="abc-123")


def test_invoke_open_run_unknown_uid_raises(qtbot, monkeypatch):
    panel = VisualizationPanel()
    qtbot.addWidget(panel)

    client = MagicMock()
    client.__getitem__.side_effect = KeyError("missing")
    _patch_tiled_service(monkeypatch, client=client)

    with pytest.raises(ValueError, match="not found"):
        panel.invoke_action("open_run", uid="abc-123")


def test_invoke_unknown_action_falls_through_to_super(qtbot):
    panel = VisualizationPanel()
    qtbot.addWidget(panel)

    with pytest.raises(ValueError):
        panel.invoke_action("nonexistent_action")
