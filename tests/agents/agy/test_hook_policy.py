from __future__ import annotations

import os

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


async def _decide(payload, pm=None, *, require_approval=True, roots=()):
    return await decide(payload, permission_manager=pm or FakePM(), require_approval=require_approval,
                        server_keys={"qt", "ask", "ext"}, allowed_view_roots=list(roots))


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


async def test_view_file_inside_roots_allowed(tmp_path):
    root = tmp_path / "mcp"
    root.mkdir()
    f = root / "qt" / "tool.json"
    f.parent.mkdir()
    f.write_text("{}")
    p = {"toolCall": {"name": "view_file", "args": {"AbsolutePath": str(f)}}}
    assert (await _decide(p, roots=[root]))["decision"] == "allow"


async def test_view_file_outside_roots_denied(tmp_path):
    root = tmp_path / "mcp"
    root.mkdir()
    (tmp_path / "secret.txt").write_text("x")
    for path in (str(tmp_path / "secret.txt"), str(root / ".." / "secret.txt"), "", None):
        p = {"toolCall": {"name": "view_file", "args": {"AbsolutePath": path}}}
        assert (await _decide(p, roots=[root]))["decision"] == "deny"


async def test_unknown_server_denied():
    assert (await _decide(_mcp("evil", "x")))["decision"] == "deny"
    assert (await _decide(_mcp("lightfall_nope", "x")))["decision"] == "deny"
    p = {"toolCall": {"name": "list_resources", "args": {"ServerName": "evil"}}}
    assert (await _decide(p))["decision"] == "deny"


async def test_resource_probes_allowed_on_our_servers():
    for n in ("list_resources", "read_resource"):
        p = {"toolCall": {"name": n, "args": {"ServerName": "lightfall_qt"}}}
        assert (await _decide(p))["decision"] == "allow"


@pytest.mark.parametrize("name", ["run_command", "write_to_file", "search_web"])
async def test_builtin_tools_denied(name):
    r = await _decide({"toolCall": {"name": name, "args": {}}})
    assert r == {"decision": "deny", "reason": f"Tool {name} is not available in Lightfall."}


async def test_malformed_payload_denied():
    assert (await _decide({}))["decision"] == "deny"


async def test_empty_and_unlisted_server_keys_denied():
    assert (await _decide(_mcp("lightfall_", "x")))["decision"] == "deny"
    pm = FakePM()
    r = await decide(_mcp("lightfall_qt", "x"), permission_manager=pm, require_approval=False,
                     server_keys={"other"}, allowed_view_roots=[])
    assert r["decision"] == "deny"
    assert pm.calls == []


async def test_read_resource_on_foreign_server_denied():
    p = {"toolCall": {"name": "read_resource", "args": {"ServerName": "evil"}}}
    assert (await _decide(p))["decision"] == "deny"


async def test_view_file_sibling_prefix_and_relative_denied(tmp_path):
    root = tmp_path / "mcp"
    evil = tmp_path / "mcp_evil"
    root.mkdir()
    evil.mkdir()
    (evil / "x").write_text("x")
    for path in (str(evil / "x"), "mcp/x", "x"):
        p = {"toolCall": {"name": "view_file", "args": {"AbsolutePath": path}}}
        assert (await _decide(p, roots=[root]))["decision"] == "deny"


async def test_view_file_through_link_root_allowed(tmp_path):
    target = tmp_path / "store"
    target.mkdir()
    (target / "f.json").write_text("{}")
    link = tmp_path / "link"
    try:
        os.symlink(target, link, target_is_directory=True)
    except (OSError, NotImplementedError):
        if os.name != "nt":
            pytest.skip("cannot create symlink")
        import subprocess
        r = subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(target)],
                           capture_output=True)
        if r.returncode != 0:
            pytest.skip("cannot create junction or symlink")
    for path in (link / "f.json", target / "f.json"):
        p = {"toolCall": {"name": "view_file", "args": {"AbsolutePath": str(path)}}}
        assert (await _decide(p, roots=[link]))["decision"] == "allow"
