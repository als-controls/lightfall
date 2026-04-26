"""Tests for PluginType.__init_subclass__ user-plugin auto-enqueue."""
from __future__ import annotations

import sys
import textwrap
from pathlib import Path
from unittest.mock import MagicMock

import pytest


@pytest.fixture
def mock_user_service(monkeypatch):
    """Replace UserPluginService.get_instance() with a mock."""
    service = MagicMock()
    monkeypatch.setattr(
        "lucid.plugins.user_plugins.UserPluginService.get_instance",
        lambda: service,
    )
    return service


@pytest.fixture
def fake_user_plugin_dir(tmp_path, monkeypatch):
    """Make tmp_path act as the canonical user plugin dir."""
    monkeypatch.setattr(
        "lucid.plugins.types._user_plugin_roots",
        lambda: [tmp_path.resolve()],
    )
    return tmp_path


def _write_module(dir_: Path, name: str, body: str) -> Path:
    """Write a Python module to dir_ and import it cleanly."""
    path = dir_ / f"{name}.py"
    path.write_text(textwrap.dedent(body), encoding="utf-8")
    sys.path.insert(0, str(dir_))
    try:
        import importlib
        mod = importlib.import_module(name)
    finally:
        sys.path.pop(0)
    return path, mod


def test_subclass_in_user_dir_enqueues(mock_user_service, fake_user_plugin_dir):
    _, mod = _write_module(fake_user_plugin_dir, "user_one", """
        from lucid.plugins.agent_plugin import AgentPlugin

        class UserAgent(AgentPlugin):
            @property
            def name(self): return "user_one"
            @property
            def description(self): return "user contributed"
    """)
    mock_user_service.enqueue.assert_called_once()
    cls_arg, _path_arg = mock_user_service.enqueue.call_args.args
    assert cls_arg is mod.UserAgent


def test_subclass_outside_user_dir_does_not_enqueue(mock_user_service, fake_user_plugin_dir, tmp_path):
    other_dir = tmp_path.parent / "outside"
    other_dir.mkdir(exist_ok=True)
    _write_module(other_dir, "outside_one", """
        from lucid.plugins.agent_plugin import AgentPlugin

        class OutsideAgent(AgentPlugin):
            @property
            def name(self): return "outside_one"
            @property
            def description(self): return "should be ignored"
    """)
    mock_user_service.enqueue.assert_not_called()


def test_abstract_subclass_does_not_enqueue(mock_user_service, fake_user_plugin_dir):
    _write_module(fake_user_plugin_dir, "abstract_one", """
        from lucid.plugins.agent_plugin import AgentPlugin

        class Abstract(AgentPlugin):
            pass
    """)
    mock_user_service.enqueue.assert_not_called()


def test_main_module_subclass_does_not_enqueue(mock_user_service, fake_user_plugin_dir):
    """Classes defined at REPL (__main__) are skipped."""
    from lucid.plugins.agent_plugin import AgentPlugin

    # Simulate a class with __module__ == "__main__"
    DynamicClass = type("REPLAgent", (AgentPlugin,), {
        "__module__": "__main__",
        "name": property(lambda self: "repl"),
        "description": property(lambda self: "repl class"),
    })
    mock_user_service.enqueue.assert_not_called()
