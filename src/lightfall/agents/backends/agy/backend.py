"""AgentBackend that drives the Antigravity CLI (``agy``) in stream-json mode.

One backend = one Lightfall chat session:

* ``connect()`` prepares a private per-session agy home (plugin with literal
  port/token, linked per-spec store), starts the in-process :class:`ToolHost`
  (Lightfall tools over bearer-auth HTTP MCP plus the PreToolUse ``/hook``) and
  writes the plugin config.  The agy child is spawned lazily on the first turn.
* Each turn writes one ``user`` line and translates NDJSON events until a
  terminal event.  Interrupt makes agy exit, so the next turn respawns with
  ``--conversation <id>``; so does a model switch.  On Windows interrupt kills
  the child outright (CTRL_BREAK cannot reach it from a console-less GUI), and
  the turn ends cancelled as soon as its stdout closes.

This module must not import ``claude_agent_sdk``.
"""
from __future__ import annotations

import asyncio
import functools
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
)
from lightfall.agents.backends.agy.ask_tool import ASK_SERVER_NAME, create_ask_user_server
from lightfall.agents.backends.agy.discover import console_python
from lightfall.agents.backends.agy.events import TERMINAL_EVENTS, AgyEventTranslator
from lightfall.agents.backends.agy.home import (
    PLUGIN_NAME,
    AgyHomeConfig,
    prepare_session_home,
    remove_session_home,
    spec_home,
    write_session_config,
    write_spec_settings,
)
from lightfall.agents.backends.agy.hook_policy import decide
from lightfall.agents.backends.agy.host import ToolHost
from lightfall.agents.backends.agy.process import AgyProcess
from lightfall.utils.logging import logger

HOOK_MODULE = "lightfall.agents.backends.agy.hook_client"
_CANCEL_GRACE_S = 5.0
_POLL_S = 0.1
_EXIT_WAIT_S = 2.0
_NO_PROXY = "127.0.0.1,localhost"
_RETRY = object()  # internal: the reused child died before answering


def _no_proxy_env() -> dict[str, str]:
    # Windows env names are case-insensitive: one key, or the block has duplicates.
    if sys.platform == "win32":
        return {"NO_PROXY": _NO_PROXY}
    return {"NO_PROXY": _NO_PROXY, "no_proxy": _NO_PROXY}


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

    async def connect(self) -> None:
        cfg = self._cfg
        session_id = uuid.uuid4().hex
        self._token = token = secrets.token_urlsafe(32)
        self._session_home = home = prepare_session_home(self._spec_name, session_id)
        write_spec_settings(spec_home(self._spec_name), workspace=cfg.cwd,
                            server_keys=self._all_keys, project=self._project,
                            location=self._location)
        roots = [home / ".gemini" / "antigravity-cli" / "mcp",
                 home / ".gemini" / "config" / "plugins" / PLUGIN_NAME / "skills"]
        handler = functools.partial(
            decide, permission_manager=self._pm, require_approval=self._require_approval,
            server_keys=set(self._all_keys), allowed_view_roots=roots)
        servers = list(cfg.tool_servers) + [(ASK_SERVER_NAME, create_ask_user_server(self._pm))]
        self._host = ToolHost(servers, handler, token)
        port = await self._host.start()
        write_session_config(home, AgyHomeConfig(
            servers=self._tool_keys,
            external_servers=dict(cfg.external_servers),
            system_prompt=cfg.system_prompt,
            hook_command=f'"{console_python()}" -I -m {HOOK_MODULE}',
            port=port,
            token=token,
            skills_src=Path(cfg.skills_dir) / "skills",
        ))
        logger.info("agy backend connected: home={} port={}", home, port)

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

    async def _ensure_child(self) -> AgyProcess:
        if self._proc is not None and self._proc.alive:
            return self._proc
        await self._drop_child()
        assert self._session_home is not None and self._host is not None
        proc = AgyProcess(
            self._agy, self._session_home, self._cfg.cwd,
            env_extra={"LF_AGY_PORT": str(self._host.port), "LF_AGY_TOKEN": self._token or "",
                       **_no_proxy_env()},
            model=self._model, conversation=self._conversation_id or self._cfg.resume)
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
            async for ev in self._turn(prompt, cancelled, stopping):
                yield ev
        finally:
            self._in_turn = False

    async def _turn(self, prompt: str, cancelled: Callable[[], bool],
                    stopping: Callable[[], bool]) -> AsyncIterator[AgentEvent]:
        self._translator.begin_turn()
        # A reused child may have died while idle without us noticing yet; if it
        # fails before producing anything, respawn once (with --conversation).
        for attempt in (0, 1):
            reused = self._proc is not None and self._proc.alive
            try:
                proc = await self._ensure_child()
                await proc.send(prompt)
            except Exception as e:  # noqa: BLE001 - spawn/write failure ends the turn
                tail = self._proc.stderr_tail if self._proc is not None else ""
                await self._drop_child()
                if reused and attempt == 0 and not cancelled():
                    logger.info("agy child unusable at turn start ({}); respawning", e)
                    continue
                yield TurnErrorEvent(
                    f"could not start agy turn: {e}" + (f"\n{tail}" if tail else ""))
                return
            retry = False
            async for ev in self._stream(proc, cancelled, stopping,
                                         retryable=reused and attempt == 0):
                if ev is _RETRY:
                    retry = True
                    break
                yield ev
            if not retry:
                return
            logger.info("agy child exited before answering; respawning with --conversation")

    async def _stream(self, proc: AgyProcess, cancelled: Callable[[], bool],
                      stopping: Callable[[], bool], *, retryable: bool,
                      ) -> AsyncIterator[AgentEvent | object]:
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
                    elif retryable and not got_any:
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
