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


def test_stub_prompt_mentions_key_tools_and_steps():
    agent = AutonomousExperimentAgent()
    prompt = agent.get_system_prompt()

    # Workflow steps
    for token in (
        "experiment-designer",
        "tsuchinoko_discover",
        "tsuchinoko_upload_design_code",
        "tsuchinoko_configure",
        "ncs_run_plan",
        "adaptive_experiment",
        "AdaptiveHeatmapVisualization",
        "AdaptiveHyperparameterPlot",
        "tsuchinoko_status",
        "tsuchinoko_pause",
        "tsuchinoko_resume",
        "tsuchinoko_stop",
    ):
        assert token in prompt, f"prompt missing reference to {token!r}"

    # Sibling skills surfaced for lazy load
    for skill in (
        "acquisition-functions",
        "kernel-designer",
        "prior-mean-functions",
        "noise-functions",
        "cost-functions",
        "gp2scale-advanced",
        "multi-task-advanced",
    ):
        assert skill in prompt, f"prompt missing skill reference {skill!r}"

    # Install hint for the gpcam-missing path
    assert "pip install gpcam" in prompt
