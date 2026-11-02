"""Publish/retract/restore actions, the live scope state, and content rows from the loaders."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

from lightfall.acquire.plans.registry import PlanRegistry
from lightfall.acquire.plans.user_plans import UserPlanService
from lightfall.agents.registry import AgentSpecRegistry
from lightfall.content import actions
from lightfall.content.actions import ContentRow
from lightfall.content.clashes import ClashLog
from lightfall.content.publish import Bundle, LocalItem
from lightfall.content.service import ContentSync
from lightfall.content.sources import SourceItem
from lightfall.content.store import ContentStore
from lightfall.core.services import ServiceRegistry
from lightfall.plugins.user_plugins import UserPluginService
from lightfall.scopes.client import ChainInfo, ContentItem, ContentListing, ContentPolicy
from lightfall.ui.panels.claude.tool_registry import ToolRegistry
from lightfall.ui.panels.registry import PanelRegistry
from lightfall.ui.preferences.manager import PreferencesManager, ScopeInfo
from lightfall.utils.git_tracker import GitTracker

BRANCH = "branch:7.0.1.1"
CHAIN = ("facility", "beamline:7.0.1", BRANCH, "user:u1")


# -- publishable scopes / admin ----------------------------------------------------------

def test_publishable_scopes_only_maintainer_plus_and_no_user():
    roles = {"facility": "viewer", "beamline:7.0.1": "maintainer", BRANCH: "admin",
             "user:u1": "owner"}
    assert actions.publishable_scopes(CHAIN, roles) == [BRANCH, "beamline:7.0.1"]
    assert actions.publishable_scopes(CHAIN, {"facility": "operator", BRANCH: None}) == []
    # A role this client doesn't know is not a grant.
    assert actions.publishable_scopes(("facility",), {"facility": "superuser"}) == []
    assert actions.publishable_scopes((), {}) == []


def test_is_admin():
    roles = {"facility": "owner", "beamline:7.0.1": "admin", BRANCH: "maintainer"}
    assert actions.is_admin("facility", roles)
    assert actions.is_admin("beamline:7.0.1", roles)
    assert not actions.is_admin(BRANCH, roles)
    assert not actions.is_admin("branch:9.9", roles)
    assert not actions.is_admin("facility", {"facility": "weird"})


# -- client calls ------------------------------------------------------------------------

class FakeClient:
    def __init__(self):
        self.calls = []

    def publish_content(self, scope, type_, name, manifest, bundle, *, agent=False):
        self.calls.append(("publish", scope, type_, name, manifest, bundle, agent))
        return {"revision": 4}

    def retract_content(self, scope, type_, name, *, agent=False):
        self.calls.append(("retract", scope, type_, name, agent))
        return {"retracted": True}

    def restore_content(self, scope, type_, name, revision, *, agent=False):
        self.calls.append(("restore", scope, type_, name, revision, agent))
        return {"revision": revision}

    def content_history(self, scope, type_, name):
        self.calls.append(("history", scope, type_, name))
        return [{"revision": 1}, {"revision": 2}]


class FakeSync:
    def __init__(self):
        self.refreshes = 0

    def refresh(self):
        self.refreshes += 1


@pytest.fixture
def registered_sync():
    sync = FakeSync()
    ServiceRegistry.get_instance().register_instance(ContentSync, sync, replace=True)
    yield sync
    ServiceRegistry.get_instance().unregister(ContentSync)


@pytest.fixture
def no_git(monkeypatch):
    class FakeTracker:
        def head_commit(self, path):
            return None

    monkeypatch.setattr(GitTracker, "get_instance", staticmethod(lambda: FakeTracker()))


def _bundle(tmp_path) -> Bundle:
    p = tmp_path / "b.zip"
    p.write_bytes(b"zip")
    return Bundle(path=p, sha256="ab" * 32, size=3, file_count=1, uncompressed_size=6,
                  files=("scan.py",))


def test_publish_calls_client_and_refreshes_sync(tmp_path, registered_sync, no_git):
    client = FakeClient()
    item = LocalItem("plan", "scan", tmp_path / "scan.py")
    bundle = _bundle(tmp_path)
    out = actions.publish(client, item, bundle, BRANCH, message="first", author="Ron")
    assert out == {"revision": 4}
    (kind, scope, type_, name, manifest, path, agent), = client.calls
    assert (kind, scope, type_, name, path, agent) == ("publish", BRANCH, "plan", "scan",
                                                       bundle.path, False)
    assert manifest["sha256"] == bundle.sha256 and manifest["message"] == "first"
    assert manifest["author"] == "Ron" and manifest["agent"] is False
    assert registered_sync.refreshes == 1

    actions.publish(client, item, bundle, BRANCH, message="", author=None, agent=True)
    manifest, agent = client.calls[-1][4], client.calls[-1][6]
    assert manifest["agent"] is True and agent is True and "author" not in manifest
    assert registered_sync.refreshes == 2


def test_publish_failure_does_not_refresh(tmp_path, registered_sync, no_git):
    from lightfall.scopes.client import ScopesError

    class Failing(FakeClient):
        def publish_content(self, *a, **k):
            raise ScopesError("nope", status=403)

    with pytest.raises(ScopesError):
        actions.publish(Failing(), LocalItem("plan", "scan", tmp_path / "scan.py"),
                        _bundle(tmp_path), BRANCH, message="m", author=None)
    assert registered_sync.refreshes == 0


def test_retract_restore_history_pass_agent_flag(registered_sync):
    client = FakeClient()
    assert actions.retract(client, BRANCH, "plugin", "tools") == {"retracted": True}
    assert actions.retract(client, BRANCH, "plugin", "tools", agent=True)
    assert actions.restore(client, BRANCH, "plugin", "tools", 2) == {"revision": 2}
    assert actions.restore(client, BRANCH, "plugin", "tools", 3, agent=True)
    assert actions.history(client, BRANCH, "plugin", "tools") == [{"revision": 1},
                                                                  {"revision": 2}]
    assert client.calls == [
        ("retract", BRANCH, "plugin", "tools", False),
        ("retract", BRANCH, "plugin", "tools", True),
        ("restore", BRANCH, "plugin", "tools", 2, False),
        ("restore", BRANCH, "plugin", "tools", 3, True),
        ("history", BRANCH, "plugin", "tools"),
    ]
    assert registered_sync.refreshes == 4  # writes refresh; history only reads


def test_refresh_sync_without_a_sync_or_with_a_broken_one_never_raises():
    ServiceRegistry.get_instance().unregister(ContentSync)
    actions.refresh_sync()

    class Broken:
        def refresh(self):
            raise RuntimeError("boom")

    ServiceRegistry.get_instance().register_instance(ContentSync, Broken(), replace=True)
    try:
        actions.refresh_sync()
    finally:
        ServiceRegistry.get_instance().unregister(ContentSync)


def test_refresh_sync_from_a_worker_thread_runs_on_the_sync_thread(qapp, qtbot):
    import threading

    from PySide6.QtCore import QThread

    threads = []
    sync = ContentSync(client=None, store=ContentStore(Path(".")), bind=lambda: "facility",
                       run=lambda fn, ok, err: threads.append(QThread.currentThread()),
                       ipc=lambda: None, poll_ms=0)
    sync._started = True  # as after start(), without its first refresh
    ServiceRegistry.get_instance().register_instance(ContentSync, sync, replace=True)
    try:
        t = threading.Thread(target=actions.refresh_sync)
        t.start()
        t.join()
        qtbot.waitUntil(lambda: len(threads) == 1, timeout=2000)
        assert threads[0] is qapp.thread()
    finally:
        ServiceRegistry.get_instance().unregister(ContentSync)


def test_mainwindow_registers_its_content_sync(qapp, monkeypatch):
    from lightfall.ui.mainwindow import LFMainWindow

    monkeypatch.delenv("LIGHTFALL_SCOPES_DISABLED", raising=False)
    PreferencesManager.reset()  # a manager of our own: the sync follows its scopes_changed

    class Window:
        _prefs_manager = PreferencesManager.get_instance()

        def _on_content_synced(self, result):
            pass

    try:
        sync = LFMainWindow._build_content_sync(Window())
        assert isinstance(sync, ContentSync)
        assert ServiceRegistry.get_instance().get(ContentSync, None) is sync
    finally:
        ServiceRegistry.get_instance().unregister(ContentSync)
        PreferencesManager.reset()


# -- live scope state ----------------------------------------------------------------------

def test_scope_info_carries_the_content_policy(qapp):
    PreferencesManager.reset()
    try:
        manager = PreferencesManager.get_instance()
        seen = []
        manager.scopes_changed.connect(seen.append)
        roles = {"facility": "viewer", BRANCH: "maintainer"}
        manager._on_scoped_chain(ChainInfo(chain=CHAIN, roles=roles, entitled=(),
                                           facility_label="ALS",
                                           content_policy=ContentPolicy(10, 20, 3)))
        assert manager.scope_info.content_policy == ContentPolicy(10, 20, 3)
        assert len(seen) == 1
        # Same chain, same policy: no change. A new policy alone is a change.
        manager._on_scoped_chain(ChainInfo(chain=CHAIN, roles=roles, entitled=(),
                                           facility_label="ALS",
                                           content_policy=ContentPolicy(10, 20, 3)))
        assert len(seen) == 1
        manager._on_scoped_chain(ChainInfo(chain=CHAIN, roles=roles, entitled=(),
                                           facility_label="ALS"))
        assert len(seen) == 2 and seen[-1].content_policy == ContentPolicy()
        assert ScopeInfo("off").content_policy == ContentPolicy()
    finally:
        PreferencesManager.reset()


def test_current_scope_state(qapp, monkeypatch):
    PreferencesManager.reset()
    try:
        assert actions.current_scope_state() == ((), {}, ContentPolicy())
        manager = PreferencesManager.get_instance()
        roles = {"facility": "viewer", BRANCH: "maintainer"}
        manager._on_scoped_chain(ChainInfo(chain=CHAIN, roles=roles, entitled=(),
                                           facility_label="ALS",
                                           content_policy=ContentPolicy(10, 20, 3)))
        chain, got_roles, policy = actions.current_scope_state()
        assert chain == CHAIN and got_roles == roles and policy == ContentPolicy(10, 20, 3)
        got_roles["facility"] = "owner"  # a copy: callers can't change the manager's state
        assert manager.scope_info.roles["facility"] == "viewer"
        manager._set_scope_info(ScopeInfo("local", ("facility",)))
        assert actions.current_scope_state() == ((), {}, ContentPolicy())
    finally:
        PreferencesManager.reset()

    def boom():
        raise RuntimeError("no manager")

    monkeypatch.setattr(PreferencesManager, "get_instance", staticmethod(boom))
    assert actions.current_scope_state() == ((), {}, ContentPolicy())


# -- content rows --------------------------------------------------------------------------

@pytest.fixture
def home(tmp_path, monkeypatch):
    h = tmp_path / "home"
    h.mkdir()
    monkeypatch.setenv("USERPROFILE", str(h))
    monkeypatch.setenv("HOME", str(h))
    return h


@pytest.fixture
def clean(qapp, monkeypatch, tmp_path):
    for cls in (PlanRegistry, UserPlanService, UserPluginService, AgentSpecRegistry,
                ClashLog, ToolRegistry, GitTracker):
        cls.reset_instance()
    PanelRegistry.reset()
    monkeypatch.setattr("lightfall.plugins.types._user_plugin_roots",
                        lambda: [tmp_path.resolve()])

    class FakeTracker:
        def commit(self, paths, msg):
            return False

        def commit_removal(self, paths, msg):
            return False

        def head_commit(self, path):
            return None

    monkeypatch.setattr(GitTracker, "get_instance", staticmethod(lambda: FakeTracker()))
    yield
    for cls in (PlanRegistry, UserPlanService, UserPluginService, AgentSpecRegistry,
                ClashLog, ToolRegistry):
        cls.reset_instance()
    PanelRegistry.reset()
    for key in [k for k in sys.modules if k.startswith("lightfall_user_plugins.")]:
        del sys.modules[key]


def _install(store: ContentStore, scope: str, type_: str, name: str, rev: int,
             files: dict[str, str]) -> Path:
    folder = store.item_path(scope, type_, name, rev)
    for rel, text in files.items():
        p = folder / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
    listing = store.listing(scope) or ContentListing(scope=scope)
    item = ContentItem(type=type_, name=name, revision=rev, sha256="0" * 64, size=1)
    store.save_listing(ContentListing(scope=scope, items=listing.items + (item,)))
    return folder


PLAN = "def plan(detectors):\n    yield from []\n"


def _agent(name: str) -> str:
    return f"---\nname: {name}\ndescription: helps\n---\nBe helpful.\n"


def _skill(name: str) -> str:
    return f"---\nname: {name}\ndescription: a skill\n---\nDo it.\n"


def _row(rows, type_, name, source=None) -> ContentRow:
    found = [r for r in rows if r.type == type_ and r.name == name
             and (source is None or r.source == source)]
    assert len(found) == 1, (type_, name, source, rows)
    return found[0]


def test_content_rows_cover_all_types_with_sources(home, clean, tmp_path, monkeypatch):
    store = ContentStore(tmp_path / "content")
    mine = home / "lightfall"

    # plans: a branch plan beating a facility one, a local plan, and the core plans
    branch_plan = _install(store, BRANCH, "plan", "xafs", 3, {"xafs.py": PLAN}) / "xafs.py"
    fac_plan = _install(store, "facility", "plan", "xafs", 1, {"xafs.py": PLAN}) / "xafs.py"
    (mine / "plans").mkdir(parents=True)
    (mine / "plans" / "myscan.py").write_text(PLAN, encoding="utf-8")
    plans = UserPlanService.get_instance()
    plans.load_all_plans(synced=[SourceItem("plan", "xafs", BRANCH, branch_plan, 3),
                                 SourceItem("plan", "xafs", "facility", fac_plan, 1)])

    # plugins: a synced folder plugin and a broken local one
    tools = _install(store, BRANCH, "plugin", "tools", 7,
                     {"__init__.py": "X = 1\n", "sub/helper.py": "Y = 2\n"})
    (mine / "plugins").mkdir(parents=True)
    (mine / "plugins" / "broken.py").write_text("def (:\n", encoding="utf-8")
    plugins = UserPluginService.get_instance()
    plugins.load_all_plugins(synced=[SourceItem("plugin", "tools", BRANCH, tools, 7)])

    # agents: a local one shadowing a branch one, a branch-only one, and a broken file
    (mine / "agents").mkdir(parents=True)
    (mine / "agents" / "helper.md").write_text(_agent("helper"), encoding="utf-8")
    (mine / "agents" / "bad.md").write_text("no frontmatter", encoding="utf-8")
    helper = _install(store, BRANCH, "agent", "helper", 2, {"helper.md": _agent("helper")})
    scout = _install(store, BRANCH, "agent", "scout", 5, {"scout.md": _agent("scout")})
    agents = AgentSpecRegistry.get_instance()
    agents.register_scope_dir("user", mine / "agents")
    agents.set_synced([SourceItem("agent", "helper", BRANCH, helper / "helper.md", 2),
                       SourceItem("agent", "scout", BRANCH, scout / "scout.md", 5)])

    # skills: a synced one and a local one
    fit = _install(store, "beamline:7.0.1", "skill", "fitting", 9, {"SKILL.md": _skill("fitting")})
    (mine / "skills" / "notes").mkdir(parents=True)
    (mine / "skills" / "notes" / "SKILL.md").write_text(_skill("notes"), encoding="utf-8")
    from lightfall.agents import skills_store
    monkeypatch.setattr(skills_store, "default_synced_items", lambda type_: [
        SourceItem("skill", "fitting", "beamline:7.0.1", fit, 9)] if type_ == "skill" else [])

    rows = actions.content_rows(store=store)

    xafs = _row(rows, "plan", "xafs")
    assert (xafs.source, xafs.revision, xafs.path) == (BRANCH, 3, branch_plan)
    assert xafs.shadows == ("facility",) and xafs.error is None
    myscan = _row(rows, "plan", "myscan")
    assert (myscan.source, myscan.revision) == ("local", None)
    assert myscan.path == mine / "plans" / "myscan.py"
    count = _row(rows, "plan", "count")
    assert (count.source, count.path, count.revision) == ("core", None, None)

    tools_row = _row(rows, "plugin", "tools")
    assert (tools_row.source, tools_row.revision, tools_row.path) == (BRANCH, 7, tools)
    assert tools_row.error is None
    broken = _row(rows, "plugin", "broken")
    assert broken.source == "local" and broken.error and "Syntax error" in broken.error

    helper_row = _row(rows, "agent", "helper")
    assert (helper_row.source, helper_row.revision) == ("local", None)
    assert helper_row.shadows == (BRANCH,)
    scout_row = _row(rows, "agent", "scout")
    assert (scout_row.source, scout_row.revision, scout_row.path) == (BRANCH, 5,
                                                                      scout / "scout.md")
    bad = _row(rows, "agent", "bad")
    assert bad.source == "local" and bad.error and bad.path == mine / "agents" / "bad.md"

    fit_row = _row(rows, "skill", "fitting")
    assert (fit_row.source, fit_row.revision, fit_row.path) == ("beamline:7.0.1", 9, fit)
    notes = _row(rows, "skill", "notes")
    assert (notes.source, notes.path) == ("local", mine / "skills" / "notes")
    assert all(r.source != "builtin" and r.source != "user" for r in rows)

    order = [r.type for r in rows]
    assert order == sorted(order, key=("plan", "plugin", "agent", "skill").index)


def test_content_rows_without_running_services(home, clean, tmp_path):
    """Only what exists is read: no plan or plugin service is created just to list rows."""
    rows = actions.content_rows(store=ContentStore(tmp_path / "content"))
    assert UserPlanService._instance is None and UserPluginService._instance is None
    assert {r.type for r in rows} <= {"plan", "agent", "skill"}
    assert not (home / "lightfall" / "plans").exists()
