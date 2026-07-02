"""Optional LLM advisor: fuses a batch of deterministic Observations into one
plain-language message. Off by default. A lean wrapper over a minimal
ClaudeSDKClient — no tools, max_turns=1, its own session/cwd, same auth. It
does NOT sense data; it only voices/triages what the feeds already found."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from pathlib import Path

from lightfall.monitor.models import Observation
from lightfall.utils.logging import logger

# Imported at module load so tests can monkeypatch these names with fakes.
try:  # pragma: no cover - exercised indirectly
    from claude_agent_sdk.types import (
        AssistantMessage as _AssistantMessage,
    )
    from claude_agent_sdk.types import (
        ResultMessage as _ResultMessage,
    )
    from claude_agent_sdk.types import (
        TextBlock as _TextBlock,
    )
except Exception:  # noqa: BLE001 - SDK optional at import time
    _AssistantMessage = _ResultMessage = _TextBlock = ()  # type: ignore[assignment]

ADVISOR_SYSTEM_PROMPT = (
    "You are a measurement-quality advisor for a synchrotron beamline. You receive "
    "a batch of structured observations produced by deterministic monitors during a "
    "running measurement. Fuse them into ONE short, plain-language message for the "
    "scientist: say whether anything needs attention and, if so, the single most "
    "useful next action. If nothing is worth interrupting for, reply exactly "
    "'nothing to report'. Be concise (1-3 sentences). Do not invent data."
)


def format_advisor_prompt(observations: list[Observation]) -> str:
    lines = ["Observations this interval:"]
    for o in observations:
        rec = f" | suggested: {o.recommendation}" if o.recommendation else ""
        lines.append(
            f"- [{o.severity}] {o.feed_name}: {o.title} — {o.message} "
            f"| metrics={o.metrics}{rec}"
        )
    lines.append("\nFuse into one short message, or 'nothing to report'.")
    return "\n".join(lines)


async def collect_reply(client, prompt: str) -> str:
    """Drive one SDK turn and return the joined assistant text."""
    await client.query(prompt)
    parts: list[str] = []
    async for msg in client.receive_response():
        if isinstance(msg, _AssistantMessage):
            for block in msg.content:
                if isinstance(block, _TextBlock):
                    parts.append(block.text or "")
        elif isinstance(msg, _ResultMessage):
            break
    return "".join(parts).strip()


class MonitorAdvisor:
    def __init__(
        self,
        query_fn: Callable[[str], str] | None = None,
        model: str | None = None,
    ) -> None:
        self._query_fn = query_fn or self._sdk_query
        self._model = model

    def advise(self, observations: list[Observation]) -> str:
        if not observations:
            return ""
        prompt = format_advisor_prompt(observations)
        try:
            return self._query_fn(prompt).strip()
        except Exception:  # noqa: BLE001 - advisory; never crash the monitor
            logger.exception("monitor advisor query failed")
            return ""

    def _sdk_query(self, prompt: str) -> str:
        """Real SDK-backed one-shot query. Runs its own event loop + client.
        Integration path (the response-collection logic is unit-tested via
        collect_reply with a fake client)."""
        from claude_agent_sdk import ClaudeAgentOptions, ClaudeSDKClient

        cwd = (Path.home() / "lightfall" / "advisor")
        cwd.mkdir(parents=True, exist_ok=True)
        opts = ClaudeAgentOptions(
            cwd=str(cwd.resolve()),
            mcp_servers={},
            allowed_tools=[],
            system_prompt=ADVISOR_SYSTEM_PROMPT,
            permission_mode="bypassPermissions",
            max_turns=1,
            include_partial_messages=False,
            **({"model": self._model} if self._model else {}),
        )
        loop = asyncio.new_event_loop()
        try:
            asyncio.set_event_loop(loop)
            client = ClaudeSDKClient(options=opts)
            loop.run_until_complete(client.connect())
            try:
                return loop.run_until_complete(collect_reply(client, prompt))
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
