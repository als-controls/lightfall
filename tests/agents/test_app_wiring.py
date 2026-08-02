# tests/agents/test_app_wiring.py — spec resolution end-to-end
from lightfall.agents import builtin_agents_dir, user_agents_dir  # noqa: F401
from lightfall.agents.registry import AgentSpecRegistry


def test_bootstrap_registers_core_and_user(tmp_path, monkeypatch):
    from pathlib import Path
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    AgentSpecRegistry.reset_instance()
    from lightfall.main import _setup_agent_specs  # new function, called from app startup
    _setup_agent_specs()
    reg = AgentSpecRegistry.get_instance()
    assert reg.get("lightfall") is not None
    assert reg.get("observer") is not None
    AgentSpecRegistry.reset_instance()
