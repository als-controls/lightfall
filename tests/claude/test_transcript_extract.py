from __future__ import annotations

from dataclasses import dataclass

from lightfall.claude.transcript import extract_message_text


@dataclass
class _SM:
    type: str
    message: dict


def test_user_string_content():
    role, text, tools = extract_message_text(
        _SM("user", {"role": "user", "content": "scan the sample"})
    )
    assert role == "user"
    assert text == "scan the sample"
    assert tools == []


def test_assistant_text_and_tool_use():
    sm = _SM("assistant", {"role": "assistant", "content": [
        {"type": "text", "text": "Running it now."},
        {"type": "tool_use", "name": "mcp__qt__click_widget", "input": {}},
    ]})
    role, text, tools = extract_message_text(sm)
    assert role == "assistant"
    assert text == "Running it now."
    assert tools == ["mcp__qt__click_widget"]


def test_tool_result_blocks_skipped():
    sm = _SM("user", {"role": "user", "content": [
        {"type": "tool_result", "content": "noisy output"},
        {"type": "text", "text": "thanks"},
    ]})
    role, text, tools = extract_message_text(sm)
    assert text == "thanks"
    assert tools == []


def test_empty_and_malformed_safe():
    assert extract_message_text(_SM("assistant", {})) == ("assistant", "", [])
    assert extract_message_text(_SM("user", {"content": None})) == ("user", "", [])
