"""Optional LLM advisor: fuses a batch of deterministic Observations into one
plain-language message. Off by default. A lean wrapper over a minimal
ClaudeSDKClient — no tools, max_turns=1, its own session/cwd, same auth. It
does NOT sense data; it only voices/triages what the feeds already found."""

from __future__ import annotations

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
            _record_usage(msg)
            break
    return "".join(parts).strip()


def _record_usage(msg) -> None:
    """Count the advisor's turn in agent telemetry as agent "observer"."""
    try:
        from lightfall.agents.telemetry import AgentTelemetry

        usage = getattr(msg, "usage", None) or {}
        AgentTelemetry.get_instance().record_turn("observer", "claude_code", None, {
            "total_cost_usd": getattr(msg, "total_cost_usd", 0) or 0,
            "input_tokens": usage.get("input_tokens", 0),
            "output_tokens": usage.get("output_tokens", 0),
            "cache_read_input_tokens": usage.get("cache_read_input_tokens", 0),
            "cache_creation_input_tokens": usage.get("cache_creation_input_tokens", 0),
            "duration_ms": getattr(msg, "duration_ms", 0) or 0,
            "num_turns": getattr(msg, "num_turns", 0) or 0,
            "is_error": bool(getattr(msg, "is_error", False)),
        })
    except Exception:  # noqa: BLE001 - telemetry must never break the advisor
        pass


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

    def _resolve_system_prompt(self) -> str:
        """Resolve the "observer" agent spec's prompt, falling back to the
        static ADVISOR_SYSTEM_PROMPT when the registry has no such spec
        (headless/unit-test paths) or anything goes wrong resolving it."""
        try:
            from lightfall.agents.registry import AgentSpecRegistry
            from lightfall.agents.skills_store import template_variables
            from lightfall.agents.spec import resolve_template

            spec = AgentSpecRegistry.get_instance().get("observer")
            if spec is None:
                return ADVISOR_SYSTEM_PROMPT
            return resolve_template(spec.prompt, template_variables())
        except Exception as e:  # noqa: BLE001 - advisory; never fail the query
            logger.debug("monitor advisor: could not resolve observer spec: {}", e)
            return ADVISOR_SYSTEM_PROMPT

    def _sdk_query(self, prompt: str) -> str:
        """Real SDK-backed one-shot query. Delegates to the Claude backend's
        tool-less one-shot helper (imported lazily to avoid an import cycle)."""
        from lightfall.agents.backends.claude_code.oneshot import claude_oneshot

        # Predates agent_cwd() conventions (Task 2+); kept as-is so existing
        # advisor transcripts under ~/lightfall/advisor stay where they are.
        cwd = (Path.home() / "lightfall" / "advisor")
        cwd.mkdir(parents=True, exist_ok=True)
        return claude_oneshot(
            prompt,
            system_prompt=self._resolve_system_prompt(),
            cwd=str(cwd.resolve()),
            model=self._model,
        )
