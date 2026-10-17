from __future__ import annotations

import pytest

from lightfall.agents.backends.agy.ask_tool import ASK_TOOL_ID
from lightfall.agents.backends.agy.hook_policy import decide


class FakePM:
    def __init__(self, auto=False, result=(True, ""), exc=None):
        self.auto = auto
        self.result = result
        self.exc = exc
        self.calls = []

    def is_auto_approved(self, tool):
        return self.auto

    async def request_permission(self, tool, tool_input):
        self.calls.append((tool, tool_input))
        if self.exc:
            raise self.exc
        return self.result


def _mcp(server, tool, args=None):
    return {"toolCall": {"name": "call_mcp_tool",
                         "args": {"ServerName": server, "ToolName": tool, "Arguments": args or {}}}}


async def _decide(payload, pm=None, *, require_approval=True):
    return await decide(payload, permission_manager=pm or FakePM(), require_approval=require_approval,
                        server_keys={"qt", "ask", "ext"})


async def test_lightfall_mcp_tool_asks_pm_with_tool_id():
    pm = FakePM()
    r = await _decide(_mcp("lightfall_qt", "click_widget", {"w": 1}), pm)
    assert r["decision"] == "allow"
    assert pm.calls == [("mcp__qt__click_widget", {"w": 1})]


async def test_external_server_uses_its_own_name():
    pm = FakePM()
    await _decide(_mcp("lightfall_ext", "do"), pm)
    assert pm.calls == [("mcp__ext__do", {})]


async def test_auto_approved_skips_pm():
    pm = FakePM(auto=True)
    assert (await _decide(_mcp("lightfall_qt", "x"), pm))["decision"] == "allow"
    assert pm.calls == []


async def test_require_approval_false_allows():
    pm = FakePM(result=(False, "no"))
    assert (await _decide(_mcp("lightfall_qt", "x"), pm, require_approval=False))["decision"] == "allow"
    assert pm.calls == []


async def test_ask_user_always_allowed():
    pm = FakePM(result=(False, "no"))
    assert ASK_TOOL_ID == "mcp__ask__ask_user"
    r = await _decide(_mcp("lightfall_ask", "ask_user"), pm)
    assert r["decision"] == "allow"
    assert pm.calls == []


async def test_pm_denial_reason_passed():
    r = await _decide(_mcp("lightfall_qt", "x"), FakePM(result=(False, "Nope")))
    assert r == {"decision": "deny", "reason": "Nope"}
    r = await _decide(_mcp("lightfall_qt", "x"), FakePM(result=(False, "")))
    assert r["reason"] == "User denied permission"


async def test_pm_exception_denies():
    r = await _decide(_mcp("lightfall_qt", "x"), FakePM(exc=RuntimeError("boom")))
    assert r == {"decision": "deny", "reason": "Permission system error: boom"}


async def test_unknown_server_denied():
    assert (await _decide(_mcp("evil", "x")))["decision"] == "deny"
    assert (await _decide(_mcp("lightfall_nope", "x")))["decision"] == "deny"
    p = {"toolCall": {"name": "list_resources", "args": {"ServerName": "evil"}}}
    assert (await _decide(p))["decision"] == "deny"


async def test_resource_probes_allowed_on_our_servers():
    for n in ("list_resources", "read_resource"):
        p = {"toolCall": {"name": n, "args": {"ServerName": "lightfall_qt"}}}
        assert (await _decide(p))["decision"] == "allow"


@pytest.mark.parametrize("name,perm", [
    ("view_file", "Read"), ("list_dir", "LS"), ("grep_search", "Grep"), ("find_by_name", "Glob"),
    ("write_to_file", "Write"), ("replace_file_content", "Edit"),
    ("multi_replace_file_content", "MultiEdit"), ("edit_file", "Edit"),
    ("run_command", "Bash"), ("read_url_content", "WebFetch"), ("search_web", "WebSearch"),
])
async def test_builtin_tools_go_through_pm_under_claude_names(name, perm):
    pm = FakePM()
    args = {"CommandLine": "dir"}
    r = await _decide({"toolCall": {"name": name, "args": args}}, pm)
    assert r["decision"] == "allow"
    assert pm.calls == [(perm, args)]


async def test_builtin_auto_approved_skips_pm():
    pm = FakePM(auto=True)
    r = await _decide({"toolCall": {"name": "view_file", "args": {"AbsolutePath": "C:/x"}}}, pm)
    assert r["decision"] == "allow" and pm.calls == []


async def test_builtin_denied_by_user():
    pm = FakePM(result=(False, "No shell"))
    r = await _decide({"toolCall": {"name": "run_command", "args": {}}}, pm)
    assert r == {"decision": "deny", "reason": "No shell"}


async def test_builtin_without_approval_allowed():
    r = await _decide({"toolCall": {"name": "run_command", "args": {}}}, require_approval=False)
    assert r["decision"] == "allow"


@pytest.mark.parametrize("name", ["browser_click", "execute_command", "schedule"])
async def test_unoffered_builtin_tools_denied(name):
    r = await _decide({"toolCall": {"name": name, "args": {}}})
    assert r == {"decision": "deny", "reason": f"Tool {name} is not available in Lightfall."}


async def test_malformed_payload_denied():
    assert (await _decide({}))["decision"] == "deny"


async def test_empty_and_unlisted_server_keys_denied():
    assert (await _decide(_mcp("lightfall_", "x")))["decision"] == "deny"
    pm = FakePM()
    r = await decide(_mcp("lightfall_qt", "x"), permission_manager=pm, require_approval=False,
                     server_keys={"other"})
    assert r["decision"] == "deny"
    assert pm.calls == []


async def test_read_resource_on_foreign_server_denied():
    p = {"toolCall": {"name": "read_resource", "args": {"ServerName": "evil"}}}
    assert (await _decide(p))["decision"] == "deny"
