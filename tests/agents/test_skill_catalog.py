"""The skill catalog, per-agent approvals, and the list/get skill tools."""

import asyncio
import importlib.metadata
import json
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from lightfall.agents import skill_approvals, skill_tools, skills_store

GROUP = skills_store.ENTRY_POINT_GROUP


def _skill(root: Path, name: str, description: str = "d", body: str = "b") -> Path:
    d = root / name
    d.mkdir(parents=True)
    (d / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: {description}\n---\n{body}", encoding="utf-8"
    )
    return d


@pytest.fixture
def env(tmp_path, monkeypatch):
    """Isolated skill roots, a fake ``pkgskills`` entry point, and approvals file."""
    builtin, user, site = tmp_path / "builtin", tmp_path / "user", tmp_path / "site"
    for d in (builtin, user, site):
        d.mkdir()
    pkg = site / "lf_cat_pkg"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("", encoding="utf-8")
    monkeypatch.syspath_prepend(str(site))
    eps = importlib.metadata.EntryPoints(
        [importlib.metadata.EntryPoint(name="pkgskills", value="lf_cat_pkg", group=GROUP)]
    )
    monkeypatch.setattr(importlib.metadata, "entry_points", lambda **kw: eps)
    monkeypatch.setattr(skills_store, "builtin_skills_dir", lambda: builtin)
    monkeypatch.setattr(skills_store, "user_skills_dir", lambda: user)
    monkeypatch.setattr(skills_store, "plugin_skill_dirs", lambda: {})
    monkeypatch.setattr(skills_store, "plugin_extra_skill_dirs", lambda: {})
    approvals = tmp_path / "approvals.json"
    monkeypatch.setattr(skill_approvals, "approvals_path", lambda: approvals)
    yield {"builtin": builtin, "user": user, "pkg": pkg, "approvals": approvals,
           "session": tmp_path / "session"}
    sys.modules.pop("lf_cat_pkg", None)


# ---------------------------------------------------------------------------
# catalog
# ---------------------------------------------------------------------------


def test_catalog_namespaces_package_skills_and_keeps_shadowed_ones(env):
    _skill(env["builtin"], "core", description="Use when doing core things.")
    _skill(env["pkg"], "kernel", description="Use when designing kernels.")
    _skill(env["pkg"], "shadowed", description="package version")
    _skill(env["user"], "shadowed", description="user version")

    catalog = skills_store.skill_catalog()

    assert set(catalog) == {"core", "pkgskills:kernel", "pkgskills:shadowed", "shadowed"}
    assert catalog["core"].source == "builtin"
    assert catalog["pkgskills:kernel"].source == "package"
    assert catalog["pkgskills:kernel"].name == "kernel"
    assert catalog["pkgskills:kernel"].description == "Use when designing kernels."
    assert catalog["shadowed"].source == "user"
    assert catalog["pkgskills:shadowed"].path == env["pkg"] / "shadowed"
    # resolve_skills is unchanged: bare names, the user copy wins.
    assert skills_store.resolve_skills()["shadowed"] == env["user"] / "shadowed"


def test_read_skill_description_tolerates_bad_frontmatter(tmp_path):
    bare = tmp_path / "bare"
    bare.mkdir()
    (bare / "SKILL.md").write_text("no frontmatter", encoding="utf-8")
    broken = tmp_path / "broken"
    broken.mkdir()
    (broken / "SKILL.md").write_text("---\ndescription: [unclosed\n---\nb", encoding="utf-8")
    folded = _skill(tmp_path, "folded", description="'two\n  lines'")

    assert skills_store.read_skill_description(bare) == ""
    assert skills_store.read_skill_description(broken) == ""
    assert skills_store.read_skill_description(tmp_path / "missing") == ""
    assert skills_store.read_skill_description(folded) == "two lines"


# ---------------------------------------------------------------------------
# approvals
# ---------------------------------------------------------------------------


def test_approvals_are_per_agent_and_persist(env):
    skill_approvals.approve_skill("planner", "pkgskills:kernel")
    skill_approvals.approve_skill("planner", "pkgskills:kernel")  # idempotent

    assert skill_approvals.approved_skill_ids("planner") == ("pkgskills:kernel",)
    assert skill_approvals.approved_skill_ids("lightfall") == ()
    data = json.loads(env["approvals"].read_text(encoding="utf-8"))
    assert data == {"version": 1, "agents": {"planner": ["pkgskills:kernel"]}}

    assert skill_approvals.revoke_skill("planner", "pkgskills:kernel") is True
    assert skill_approvals.revoke_skill("planner", "pkgskills:kernel") is False
    assert skill_approvals.approved_skill_ids("planner") == ()


def test_unreadable_approvals_file_is_ignored(env):
    env["approvals"].write_text("{not json", encoding="utf-8")
    assert skill_approvals.approved_skill_ids("planner") == ()


def test_materialize_includes_approved_skills(env, monkeypatch):
    _skill(env["builtin"], "core")
    _skill(env["pkg"], "kernel")
    _skill(env["pkg"], "core")  # same folder name as an explicit skill
    skill_approvals.approve_skill("planner", "pkgskills:kernel")
    skill_approvals.approve_skill("planner", "pkgskills:core")
    skill_approvals.approve_skill("planner", "pkgskills:gone")
    log = MagicMock()
    monkeypatch.setattr(skills_store, "logger", log)

    done = skills_store.materialize_skills(("core",), env["session"], approved_for="planner")

    assert done == ["core", "kernel"]
    assert (env["session"] / "skills" / "kernel" / "SKILL.md").is_file()
    assert log.warning.call_count == 1  # pkgskills:gone no longer resolves
    assert "pkgskills:gone" in log.warning.call_args.args
    # Another agent gets none of them.
    other = skills_store.materialize_skills((), env["session"] / "other", approved_for="x")
    assert other == []


# ---------------------------------------------------------------------------
# tools
# ---------------------------------------------------------------------------


class _FakePermissionManager:
    def __init__(self, choice: str | None):
        self.choice = choice
        self.questions: list[dict] = []

    async def request_question(self, questions):
        self.questions.extend(questions)
        if self.choice is None:
            return (False, {})
        return (True, {questions[0]["question"]: self.choice})


def _tools(env, pm=None, agent="planner"):
    tools = skill_tools._make_tools(
        lambda: f"{agent}-bus",
        lambda: "s1",
        agent_name=lambda: agent,
        session_plugin_dir=lambda: env["session"],
        permission_manager=pm,
    )
    return {t.name: t for t in tools}


def _call(tool, args):
    result = asyncio.run(tool.handler(args))
    return result.get("is_error", False), result["content"][0]["text"]


def test_tool_names_match_allowed_list(env):
    assert [skill_tools.tool_id("skills", n) for n in _tools(env)] == (
        skill_tools.SKILLS_ALLOWED_TOOLS
    )


def test_list_skills_reports_descriptions_sources_and_availability(env):
    _skill(env["builtin"], "core", description="Use when doing core things.")
    _skill(env["pkg"], "kernel", description="Use when designing kernels. " + "x" * 400)
    skills_store.materialize_skills(("core",), env["session"])

    err, text = _call(_tools(env)["list_skills"], {})

    assert not err
    rows = {r["id"]: r for r in json.loads(text)["skills"]}
    assert rows["core"]["available"] is True
    assert rows["core"]["source"] == "builtin"
    assert rows["pkgskills:kernel"]["available"] is False
    assert rows["pkgskills:kernel"]["source"] == "package"
    assert len(rows["pkgskills:kernel"]["description"]) <= skill_tools.DESCRIPTION_LIMIT
    assert rows["pkgskills:kernel"]["description"].startswith("Use when designing kernels.")


def test_list_skills_query_matches_every_word(env):
    _skill(env["pkg"], "kernel", description="Periodic covariance kernels")
    _skill(env["pkg"], "noise", description="Heteroscedastic noise")

    _, text = _call(_tools(env)["list_skills"], {"query": "PERIODIC kernel"})

    assert [r["id"] for r in json.loads(text)["skills"]] == ["pkgskills:kernel"]


def test_get_skill_in_session_needs_no_approval(env):
    _skill(env["builtin"], "core", body="Beamline is {{beamline}}.")
    skills_store.materialize_skills(("core",), env["session"])
    pm = _FakePermissionManager(skill_tools.DENY)

    err, text = _call(_tools(env, pm)["get_skill"], {"id": "core"})

    assert not err
    assert pm.questions == []
    assert "Beamline is " in text and "{{beamline}}" not in text


def test_get_skill_allow_once_lasts_for_the_session_only(env):
    d = _skill(env["pkg"], "kernel", description="Use when designing kernels.", body="BODY")
    (d / "references").mkdir()
    (d / "references" / "more.md").write_text("MORE", encoding="utf-8")
    pm = _FakePermissionManager(skill_tools.ALLOW_ONCE)
    tools = _tools(env, pm)

    err, text = _call(tools["get_skill"], {"id": "pkgskills:kernel", "reason": "user asked"})

    assert not err
    assert "BODY" in text and "references/more.md" in text
    q = pm.questions[0]
    assert "planner" in q["question"] and "pkgskills:kernel" in q["question"]
    assert "Use when designing kernels." in q["question"] and "user asked" in q["question"]
    assert [o["label"] for o in q["options"]] == [
        skill_tools.ALLOW_ONCE, "Always allow for planner", skill_tools.DENY
    ]
    # Second call and references don't ask again; nothing persisted.
    _call(tools["get_skill"], {"id": "pkgskills:kernel"})
    err, ref = _call(tools["get_skill_reference"],
                     {"id": "pkgskills:kernel", "path": "references/more.md"})
    assert not err and ref == "MORE"
    assert len(pm.questions) == 1
    assert skill_approvals.approved_skill_ids("planner") == ()
    # A new session (fresh tools) must ask again.
    pm2 = _FakePermissionManager(skill_tools.DENY)
    assert _call(_tools(env, pm2)["get_skill"], {"id": "pkgskills:kernel"})[0] is True
    assert len(pm2.questions) == 1


def test_get_skill_always_allow_is_per_agent(env):
    _skill(env["pkg"], "kernel")
    pm = _FakePermissionManager("Always allow for planner")

    err, _ = _call(_tools(env, pm)["get_skill"], {"id": "pkgskills:kernel"})

    assert not err
    assert skill_approvals.approved_skill_ids("planner") == ("pkgskills:kernel",)
    # Planner's next session loads it without asking; another agent must ask.
    pm_next = _FakePermissionManager(skill_tools.DENY)
    assert _call(_tools(env, pm_next)["get_skill"], {"id": "pkgskills:kernel"})[0] is False
    assert pm_next.questions == []
    pm_other = _FakePermissionManager(skill_tools.DENY)
    other = _tools(env, pm_other, agent="analyst")
    assert _call(other["get_skill"], {"id": "pkgskills:kernel"})[0] is True
    assert len(pm_other.questions) == 1


@pytest.mark.parametrize("choice", [skill_tools.DENY, None, "something else"])
def test_get_skill_denied_or_cancelled(env, choice):
    _skill(env["pkg"], "kernel", body="SECRET-BODY")
    tools = _tools(env, _FakePermissionManager(choice))

    err, text = _call(tools["get_skill"], {"id": "pkgskills:kernel"})

    assert err and "SECRET-BODY" not in text
    assert _call(tools["get_skill_reference"],
                 {"id": "pkgskills:kernel", "path": "SKILL.md"})[0] is True
    assert skill_approvals.approved_skill_ids("planner") == ()


def test_get_skill_without_permission_manager_refuses_unapproved(env):
    _skill(env["pkg"], "kernel", body="SECRET-BODY")

    err, text = _call(_tools(env, pm=None)["get_skill"], {"id": "pkgskills:kernel"})

    assert err and "SECRET-BODY" not in text


def test_get_skill_unknown_id(env):
    err, text = _call(_tools(env)["get_skill"], {"id": "nope"})
    assert err and "list_skills" in text


def test_get_skill_reference_stays_inside_the_skill(env):
    _skill(env["builtin"], "core")
    (env["builtin"] / "outside.md").write_text("OUTSIDE", encoding="utf-8")
    skills_store.materialize_skills(("core",), env["session"])
    tools = _tools(env)

    for path in ("../outside.md", str(env["builtin"] / "outside.md"), "missing.md", ""):
        err, text = _call(tools["get_skill_reference"], {"id": "core", "path": path})
        assert err and "OUTSIDE" not in text, path


def test_create_server_passes_options_through(env):
    server = skill_tools.create_skill_tools_server(
        lambda: "x", lambda: None, agent_name=lambda: "x",
        session_plugin_dir=lambda: env["session"], permission_manager=None,
    )
    assert [t.name for t in server.tools] == [
        "draft_skill", "list_skills", "get_skill", "get_skill_reference"
    ]
