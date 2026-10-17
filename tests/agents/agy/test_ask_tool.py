# tests/agents/agy/test_ask_tool.py
from __future__ import annotations

import asyncio


class _PM:
    def __init__(self, answered, answers):
        self.answered, self.answers, self.seen = answered, answers, None

    async def request_question(self, questions):
        self.seen = questions
        return self.answered, self.answers


def _q():
    return [{"question": "Which detector?", "header": "Det",
             "options": [{"label": "pilatus"}, {"label": "eiger"}], "multiSelect": False}]


def test_ask_user_formats_answers():
    from lightfall.agents.backends.agy.ask_tool import create_ask_user_server

    pm = _PM(True, {"Which detector?": "eiger"})
    srv = create_ask_user_server(pm)
    (t,) = srv.tools
    assert (srv.name, t.name) == ("ask", "ask_user")
    out = asyncio.run(t.handler({"questions": _q()}))
    assert pm.seen == _q()
    assert out["content"][0]["text"] == "User answers:\n- Which detector?: eiger"
    assert not out.get("is_error")


def test_ask_user_cancel_is_error():
    from lightfall.agents.backends.agy.ask_tool import create_ask_user_server

    (t,) = create_ask_user_server(_PM(False, {})).tools
    out = asyncio.run(t.handler({"questions": _q()}))
    assert out == {"content": [{"type": "text", "text": "User declined to answer"}], "is_error": True}
