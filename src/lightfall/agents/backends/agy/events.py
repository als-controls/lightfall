"""Translate agy ``--output-format stream-json`` events into neutral AgentEvents.

Field names follow agy 1.3.5 output (verified live): ``init``, ``step_update``
(``conversation_id``, ``step_index``, ``state``, ``step_type``, ``text_delta``,
``tool_info{name, parameters, output, error}``) and ``result`` (``status``,
``response``, ``error``, cumulative ``usage``).

This module must not import ``claude_agent_sdk``.
"""
from __future__ import annotations

from typing import Any

from lightfall.agents.backend import (
    AgentEvent,
    PartialFinishedEvent,
    PartialStartedEvent,
    PartialTextEvent,
    SessionIdEvent,
    ToolCallEvent,
    ToolResultEvent,
    TurnCancelledEvent,
    TurnErrorEvent,
    TurnResultEvent,
)
from lightfall.agents.tooling import tool_id
from lightfall.utils.logging import logger

_PREFIX = "lightfall_"
_USAGE_KEYS = ("input_tokens", "output_tokens")
TERMINAL_EVENTS = (TurnResultEvent, TurnCancelledEvent, TurnErrorEvent)


def _int(v: Any) -> int:
    return v if isinstance(v, int) and not isinstance(v, bool) else 0


class AgyEventTranslator:
    """Stateful translator for one backend session (spans child respawns)."""

    def __init__(self, session_keys: dict[str, str]) -> None:
        self._session_keys = dict(session_keys)
        self._started: set[str] = set()
        self._finished: set[str] = set()
        self._tools_called: set[str] = set()
        self._tools_done: set[str] = set()
        self._prev_usage: dict[str, int] = {}
        self.conversation_id: str | None = None

    def reset_usage(self) -> None:
        """Forget the cumulative-usage baseline (a fresh agy process restarts it)."""
        self._prev_usage = {}

    def _note_cid(self, cid: Any) -> None:
        if isinstance(cid, str) and cid:
            self.conversation_id = cid

    def feed(self, event: dict[str, Any]) -> list[AgentEvent]:
        if not isinstance(event, dict):
            return []
        kind = event.get("event")
        if kind == "init":
            self._note_cid(event.get("conversation_id"))
            return []
        if kind == "step_update":
            su = event.get("step_update")
            return self._step(su) if isinstance(su, dict) else []
        if kind == "result":
            r = event.get("result")
            return self._result(r) if isinstance(r, dict) else []
        return []

    # -- step_update --------------------------------------------------------

    def _step(self, su: dict[str, Any]) -> list[AgentEvent]:
        cid = su.get("conversation_id") or ""
        self._note_cid(cid)
        bid = f"{cid}:{su.get('step_index')}"
        step_type = su.get("step_type")
        state = su.get("state")
        if step_type == "agent_response":
            return self._response_step(bid, su, state)
        if step_type == "tool":
            return self._tool_step(bid, su, state)
        return []

    def _response_step(self, bid: str, su: dict[str, Any], state: Any) -> list[AgentEvent]:
        out: list[AgentEvent] = []
        delta = su.get("text_delta")
        if isinstance(delta, str) and delta and bid not in self._finished:
            if bid not in self._started:
                self._started.add(bid)
                out.append(PartialStartedEvent(bid, "text"))
            out.append(PartialTextEvent(bid, delta))
        if state == "DONE" and bid in self._started and bid not in self._finished:
            self._finished.add(bid)
            out.append(PartialFinishedEvent(bid))
        return out

    def _tool_call(self, su: dict[str, Any]) -> ToolCallEvent:
        info = su.get("tool_info") if isinstance(su.get("tool_info"), dict) else {}
        name = info.get("name") or su.get("tool_name") or "unknown"
        params = info.get("parameters") if isinstance(info.get("parameters"), dict) else {}
        if name == "call_mcp_tool":
            server = str(params.get("ServerName") or "")
            key = self._session_keys.get(server, server.removeprefix(_PREFIX))
            args = params.get("Arguments")
            return ToolCallEvent(tool_id(key, str(params.get("ToolName") or "")),
                                 args if isinstance(args, dict) else {})
        return ToolCallEvent(str(name), params)

    def _tool_step(self, bid: str, su: dict[str, Any], state: Any) -> list[AgentEvent]:
        out: list[AgentEvent] = []
        if state in ("ACTIVE", "DONE", "ERROR") and bid not in self._tools_called:
            self._tools_called.add(bid)
            out.append(self._tool_call(su))
        if state in ("DONE", "ERROR") and bid not in self._tools_done:
            self._tools_done.add(bid)
            info = su.get("tool_info") if isinstance(su.get("tool_info"), dict) else {}
            if state == "ERROR":
                err = info.get("error")
                content = err.get("message") if isinstance(err, dict) else err
                content = str(content or "tool failed")
            else:
                out_v = info.get("output")
                content = "" if out_v is None else str(out_v)
            out.append(ToolResultEvent(bid, {"content": content, "is_error": state == "ERROR"}))
        return out

    # -- result -------------------------------------------------------------

    def _usage_delta(self, usage: Any) -> dict[str, int]:
        cur = {k: _int(usage.get(k)) for k in _USAGE_KEYS} if isinstance(usage, dict) else {}
        if not cur:
            return dict.fromkeys(_USAGE_KEYS, 0)
        prev = self._prev_usage
        self._prev_usage = cur
        if any(cur[k] < prev.get(k, 0) for k in _USAGE_KEYS):
            return cur  # counter restarted: the totals are this turn's
        return {k: cur[k] - prev.get(k, 0) for k in _USAGE_KEYS}

    def _result(self, r: dict[str, Any]) -> list[AgentEvent]:
        cid = r.get("conversation_id") or ""
        self._note_cid(cid)
        status = r.get("status")
        error = r.get("error") or ""
        response = r.get("response") or ""
        usage = self._usage_delta(r.get("usage"))
        if status in ("INTERRUPTED", "CANCELED") or (status == "ERROR" and error == "interrupted"):
            return [TurnCancelledEvent()]
        if status == "ERROR" and not error and response:
            logger.info("agy result status ERROR with a response and no error; treating as success "
                        "(denied actions: {})", r.get("denied_actions"))
            status = "SUCCESS"
        if status != "SUCCESS":
            return [TurnErrorEvent(str(error or "agy turn failed"))]
        out: list[AgentEvent] = [TurnResultEvent({
            "total_cost_usd": None,
            "input_tokens": usage["input_tokens"],
            "output_tokens": usage["output_tokens"],
            "session_id": cid,
        })]
        if cid:
            out.append(SessionIdEvent(cid))
        return out
