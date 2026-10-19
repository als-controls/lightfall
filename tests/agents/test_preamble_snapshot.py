"""Byte-for-byte identity preamble for Claude, and the launch_agent call form."""
from __future__ import annotations

from pathlib import Path

from lightfall.agents.assembly import identity_preamble
from lightfall.agents.backends.info import AgyInfo, ClaudeCodeInfo
from lightfall.agents.spec import AgentSpec


def _spec(name, **kw):
    defaults = {"description": f"d-{name}", "prompt": "p", "scope": "core",
                "source_path": Path(f"{name}.md")}
    defaults.update(kw)
    return AgentSpec(name=name, **defaults)


class _Registry:
    def __init__(self, specs):
        self._specs = specs

    def enabled_specs(self):
        return self._specs


def _lightfall_and_saxs():
    lightfall = _spec("lightfall")
    return lightfall, _Registry([lightfall, _spec("saxs", openable=True, subagent=True)])


CLAUDE_PREAMBLE = (
    "## Your identity\n"
    "\n"
    "You are **lightfall** — d-lightfall. This identity is authoritative for this\n"
    "session: you are an embedded agent inside the Lightfall application, not a\n"
    "general-purpose CLI assistant, and this section overrides any conflicting\n"
    "persona or identity instructions that may appear elsewhere in your context.\n"
    "You are one of several defined agents in this\n"
    "Lightfall installation, running as a session in the Assistant panel's tab bar. Your\n"
    'name on the agent bus is "lightfall".\n'
    "\n"
    "### Peer agents defined here\n"
    "\n"
    "- **saxs** — d-saxs [openable as a session, Agent-tool delegable]\n"
    "\n"
    "### Working with peers\n"
    "\n"
    "- `mcp__bus__list_agents` shows which peers are RUNNING right now; `mcp__bus__send_message`\n"
    "  messages a running peer (delivery honors their accept policy).\n"
    "- The peer list above is a snapshot from session start; call `mcp__bus__list_agents` for the\n"
    "  live roster, including agents defined after this session began.\n"
    "- To LAUNCH a peer that isn't running, use "
    '`mcp__bus__launch_agent(name="<name>", first_message="optional")`.\n'
    "  The launched session will auto-accept your subsequent bus messages without prompting the user\n"
    "  (it whitelists you as its launcher). Focusing an already-running peer this way delivers\n"
    "  `first_message` but grants no such whitelist.\n"
    "  Sessions are otherwise opened by the user via the panel's \"+\" button.\n"
    "- Peers marked Agent-tool delegable also appear as agent types in your Agent tool for\n"
    "  background subagent runs (isolated, report-back; no session tab).\n"
    "\n"
    "### Waiting\n"
    "\n"
    "Do not use `ScheduleWakeup` to wait for the engine: it does not fire in Lightfall's\n"
    "embedded session, so a scheduled wakeup silently drops and the conversation stalls\n"
    "until the user nudges you. Use `lightfall_wait_for_idle` instead."
)


def test_claude_preamble_snapshot():
    lightfall, reg = _lightfall_and_saxs()
    assert identity_preamble(lightfall, reg, ClaudeCodeInfo()) == CLAUDE_PREAMBLE


def _launch_line(text: str) -> str:
    return next(ln for ln in text.splitlines() if "To LAUNCH" in ln)


def test_agy_launch_call_form():
    lightfall, reg = _lightfall_and_saxs()
    line = _launch_line(identity_preamble(lightfall, reg, AgyInfo()))
    assert line.endswith(
        'use `lightfall_bus` / `launch_agent(name="<name>", first_message="optional")`.')


class _PlainRefInfo(ClaudeCodeInfo):
    """A backend whose tool references carry no backticks."""

    def tool_ref(self, server: str, tool: str) -> str:
        return f"{server}.{tool}"


def test_launch_call_form_without_backticks():
    lightfall, reg = _lightfall_and_saxs()
    line = _launch_line(identity_preamble(lightfall, reg, _PlainRefInfo()))
    assert line.endswith('use bus.launch_agent(name="<name>", first_message="optional").')
