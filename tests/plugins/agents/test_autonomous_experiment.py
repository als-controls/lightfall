"""Tests for the AutonomousExperimentAgent plugin."""
from __future__ import annotations

import asyncio
import json
from unittest.mock import MagicMock, patch

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


# ---------------------------------------------------------------------------
# nats_tools — tsuchinoko_discover
# ---------------------------------------------------------------------------
#
# Adaptation notes (SDK quirks):
#
#   - In this environment ``claude_agent_sdk.tool`` is variant **B**: a
#     decorator factory that wraps the user function in an
#     ``SdkMcpTool`` dataclass with ``.name`` and ``.handler`` attributes.
#     The helpers below pick the handler via ``getattr(tool, "handler")``.
#
#   - ``mcp_result`` (see ``lucid/plugins/agents/_mcp_helpers.py``) wraps
#     the tool's payload as
#     ``{"content": [{"type": "text", "text": "<json>"}], ...}``.
#     The tests want to assert against the *inner* dict (``success``,
#     ``instances``, ``error``), so ``_call`` unwraps the envelope by
#     parsing the JSON text block back to a dict.


def _patch_ipc(reply):
    """Patch get_ipc_service to return a stub IPC with .request → *reply*."""
    ipc = MagicMock()
    ipc.request = MagicMock(return_value=reply)
    return patch("lucid.ipc.service.get_ipc_service", return_value=ipc), ipc


def _find_tool(tools, name):
    for t in tools:
        # SDK @tool returns objects with a `.name` or accessible via spec.
        # Inspect by name attribute first, fall back to repr.
        if getattr(t, "name", None) == name:
            return t
        if hasattr(t, "tool_spec") and t.tool_spec.get("name") == name:
            return t
    raise AssertionError(f"tool {name!r} not in {tools!r}")


def _unwrap_mcp(result):
    """Pull the inner dict out of an MCP envelope, if needed."""
    if isinstance(result, dict) and "content" in result:
        blocks = result.get("content") or []
        for block in blocks:
            if isinstance(block, dict) and block.get("type") == "text":
                try:
                    return json.loads(block["text"])
                except (TypeError, ValueError, KeyError):
                    continue
    return result


def _call(tool, args=None):
    handler = getattr(tool, "handler", None) or getattr(tool, "_handler", None) or tool
    raw = asyncio.run(handler(args or {}))
    return _unwrap_mcp(raw)


def test_discover_returns_responder_list_from_request():
    agent = AutonomousExperimentAgent()
    tools = agent.create_tools()
    discover = _find_tool(tools, "tsuchinoko_discover")

    patcher, ipc = _patch_ipc(reply={"instance_id": "abc", "state": "Inactive"})
    with patcher:
        result = _call(discover)

    assert result["success"] is True
    assert isinstance(result["instances"], list)
    assert any(i.get("instance_id") == "abc" for i in result["instances"])
    ipc.request.assert_called_once()
    call = ipc.request.call_args
    # subject is positional[0]; payload (dict) is positional[1]
    assert call.args[0] == "_tsuchinoko.discover"
    assert isinstance(call.args[1], dict)
    # timeout_ms is a kwarg, in ms
    assert "timeout_ms" in call.kwargs
    assert isinstance(call.kwargs["timeout_ms"], int)


def test_discover_empty_when_no_responders():
    agent = AutonomousExperimentAgent()
    discover = _find_tool(agent.create_tools(), "tsuchinoko_discover")

    patcher, _ = _patch_ipc(reply=None)
    with patcher:
        result = _call(discover)
    assert result["success"] is True
    assert result["instances"] == []


def test_nats_unavailable_raises_actionable_message():
    agent = AutonomousExperimentAgent()
    discover = _find_tool(agent.create_tools(), "tsuchinoko_discover")
    with patch("lucid.ipc.service.get_ipc_service", return_value=None):
        result = _call(discover)
    assert result["success"] is False
    assert "Settings" in result["error"] or "IPC" in result["error"]
