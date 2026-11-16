"""The agent's GitHub tools: push only after the user says Allow, never leak the token."""
from __future__ import annotations

import asyncio
import json
import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

from lightfall.agents import github_tools
from lightfall.agents.tooling import tool_id
from lightfall.github.push import PushResult, push_target
from lightfall.github.session import GitHubCredential
from lightfall.updates.git import RepoState
from lightfall.utils.logging import logger

TOKEN = "ghu_SECRETTOKEN1234567890"
URL = "https://github.com/o/r.git"


class _FakePermissionManager:
    def __init__(self, choice: str | None):
        self.choice = choice
        self.questions: list[dict] = []

    async def request_question(self, questions):
        self.questions.extend(questions)
        if self.choice is None:
            return (False, {})
        return (True, {questions[0]["question"]: self.choice})


class FakeChecker:
    def __init__(self, repos=(), allowed=True):
        self.result = SimpleNamespace(repos=tuple(repos))
        self.allowed = allowed

    def is_allowed(self):
        return self.allowed


class FakeSession:
    def __init__(self, signed_in=True):
        self.cred = GitHubCredential(
            token=TOKEN, kind="app", login="ron",
            expires_at=datetime.now(UTC) + timedelta(hours=1), lightfall_user="ron",
        ) if signed_in else None

    def credential(self):
        return self.cred


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-c", "user.name=T", "-c", "user.email=t@example.com",
                    "-c", "init.defaultBranch=master", *args],
                   cwd=repo, check=True, capture_output=True)


def make_repo(path: Path, remote: str) -> Path:
    path.mkdir(parents=True)
    _git(path, "init")
    (path / "f.txt").write_text("x", encoding="utf-8")
    _git(path, "add", "f.txt")
    _git(path, "commit", "-m", "c")
    _git(path, "config", "remote.origin.url", remote)
    return path


def repo_state(root: Path, name: str = "mine") -> RepoState:
    return RepoState(root=root, dists=(name,), branch="master", default_branch="master",
                     remote="origin", dirty=False, head="aaa1111", ahead=1, behind=0,
                     remote_head="bbb2222")


class Pushes:
    def __init__(self, ok=True, message="Pushed master to origin."):
        self.calls: list = []
        self.ok, self.message = ok, message

    def __call__(self, target, cred, **kw):
        self.calls.append((target, cred, kw))
        return PushResult(self.ok, self.message, target, "old1", "new2")


def fake_target(root):
    return SimpleNamespace(root=root, branch="master", remote="origin", url=URL,
                           has_upstream=True, ahead=1)


def _tools(pm=None, *, session=None, checker=None, target=fake_target, push=None):
    server = github_tools.create_github_tools_server(
        permission_manager=pm, agent_name=lambda: "planner",
        github_session=session if session is not None else FakeSession(),
        checker=checker if checker is not None else FakeChecker(),
        target=target, push=push or Pushes())
    return {t.name: t for t in server.tools}


def _call(tool, args):
    result = asyncio.run(tool.handler(args))
    return result.get("is_error", False), result["content"][0]["text"]


@pytest.fixture
def sink():
    records: list = []
    sink_id = logger.add(records.append, format="{message}", level="DEBUG")
    yield records
    logger.remove(sink_id)


@pytest.fixture
def mine(tmp_path):
    return make_repo(tmp_path / "mine", URL)


def test_allow_pushes(mine):
    pm, push = _FakePermissionManager(github_tools.ALLOW), Pushes()
    checker = FakeChecker([repo_state(mine)])

    err, text = _call(_tools(pm, checker=checker, push=push)["lightfall_git_push"],
                      {"repo": "mine"})

    assert not err
    data = json.loads(text)
    assert data == {"ok": True, "message": "Pushed master to origin.", "branch": "master",
                    "url": URL, "old_head": "old1", "new_head": "new2"}
    assert len(push.calls) == 1 and push.calls[0][0].root == mine
    assert "url" not in push.calls[0][2]
    q = pm.questions[0]
    assert q["header"] == "GitHub push" and "as ron" in q["question"]
    assert [o["label"] for o in q["options"]] == ["Allow", "Deny"]
    assert q["options"][0]["description"] == "Push now, this once."


def test_deny_does_not_push(mine):
    push = Pushes()
    err, text = _call(
        _tools(_FakePermissionManager(github_tools.DENY),
               checker=FakeChecker([repo_state(mine)]), push=push)["lightfall_git_push"],
        {"repo": "mine"})
    assert err and text == "Not pushed: the user declined."
    assert push.calls == []


def test_no_answer_does_not_push(mine):
    push = Pushes()
    err, text = _call(
        _tools(_FakePermissionManager(None), checker=FakeChecker([repo_state(mine)]),
               push=push)["lightfall_git_push"], {"repo": "mine"})
    assert err and text == "Not pushed: no answer."
    assert push.calls == []


def test_not_signed_in_asks_nothing(mine):
    pm, push = _FakePermissionManager(github_tools.ALLOW), Pushes()
    err, text = _call(
        _tools(pm, session=FakeSession(False), checker=FakeChecker([repo_state(mine)]),
               push=push)["lightfall_git_push"], {"repo": "mine"})
    assert err and "sign in" in text
    assert pm.questions == [] and push.calls == []


def test_gate_closed_refuses(mine):
    pm, push = _FakePermissionManager(github_tools.ALLOW), Pushes()
    err, text = _call(
        _tools(pm, checker=FakeChecker([repo_state(mine)], allowed=False),
               push=push)["lightfall_git_push"], {"repo": "mine"})
    assert err and "system-admin permission" in text
    assert pm.questions == [] and push.calls == []


def test_push_refuses_non_github_path(tmp_path):
    local = make_repo(tmp_path / "local", str(tmp_path / "elsewhere.git"))
    pm, push = _FakePermissionManager(github_tools.ALLOW), Pushes()
    err, text = _call(
        _tools(pm, target=push_target, push=push)["lightfall_git_push"], {"repo": str(local)})
    assert err and "no GitHub remote" in text
    assert pm.questions == [] and push.calls == []


def test_not_a_repo(tmp_path):
    push = Pushes()
    tools = _tools(_FakePermissionManager(github_tools.ALLOW), push=push)
    for bad in (str(tmp_path / "missing"), str(tmp_path)):
        err, text = _call(tools["lightfall_git_push"], {"repo": bad})
        assert err and "is not a git repository" in text
    assert push.calls == []


def test_resolves_any_path(tmp_path):
    other = make_repo(tmp_path / "other", URL)
    push = Pushes()
    err, _ = _call(
        _tools(_FakePermissionManager(github_tools.ALLOW), push=push)["lightfall_git_push"],
        {"repo": str(other / "." )})
    assert not err
    assert push.calls[0][0].root.resolve() == other.resolve()


def test_push_failure_is_an_error(mine):
    push = Pushes(ok=False, message=f"boom {TOKEN}")
    err, text = _call(
        _tools(_FakePermissionManager(github_tools.ALLOW),
               checker=FakeChecker([repo_state(mine)]), push=push)["lightfall_git_push"],
        {"repo": "mine"})
    assert err and "boom" in text and TOKEN not in text


def test_list_repos(mine, tmp_path):
    odd = make_repo(tmp_path / "odd", str(tmp_path / "x.git"))
    checker = FakeChecker([repo_state(mine), repo_state(odd, "odd")])
    err, text = _call(_tools(checker=checker, target=push_target)["lightfall_list_git_repos"], {})
    assert not err
    data = json.loads(text)
    by = {r["name"]: r for r in data["repos"]}
    assert by["odd"]["eligible"] is False and by["odd"]["reason"] == "no GitHub remote"
    assert by["mine"]["branch"] == "master" and by["mine"]["path"] == str(mine)
    assert data["github"].startswith("signed in as ron")


def test_list_repos_not_signed_in():
    _, text = _call(_tools(session=FakeSession(False))["lightfall_list_git_repos"], {})
    assert json.loads(text)["github"] == "not signed in"


def test_token_never_in_results(mine, tmp_path, sink):
    local = make_repo(tmp_path / "local", str(tmp_path / "elsewhere.git"))
    checker = FakeChecker([repo_state(mine)])
    payloads = []
    for choice in (github_tools.ALLOW, github_tools.DENY, None):
        tools = _tools(_FakePermissionManager(choice), checker=checker,
                       push=Pushes(message=f"remote said {TOKEN}"))
        payloads.append(_call(tools["lightfall_git_push"], {"repo": "mine"})[1])
        payloads.append(_call(tools["lightfall_list_git_repos"], {})[1])
    payloads.append(_call(_tools(target=push_target)["lightfall_git_push"],
                          {"repo": str(local)})[1])
    payloads.append(_call(_tools(session=FakeSession(False), checker=checker)
                          ["lightfall_git_push"], {"repo": "mine"})[1])
    assert all(TOKEN not in p for p in payloads)
    assert all(TOKEN not in str(r) for r in sink)


def test_tool_ids_allow_listed():
    assert github_tools.GITHUB_ALLOWED_TOOLS == [
        "mcp__github__lightfall_list_git_repos", "mcp__github__lightfall_git_push"]
    assert github_tools.GITHUB_ALLOWED_TOOLS == [
        tool_id("github", n) for n in github_tools.TOOL_NAMES]


def test_server_registered_in_agent(qtbot, monkeypatch):
    from unittest.mock import MagicMock

    from PySide6.QtWidgets import QWidget

    from lightfall.claude.agent import QtClaudeAgent
    from lightfall.claude.permission_manager import PermissionManager
    from lightfall.ui.panels.claude.tool_registry import ToolRegistry

    monkeypatch.setattr("lightfall.claude.agent.ClaudeSDKClient", MagicMock())
    ToolRegistry.reset_instance()
    try:
        window = QWidget()
        qtbot.addWidget(window)
        options = QtClaudeAgent(target_window=window, require_approval=False).options
    finally:
        ToolRegistry.reset_instance()
    assert "github" in options.mcp_servers
    for i in github_tools.GITHUB_ALLOWED_TOOLS:
        assert i in options.allowed_tools
        assert i not in PermissionManager.DEFAULT_AUTO_APPROVED
