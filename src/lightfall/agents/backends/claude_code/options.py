"""SessionConfig -> ClaudeAgentOptions (historical QtClaudeAgent options)."""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

from claude_agent_sdk import ClaudeAgentOptions

from lightfall.agents.backend import SessionConfig
from lightfall.agents.backends.claude_code.permissions import (
    create_can_use_tool_callback,
    create_pre_tool_use_hook,
)
from lightfall.agents.backends.claude_code.tools import to_sdk_server
from lightfall.utils.logging import logger

if TYPE_CHECKING:
    from lightfall.claude.permission_manager import PermissionManager

# Single-JSON-message read limit for the SDK transport. The default (1 MiB)
# is too small for image tool_results (base64 screenshot payloads) and kills
# the turn with "JSON message exceeded maximum buffer size". 16 MiB bounds
# runaway messages while leaving ample headroom.
SDK_MAX_BUFFER_SIZE = 16 * 1024 * 1024


def _agent_definitions(cfg: SessionConfig) -> dict[str, Any]:
    if not cfg.subagents:
        return {}
    try:
        from claude_agent_sdk.types import AgentDefinition
    except ImportError:
        logger.debug("AgentDefinition not available; skipping subagent definitions")
        return {}
    return {
        s.name: AgentDefinition(
            description=s.description,
            prompt=s.prompt,
            model=s.model,
            effort=s.effort,
            memory=("project" if s.memory else None),
        )
        for s in cfg.subagents
    }


def build_claude_options(
    cfg: SessionConfig,
    permission_manager: PermissionManager,
    require_approval: bool,
) -> ClaudeAgentOptions:
    mcp_servers: dict[str, Any] = {key: to_sdk_server(srv) for key, srv in cfg.tool_servers}
    mcp_servers.update(cfg.external_servers)

    options: dict[str, Any] = {
        "cwd": cfg.cwd,
        "plugins": [{"type": "local", "path": str(cfg.skills_dir)}],
        "mcp_servers": mcp_servers,
        "allowed_tools": list(cfg.allowed_tools),
        "system_prompt": cfg.system_prompt,
        "permission_mode": cfg.permission_mode,
        "max_turns": cfg.max_turns,
        # Opus 4.7's CLI default is --thinking-display omitted, which makes
        # ThinkingBlock.thinking arrive empty. Opt in to summarized text so
        # the agent panel's thinking boxes have content.
        "thinking": {"type": "adaptive", "display": "summarized"},
        # Stream per-token deltas so the chat bubble grows live instead of
        # waiting for the whole block. The worker translates these into
        # partial_* signals and the widget appends as they arrive.
        "include_partial_messages": True,
        # See SDK_MAX_BUFFER_SIZE: default 1 MiB rejects screenshot payloads.
        "max_buffer_size": SDK_MAX_BUFFER_SIZE,
        # Never let the developer's user-scope Claude settings (global
        # ~/.claude CLAUDE.md, personal plugins/hooks/skills) leak into an
        # embedded beamline session. Leaving setting_sources unset (None)
        # means "all sources" per the SDK. "project" is ALSO unsafe: it
        # activates the whole CLAUDE.md chain including the user-level
        # ~/.claude/CLAUDE.md, not just per-cwd project config (verified
        # empirically 2026-08-03: the operator's personal assistant
        # persona leaked into an embedded session). Empty setting_sources
        # ([]) gives true SDK isolation -- no CLAUDE.md chain, no personal
        # plugins/hooks -- while --plugin-dir and --mcp-config (passed via
        # options above) still work. Lightfall's own agent/skill scopes
        # (AgentSpec, per-session plugin dir) are the extension mechanism,
        # not the CLI's setting_sources.
        "setting_sources": [],
    }
    agents = _agent_definitions(cfg)
    if agents:
        options["agents"] = agents
    if cfg.model:
        options["model"] = cfg.model
    if cfg.effort:
        options["effort"] = cfg.effort
    if cfg.resume:
        options["resume"] = cfg.resume
    if cfg.cli_path:
        options["cli_path"] = cfg.cli_path

    # Register can_use_tool + PreToolUse hook ALWAYS. They serve two
    # purposes:
    # 1. Route ``AskUserQuestion`` to our question UI. This must work
    #    regardless of ``permission_mode`` — even with
    #    ``bypassPermissions`` the user wants to see the question.
    # 2. Gate normal tools through the approval UI when require_approval.
    #    With require_approval=False (the bypassPermissions path), the
    #    hook returns ``{}`` for non-AskUserQuestion tools so the CLI's
    #    auto-allow takes effect.
    options["can_use_tool"] = create_can_use_tool_callback(
        permission_manager, require_approval=require_approval,
    )
    try:
        from claude_agent_sdk import HookMatcher
        options["hooks"] = {
            "PreToolUse": [
                HookMatcher(
                    matcher=None,  # match all tools
                    hooks=[create_pre_tool_use_hook(
                        permission_manager,
                        require_approval=require_approval,
                    )],
                )
            ],
        }
    except ImportError:
        logger.debug("HookMatcher not available, using can_use_tool only")

    return ClaudeAgentOptions(**options)
