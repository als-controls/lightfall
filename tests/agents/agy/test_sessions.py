"""AgySessionStore against a sqlite DB / transcript shaped like real agy 1.3.5 output."""
from __future__ import annotations

import json
import sqlite3

from lightfall.agents.backend import TranscriptEntry
from lightfall.agents.backends.agy.sessions import AgySessionStore

_SCHEMA = (
    "CREATE TABLE `conversation_summaries` (`conversation_id` text,"
    "`title` text NOT NULL DEFAULT \"\",`preview` text NOT NULL DEFAULT \"\","
    "`step_count` integer NOT NULL DEFAULT 0,`last_modified_time` datetime NOT NULL,"
    "`workspace_uris` text NOT NULL,`status` text NOT NULL DEFAULT \"\","
    "`source` text NOT NULL DEFAULT \"\",`project_id` text NOT NULL DEFAULT \"\","
    "`agent_name` text NOT NULL DEFAULT \"\","
    "`parent_conversation_id` text NOT NULL DEFAULT \"\","
    "`nesting_depth` integer NOT NULL DEFAULT 0,PRIMARY KEY (`conversation_id`))"
)


def _store_dir(tmp_path):
    d = tmp_path / ".gemini" / "antigravity-cli"
    d.mkdir(parents=True)
    return d


def _row(con, cid, title, preview, mtime, uris, parent=""):
    con.execute(
        "INSERT INTO conversation_summaries (conversation_id, title, preview, "
        "last_modified_time, workspace_uris, parent_conversation_id) VALUES (?,?,?,?,?,?)",
        (cid, title, preview, mtime, json.dumps(uris), parent))


def test_lists_top_level_sessions_for_workspace(tmp_path):
    d = _store_dir(tmp_path)
    con = sqlite3.connect(d / "conversation_summaries.db")
    con.execute(_SCHEMA)
    ws = tmp_path / "Work Dir"
    ws.mkdir()
    mine = [ws.as_uri()]
    _row(con, "old-id", "Older", "", "2026-10-16 10:00:00.1+00:00", mine)
    _row(con, "new-id", "", "Preview text", "2026-10-16 23:58:40.4496517+00:00", mine)
    _row(con, "sub-id", "Sub", "", "2026-10-16 23:59:00+00:00", mine, parent="new-id")
    _row(con, "other-id", "Other", "", "2026-10-16 23:59:30+00:00", ["file:///elsewhere/x"])
    _row(con, "long-id", "x" * 80, "", "2026-10-16 12:00:00+00:00", mine)
    con.commit()
    con.close()
    store = AgySessionStore(tmp_path, str(ws))
    infos = store.list_sessions()
    assert [i.session_id for i in infos] == ["new-id", "long-id", "old-id"]
    assert infos[0].title == "Preview text"
    assert infos[1].title == "x" * 60 + "…"
    assert len(store.list_sessions(limit=1)) == 1


def test_windows_drive_uri_matches_case_insensitively(tmp_path):
    d = _store_dir(tmp_path)
    con = sqlite3.connect(d / "conversation_summaries.db")
    con.execute(_SCHEMA)
    _row(con, "a", "A", "", "2026-10-16 10:00:00+00:00",
         ["file:///C:/Users/rp/AppData/Temp/scratch/work"])
    con.commit()
    con.close()
    store = AgySessionStore(tmp_path, "c:/users/RP/appdata/temp/scratch/work")
    # Paths are casefolded on every platform (agy runs on Windows).
    assert [i.session_id for i in store.list_sessions()] == ["a"]


def test_missing_db_is_empty(tmp_path):
    assert AgySessionStore(tmp_path, "/w").list_sessions() == []


def _write_transcript(tmp_path, cid, records):
    p = (tmp_path / ".gemini" / "antigravity-cli" / "brain" / cid / ".system_generated"
         / "logs" / "transcript.jsonl")
    p.parent.mkdir(parents=True)
    p.write_text("\n".join(json.dumps(r) for r in records) + "\n", encoding="utf-8")


def test_transcript_strips_user_request_tags(tmp_path):
    _write_transcript(tmp_path, "c1", [
        {"step_index": 0, "source": "USER_EXPLICIT", "type": "USER_INPUT", "status": "DONE",
         "content": "<USER_REQUEST>\nWhat now?\n</USER_REQUEST>\n<ADDITIONAL_METADATA>\n"
                    "The current local time is: x\n</ADDITIONAL_METADATA>"},
        {"step_index": 1, "source": "MODEL", "type": "PLANNER_RESPONSE", "status": "DONE",
         "thinking": "hmm", "tool_calls": [{"name": "view_file", "args": {}}]},
        {"step_index": 2, "source": "MODEL", "type": "GENERIC", "status": "DONE", "content": "x"},
        {"step_index": 3, "source": "MODEL", "type": "PLANNER_RESPONSE", "status": "DONE",
         "content": "Answer."},
        {"step_index": 4, "source": "USER_EXPLICIT", "type": "USER_INPUT", "status": "DONE",
         "content": "no tags here"},
    ])
    assert AgySessionStore(tmp_path, "/w").load_transcript("c1") == [
        TranscriptEntry("user", "What now?", []),
        TranscriptEntry("assistant", "Answer.", []),
        TranscriptEntry("user", "no tags here", []),
    ]


def test_transcript_tool_names(tmp_path):
    _write_transcript(tmp_path, "c2", [
        {"step_index": 1, "source": "MODEL", "type": "PLANNER_RESPONSE", "status": "DONE",
         "content": "Moving.", "tool_calls": [
             {"name": "call_mcp_tool", "args": {
                 "Arguments": "{\"motor\":\"sx\",\"position\":50}",
                 "ServerName": "\"lightfall_beamline\"", "ToolName": "\"move_motor\"",
                 "toolAction": "\"Moving\"", "toolSummary": "\"Move\""}},
             {"name": "call_mcp_tool"},
             {"name": "view_file", "args": {"AbsolutePath": "\"x\""}}]},
    ])
    [entry] = AgySessionStore(tmp_path, "/w").load_transcript("c2")
    assert entry.tool_names == ["mcp__beamline__move_motor", "call_mcp_tool", "view_file"]


def test_missing_transcript_is_empty(tmp_path):
    assert AgySessionStore(tmp_path, "/w").load_transcript("nope") == []


def test_transcript_tolerates_partial_utf8(tmp_path):
    p = (tmp_path / ".gemini" / "antigravity-cli" / "brain" / "c9" / ".system_generated"
         / "logs" / "transcript.jsonl")
    p.parent.mkdir(parents=True)
    good = json.dumps({"type": "USER_INPUT", "content": "hi"}).encode("utf-8")
    # A multi-byte character cut in half, as when agy is mid-write.
    p.write_bytes(good + b"\n" + '{"type":"PLANNER_RESPONSE","content":"é'.encode()[:-1])
    got = AgySessionStore(tmp_path, "/w").load_transcript("c9")
    assert isinstance(got, list)


def test_list_sessions_with_special_chars_in_path(tmp_path):
    root = tmp_path / "we ird#dir%20x"
    root.mkdir()
    store = root / ".gemini" / "antigravity-cli"
    store.mkdir(parents=True)
    con = sqlite3.connect(store / "conversation_summaries.db")
    con.execute(
        "CREATE TABLE conversation_summaries (conversation_id TEXT, title TEXT, preview TEXT, "
        "last_modified_time INTEGER, workspace_uris TEXT, parent_conversation_id TEXT)"
    )
    con.execute("INSERT INTO conversation_summaries VALUES "
                "('c1','T','',1,?, NULL)", (json.dumps(["file:///w"]),))
    con.commit()
    con.close()
    got = AgySessionStore(root, "/w").list_sessions()
    assert [s.session_id for s in got] == ["c1"]


def test_untitled_empty_conversations_are_not_listed(tmp_path):
    """agy creates a summary row before the first reply; an empty one is not a session."""
    d = _store_dir(tmp_path)
    con = sqlite3.connect(d / "conversation_summaries.db")
    con.execute(_SCHEMA)
    ws = tmp_path / "w"
    ws.mkdir()
    _row(con, "real", "Real", "", "2026-10-16 10:00:00+00:00", [ws.as_uri()])
    _row(con, "blank", "", "", "2026-10-16 11:00:00+00:00", [ws.as_uri()])
    con.commit()
    con.close()
    assert [i.session_id for i in AgySessionStore(tmp_path, str(ws)).list_sessions()] == ["real"]


def test_transcript_rejects_path_like_session_ids(tmp_path):
    _write_transcript(tmp_path, "ok-1", [
        {"type": "USER_INPUT", "content": "hi"}])
    outside = tmp_path / ".gemini" / "antigravity-cli" / "evil"
    (outside / ".system_generated" / "logs").mkdir(parents=True)
    (outside / ".system_generated" / "logs" / "transcript.jsonl").write_text(
        json.dumps({"type": "USER_INPUT", "content": "leak"}) + "\n", encoding="utf-8")
    store = AgySessionStore(tmp_path, "/w")
    assert store.load_transcript("ok-1") == [TranscriptEntry("user", "hi", [])]
    for bad in ("../evil", r"..\evil", "a/b", "", "."):
        assert store.load_transcript(bad) == []


def test_transcript_tool_name_without_server_or_tool(tmp_path):
    _write_transcript(tmp_path, "c3", [
        {"type": "PLANNER_RESPONSE", "content": "x", "tool_calls": [
            {"name": "call_mcp_tool", "args": {"ServerName": "\"\"", "ToolName": "\"t\""}},
            {"name": "call_mcp_tool", "args": {"ServerName": "\"lightfall_qt\""}}]}])
    [entry] = AgySessionStore(tmp_path, "/w").load_transcript("c3")
    assert entry.tool_names == ["call_mcp_tool", "call_mcp_tool"]


def test_transcript_hides_synthetic_subagent_continuation(tmp_path):
    from lightfall.agents.backends.agy.events import SUBAGENT_CONTINUE_PROMPT

    _write_transcript(tmp_path, "c4", [
        {"type": "USER_INPUT", "content": "<USER_REQUEST>\nAsk the echoer\n</USER_REQUEST>"},
        {"type": "PLANNER_RESPONSE", "content": "Waiting."},
        {"type": "USER_INPUT",
         "content": f"<USER_REQUEST>\n{SUBAGENT_CONTINUE_PROMPT}\n</USER_REQUEST>\n"
                    "<ADDITIONAL_METADATA>\nx\n</ADDITIONAL_METADATA>"},
        {"type": "PLANNER_RESPONSE", "content": "ECHOER: persimmon"},
    ])
    assert AgySessionStore(tmp_path, "/w").load_transcript("c4") == [
        TranscriptEntry("user", "Ask the echoer", []),
        TranscriptEntry("assistant", "Waiting.", []),
        TranscriptEntry("assistant", "ECHOER: persimmon", []),
    ]
