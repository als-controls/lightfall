"""json_schema() matches the Claude SDK's wire schema for every input form."""
from __future__ import annotations

from typing import Annotated, NotRequired, Required, TypedDict

import pytest

from lightfall.agents.tooling import ToolSpec, json_schema


class _TD(TypedDict):
    motor: Annotated[str, "Motor name"]
    position: float
    tags: NotRequired[list[str]]


class _Inner(TypedDict):
    x: float
    y: float


class _Partial(TypedDict, total=False):
    name: Required[str]
    note: str


class _Nested(TypedDict):
    origin: _Inner
    points: list[_Inner]


async def _h(args):
    return {"content": []}


CASES = [
    {"type": "object", "properties": {"a": {"type": "string"}}, "required": ["a"]},
    {"text": str, "n": int, "x": float, "ok": bool, "xs": list[int], "d": dict, "opt": str | None},
    {"either": int | str},
    _TD,
    _Partial,  # Required[] inside total=False
    _Nested,  # TypedDict nested in a TypedDict and in a list
    {"xs": list, "pairs": list[list[int]]},  # bare list, list of lists
    "not-a-schema",
]


@pytest.mark.parametrize("schema", CASES, ids=range(len(CASES)))
def test_json_schema_matches_sdk(schema):
    from claude_agent_sdk import _build_input_schema

    from lightfall.agents.backends.claude_code.tools import to_sdk_tool

    spec = ToolSpec("t", "T", schema, _h)
    assert json_schema(spec) == _build_input_schema(to_sdk_tool(spec))


def test_in_tree_tools_render():
    """Every in-tree always-on tool renders to an object schema."""
    from lightfall.agents.bus_tools import _make_tools as bus
    from lightfall.agents.skill_tools import _make_tools as skills

    for t in [*bus(lambda: "x"), *skills(lambda: "x", lambda: None)]:
        s = json_schema(t)
        assert s["type"] == "object" and "properties" in s
