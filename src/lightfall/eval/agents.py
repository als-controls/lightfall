"""Agents an evaluation can run: a scripted baseline and the real assistant.

Both return an :class:`AgentResult`; the scenario scores the sim's end state,
so the two are judged by exactly the same yardstick.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

from lightfall.utils.logging import logger


@dataclass
class AgentResult:
    agent: str
    finished: bool
    wall_s: float
    final_text: str = ""
    turns: int = 0
    cost_usd: float = 0.0
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    tool_calls: list[str] = field(default_factory=list)
    tool_errors: int = 0
    questions_asked: int = 0
    operator_turns: int = 0
    errors: list[str] = field(default_factory=list)
    transcript: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        d = dict(self.__dict__)
        d["tool_call_count"] = len(self.tool_calls)
        return d


class ScriptedAligner:
    """Reference solution for ``reflection_alignment``: the 7011 skill's
    procedure done by a script, through the same engine, Tiled and fitting
    code the agent's tools use. Holds ``sample_translate`` on lift scans."""

    name = "scripted"

    def __init__(self, cycles: int = 3) -> None:
        self.cycles = cycles

    def run(self, env, scenario) -> AgentResult:
        import bluesky.plan_stubs as bps
        import bluesky.plans as bp
        import numpy as np
        from lightfall_endstation_7011.alignment.skill import (
            _fit_lift_from_uid,
            _fit_theta_from_uid,
        )

        t0 = time.monotonic()
        d = env.devices
        lift, theta, translate, det = (d["sample_lift"], d["sample_rotate_steppertheta"],
                                       d["sample_translate"], d["DetectorDiodeCurrent"])
        hold = float(translate.position)

        def lift_scan(points):
            def per_step(detectors, step, pos_cache):
                yield from bps.mv(lift, step[lift], translate, hold)
                yield from bps.trigger_and_read(list(detectors) + [lift, translate])
            yield from bp.list_scan([det], lift, list(points), per_step=per_step)

        def last_uid() -> str:
            return env.runs()[-1]["uid"]

        result = AgentResult(agent=self.name, finished=False, wall_s=0.0)
        lift_c, theta_c, span_l, span_t = 1500.0, 0.0, 1500.0, 1.0
        try:
            for _ in range(self.cycles):
                env.run_plan(lift_scan(np.linspace(lift_c - span_l, lift_c + span_l, 41)), "lift scan")
                fit = _fit_lift_from_uid(last_uid())
                result.tool_calls.append("fit_lift_halfcut")
                if fit.get("halfcut") is not None:
                    lift_c = float(fit["halfcut"])
                    span_l = max(span_l / 4.0, 60.0)
                env.run_plan(bps.mv(lift, lift_c, translate, hold), "move lift")
                env.run_plan(bp.scan([det], theta, theta_c - span_t, theta_c + span_t, 41), "theta scan")
                peak = _fit_theta_from_uid(last_uid())
                result.tool_calls.append("fit_theta_peak")
                if peak.get("peak") is not None and not peak.get("peak_at_boundary"):
                    theta_c = float(peak["peak"])
                    span_t = max(span_t / 2.0, 0.3)
                env.run_plan(bps.mv(theta, theta_c), "move theta")
            result.finished = True
            result.final_text = f"sample_lift={lift_c:.1f} sample_rotate_steppertheta={theta_c:.3f}"
        except Exception as e:  # noqa: BLE001
            logger.exception("scripted aligner failed")
            result.errors.append(f"{type(e).__name__}: {e}")
        result.wall_s = round(time.monotonic() - t0, 1)
        return result


#: Tool plugins the eval assistant gets: device, plan and engine tools,
#: checkpoints, and the 7011 reflection-alignment skill + tools.
EVAL_TOOLS = ("device_tools", "plan_tools", "engine_tools", "checkpoint_tools",
              "reflection_alignment")
EVAL_SKILLS = ("reflection_alignment", "plan_review")


def _register_tool_plugins() -> list[str]:
    """Register the eval tool plugins with a fresh ToolRegistry."""
    import importlib

    from lightfall.ui.panels.claude.tool_registry import ToolRegistry

    paths = {
        "device_tools": "lightfall.plugins.agents.device_tools:DeviceToolsAgent",
        "plan_tools": "lightfall.plugins.agents.plan_tools:PlanToolsAgent",
        "engine_tools": "lightfall.plugins.agents.engine_tools:EngineToolsAgent",
        "checkpoint_tools": "lightfall.plugins.agents.checkpoint_tools:CheckpointToolsAgent",
        "reflection_alignment": "lightfall_endstation_7011.alignment.skill:ReflectionAlignmentAgent",
    }
    ToolRegistry.reset_instance()
    registry = ToolRegistry.get_instance()
    registered = []
    for name, path in paths.items():
        module, cls = path.split(":")
        try:
            registry.register(getattr(importlib.import_module(module), cls)())
            registered.append(name)
        except Exception as e:  # noqa: BLE001
            logger.warning("eval: tool plugin {} unavailable: {}", name, e)
    return registered


def _eval_spec():
    """The main 'lightfall' agent, restricted to the eval tools and skills."""
    import dataclasses

    from lightfall.agents import builtin_agents_dir
    from lightfall.agents.registry import AgentSpecRegistry

    AgentSpecRegistry.reset_instance()
    reg = AgentSpecRegistry.get_instance()
    reg.register_scope_dir("core", builtin_agents_dir())  # no user scope: reproducible
    base = reg.get("lightfall")
    return dataclasses.replace(base, tools=EVAL_TOOLS, skills=EVAL_SKILLS)


class ClaudeAgentRunner:
    """The real assistant (QtClaudeAgent, Claude Code backend), headless.

    Permissions are bypassed (an eval has nobody to click Approve); questions
    the agent asks are answered by the scenario's simulated operator and
    counted in ``questions_asked``."""

    name = "claude"

    def __init__(self, model: str | None = None, effort: str | None = None,
                 max_turns: int = 60, timeout_s: float = 1800.0) -> None:
        self.model = model
        self.effort = effort
        self.max_turns = max_turns
        self.timeout_s = timeout_s

    def run(self, env, scenario) -> AgentResult:
        from PySide6.QtWidgets import QWidget

        from lightfall.claude.agent import QtClaudeAgent

        registered = _register_tool_plugins()
        result = AgentResult(agent=f"claude:{self.model or 'default'}", finished=False, wall_s=0.0)
        if "reflection_alignment" not in registered:
            result.errors.append("lightfall-endstation-7011 is not installed")
            return result

        target = QWidget()
        agent = QtClaudeAgent(
            target_window=target, permission_mode="bypassPermissions", require_approval=False,
            model=self.model, effort=self.effort, max_turns=self.max_turns, spec=_eval_spec(),
        )
        done = {"flag": False}
        texts: list[str] = []
        pending: list[str] = []

        def on_result(info: dict) -> None:
            if info.get("text"):
                texts.append(str(info["text"]))
                result.transcript.append({"assistant": str(info["text"])[:2000]})
            result.turns += int(info.get("num_turns") or 1)
            result.cost_usd += float(info.get("total_cost_usd") or 0.0)
            result.input_tokens += int(info.get("input_tokens") or 0)
            result.output_tokens += int(info.get("output_tokens") or 0)
            result.cache_read_tokens += int(info.get("cache_read_input_tokens") or 0)

        def on_tool(name: str, tool_input: dict) -> None:
            result.tool_calls.append(name)
            pending.append(name)
            result.transcript.append({"tool": name, "input": str(tool_input)[:500]})

        def on_tool_result(_id: str, payload: dict) -> None:
            name = pending.pop(0) if pending else "?"
            result.transcript.append({"result": name, "output": str(payload)[:800]})
            if isinstance(payload, dict) and payload.get("is_error"):
                result.tool_errors += 1

        def on_question(request_id: str, questions: list) -> None:
            result.questions_asked += 1
            answers = {q.get("question", str(i)): scenario.question_reply(q.get("question", ""))
                       for i, q in enumerate(questions or [])}
            agent.respond_to_question(request_id, answers)

        agent.message_received.connect(texts.append)
        agent.result_received.connect(on_result)
        agent.tool_called.connect(on_tool)
        agent.tool_result.connect(on_tool_result)
        agent.error_occurred.connect(result.errors.append)
        agent.query_completed.connect(lambda: done.update(flag=True))
        try:
            agent.question_requested.connect(on_question)
        except Exception:  # noqa: BLE001 - older signal shapes
            pass

        t0 = time.monotonic()
        deadline = t0 + self.timeout_s
        prompt = scenario.prompt
        turn = 0
        while prompt is not None:
            done["flag"] = False
            agent.query_sync(prompt)
            result.finished = env.pump(lambda: done["flag"], max(1.0, deadline - time.monotonic()))
            if not result.finished:
                result.errors.append("timed out")
                break
            prompt = scenario.operator_reply(turn, texts[-1] if texts else "")
            if prompt is not None:
                result.operator_turns += 1
                result.transcript.append({"operator": prompt})
            turn += 1
        result.wall_s = round(time.monotonic() - t0, 1)
        result.final_text = texts[-1] if texts else ""
        try:
            agent.stop(wait_ms=10000)
        except Exception:  # noqa: BLE001
            logger.debug("eval: agent stop failed", exc_info=True)
        return result


AGENTS = {"scripted": ScriptedAligner, "claude": ClaudeAgentRunner}
