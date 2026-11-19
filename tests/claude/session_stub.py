"""Make a per-turn scripted SDK client stub speak the session-stream contract.

ClaudeCodeBackend reads ``client.receive_messages()`` for the session's whole
life. Stubs written per turn define ``receive_response()`` (one turn's
messages); mixing in :class:`TurnScriptedStream` serves that turn after each
``query()``, preceded by the prompt echo the real CLI sends under
``--replay-user-messages``, and keeps the stream open between turns.
"""
from __future__ import annotations

import asyncio

from claude_agent_sdk.types import UserMessage


class TurnScriptedStream:
    def _stream_queue(self) -> asyncio.Queue:
        q = getattr(self, "_stream_q", None)
        if q is None:
            q = self._stream_q = asyncio.Queue()
        return q

    async def query(self, prompt: str) -> None:
        if not hasattr(self, "query_calls"):
            self.query_calls = []
        self.query_calls.append(prompt)
        self._stream_queue().put_nowait(prompt)

    async def receive_messages(self):
        while True:
            prompt = await self._stream_queue().get()
            yield UserMessage(content=prompt)
            async for m in self.receive_response():
                yield m
