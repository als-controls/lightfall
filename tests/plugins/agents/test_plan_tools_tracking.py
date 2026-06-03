"""Verify plan_tools commits with agent-supplied descriptions and on delete."""
from __future__ import annotations

import asyncio
import json as json_mod
import subprocess
from pathlib import Path

import pytest

from lightfall.acquire.plans.user_plans import UserPlanService
from lightfall.plugins.agents.plan_tools import PlanToolsAgent
from lightfall.utils.git_tracker import GitTracker


@pytest.fixture(autouse=True)
def reset_singletons():
    UserPlanService.reset_instance()
    GitTracker.reset_instance()
    yield
    UserPlanService.reset_instance()
    GitTracker.reset_instance()


@pytest.fixture
def tracked_dirs(tmp_path, monkeypatch):
    GitTracker.reset_instance()
    repo_root = tmp_path / "lightfall"
    repo_root.mkdir()
    plans_dir = repo_root / "plans"
    plans_dir.mkdir()

    tracker = GitTracker(repo_root=repo_root)
    monkeypatch.setattr(GitTracker, "_instance", tracker)

    service = UserPlanService.get_instance()
    monkeypatch.setattr(service, "_plans_dir", plans_dir)
    yield plans_dir


def _git_log_subjects(repo_root: Path) -> list[str]:
    out = subprocess.run(
        ["git", "log", "--format=%s"],
        cwd=repo_root, capture_output=True, text=True,
    )
    return [line for line in out.stdout.splitlines() if line]


def _unwrap(result):
    """Tools may return raw dicts or mcp_result-wrapped envelopes."""
    if isinstance(result, dict) and "content" in result:
        return json_mod.loads(result["content"][0]["text"])
    return result


def test_create_user_plan_commits_with_description(tracked_dirs):
    agent = PlanToolsAgent()
    tools = agent.create_tools()
    create_tool = next(t for t in tools if getattr(t, "name", None) == "lightfall_create_user_plan")

    code = '''"""my_scan."""
from __future__ import annotations
import bluesky.plans as bp
def plan(detectors, motor, start=-1.0, stop=1.0, num=3):
    yield from bp.scan(detectors, motor, start, stop, num)
'''
    result = asyncio.run(create_tool.handler({
        "name": "my_scan",
        "code": code,
        "description": "create my_scan: 1D scan with 3 points",
    }))
    body = _unwrap(result)
    assert body["success"], body

    subjects = _git_log_subjects(tracked_dirs.parent)
    assert subjects == ["agent: create my_scan: 1D scan with 3 points"]


def test_delete_user_plan_commits_removal_with_agent_subject(tracked_dirs):
    """Deletion of a plan via the MCP tool commits with `agent: delete plan <name>`."""
    # First create a plan
    agent = PlanToolsAgent()
    tools = agent.create_tools()
    create_tool = next(t for t in tools if getattr(t, "name", None) == "lightfall_create_user_plan")
    delete_tool = next(t for t in tools if getattr(t, "name", None) == "lightfall_delete_user_plan")

    code = '''"""doomed."""
from __future__ import annotations
import bluesky.plans as bp
def plan(detectors, motor):
    yield from bp.scan(detectors, motor, -1, 1, 3)
'''
    asyncio.run(create_tool.handler({
        "name": "doomed",
        "code": code,
        "description": "doomed plan",
    }))

    # Delete it
    result = asyncio.run(delete_tool.handler({"name": "doomed", "confirm": True}))
    body = _unwrap(result)
    assert body["success"], body

    subjects = _git_log_subjects(tracked_dirs.parent)
    # First commit: agent: doomed plan; Second commit: agent: delete plan doomed
    assert subjects[0] == "agent: delete plan doomed"
    assert subjects[1] == "agent: doomed plan"
