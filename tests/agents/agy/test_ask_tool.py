# tests/agents/agy/test_ask_tool.py
from __future__ import annotations

import asyncio

import pytest


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


def _tool(pm):
    from lightfall.agents.backends.agy.ask_tool import create_ask_user_server

    (t,) = create_ask_user_server(pm).tools
    return t


def test_multi_question_and_multiselect_answers_in_question_order():
    qs = [{"question": "Which detectors?", "header": "Det", "multiSelect": True,
           "options": [{"label": "pilatus"}, {"label": "eiger"}]},
          {"question": "Exposure?", "header": "Exp",
           "options": [{"label": "1 s"}, {"label": "10 s"}]}]
    # The UI joins multi-select labels with commas (SDK contract).
    pm = _PM(True, {"Exposure?": "10 s", "Which detectors?": "pilatus,eiger"})
    out = asyncio.run(_tool(pm).handler({"questions": qs}))
    assert pm.seen == qs
    assert out["content"][0]["text"] == ("User answers:\n- Which detectors?: pilatus,eiger\n"
                                         "- Exposure?: 10 s")
    assert not out.get("is_error")


def test_unanswered_question_is_reported_not_dropped():
    qs = _q() + [{"question": "Exposure?", "options": [{"label": "1 s"}]}]
    out = asyncio.run(_tool(_PM(True, {"Which detector?": "eiger"})).handler({"questions": qs}))
    assert out["content"][0]["text"] == ("User answers:\n- Which detector?: eiger\n"
                                         "- Exposure?: (no answer)")


def test_empty_answers_dict():
    out = asyncio.run(_tool(_PM(True, {})).handler({"questions": _q()}))
    assert out["content"][0]["text"] == "User answers:\n- Which detector?: (no answer)"
    assert not out.get("is_error")



@pytest.mark.parametrize("args", [{}, {"questions": []}, {"questions": None},
                                  {"questions": "Which?"}, {"questions": ["Which?"]}])
def test_missing_or_bad_questions_is_an_error_without_asking(args):
    pm = _PM(True, {})
    out = asyncio.run(_tool(pm).handler(args))
    assert out["is_error"] is True
    assert "questions" in out["content"][0]["text"]
    assert pm.seen is None  # the user was never shown an empty dialog
