"""MCP tool server that lets an agy session ask the user multi-choice questions.

agy has no usable built-in question tool in headless mode, so Lightfall exposes
``ask_user`` as its own MCP tool. The handler routes through
``PermissionManager.request_question`` (the same path the Claude backend uses
for AskUserQuestion) and formats the answers for the model.

This module must not import ``claude_agent_sdk``.
"""
from __future__ import annotations

from typing import Any

from lightfall.agents.tooling import ToolServer, tool, tool_id

ASK_SERVER_NAME = "ask"
ASK_SERVER_VERSION = "1.0.0"
ASK_TOOL_NAME = "ask_user"
ASK_TOOL_ID = tool_id(ASK_SERVER_NAME, ASK_TOOL_NAME)

ASK_USER_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["questions"],
    "properties": {
        "questions": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["question", "options"],
                "properties": {
                    "question": {"type": "string"},
                    "header": {"type": "string"},
                    "multiSelect": {"type": "boolean"},
                    "options": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "required": ["label"],
                            "properties": {
                                "label": {"type": "string"},
                                "description": {"type": "string"},
                            },
                        },
                    },
                },
            },
        }
    },
}

_ASK_USER_DESCRIPTION = (
    "Ask the user one or more multiple-choice questions and wait for their answers. "
    "Use this instead of guessing when a choice needs the user's input."
)


def _format_answers(questions: list[dict[str, Any]], answers: dict[str, str]) -> str:
    lines = ["User answers:"]
    for q in questions:
        text = q.get("question", "")
        lines.append(f"- {text}: {answers.get(text) or '(no answer)'}")
    return "\n".join(lines)


def create_ask_user_server(permission_manager: Any) -> ToolServer:
    """Build the ``ask`` MCP server bound to a PermissionManager."""

    @tool(ASK_TOOL_NAME, _ASK_USER_DESCRIPTION, ASK_USER_SCHEMA)
    async def ask_user(args: dict[str, Any]) -> dict[str, Any]:
        questions = args.get("questions")
        if (not isinstance(questions, list) or not questions
                or not all(isinstance(q, dict) for q in questions)):
            return {
                "content": [{"type": "text", "text": "ask_user needs a non-empty 'questions' "
                             "list of question objects"}],
                "is_error": True,
            }
        answered, answers = await permission_manager.request_question(questions)
        if not answered:
            return {
                "content": [{"type": "text", "text": "User declined to answer"}],
                "is_error": True,
            }
        return {
            "content": [
                {"type": "text", "text": _format_answers(questions, answers)}
            ]
        }

    return ToolServer(
        name=ASK_SERVER_NAME,
        version=ASK_SERVER_VERSION,
        tools=(ask_user,),
    )
