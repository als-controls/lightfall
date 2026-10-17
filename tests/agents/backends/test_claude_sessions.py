"""ClaudeSessionStore wraps SDK list_sessions/get_session_messages."""
from __future__ import annotations

from types import SimpleNamespace

from lightfall.agents.backend import SessionInfo, TranscriptEntry


def test_list_sessions_titles_fall_back(monkeypatch):
    from lightfall.agents.backends.claude_code import sessions

    infos = [
        SimpleNamespace(session_id="aaaaaaaa1111", custom_title="Named", first_prompt=None,
                        summary=None),
        SimpleNamespace(session_id="bbbbbbbb2222", custom_title=None, first_prompt="x" * 70,
                        summary=None),
        SimpleNamespace(session_id="cccccccc3333", custom_title=None, first_prompt=None,
                        summary=None),
    ]
    monkeypatch.setattr(sessions, "_sdk_list_sessions", lambda directory, limit: infos)
    got = sessions.ClaudeSessionStore("D").list_sessions()
    assert got == [
        SessionInfo("aaaaaaaa1111", "Named"),
        SessionInfo("bbbbbbbb2222", "x" * 60 + "…"),
        SessionInfo("cccccccc3333", "cccccccc"),
    ]


def test_list_sessions_swallows_errors(monkeypatch):
    from lightfall.agents.backends.claude_code import sessions

    def boom(directory, limit):
        raise RuntimeError("x")

    monkeypatch.setattr(sessions, "_sdk_list_sessions", boom)
    assert sessions.ClaudeSessionStore("D").list_sessions() == []


def test_load_transcript_uses_extract_message_text(monkeypatch):
    from lightfall.agents.backends.claude_code import sessions

    msg = SimpleNamespace(type="assistant", message={"content": [
        {"type": "text", "text": "hi"}, {"type": "tool_use", "name": "mcp__qt__screenshot"},
    ]})
    monkeypatch.setattr(sessions, "_sdk_get_session_messages", lambda sid, directory: [msg])
    assert sessions.ClaudeSessionStore("D").load_transcript("s") == [
        TranscriptEntry("assistant", "hi", ["mcp__qt__screenshot"])
    ]
