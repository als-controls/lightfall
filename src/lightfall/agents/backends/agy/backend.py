"""AgentBackend that drives the Antigravity CLI (``agy``) in stream-json mode.

One backend = one Lightfall chat session:

* ``connect()`` prepares a private per-session agy home (plugin with literal
  port/token, linked per-spec store), starts the in-process :class:`ToolHost`
  (Lightfall tools over bearer-auth HTTP MCP plus the PreToolUse ``/hook``) and
  writes the plugin config.  The agy child is spawned lazily on the first turn.
* Each turn writes one ``user`` line and translates NDJSON events until a
  terminal event.  Interrupt makes agy exit, so the next turn respawns with
  ``--conversation <id>``; so does a model or effort switch.  On Windows interrupt kills
  the child outright (CTRL_BREAK cannot reach it from a console-less GUI), and
  the turn ends cancelled as soon as its stdout closes.

This module must not import ``claude_agent_sdk``.
"""
from __future__ import annotations

import asyncio
import functools
import os
import secrets
import sys
import uuid
from collections.abc import AsyncIterator, Callable
from pathlib import Path
from typing import Any

from lightfall.agents.backend import (
    AgentEvent,
    SessionConfig,
    TurnCancelledEvent,
    TurnErrorEvent,
    TurnResultEvent,
)
from lightfall.agents.backends.agy import hook_client
from lightfall.agents.backends.agy.ask_tool import ASK_SERVER_NAME, create_ask_user_server
from lightfall.agents.backends.agy.discover import console_python, hook_command
from lightfall.agents.backends.agy.events import (
    SUBAGENT_CONTINUE_PROMPT,
    TERMINAL_EVENTS,
    AgyEventTranslator,
)
from lightfall.agents.backends.agy.home import (
    AGY_MODEL_ALIASES,
    PLUGIN_NAME,
    AgyHomeConfig,
    prepare_session_home,
    remove_session_home,
    spec_home,
    sweep_all_stale_session_homes,
    write_owner_pid,
    write_session_config,
    write_spec_settings,
)
from lightfall.agents.backends.agy.hook_policy import decide
from lightfall.agents.backends.agy.host import ToolHost
from lightfall.agents.backends.agy.process import AgyProcess
from lightfall.agents.backends.agy.tool_directory import render_tool_directory
from lightfall.agents.backends.info import agy_effort_levels, agy_model_ids
from lightfall.utils.logging import logger

_CANCEL_GRACE_S = 5.0
_POLL_S = 0.1
_EXIT_WAIT_S = 2.0
_NO_PROXY = "127.0.0.1,localhost"
_RETRY = object()  # internal: the reused child died before answering

# SUBAGENT_CONTINUE_PROMPT (events.py) is the synthetic input that lets agy deliver
# a pending subagent report; see _turn_with_subagents.
MAX_SUBAGENT_CONTINUATIONS = 3


def _merge_usage(a: dict[str, Any], b: dict[str, Any]) -> dict[str, Any]:
    """Two TurnResultEvent infos as one: token counts summed, the later session id."""
    out = {**a, **b}
    for k in ("input_tokens", "output_tokens"):
        out[k] = (a.get(k) or 0) + (b.get(k) or 0)
    if a.get("total_cost_usd") is not None or b.get("total_cost_usd") is not None:
        out["total_cost_usd"] = (a.get("total_cost_usd") or 0) + (b.get("total_cost_usd") or 0)
    return out


def effort_flag(model: str | None, effort: str | None,
                levels: dict[str, tuple[str, ...]] | None) -> str | None:
    """The ``--effort`` value for this spawn, or None to omit the flag.

    Only a base model id with ``effort`` among its listed levels gets one. agy
    rejects ``--effort`` with a variant id (``gemini-4-argon-low``), with a level
    the model does not offer, and the default model's levels are unknown.
    """
    if not effort:
        return None
    if model and levels is not None and effort in levels.get(model, ()):
        return effort
    logger.debug("agy: dropping --effort {!r} (not offered by model {!r})", effort, model)
    return None


def _no_proxy_env() -> dict[str, str]:
    """NO_PROXY with 127.0.0.1/localhost added to whatever the user already has."""
    parts: list[str] = []
    for name in ("NO_PROXY", "no_proxy"):
        for p in os.environ.get(name, "").split(","):
            p = p.strip()
            if p and p not in parts:
                parts.append(p)
    for p in _NO_PROXY.split(","):
        if p not in parts:
            parts.append(p)
    value = ",".join(parts)
    # Windows env names are case-insensitive: one key, or the block has duplicates.
    if sys.platform == "win32":
        return {"NO_PROXY": value}
    return {"NO_PROXY": value, "no_proxy": value}


class AgyCliBackend:
    """Run Lightfall agent turns through ``agy``."""

    def __init__(self, cfg: SessionConfig, permission_manager: Any, require_approval: bool, *,
                 agy: str | list[str], spec_name: str | None = None, project: str,
                 location: str) -> None:
        self._cfg = cfg
        self._pm = permission_manager
        self._require_approval = require_approval
        self._agy = agy
        self._spec_name = spec_name
        self._project = project
        self._location = location
        self._model: str | None = cfg.model
        self._effort: str | None = cfg.effort or None
        self._effort_levels: dict[str, tuple[str, ...]] | None = None  # from `agy models`
        self._conversation_id: str | None = None
        self._session_home: Path | None = None
        self._token: str | None = None
        self._host: ToolHost | None = None
        self._proc: AgyProcess | None = None
        self._in_turn = False
        keys = [k for k, _ in cfg.tool_servers] + [ASK_SERVER_NAME]
        self._tool_keys = keys
        self._all_keys = keys + [k for k in cfg.external_servers if k not in keys]
        self._translator = AgyEventTranslator({f"{PLUGIN_NAME}_{k}": k for k in self._all_keys})

    # -- introspection --------------------------------------------------------

    @property
    def session_home(self) -> Path | None:
        return self._session_home

    @property
    def conversation_id(self) -> str | None:
        return self._conversation_id or self._cfg.resume

    # -- lifecycle ------------------------------------------------------------

    async def _subagent_model_ids(self) -> frozenset[str]:
        """agy model ids, fetched only if a subagent names a model that is not an alias."""
        if not any((s.model or "").strip() not in ("", *AGY_MODEL_ALIASES)
                   for s in self._cfg.subagents):
            return frozenset()
        try:
            return await asyncio.to_thread(agy_model_ids, self._agy)
        except Exception as e:  # noqa: BLE001 - unknown models just become ``inherit``
            logger.warning("agy: could not list models for subagents: {}", e)
            return frozenset()

    async def connect(self) -> None:
        cfg = self._cfg
        # Anything that can raise from config comes first, before resources exist.
        hook_cmd = hook_command(console_python(), hook_client.__file__)
        session_id = uuid.uuid4().hex
        try:
            self._token = token = secrets.token_urlsafe(32)
            self._session_home = home = prepare_session_home(self._spec_name, session_id)
            write_owner_pid(home)
            try:
                for stale in sweep_all_stale_session_homes(keep=home):
                    logger.info("agy: removed stale session home {}", stale)
            except Exception as e:  # noqa: BLE001 - housekeeping must not block connect
                logger.warning("agy: stale session-home sweep failed: {}", e)
            write_spec_settings(spec_home(self._spec_name), workspace=cfg.cwd,
                                server_keys=self._all_keys, project=self._project,
                                location=self._location)
            handler = functools.partial(
                decide, permission_manager=self._pm, require_approval=self._require_approval,
                server_keys=set(self._all_keys))
            servers = list(cfg.tool_servers) + [(ASK_SERVER_NAME, create_ask_user_server(self._pm))]
            self._host = ToolHost(servers, handler, token)
            port = await self._host.start()
            # The operator agent drops agy's own MCP listing, so name the servers here.
            directory = render_tool_directory(
                servers, [k for k in self._all_keys if k not in self._tool_keys],
                home / ".gemini" / "antigravity-cli" / "mcp")
            write_session_config(home, AgyHomeConfig(
                servers=self._tool_keys,
                external_servers=dict(cfg.external_servers),
                system_prompt=f"{cfg.system_prompt}\n\n{directory}" if directory else cfg.system_prompt,
                hook_command=hook_cmd,
                port=port,
                token=token,
                skills_src=Path(cfg.skills_dir) / "skills",
                subagents=list(cfg.subagents),
                model_ids=await self._subagent_model_ids(),
                tool_directory=directory,
            ))
            logger.info("agy backend connected: home={} port={}", home, port)
        except BaseException:
            await self.close()
            raise

    async def close(self) -> None:
        """Kill the child, stop the host, delete the session home. Idempotent, bounded."""
        proc, host, home = self._proc, self._host, self._session_home
        self._proc = None
        self._host = None
        self._session_home = None
        if proc is not None:
            try:
                await proc.kill()
            except Exception as e:  # noqa: BLE001 - close must not raise
                logger.warning("agy close: kill failed: {}", e)
        if host is not None:
            try:
                await host.stop()
            except Exception as e:  # noqa: BLE001
                logger.warning("agy close: host stop failed: {}", e)
        if home is not None:
            try:
                remove_session_home(home)
            except Exception as e:  # noqa: BLE001
                logger.warning("agy close: could not remove {}: {}", home, e)

    # -- child management -----------------------------------------------------

    async def _drop_child(self) -> None:
        proc, self._proc = self._proc, None
        if proc is not None:
            await proc.kill()

    async def _spawn_effort(self) -> str | None:
        """``--effort`` for the next spawn; lists models once (only if an effort is set)."""
        if self._effort and self._model and self._effort_levels is None:
            try:
                self._effort_levels = await asyncio.to_thread(agy_effort_levels, self._agy)
            except Exception as e:  # noqa: BLE001 - no level list: spawn without --effort
                logger.warning("agy: could not list models for --effort: {}", e)
        return effort_flag(self._model, self._effort, self._effort_levels)

    async def _ensure_child(self) -> AgyProcess:
        if self._proc is not None and self._proc.alive:
            return self._proc
        await self._drop_child()
        assert self._session_home is not None and self._host is not None
        effort = await self._spawn_effort()
        proc = AgyProcess(
            self._agy, self._session_home, self._cfg.cwd,
            env_extra={"LF_AGY_PORT": str(self._host.port), "LF_AGY_TOKEN": self._token or "",
                       **_no_proxy_env()},
            model=self._model, conversation=self._conversation_id or self._cfg.resume,
            effort=effort)
        await proc.start()
        self._translator.reset_usage()
        self._proc = proc
        return proc

    # -- turns ----------------------------------------------------------------

    async def run_turn(self, prompt: str, cancelled: Callable[[], bool],
                       stopping: Callable[[], bool]) -> AsyncIterator[AgentEvent]:
        if self._host is None or self._session_home is None:
            yield TurnErrorEvent("agy backend is not connected")
            return
        self._in_turn = True
        try:
            async for ev in self._turn_with_subagents(prompt, cancelled, stopping):
                yield ev
        finally:
            self._in_turn = False

    async def _turn_with_subagents(self, prompt: str, cancelled: Callable[[], bool],
                                   stopping: Callable[[], bool]) -> AsyncIterator[AgentEvent]:
        """One Lightfall turn: an agy turn, plus up to MAX_SUBAGENT_CONTINUATIONS
        synthetic follow-ups when it ended with a subagent report still unread.

        agy 1.4.2 holds the turn's ``result`` until the subagent finishes but
        often hands the report to the operator only with the next input (as a
        ``system_message`` step). The follow-up line is that next input. The
        intermediate results are merged into one TurnResultEvent (usage summed);
        their SessionIdEvents follow it.
        """
        held: TurnResultEvent | None = None
        trailing: list[AgentEvent] = []
        for round_no in range(MAX_SUBAGENT_CONTINUATIONS + 1):
            result: TurnResultEvent | None = None
            async for ev in self._turn(prompt if round_no == 0 else SUBAGENT_CONTINUE_PROMPT,
                                       cancelled, stopping):
                if isinstance(ev, TurnResultEvent):
                    result = ev
                elif result is not None:
                    trailing.append(ev)  # SessionIdEvent / ContextUsageEvent after the result
                else:
                    yield ev
            if result is None:
                return  # cancelled, errored or stopping: _turn already ended the turn
            held = result if held is None else TurnResultEvent(_merge_usage(held.info, result.info))
            if not self._translator.subagent_report_pending or cancelled() or stopping():
                break
            if round_no == MAX_SUBAGENT_CONTINUATIONS:
                logger.debug("agy: subagent continuation cap ({}) reached; ending the turn",
                             MAX_SUBAGENT_CONTINUATIONS)
                break
            logger.debug("agy: subagent report not yet delivered; continuation {}/{}",
                         round_no + 1, MAX_SUBAGENT_CONTINUATIONS)
        assert held is not None
        yield held
        latest: dict[type, AgentEvent] = {}  # one of each kind, the latest
        for ev in trailing:
            latest.pop(type(ev), None)
            latest[type(ev)] = ev
        for ev in latest.values():
            yield ev

    async def _turn(self, prompt: str, cancelled: Callable[[], bool],
                    stopping: Callable[[], bool]) -> AsyncIterator[AgentEvent]:
        self._translator.begin_turn()
        # A reused child may have died while idle without us noticing yet; if it
        # fails before producing anything, respawn once (with --conversation).
        for attempt in (0, 1):
            reused = self._proc is not None and self._proc.alive
            # Resending is only safe if the child provably did nothing with the
            # prompt: no event on stdout (checked in _stream) and no request reached
            # the tool host since just before the send (stricter than "after").
            host = self._host
            sent_at = host.request_count if host is not None else -1

            def _host_untouched(host=host, sent_at=sent_at) -> bool:
                return host is not None and host.request_count == sent_at

            try:
                proc = await self._ensure_child()
                await proc.send(prompt)
            except Exception as e:  # noqa: BLE001 - spawn/write failure ends the turn
                tail = self._proc.stderr_tail if self._proc is not None else ""
                await self._drop_child()
                if reused and attempt == 0 and not cancelled() and _host_untouched():
                    logger.info("agy child unusable at turn start ({}); respawning", e)
                    continue
                yield TurnErrorEvent(
                    f"could not start agy turn: {e}" + (f"\n{tail}" if tail else ""))
                return
            retry = False
            async for ev in self._stream(proc, cancelled, stopping,
                                         retryable=reused and attempt == 0,
                                         untouched=_host_untouched):
                if ev is _RETRY:
                    retry = True
                    break
                yield ev
            if not retry:
                return
            if cancelled():
                yield TurnCancelledEvent()
                return
            logger.info("agy child exited before answering; respawning with --conversation")

    async def _stream(self, proc: AgyProcess, cancelled: Callable[[], bool],
                      stopping: Callable[[], bool], *, retryable: bool,
                      untouched: Callable[[], bool]) -> AsyncIterator[AgentEvent | object]:
        loop = asyncio.get_running_loop()
        cancel_deadline: float | None = None
        reader: asyncio.Task[dict | None] | None = None
        got_any = False
        try:
            while True:
                if stopping():
                    return
                if cancel_deadline is None and cancelled():
                    cancel_deadline = loop.time() + _CANCEL_GRACE_S
                    proc.interrupt()  # idempotent; covers a cancel that raced the spawn
                if reader is None:
                    reader = loop.create_task(proc.read_event())
                done, _ = await asyncio.wait({reader}, timeout=_POLL_S)
                if not done:
                    if cancel_deadline is not None and loop.time() >= cancel_deadline:
                        logger.warning("agy did not end the turn {} s after interrupt; killing",
                                       _CANCEL_GRACE_S)
                        await self._drop_child()
                        yield TurnCancelledEvent()
                        return
                    continue
                raw, reader = reader.result(), None

                if raw is None:  # EOF before a result
                    await proc.wait(_EXIT_WAIT_S)
                    await proc.wait_stderr(1.0)
                    tail = proc.stderr_tail
                    await self._drop_child()
                    if cancel_deadline is not None or cancelled():
                        # Windows interrupt terminates the child: this is the normal path.
                        yield TurnCancelledEvent()
                    elif retryable and not got_any and untouched():
                        yield _RETRY
                    else:
                        yield TurnErrorEvent(f"agy exited unexpectedly: {tail}")
                    return
                got_any = True

                events = self._translator.feed(raw)
                if self._translator.conversation_id:
                    self._conversation_id = self._translator.conversation_id
                terminal = next((e for e in events if isinstance(e, TERMINAL_EVENTS)), None)
                user_cancel = cancel_deadline is not None or cancelled()
                if terminal is None:
                    if not user_cancel:
                        for e in events:
                            yield e
                    continue
                if user_cancel or isinstance(terminal, TurnCancelledEvent):
                    # agy exits after an interrupt; reap it so the next turn respawns.
                    await self._drop_child()
                    yield TurnCancelledEvent()
                    return
                for e in events:
                    yield e
                return
        finally:
            if reader is not None and not reader.done():
                reader.cancel()

    async def interrupt(self) -> None:
        if self._proc is not None and self._proc.alive:
            self._proc.interrupt()

    async def set_model(self, model: str | None) -> None:
        self._model = model
        if self._proc is not None and not self._in_turn:
            # agy takes --model only at spawn: respawn on the next turn.
            await self._drop_child()

    async def set_effort(self, effort: str | None) -> None:
        """Reasoning level for the next spawn (``""``/None = the model's default).

        Like ``set_model``: agy takes ``--effort`` only at spawn, so the child is
        dropped and the next turn respawns with ``--conversation`` (history kept).
        """
        self._effort = effort or None
        if self._proc is not None and not self._in_turn:
            await self._drop_child()
