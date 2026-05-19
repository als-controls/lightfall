"""Tests for the AutonomousExperimentAgent plugin."""
from __future__ import annotations

import pytest

from lucid.plugins.agents.autonomous_experiment import (
    AutonomousExperimentAgent,
)


def test_plugin_metadata():
    agent = AutonomousExperimentAgent()
    assert agent.name == "autonomous_experiment"
    assert agent.display_name == "Autonomous Experiment"
    assert "Tsuchinoko" in agent.description
    assert agent.category == "acquisition"
    assert agent.priority == 30
    assert agent.enabled_by_default is True


def test_plugin_reports_has_prompt_and_tools():
    agent = AutonomousExperimentAgent()
    info = agent.get_introspection_data()
    assert info["has_prompt"] is True
    assert info["has_tools"] is True
