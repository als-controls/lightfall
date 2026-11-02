"""The agent's content tools: publish, retract and restore only after the user says Allow."""
from __future__ import annotations

import asyncio
import hashlib
import json
from pathlib import Path

import pytest

from lightfall.agents import content_tools
from lightfall.agents.tooling import tool_id
from lightfall.content import actions
from lightfall.content.actions import ContentRow
from lightfall.content.publish import LocalItem
from lightfall.scopes.client import ContentPolicy, ScopesError
from lightfall.utils.git_tracker import GitTracker

BRANCH = "branch:7.0.1.1"
CHAIN = ("facility", "beamline:7.0.1", BRANCH, "user:u1")
ROLES = {"facility": "viewer", "beamline:7.0.1": "viewer", BRANCH: "maintainer",
         "user:u1": "owner"}


class _FakePermissionManager:
    def __init__(self, choice: str | None):
        self.choice = choice
        self.questions: list[dict] = []

    async def request_question(self, questions):
        self.questions.extend(questions)
        if self.choice is None:
            return (False, {})
        return (True, {questions[0]["question"]: self.choice})


class FakeClient:
    def __init__(self, history=None):
        self.calls = []
        self.uploaded: bytes | None = None
        self._history = history if history is not None else [
            {"revision": 3, "current": True}, {"revision": 2, "current": False}]

    def publish_content(self, scope, type_, name, manifest, bundle, *, agent=False):
        self.uploaded = Path(bundle).read_bytes()
        self.calls.append(("publish", scope, type_, name, manifest, agent))
        return {"revision": 4}

    def retract_content(self, scope, type_, name, *, agent=False):
        self.calls.append(("retract", scope, type_, name, agent))
        return {"revision": 3}

    def restore_content(self, scope, type_, name, revision, *, agent=False):
        self.calls.append(("restore", scope, type_, name, revision, agent))
        return {"revision": revision}

    def content_history(self, scope, type_, name):
        return list(self._history)


class FakeTracker:
    def head_commit(self, path):
        return None


@pytest.fixture(autouse=True)
def no_git(monkeypatch):
    monkeypatch.setattr(GitTracker, "get_instance", staticmethod(lambda: FakeTracker()))
    monkeypatch.setattr(actions, "refresh_sync", lambda: None)


@pytest.fixture
def plan(tmp_path):
    p = tmp_path / "plans" / "xafs.py"
    p.parent.mkdir()
    p.write_text("def xafs():\n    yield from []\n", encoding="utf-8")
    return LocalItem("plan", "xafs", p)


def _tools(pm, client, plan=None, *, roles=ROLES, policy=None, rows=()):
    server = content_tools.create_content_tools_server(
        permission_manager=pm,
        agent_name=lambda: "planner",
        author_name=lambda: "planner-bus",
        session_id=lambda: "s1",
        client_factory=lambda: client,
        scope_state=lambda: (CHAIN, dict(roles), policy or ContentPolicy()),
        local_items=lambda: [plan] if plan else [],
        rows=lambda: list(rows),
    )
    return {t.name: t for t in server.tools}


def _call(tool, args):
    result = asyncio.run(tool.handler(args))
    return result.get("is_error", False), result["content"][0]["text"]


PUBLISH = {"type": "plan", "name": "xafs", "scope": BRANCH, "message": "first cut"}


def test_publish_asks_and_uploads_with_agent_flag(plan):
    pm, client = _FakePermissionManager(content_tools.ALLOW), FakeClient()

    err, text = _call(_tools(pm, client, plan)["lightfall_publish_content"], PUBLISH)

    assert not err, text
    assert "revision 4" in text
    assert len(pm.questions) == 1
    q = pm.questions[0]["question"]
    assert q.startswith("The assistant wants to publish plan 'xafs' to Branch 7.0.1.1: 1 files, ")
    assert q.endswith("Message: first cut")
    # The bytes in the question are the bytes uploaded.
    assert f"sha {hashlib.sha256(client.uploaded).hexdigest()[:12]}" in q
    (kind, scope, type_, name, manifest, agent), = client.calls
    assert (kind, scope, type_, name, agent) == ("publish", BRANCH, "plan", "xafs", True)
    assert manifest["agent"] is True and manifest["message"] == "first cut"
    assert manifest["sha256"] == hashlib.sha256(client.uploaded).hexdigest()
    assert "author" not in manifest


def test_publish_denied_uploads_nothing(plan):
    pm, client = _FakePermissionManager(content_tools.DENY), FakeClient()

    err, text = _call(_tools(pm, client, plan)["lightfall_publish_content"], PUBLISH)

    assert len(pm.questions) == 1
    assert client.calls == []
    assert "Not published: the user declined." in text


def test_publish_timeout_uploads_nothing(plan):
    pm, client = _FakePermissionManager(None), FakeClient()

    err, text = _call(_tools(pm, client, plan)["lightfall_publish_content"], PUBLISH)

    assert client.calls == []
    assert "Not published: no answer." in text


def test_publish_refuses_scope_without_role_before_asking(plan):
    pm, client = _FakePermissionManager(content_tools.ALLOW), FakeClient()
    tools = _tools(pm, client, plan)

    for scope, why in (("beamline:7.0.1", "not a maintainer of Beamline 7.0.1"),
                       ("branch:9.9", "not a maintainer of Branch 9.9"),
                       ("user:u1", "not a shared scope")):
        err, text = _call(tools["lightfall_publish_content"], {**PUBLISH, "scope": scope})
        assert err and why in text, (scope, text)
    assert pm.questions == [] and client.calls == []


def test_publish_reports_policy_problem_before_asking(plan):
    pm, client = _FakePermissionManager(content_tools.ALLOW), FakeClient()
    tiny = ContentPolicy(max_bundle_bytes=10, max_uncompressed_bytes=209715200, max_files=2000)

    err, text = _call(_tools(pm, client, plan, policy=tiny)["lightfall_publish_content"],
                      PUBLISH)

    assert err and "the limit is 10 B" in text
    assert pm.questions == [] and client.calls == []

    # An admin of the target scope is exempt, so the question is asked.
    admin = {**ROLES, BRANCH: "admin"}
    err, _ = _call(_tools(pm, client, plan, roles=admin, policy=tiny)
                   ["lightfall_publish_content"], PUBLISH)
    assert not err and len(pm.questions) == 1 and len(client.calls) == 1


def test_publish_error_from_server_is_explained(plan):
    class Refusing(FakeClient):
        def publish_content(self, *a, **k):
            raise ScopesError("forbidden", status=403)

    pm = _FakePermissionManager(content_tools.ALLOW)
    err, text = _call(_tools(pm, Refusing(), plan)["lightfall_publish_content"], PUBLISH)
    assert err and "not a maintainer of Branch 7.0.1.1" in text


def test_publish_unknown_local_item(plan):
    pm, client = _FakePermissionManager(content_tools.ALLOW), FakeClient()
    err, text = _call(_tools(pm, client, plan)["lightfall_publish_content"],
                      {**PUBLISH, "name": "nope"})
    assert err and "nope" in text and pm.questions == []


def test_retract_and_restore_confirm():
    pm, client = _FakePermissionManager(content_tools.ALLOW), FakeClient()
    tools = _tools(pm, client)
    args = {"type": "agent", "name": "scout", "scope": BRANCH}

    err, text = _call(tools["lightfall_retract_content"], args)
    assert not err, text
    q = pm.questions[-1]["question"]
    assert "retract agent 'scout'" in q and "Branch 7.0.1.1" in q and "revision 3" in q

    err, text = _call(tools["lightfall_restore_content"], {**args, "revision": 2})
    assert not err, text
    q = pm.questions[-1]["question"]
    assert "restore agent 'scout'" in q and "Branch 7.0.1.1" in q
    assert "revision 3" in q and "revision 2" in q
    assert client.calls == [("retract", BRANCH, "agent", "scout", True),
                            ("restore", BRANCH, "agent", "scout", 2, True)]

    # Denied or unanswered: nothing sent.
    for choice, why in ((content_tools.DENY, "the user declined"), (None, "no answer")):
        pm2, client2 = _FakePermissionManager(choice), FakeClient()
        tools2 = _tools(pm2, client2)
        _, text = _call(tools2["lightfall_retract_content"], args)
        assert f"Not retracted: {why}." in text
        _, text = _call(tools2["lightfall_restore_content"], {**args, "revision": 2})
        assert f"Not restored: {why}." in text
        assert client2.calls == []

    # Without the role, nothing is asked.
    pm3, client3 = _FakePermissionManager(content_tools.ALLOW), FakeClient()
    tools3 = _tools(pm3, client3, roles={**ROLES, BRANCH: "operator"})
    err, _ = _call(tools3["lightfall_retract_content"], args)
    assert err
    err, _ = _call(tools3["lightfall_restore_content"], {**args, "revision": 2})
    assert err and pm3.questions == [] and client3.calls == []


def test_restore_unknown_revision_is_refused_before_asking():
    pm, client = _FakePermissionManager(content_tools.ALLOW), FakeClient()
    err, text = _call(_tools(pm, client)["lightfall_restore_content"],
                      {"type": "agent", "name": "scout", "scope": BRANCH, "revision": 9})
    assert err and "9" in text and pm.questions == [] and client.calls == []


def test_question_has_no_always_allow(plan):
    pm, client = _FakePermissionManager(content_tools.DENY), FakeClient()
    tools = _tools(pm, client, plan)
    _call(tools["lightfall_publish_content"], PUBLISH)
    _call(tools["lightfall_retract_content"], {"type": "plan", "name": "xafs", "scope": BRANCH})
    _call(tools["lightfall_restore_content"],
          {"type": "plan", "name": "xafs", "scope": BRANCH, "revision": 2})
    assert len(pm.questions) == 3
    for q in pm.questions:
        assert [o["label"] for o in q["options"]] == ["Allow", "Deny"]
        assert q["multiSelect"] is False


def test_list_and_history_are_read_only(plan):
    rows = [ContentRow("plan", "xafs", BRANCH, 3, None, (), None),
            ContentRow("skill", "fit", "local", None, None, (), None)]
    pm, client = _FakePermissionManager(content_tools.ALLOW), FakeClient()
    tools = _tools(pm, client, plan, rows=rows)

    err, text = _call(tools["lightfall_list_content"], {"type": "plan"})
    data = json.loads(text)
    assert not err
    assert data["items"] == [{"type": "plan", "name": "xafs", "source": BRANCH,
                              "source_label": "Branch 7.0.1.1", "revision": 3}]
    assert data["publishable_scopes"] == [{"scope": BRANCH, "label": "Branch 7.0.1.1"}]

    err, text = _call(tools["lightfall_content_history"],
                      {"type": "plan", "name": "xafs", "scope": BRANCH})
    assert not err and json.loads(text)["history"][0]["revision"] == 3
    assert pm.questions == [] and client.calls == []


def test_server_registered_in_agent(qtbot, monkeypatch):
    from unittest.mock import MagicMock

    from PySide6.QtWidgets import QWidget

    from lightfall.claude.agent import QtClaudeAgent
    from lightfall.claude.permission_manager import PermissionManager
    from lightfall.ui.panels.claude.tool_registry import ToolRegistry

    monkeypatch.setattr("lightfall.claude.agent.ClaudeSDKClient", MagicMock())
    ToolRegistry.reset_instance()
    try:
        target = QWidget()
        qtbot.addWidget(target)
        agent = QtClaudeAgent(target_window=target, require_approval=False)
        options = agent.options
    finally:
        ToolRegistry.reset_instance()

    assert "content" in options.mcp_servers
    names = ("lightfall_list_content", "lightfall_content_history",
             "lightfall_publish_content", "lightfall_retract_content",
             "lightfall_restore_content")
    ids = [tool_id("content", n) for n in names]
    assert content_tools.CONTENT_ALLOWED_TOOLS == ids
    for i in ids:
        assert i in options.allowed_tools
        assert i not in PermissionManager.DEFAULT_AUTO_APPROVED


class ChangingClient(FakeClient):
    """History says revision 3 is current before the question, 5 after it."""

    def __init__(self):
        super().__init__()
        self.reads = 0

    def content_history(self, scope, type_, name):
        self.reads += 1
        if self.reads == 1:
            return [{"revision": 3, "current": True}, {"revision": 2, "current": False}]
        return [{"revision": 5, "current": True}, {"revision": 3, "current": False},
                {"revision": 2, "current": False}]


def test_retract_and_restore_refuse_if_it_changed_while_asking():
    args = {"type": "agent", "name": "scout", "scope": BRANCH}
    for tool_name, extra, verb in (("lightfall_retract_content", {}, "retracted"),
                                   ("lightfall_restore_content", {"revision": 2}, "restored")):
        pm, client = _FakePermissionManager(content_tools.ALLOW), ChangingClient()
        err, text = _call(_tools(pm, client)[tool_name], {**args, **extra})
        assert err and len(pm.questions) == 1 and client.calls == [], text
        assert f"Not {verb}: scout changed to revision 5 while you were deciding" in text
        assert "ask again" in text


def test_bad_names_are_refused_before_asking():
    pm, client = _FakePermissionManager(content_tools.ALLOW), FakeClient()
    tools = _tools(pm, client)
    bad = {"type": "plan", "name": "../x", "scope": BRANCH}
    for tool_name, extra in (("lightfall_retract_content", {}),
                             ("lightfall_restore_content", {"revision": 2}),
                             ("lightfall_content_history", {}),
                             ("lightfall_publish_content", {"message": "m"})):
        err, text = _call(tools[tool_name], {**bad, **extra})
        assert err and "../x" in text, (tool_name, text)
    assert pm.questions == [] and client.calls == []


def test_history_error_is_explained():
    class Refusing(FakeClient):
        def content_history(self, *a):
            raise ScopesError("forbidden", status=403)

    pm = _FakePermissionManager(content_tools.ALLOW)
    err, text = _call(_tools(pm, Refusing())["lightfall_content_history"],
                      {"type": "plan", "name": "xafs", "scope": BRANCH})
    assert err and "not a maintainer of Branch 7.0.1.1" in text
