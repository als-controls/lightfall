"""One-shot, tool-less Claude query on a private event loop (monitor advisor)."""
from __future__ import annotations

import asyncio


def claude_oneshot(prompt: str, *, system_prompt: str, cwd: str, model: str | None = None) -> str:
    # Names are resolved at call time (not imported at module top) so tests can
    # patch claude_agent_sdk.* and lightfall.monitor.advisor.collect_reply, and
    # so this module does not create an import cycle with monitor.advisor.
    import claude_agent_sdk as sdk

    from lightfall.monitor import advisor as _advisor

    opts = sdk.ClaudeAgentOptions(
        cwd=cwd,
        mcp_servers={},
        allowed_tools=[],
        system_prompt=system_prompt,
        permission_mode="bypassPermissions",
        max_turns=1,
        include_partial_messages=False,
        # Never let the developer's user-scope Claude settings (global
        # ~/.claude CLAUDE.md, personal plugins/hooks/skills) leak into
        # this advisor session. Leaving setting_sources unset (None) means
        # "all sources" per the SDK; here that's worse since the advisor
        # runs bypassPermissions, so personal hooks would fire unchecked.
        # "project" is ALSO unsafe: it activates the whole CLAUDE.md chain
        # including the user-level ~/.claude/CLAUDE.md, not just per-cwd
        # project config (verified empirically 2026-08-03: persona leak).
        # Empty setting_sources ([]) gives true SDK isolation.
        setting_sources=[],
        **({"model": model} if model else {}),
    )
    loop = asyncio.new_event_loop()
    try:
        asyncio.set_event_loop(loop)
        client = sdk.ClaudeSDKClient(options=opts)
        loop.run_until_complete(client.connect())
        try:
            return loop.run_until_complete(_advisor.collect_reply(client, prompt))
        finally:
            try:
                loop.run_until_complete(client.disconnect())
            except Exception:  # noqa: BLE001
                pass
    finally:
        try:
            loop.close()
        except Exception:  # noqa: BLE001
            pass
