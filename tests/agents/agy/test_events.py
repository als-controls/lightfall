"""AgyEventTranslator: agy stream-json lines -> neutral AgentEvents.

Lines are copied from the Phase 0 captures (agy 1.3.5), trimmed where noted.
"""
from __future__ import annotations

import json

from lightfall.agents.backend import (
    PartialFinishedEvent,
    PartialStartedEvent,
    PartialTextEvent,
    SessionIdEvent,
    ToolCallEvent,
    ToolResultEvent,
    TurnCancelledEvent,
    TurnErrorEvent,
    TurnResultEvent,
)
from lightfall.agents.backends.agy.events import AgyEventTranslator

CID = "00e724dc-c2e7-4d2a-bdcf-455b22c7767b"

TEXT_LINES = [
    '{"event":"step_update","step_update":{"conversation_id":"00e724dc-c2e7-4d2a-bdcf-455b22c7767b","step_index":14,"state":"ACTIVE","step_type":"agent_response","text_delta":"1. **Move motor `sx` to "}}',
    '{"event":"step_update","step_update":{"conversation_id":"00e724dc-c2e7-4d2a-bdcf-455b22c7767b","step_index":14,"state":"ACTIVE","step_type":"agent_response","text_delta":"50:** Succee"}}',
    '{"event":"step_update","step_update":{"conversation_id":"00e724dc-c2e7-4d2a-bdcf-455b22c7767b","step_index":14,"state":"DONE","step_type":"agent_response","text_delta":"_command is not available in Lightfall.`).\\n","duration_seconds":4.3515978,"usage":{"input_tokens":2788,"output_tokens":191,"thinking_tokens":21,"cache_read_tokens":13970,"total_tokens":2979}}}',
]

TOOL_ACTIVE = '{"event":"step_update","step_update":{"conversation_id":"00e724dc-c2e7-4d2a-bdcf-455b22c7767b","step_index":5,"state":"ACTIVE","step_type":"tool","tool_name":"call_mcp_tool","tool_info":{"name":"call_mcp_tool","parameters":{"Arguments":{"motor":"sx","position":50},"ServerName":"lightfall_beamline","ToolName":"move_motor"}}}}'
TOOL_DONE = '{"event":"step_update","step_update":{"conversation_id":"00e724dc-c2e7-4d2a-bdcf-455b22c7767b","step_index":5,"state":"DONE","step_type":"tool","tool_name":"call_mcp_tool","duration_seconds":0.104408,"tool_info":{"name":"call_mcp_tool","parameters":{"Arguments":{"motor":"sx","position":50},"ServerName":"lightfall_beamline","ToolName":"move_motor"},"output":"moved sx to 50.0"}}}'

TOOL_ERROR = '{"event":"step_update","step_update":{"conversation_id":"c7f4360a-e343-4726-8c62-11031e7cf406","step_index":5,"state":"ERROR","step_type":"tool","tool_name":"call_mcp_tool","duration_seconds":0.1042351,"tool_info":{"name":"call_mcp_tool","parameters":{"Arguments":{"a":2.5,"b":4},"ServerName":"lightfall","ToolName":"add"},"error":{"type":"TOOL_ERROR","message":"permission check failed for mcp \\"lightfall/add\\": user denied permission for mcp(lightfall/add)"}}}}'

VIEW_ACTIVE = '{"event":"step_update","step_update":{"conversation_id":"c7f4360a-e343-4726-8c62-11031e7cf406","step_index":2,"state":"ACTIVE","step_type":"tool","tool_name":"view_file","tool_info":{"name":"view_file","parameters":{"AbsolutePath":"C:\\\\h\\\\.gemini\\\\antigravity-cli\\\\mcp\\\\lightfall\\\\add.json"}}}}'

INIT = '{"event":"init","conversation_id":"c7f4360a-e343-4726-8c62-11031e7cf406","init":{"cwd":"C:\\\\work","tools":["call_mcp_tool","view_file"],"permission_mode":"request-review"}}'
USER_INPUT = '{"event":"step_update","step_update":{"conversation_id":"c7f4360a-e343-4726-8c62-11031e7cf406","step_index":0,"state":"DONE","step_type":"user_input"}}'

RESULT_OK = '{"event":"result","result":{"conversation_id":"00e724dc-c2e7-4d2a-bdcf-455b22c7767b","status":"SUCCESS","response":"1. **Move motor `sx` to 50:** Succeeded.","duration_seconds":12.0,"num_turns":1,"usage":{"input_tokens":29422,"output_tokens":1814,"thinking_tokens":814,"cache_read_tokens":80866,"total_tokens":31236}}}'
RESULT_UNKNOWN_AGENT = '{"event":"result","result":{"conversation_id":"","status":"ERROR","response":"","error":"unknown agent \\"lightfall\\" (no custom agents are available)","duration_seconds":0.0475464,"num_turns":0,"usage":{"input_tokens":0,"output_tokens":0,"thinking_tokens":0,"cache_read_tokens":0,"total_tokens":0}}}'


def _feed(tr, *lines):
    out = []
    for line in lines:
        out += tr.feed(json.loads(line))
    return out


def _tr():
    return AgyEventTranslator({"lightfall_beamline": "beamline", "lightfall_qt": "qt"})


def test_text_deltas_become_partials():
    bid = f"{CID}:14"
    assert _feed(_tr(), *TEXT_LINES) == [
        PartialStartedEvent(bid, "text"),
        PartialTextEvent(bid, "1. **Move motor `sx` to "),
        PartialTextEvent(bid, "50:** Succee"),
        PartialTextEvent(bid, "_command is not available in Lightfall.`).\n"),
        PartialFinishedEvent(bid),
    ]


def test_response_done_without_text_emits_nothing():
    line = ('{"event":"step_update","step_update":{"conversation_id":"bf16","step_index":1,'
            '"state":"DONE","step_type":"agent_response","usage":{"input_tokens":1}}}')
    assert _feed(_tr(), line) == []


def test_init_and_user_input_and_unknown_are_ignored():
    tr = _tr()
    assert _feed(tr, INIT, USER_INPUT, '{"event":"mystery"}') == []
    assert tr.conversation_id == "c7f4360a-e343-4726-8c62-11031e7cf406"


def test_tool_step_maps_mcp_name_to_tool_id():
    tr = _tr()
    assert _feed(tr, TOOL_ACTIVE, TOOL_DONE) == [
        ToolCallEvent("mcp__beamline__move_motor", {"motor": "sx", "position": 50}),
        ToolResultEvent(f"{CID}:5", {"content": "moved sx to 50.0", "is_error": False}),
    ]
    # builtin tools keep their own name and parameters
    assert _feed(tr, VIEW_ACTIVE) == [
        ToolCallEvent("view_file", {"AbsolutePath": "C:\\h\\.gemini\\antigravity-cli\\mcp\\lightfall\\add.json"})
    ]


def test_tool_error_result():
    # first seen in ERROR: the call is emitted first; unprefixed server name kept as-is
    ev = _feed(_tr(), TOOL_ERROR)
    assert ev[0] == ToolCallEvent("mcp__lightfall__add", {"a": 2.5, "b": 4})
    assert isinstance(ev[1], ToolResultEvent)
    assert ev[1].tool_use_id == "c7f4360a-e343-4726-8c62-11031e7cf406:5"
    assert ev[1].payload["is_error"] is True
    assert "user denied permission" in ev[1].payload["content"]


def test_success_result_then_session_id():
    assert _feed(_tr(), RESULT_OK) == [
        TurnResultEvent({"total_cost_usd": None, "input_tokens": 29422, "output_tokens": 1814,
                         "session_id": CID}),
        SessionIdEvent(CID),
    ]


def test_error_with_response_and_no_error_is_success():
    line = RESULT_OK.replace('"status":"SUCCESS"', '"status":"ERROR"')
    ev = _feed(_tr(), line)
    assert isinstance(ev[0], TurnResultEvent) and ev[1] == SessionIdEvent(CID)


def test_interrupted_result_is_cancelled():
    line = ('{"event":"result","result":{"conversation_id":"x","status":"ERROR","response":"",'
            '"error":"interrupted","usage":{"input_tokens":5,"output_tokens":1}}}')
    assert _feed(_tr(), line) == [TurnCancelledEvent()]
    for status in ("INTERRUPTED", "CANCELED"):
        line = f'{{"event":"result","result":{{"conversation_id":"x","status":"{status}"}}}}'
        assert _feed(_tr(), line) == [TurnCancelledEvent()]


def test_other_error_is_turn_error():
    assert _feed(_tr(), RESULT_UNKNOWN_AGENT) == [
        TurnErrorEvent('unknown agent "lightfall" (no custom agents are available)')
    ]
    assert _feed(_tr(), '{"event":"result","result":{"status":"INVALID"}}') == [
        TurnErrorEvent("agy turn failed")
    ]


def _result(inp, out):
    return json.dumps({"event": "result", "result": {
        "conversation_id": "c1", "status": "SUCCESS", "response": "ok",
        "usage": {"input_tokens": inp, "output_tokens": out}}})


def test_usage_is_per_turn_delta():
    tr = _tr()
    first = _feed(tr, _result(1000, 100))[0]
    second = _feed(tr, _result(1500, 160))[0]
    assert (first.info["input_tokens"], first.info["output_tokens"]) == (1000, 100)
    assert (second.info["input_tokens"], second.info["output_tokens"]) == (500, 60)
    # a respawned process restarts its counters
    tr.reset_usage()
    third = _feed(tr, _result(700, 40))[0]
    assert (third.info["input_tokens"], third.info["output_tokens"]) == (700, 40)
    # a counter that went backwards without a reset is taken as this turn's total
    fourth = _feed(tr, _result(300, 50))[0]
    assert (fourth.info["input_tokens"], fourth.info["output_tokens"]) == (300, 50)


def test_step_index_reuse_across_turns():
    tr = _tr()
    first = _feed(tr, *TEXT_LINES, TOOL_ACTIVE, TOOL_DONE)
    tr.begin_turn()
    # a respawned agy resumes the same conversation and restarts step indexes
    second = _feed(tr, *TEXT_LINES, TOOL_ACTIVE, TOOL_DONE)
    assert second == first
    assert isinstance(second[0], PartialStartedEvent)
    assert sum(isinstance(e, ToolCallEvent) for e in second) == 1
