"""Builtin agent definition files: operator/dev split invariants."""

from __future__ import annotations

from pathlib import Path

import pytest

from lightfall.agents.spec import parse_agent_file

BUILTIN_DIR = Path(__file__).resolve().parents[2] / "src" / "lightfall" / "agents" / "builtin"


@pytest.fixture(scope="module")
def specs() -> dict[str, object]:
    return {
        spec.name: spec
        for spec in (parse_agent_file(p, "core") for p in sorted(BUILTIN_DIR.glob("*.md")))
    }


def test_builtin_agent_names(specs):
    assert set(specs) == {"lightfall", "lightfall-dev", "observer"}


def test_operator_prompt_drops_widget_interaction(specs):
    prompt = specs["lightfall"].prompt
    assert "click_widget" not in prompt
    assert "type_text" not in prompt
    # inspection-only Qt guidance stays
    assert "screenshot" in prompt
    assert "get_widget_tree" in prompt
    assert "get_recent_logs" in prompt


def test_dev_prompt_owns_widget_interaction(specs):
    prompt = specs["lightfall-dev"].prompt
    assert "click_widget" in prompt
    assert "type_text" in prompt
    assert "Qt Tool Notes" in prompt


def test_dev_agent_is_not_a_subagent(specs):
    assert specs["lightfall-dev"].subagent is False
    assert specs["lightfall"].subagent is True


def test_skill_and_tool_split(specs):
    operator = specs["lightfall"]
    dev = specs["lightfall-dev"]
    assert set(operator.skills) == {
        "scan_planning",
        "alignment",
        "current_esaf",
        "autonomous_experiment",
    }
    assert set(dev.skills) == {"panel_builder", "panel_design", "plan_design"}
    assert "panel_builder" not in operator.tools
    assert "ipython_tools" not in operator.tools
    assert {"panel_builder", "ipython_tools"} <= set(dev.tools)


def test_skills_reference_existing_builtin_skill_dirs(specs):
    skills_dir = BUILTIN_DIR.parents[1] / "skills" / "builtin"
    for name in ("lightfall", "lightfall-dev"):
        for skill in specs[name].skills:
            assert (skills_dir / skill).is_dir(), f"{name}: missing skill dir {skill}"


def test_distillation_nudge_in_prompts(specs):
    """Both operator and dev specs must contain distillation guidance."""
    for name in ("lightfall", "lightfall-dev"):
        prompt = specs[name].prompt
        assert "## Distilling knowledge" in prompt, f"{name}: missing distillation section header"
        assert "draft_skill" in prompt, f"{name}: missing draft_skill tool reference"
        assert "memory" in prompt, f"{name}: missing memory reference"
