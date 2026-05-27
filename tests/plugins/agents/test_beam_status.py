"""Tests for the ncs_get_beam_status MCP tool helper."""
from __future__ import annotations

import lucid.plugins.agents.engine_tools as et
from lucid.services import als_beam_status


class _FakeService:
    def __init__(self):
        self.refreshed = False

    def force_refresh(self):
        self.refreshed = True

    def get_introspection_data(self):
        return {
            "is_connected": True,
            "beam_current_mA": 500.2,
            "beam_available": True,
            "beam_energy_GeV": 1.9,
        }


def _patch_service(monkeypatch, fake):
    monkeypatch.setattr(
        als_beam_status.ALSBeamStatusService,
        "get_instance",
        classmethod(lambda cls: fake),
    )


def test_beam_status_payload(monkeypatch):
    fake = _FakeService()
    _patch_service(monkeypatch, fake)
    out = et._beam_status_payload()
    assert out["success"] is True
    assert out["beam_current_mA"] == 500.2
    assert out["beam_available"] is True
    assert fake.refreshed is False


def test_beam_status_payload_force_refresh(monkeypatch):
    fake = _FakeService()
    _patch_service(monkeypatch, fake)
    out = et._beam_status_payload(force_refresh=True)
    assert fake.refreshed is True
    assert out["success"] is True


def test_engine_tools_registers_beam_status():
    tools = et.EngineToolsAgent().create_tools()
    if not tools:
        import pytest

        pytest.skip("claude_agent_sdk not available")
    names = {getattr(t, "name", None) or getattr(t, "__name__", None) for t in tools}
    assert "ncs_get_beam_status" in names
