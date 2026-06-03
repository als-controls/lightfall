# SDK-Native Plugins Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace lightfall's homegrown skill system with the Claude Agent SDK's native plugin/skill mechanism, split the bundled MCP server into per-plugin servers, and unify `SkillPlugin` + `MCPToolPlugin` into one `AgentPlugin` type.

**Architecture:** A new `AgentPlugin` base class replaces both `SkillPlugin` and `MCPToolPlugin`. At session start, lightfall synthesizes a temp SDK plugin directory containing `SKILL.md` files materialized from each enabled plugin's `get_system_prompt()`, plus per-plugin in-process MCP servers (`mcp__<plugin.name>__*`). User plugins auto-register via `__init_subclass__` on `PluginType`, removing the `RegistrationTracker` patching dance.

**Tech Stack:** Python 3.10+, PySide6, `claude-agent-sdk>=0.1.30`, pytest + pytest-qt + pytest-asyncio.

**Spec:** `docs/superpowers/specs/2026-04-25-lightfall-sdk-native-plugins-design.md`

**Repo working dir:** `~/PycharmProjects/ncs/ncs/` (the inner `ncs` package). All paths in this plan are relative to that directory unless otherwise noted.

**Test command:** `pytest tests/ -v` from repo root. Specific tests: `pytest tests/path/test_x.py::test_name -v`.

---

## Phase 0: Branch setup

### Task 0.1: Create feature branch

**Files:** none

- [ ] **Step 1: Create + check out feature branch**

Run: `cd ~/PycharmProjects/ncs/ncs && git checkout -b feature/sdk-native-agents`
Expected: `Switched to a new branch 'feature/sdk-native-agents'`

- [ ] **Step 2: Verify clean working tree**

Run: `git status`
Expected: `nothing to commit, working tree clean` (apart from any pre-existing untracked files like `.venv-integration/`)

---

## Phase 1: Foundation — `AgentPlugin`, `AgentRegistry`, `__init_subclass__`

This phase adds the new types but doesn't migrate anything yet. Old `SkillPlugin` and `MCPToolPlugin` continue to work unchanged. After this phase, the codebase compiles, all existing tests pass, and the new types exist as additions.

### Task 1.1: Add `AgentPlugin` base class

**Files:**
- Create: `src/lightfall/plugins/agent_plugin.py`
- Test: `tests/plugins/test_agent_plugin.py`

- [ ] **Step 1: Write failing test for default contributions**

Create `tests/plugins/test_agent_plugin.py`:

```python
"""Tests for AgentPlugin base class."""
from __future__ import annotations

import pytest

from lightfall.plugins.agent_plugin import AgentPlugin


class _StubAgent(AgentPlugin):
    @property
    def name(self) -> str:
        return "stub"

    @property
    def description(self) -> str:
        return "Stub agent for tests"


def test_default_get_system_prompt_returns_empty():
    plugin = _StubAgent()
    assert plugin.get_system_prompt() == ""


def test_default_create_tools_returns_empty_list():
    plugin = _StubAgent()
    assert plugin.create_tools() == []


def test_default_get_references_dir_returns_none():
    plugin = _StubAgent()
    assert plugin.get_references_dir() is None


def test_default_display_name_titlecases_name():
    plugin = _StubAgent()
    assert plugin.display_name == "Stub"


def test_default_category_is_general():
    plugin = _StubAgent()
    assert plugin.category == "general"


def test_default_enabled_by_default_is_true():
    plugin = _StubAgent()
    assert plugin.enabled_by_default is True


def test_default_priority_is_100():
    plugin = _StubAgent()
    assert plugin.priority == 100


def test_type_name_is_agent():
    assert AgentPlugin.type_name == "agent"


def test_is_singleton():
    assert AgentPlugin.is_singleton is True


def test_name_is_abstract():
    """Cannot instantiate without overriding name + description."""
    class Incomplete(AgentPlugin):
        pass

    with pytest.raises(TypeError):
        Incomplete()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/plugins/test_agent_plugin.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'lightfall.plugins.agent_plugin'`

- [ ] **Step 3: Implement `AgentPlugin`**

Create `src/lightfall/plugins/agent_plugin.py`:

```python
"""Unified plugin type for plugins that extend the embedded Claude agent.

Replaces both SkillPlugin and MCPToolPlugin. One AgentPlugin contributes
an optional SKILL.md (via get_system_prompt) and/or an in-process MCP
server (via create_tools). One settings toggle controls both.
"""

from __future__ import annotations

from abc import abstractmethod
from pathlib import Path
from typing import Any, ClassVar

from lightfall.plugins.types import PluginType


class AgentPlugin(PluginType):
    """Extends the embedded Claude agent with an optional skill prompt and/or
    a bag of MCP tools.

    When enabled, contributes:

    - a SKILL.md (if get_system_prompt() returns non-empty text), materialized
      into the per-session SDK plugin dir at agent construction time;
    - an in-process MCP server (if create_tools() returns tools), registered
      as mcp_servers[plugin.name] with namespace mcp__<plugin.name>__*.

    See docs/superpowers/specs/2026-04-25-lightfall-sdk-native-plugins-design.md.
    """

    type_name: ClassVar[str] = "agent"
    is_singleton: ClassVar[bool] = True

    @property
    @abstractmethod
    def name(self) -> str:
        """Unique plugin identifier.

        ≤64 chars. Lowercase + hyphens/underscores. Used as:
        - the manifest entry name,
        - the SKILL.md frontmatter `name` field (with underscores → hyphens
          conversion at materialization, per spec Open question),
        - the MCP server name (mcp__<name>__*),
        - the settings UI preference identifier.
        """
        ...

    @property
    @abstractmethod
    def description(self) -> str:
        """One-line description shown in settings UI and in SKILL.md frontmatter.

        Truncated to 1024 chars at SKILL.md materialization time (SDK limit).
        """
        ...

    @property
    def display_name(self) -> str:
        """Human-readable name for the settings UI."""
        return self.name.replace("_", " ").title()

    @property
    def category(self) -> str:
        """Settings-UI grouping. Common values: general, devices, acquisition,
        operations, development."""
        return "general"

    @property
    def enabled_by_default(self) -> bool:
        return True

    @property
    def priority(self) -> int:
        """Sort order in settings UI (lower = first)."""
        return 100

    def get_system_prompt(self) -> str:
        """Return the SKILL.md body. Empty string = no skill contribution."""
        return ""

    def create_tools(self) -> list[Any]:
        """Return @tool-decorated callables. Empty = no MCP server contribution."""
        return []

    def get_references_dir(self) -> Path | None:
        """Optional package directory containing supplementary docs.

        If returned, files are copied to <session_plugin_dir>/skills/<name>/references/
        at session start, where the SDK Skill tool loads them lazily on demand.
        """
        return None

    def get_introspection_data(self) -> dict[str, Any]:
        return {
            "type": self.type_name,
            "name": self.name,
            "display_name": self.display_name,
            "description": self.description,
            "category": self.category,
            "enabled_by_default": self.enabled_by_default,
            "priority": self.priority,
            "has_prompt": bool(self.get_system_prompt().strip()),
            "has_tools": len(self.create_tools()) > 0,
            "class": self.__class__.__name__,
            "module": self.__class__.__module__,
        }
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/plugins/test_agent_plugin.py -v`
Expected: PASS — all 9 tests green.

- [ ] **Step 5: Commit**

```bash
git add src/lightfall/plugins/agent_plugin.py tests/plugins/test_agent_plugin.py
git commit -m "Add AgentPlugin base class"
```

### Task 1.2: Add `__init_subclass__` to `PluginType`

**Files:**
- Modify: `src/lightfall/plugins/types.py`
- Test: `tests/plugins/test_plugin_type_subclass.py`

- [ ] **Step 1: Inspect current `PluginType`**

Run: `head -80 src/lightfall/plugins/types.py`
Expected: `PluginType(ABC)` definition. Note line numbers.

- [ ] **Step 2: Write failing tests for `__init_subclass__` enqueue behavior**

Create `tests/plugins/test_plugin_type_subclass.py`:

```python
"""Tests for PluginType.__init_subclass__ user-plugin auto-enqueue."""
from __future__ import annotations

import sys
import textwrap
from pathlib import Path
from unittest.mock import MagicMock

import pytest


@pytest.fixture
def mock_user_service(monkeypatch):
    """Replace UserPluginService.get_instance() with a mock."""
    service = MagicMock()
    monkeypatch.setattr(
        "lightfall.plugins.user_plugins.UserPluginService.get_instance",
        lambda: service,
    )
    return service


@pytest.fixture
def fake_user_plugin_dir(tmp_path, monkeypatch):
    """Make tmp_path act as the canonical user plugin dir."""
    monkeypatch.setattr(
        "lightfall.plugins.types._user_plugin_roots",
        lambda: [tmp_path.resolve()],
    )
    return tmp_path


def _write_module(dir_: Path, name: str, body: str) -> Path:
    """Write a Python module to dir_ and import it cleanly."""
    path = dir_ / f"{name}.py"
    path.write_text(textwrap.dedent(body), encoding="utf-8")
    sys.path.insert(0, str(dir_))
    try:
        import importlib
        mod = importlib.import_module(name)
    finally:
        sys.path.pop(0)
    return path, mod


def test_subclass_in_user_dir_enqueues(mock_user_service, fake_user_plugin_dir):
    _, mod = _write_module(fake_user_plugin_dir, "user_one", """
        from lightfall.plugins.agent_plugin import AgentPlugin

        class UserAgent(AgentPlugin):
            @property
            def name(self): return "user_one"
            @property
            def description(self): return "user contributed"
    """)
    mock_user_service.enqueue.assert_called_once()
    cls_arg, _path_arg = mock_user_service.enqueue.call_args.args
    assert cls_arg is mod.UserAgent


def test_subclass_outside_user_dir_does_not_enqueue(mock_user_service, fake_user_plugin_dir, tmp_path):
    other_dir = tmp_path.parent / "outside"
    other_dir.mkdir(exist_ok=True)
    _write_module(other_dir, "outside_one", """
        from lightfall.plugins.agent_plugin import AgentPlugin

        class OutsideAgent(AgentPlugin):
            @property
            def name(self): return "outside_one"
            @property
            def description(self): return "should be ignored"
    """)
    mock_user_service.enqueue.assert_not_called()


def test_abstract_subclass_does_not_enqueue(mock_user_service, fake_user_plugin_dir):
    _write_module(fake_user_plugin_dir, "abstract_one", """
        from lightfall.plugins.agent_plugin import AgentPlugin

        class Abstract(AgentPlugin):
            pass
    """)
    mock_user_service.enqueue.assert_not_called()


def test_main_module_subclass_does_not_enqueue(mock_user_service, fake_user_plugin_dir):
    """Classes defined at REPL (__main__) are skipped."""
    from lightfall.plugins.agent_plugin import AgentPlugin

    # Simulate a class with __module__ == "__main__"
    DynamicClass = type("REPLAgent", (AgentPlugin,), {
        "__module__": "__main__",
        "name": property(lambda self: "repl"),
        "description": property(lambda self: "repl class"),
    })
    mock_user_service.enqueue.assert_not_called()
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `pytest tests/plugins/test_plugin_type_subclass.py -v`
Expected: FAIL with `AttributeError` (no `_user_plugin_roots`) or `enqueue.assert_*` mismatches.

- [ ] **Step 4: Add `__init_subclass__` and `_user_plugin_roots` helper to `types.py`**

Open `src/lightfall/plugins/types.py` and add at the top of the file (after existing imports):

```python
import inspect
from pathlib import Path


def _user_plugin_roots() -> list[Path]:
    """Canonical user plugin root directories.

    Used by PluginType.__init_subclass__ to decide whether a newly-defined
    subclass came from a user plugin file (and thus should auto-enqueue).
    """
    home = Path.home()
    roots: list[Path] = []
    for candidate in (home / "lightfall" / "plugins", home / ".lightfall" / "plugins"):
        try:
            roots.append(candidate.resolve())
        except (OSError, RuntimeError):
            pass
    return roots


def _is_under_user_plugin_dir(p: Path) -> bool:
    try:
        resolved = p.resolve()
    except (OSError, RuntimeError):
        return False
    for root in _user_plugin_roots():
        try:
            resolved.relative_to(root)
            return True
        except ValueError:
            continue
    return False
```

Inside the `PluginType` class body, add:

```python
    def __init_subclass__(cls, **kwargs):
        super().__init_subclass__(**kwargs)
        if cls.__module__ == "__main__" or inspect.isabstract(cls):
            return
        try:
            module_file = Path(inspect.getfile(cls))
        except (TypeError, OSError):
            return
        if not _is_under_user_plugin_dir(module_file):
            return
        try:
            from lightfall.plugins.user_plugins import UserPluginService
            UserPluginService.get_instance().enqueue(cls, module_file)
        except Exception:  # noqa: BLE001 — don't crash class definition on plumbing failure
            import logging
            logging.getLogger(__name__).exception("auto-enqueue failed for %s", cls)
```

- [ ] **Step 5: Add `enqueue` stub to `UserPluginService`** so existing tests don't break

Open `src/lightfall/plugins/user_plugins.py`. Find the `UserPluginService` class definition. Add this method (near the other public methods):

```python
    def enqueue(self, cls: type, file_path: Path) -> None:
        """Auto-register a PluginType subclass discovered via __init_subclass__.

        Called from PluginType.__init_subclass__ when a class is defined in a
        file under the user plugin dir. Real implementation lands in Phase 4
        (when RegistrationTracker is removed). For now this is a no-op so that
        the foundation phase doesn't break existing user-plugin loading.
        """
        # TODO(Phase 4): route through PluginLoader._register_plugin and track
        # for unload. Currently a no-op; existing user plugins still rely on
        # explicit Registry.register() calls in their bodies.
        pass
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `pytest tests/plugins/test_plugin_type_subclass.py -v`
Expected: PASS — all 4 tests.

- [ ] **Step 7: Run the full test suite to confirm no regressions**

Run: `pytest tests/ -q`
Expected: All previously-passing tests still pass.

- [ ] **Step 8: Commit**

```bash
git add src/lightfall/plugins/types.py src/lightfall/plugins/user_plugins.py tests/plugins/test_plugin_type_subclass.py
git commit -m "Add PluginType.__init_subclass__ for user-plugin auto-enqueue"
```

### Task 1.3: Add `AgentRegistry` singleton

**Files:**
- Create: `src/lightfall/ui/panels/claude/agent_registry.py`
- Test: `tests/ui/panels/claude/test_agent_registry.py`

- [ ] **Step 1: Write failing tests**

Create `tests/ui/panels/claude/test_agent_registry.py`:

```python
"""Tests for AgentRegistry."""
from __future__ import annotations

from typing import Any

import pytest

from lightfall.plugins.agent_plugin import AgentPlugin
from lightfall.ui.panels.claude.agent_registry import AgentRegistry


class _Pure_Prompt_Agent(AgentPlugin):
    @property
    def name(self): return "alpha"
    @property
    def description(self): return "alpha plugin"
    @property
    def priority(self): return 10
    def get_system_prompt(self): return "alpha prompt"


class _Pure_Tools_Agent(AgentPlugin):
    @property
    def name(self): return "beta"
    @property
    def description(self): return "beta plugin"
    @property
    def category(self): return "devices"
    @property
    def priority(self): return 50
    def create_tools(self): return [object()]


class _Disabled_By_Default_Agent(AgentPlugin):
    @property
    def name(self): return "gamma"
    @property
    def description(self): return "gamma plugin"
    @property
    def enabled_by_default(self): return False


@pytest.fixture(autouse=True)
def reset_registry():
    AgentRegistry.reset_instance()
    yield
    AgentRegistry.reset_instance()


def test_register_adds_plugin():
    reg = AgentRegistry.get_instance()
    a = _Pure_Prompt_Agent()
    reg.register(a)
    assert reg.get_plugins() == [a]


def test_duplicate_name_replaces():
    reg = AgentRegistry.get_instance()
    a1 = _Pure_Prompt_Agent()
    a2 = _Pure_Prompt_Agent()
    reg.register(a1)
    reg.register(a2)  # same name "alpha"
    plugins = reg.get_plugins()
    assert len(plugins) == 1
    assert plugins[0] is a2


def test_unregister_removes():
    reg = AgentRegistry.get_instance()
    a = _Pure_Prompt_Agent()
    reg.register(a)
    assert reg.unregister("alpha") is True
    assert reg.get_plugins() == []


def test_unregister_unknown_returns_false():
    reg = AgentRegistry.get_instance()
    assert reg.unregister("never_registered") is False


def test_enabled_plugins_no_pref_uses_defaults(monkeypatch):
    """When enabled_tool_plugins pref is None, enabled = those with enabled_by_default=True."""
    monkeypatch.setattr(
        "lightfall.ui.panels.claude.agent_registry.AgentRegistry._get_enabled_pref",
        lambda self: None,
    )
    reg = AgentRegistry.get_instance()
    a, b, g = _Pure_Prompt_Agent(), _Pure_Tools_Agent(), _Disabled_By_Default_Agent()
    reg.register(a); reg.register(b); reg.register(g)
    enabled = reg.enabled_plugins()
    names = {p.name for p in enabled}
    assert names == {"alpha", "beta"}
    assert "gamma" not in names


def test_enabled_plugins_respects_pref(monkeypatch):
    monkeypatch.setattr(
        "lightfall.ui.panels.claude.agent_registry.AgentRegistry._get_enabled_pref",
        lambda self: ["beta", "gamma"],
    )
    reg = AgentRegistry.get_instance()
    a, b, g = _Pure_Prompt_Agent(), _Pure_Tools_Agent(), _Disabled_By_Default_Agent()
    reg.register(a); reg.register(b); reg.register(g)
    enabled = reg.enabled_plugins()
    names = {p.name for p in enabled}
    assert names == {"beta", "gamma"}


def test_enabled_plugins_sorted_by_priority(monkeypatch):
    monkeypatch.setattr(
        "lightfall.ui.panels.claude.agent_registry.AgentRegistry._get_enabled_pref",
        lambda self: ["alpha", "beta"],
    )
    reg = AgentRegistry.get_instance()
    a, b = _Pure_Prompt_Agent(), _Pure_Tools_Agent()
    reg.register(b); reg.register(a)  # registered out of order
    names = [p.name for p in reg.enabled_plugins()]
    assert names == ["alpha", "beta"]  # alpha has priority 10, beta has 50


def test_introspection_data():
    reg = AgentRegistry.get_instance()
    reg.register(_Pure_Prompt_Agent())
    data = reg.get_introspection_data()
    assert data["plugin_count"] == 1
    assert len(data["plugins"]) == 1
    assert data["plugins"][0]["name"] == "alpha"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/ui/panels/claude/test_agent_registry.py -v`
Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Implement `AgentRegistry`**

Create `src/lightfall/ui/panels/claude/agent_registry.py`:

```python
"""Agent plugin registry — slimmed singleton replacing SkillRegistry + MCPToolRegistry.

Holds registered AgentPlugin instances. The settings UI reads from it for
the enable/disable table. The agent-construction path (claude/agent.py +
claude_panel.py) reads `enabled_plugins()` to materialize SKILL.md files
and assemble per-plugin MCP servers.

The preference key `enabled_tool_plugins` is retained from the previous
SkillRegistry/MCPToolRegistry world for backward-compat with existing user
settings — set semantics, plugin names unchanged.
"""

from __future__ import annotations

import threading
from typing import TYPE_CHECKING, Any

from lightfall.utils.logging import logger

if TYPE_CHECKING:
    from lightfall.plugins.agent_plugin import AgentPlugin


ENABLED_PLUGINS_PREF: str = "enabled_tool_plugins"


class AgentRegistry:
    """Singleton registry of AgentPlugin instances.

    Use AgentRegistry.get_instance() to access. reset_instance() is for tests.
    """

    _instance: "AgentRegistry | None" = None
    _lock = threading.Lock()

    def __init__(self) -> None:
        self._plugins: dict[str, AgentPlugin] = {}

    @classmethod
    def get_instance(cls) -> "AgentRegistry":
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    @classmethod
    def reset_instance(cls) -> None:
        with cls._lock:
            cls._instance = None

    def register(self, plugin: "AgentPlugin") -> None:
        """Register an AgentPlugin. Replaces any existing plugin with the same name."""
        if plugin.name in self._plugins:
            logger.warning("agent plugin '{}' already registered, replacing", plugin.name)
        self._plugins[plugin.name] = plugin
        logger.debug(
            "Registered agent plugin: {} (category={}, priority={})",
            plugin.name, plugin.category, plugin.priority,
        )

    def unregister(self, name: str) -> bool:
        """Unregister by name. Returns True if found + removed."""
        if name in self._plugins:
            del self._plugins[name]
            logger.debug("Unregistered agent plugin: {}", name)
            return True
        return False

    def get_plugins(self) -> list["AgentPlugin"]:
        """All registered plugins (any order)."""
        return list(self._plugins.values())

    def get_plugin(self, name: str) -> "AgentPlugin | None":
        return self._plugins.get(name)

    def _get_enabled_pref(self) -> list[str] | None:
        """Read the enabled_tool_plugins preference. Returns None if not set."""
        try:
            from lightfall.ui.preferences.manager import PreferencesManager
            prefs = PreferencesManager.get_instance()
            value = prefs.get(ENABLED_PLUGINS_PREF)
            if value is None or isinstance(value, list):
                return value
        except Exception as e:  # noqa: BLE001
            logger.debug("Could not load {}: {}", ENABLED_PLUGINS_PREF, e)
        return None

    def enabled_plugins(self) -> list["AgentPlugin"]:
        """Plugins enabled by current preferences, sorted by priority (ascending)."""
        pref = self._get_enabled_pref()
        if pref is None:
            enabled_names = {p.name for p in self._plugins.values() if p.enabled_by_default}
        else:
            enabled_names = set(pref) & set(self._plugins.keys())
        result = [p for name, p in self._plugins.items() if name in enabled_names]
        result.sort(key=lambda p: p.priority)
        return result

    @property
    def plugin_count(self) -> int:
        return len(self._plugins)

    def get_introspection_data(self) -> dict[str, Any]:
        return {
            "plugin_count": len(self._plugins),
            "plugins": [p.get_introspection_data() for p in self._plugins.values()],
        }
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/ui/panels/claude/test_agent_registry.py -v`
Expected: PASS — all 8 tests.

- [ ] **Step 5: Commit**

```bash
git add src/lightfall/ui/panels/claude/agent_registry.py tests/ui/panels/claude/test_agent_registry.py
git commit -m "Add AgentRegistry singleton (replaces SkillRegistry + MCPToolRegistry)"
```

### Task 1.4: Add loader branch for `type_name="agent"`

**Files:**
- Modify: `src/lightfall/plugins/loader.py`
- Test: `tests/plugins/test_loader_agent_branch.py`

- [ ] **Step 1: Read existing loader to find the registration dispatch site**

Run: `grep -n 'elif plugin_info.type_name' src/lightfall/plugins/loader.py`
Expected: lines like `elif plugin_info.type_name == "skill":`, `elif plugin_info.type_name == "mcp_tool":`, etc. Note the line range for context.

- [ ] **Step 2: Write failing test**

Create `tests/plugins/test_loader_agent_branch.py`:

```python
"""Tests for the `agent` plugin type loader branch."""
from __future__ import annotations

import pytest

from lightfall.plugins.agent_plugin import AgentPlugin
from lightfall.plugins.manifest import PluginEntry, PluginManifest
from lightfall.ui.panels.claude.agent_registry import AgentRegistry


class _SampleAgent(AgentPlugin):
    @property
    def name(self): return "sample_agent"
    @property
    def description(self): return "sample"


@pytest.fixture(autouse=True)
def reset_registry():
    AgentRegistry.reset_instance()
    yield
    AgentRegistry.reset_instance()


def test_agent_entry_registers_with_agent_registry(monkeypatch):
    """Manifest entry with type_name='agent' triggers AgentRegistry.register."""
    from lightfall.plugins.loader import PluginLoader

    manifest = PluginManifest(
        name="test_pkg",
        version="0.0.0",
        description="",
        plugins=[
            PluginEntry(
                type_name="agent",
                name="sample_agent",
                import_path=f"{__name__}:_SampleAgent",
            ),
        ],
    )

    loader = PluginLoader()
    loader.load_manifest(manifest)

    registered = AgentRegistry.get_instance().get_plugins()
    assert len(registered) == 1
    assert registered[0].name == "sample_agent"


def test_agent_entry_invalid_class_logs_error(caplog):
    """Class that's not an AgentPlugin subclass yields a load error, not a crash."""
    from lightfall.plugins.loader import PluginLoader

    class _NotAnAgent:  # plain object, not a PluginType
        def __init__(self): pass

    # Inject into module namespace so import_path resolves
    globals()["_NotAnAgent"] = _NotAnAgent

    manifest = PluginManifest(
        name="bad_pkg", version="0.0.0", description="",
        plugins=[
            PluginEntry(
                type_name="agent",
                name="bad",
                import_path=f"{__name__}:_NotAnAgent",
            ),
        ],
    )

    loader = PluginLoader()
    loader.load_manifest(manifest)

    # No registration on AgentRegistry
    assert AgentRegistry.get_instance().get_plugins() == []
```

- [ ] **Step 3: Run test to verify it fails**

Run: `pytest tests/plugins/test_loader_agent_branch.py -v`
Expected: FAIL — registration doesn't happen because the loader has no `agent` branch yet.

- [ ] **Step 4: Add the `agent` branch to `loader.py`**

Open `src/lightfall/plugins/loader.py`. Find the `_register_plugin` method's dispatch chain (the series of `elif plugin_info.type_name == ...` statements, around lines 600-740 based on earlier exploration). Add a new branch alongside the existing `skill` branch (or wherever sensible in the order):

```python
        elif plugin_info.type_name == "agent":
            try:
                from lightfall.ui.panels.claude.agent_registry import AgentRegistry
                from lightfall.plugins.agent_plugin import AgentPlugin

                instance = plugin_info.instance
                if not isinstance(instance, AgentPlugin):
                    logger.error(
                        "Agent plugin '{}' class {} is not an AgentPlugin subclass; skipping",
                        plugin_info.name, type(instance).__name__,
                    )
                else:
                    AgentRegistry.get_instance().register(instance)
                    logger.debug("Registered agent plugin '{}' with AgentRegistry", instance.name)
            except ImportError:
                logger.debug("AgentRegistry not available, skipping agent registration")
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest tests/plugins/test_loader_agent_branch.py -v`
Expected: PASS — both tests.

- [ ] **Step 6: Run full test suite for regressions**

Run: `pytest tests/ -q`
Expected: All previously-passing tests still pass.

- [ ] **Step 7: Commit**

```bash
git add src/lightfall/plugins/loader.py tests/plugins/test_loader_agent_branch.py
git commit -m "Add 'agent' plugin type loader branch"
```

---

## Phase 2: Migrate the 9 built-in plugins

After this phase, the `lightfall/plugins/agents/` package contains all 9 migrated plugins as `AgentPlugin` subclasses. The old `SkillPlugin` and `MCPToolPlugin` classes still exist but no built-in references them. `builtin_manifest.py` is updated to use `type_name="agent"` exclusively (and drops the `skill_docs` entry).

### Task 2.1: Create `lightfall/plugins/agents/` package

**Files:**
- Create: `src/lightfall/plugins/agents/__init__.py`
- Create: `src/lightfall/plugins/agents/_mcp_helpers.py` (moved from `tools/_mcp_helpers.py`)

- [ ] **Step 1: Create the package directory + init**

```bash
mkdir -p src/lightfall/plugins/agents
touch src/lightfall/plugins/agents/__init__.py
```

Write `src/lightfall/plugins/agents/__init__.py`:

```python
"""Built-in AgentPlugin classes shipped with lightfall.

Each plugin lives in its own module. References (markdown docs surfaced via
the SDK Skill tool's lazy loading) live in <name>/references/ alongside.

To add a new built-in agent:
1. Create lightfall/plugins/agents/<name>.py defining a class extending AgentPlugin.
2. Add a PluginEntry(type_name="agent", name="<name>",
   import_path="lightfall.plugins.agents.<name>:<ClassName>") to builtin_manifest.py.
"""
```

- [ ] **Step 2: Move `_mcp_helpers.py`**

```bash
git mv src/lightfall/plugins/tools/_mcp_helpers.py src/lightfall/plugins/agents/_mcp_helpers.py
```

Update any imports of `lightfall.plugins.tools._mcp_helpers` across the repo:

Run: `grep -rln 'from lightfall.plugins.tools._mcp_helpers' src/ tests/`

For each match, replace `from lightfall.plugins.tools._mcp_helpers` with `from lightfall.plugins.agents._mcp_helpers`.

- [ ] **Step 3: Run full test suite to confirm move didn't break anything**

Run: `pytest tests/ -q`
Expected: All previously-passing tests still pass.

- [ ] **Step 4: Commit**

```bash
git add src/lightfall/plugins/agents/ src/lightfall/plugins/tools/
git commit -m "Add lightfall/plugins/agents/ package; relocate _mcp_helpers"
```

### Task 2.2: Migrate `alignment` (pure-prompt)

**Files:**
- Create: `src/lightfall/plugins/agents/alignment.py`
- Delete (later): `src/lightfall/plugins/skills/alignment.py`
- Test: `tests/plugins/agents/test_alignment_parity.py`

- [ ] **Step 1: Read the existing skill source**

Run: `cat src/lightfall/plugins/skills/alignment.py`
Expected: full source of `BeamlineAlignmentSkill(SkillPlugin)`. Note the `get_system_prompt()` body as a fixture.

- [ ] **Step 2: Write parity test**

Create `tests/plugins/agents/__init__.py` (empty file).

Create `tests/plugins/agents/test_alignment_parity.py`:

```python
"""Snapshot/parity test: migrated alignment agent matches old skill content."""
from __future__ import annotations


def test_migrated_prompt_matches_legacy():
    """get_system_prompt() body must be byte-identical to the legacy skill's.

    This guards against accidental content drift during the file move.
    """
    from lightfall.plugins.agents.alignment import BeamlineAlignmentAgent
    from lightfall.plugins.skills.alignment import BeamlineAlignmentSkill

    new = BeamlineAlignmentAgent().get_system_prompt()
    old = BeamlineAlignmentSkill().get_system_prompt()
    assert new == old


def test_metadata_preserved():
    from lightfall.plugins.agents.alignment import BeamlineAlignmentAgent

    p = BeamlineAlignmentAgent()
    assert p.name == "alignment"
    assert p.display_name == "Beamline Alignment"
    assert p.category == "operations"
    assert p.enabled_by_default is True
    assert p.priority == 10
```

- [ ] **Step 3: Run test to verify it fails**

Run: `pytest tests/plugins/agents/test_alignment_parity.py -v`
Expected: FAIL — `ModuleNotFoundError` for new module.

- [ ] **Step 4: Migrate `alignment.py`**

Create `src/lightfall/plugins/agents/alignment.py` by copying the body of `src/lightfall/plugins/skills/alignment.py` and changing the import + base class. The `get_system_prompt()` body must be copied verbatim. Skeleton:

```python
"""Beamline alignment agent plugin."""

from __future__ import annotations

from lightfall.plugins.agent_plugin import AgentPlugin


class BeamlineAlignmentAgent(AgentPlugin):
    """Domain expertise for motor alignment and beam optimization tasks."""

    @property
    def name(self) -> str:
        return "alignment"

    @property
    def display_name(self) -> str:
        return "Beamline Alignment"

    @property
    def description(self) -> str:
        return "Expertise in motor alignment and beam optimization procedures"

    @property
    def category(self) -> str:
        return "operations"

    @property
    def enabled_by_default(self) -> bool:
        return True

    @property
    def priority(self) -> int:
        return 10

    def get_system_prompt(self) -> str:
        # COPY THE BODY FROM src/lightfall/plugins/skills/alignment.py:get_system_prompt
        # VERBATIM. Do not paraphrase.
        return """
## Beamline Alignment Expertise
... [full body from old skill] ...
"""
```

To copy the body precisely, run:

```bash
sed -n '/def get_system_prompt/,/^    [^ ]/p' src/lightfall/plugins/skills/alignment.py
```

…and paste the returned text into the new module's `get_system_prompt()`.

- [ ] **Step 5: Run parity tests to verify they pass**

Run: `pytest tests/plugins/agents/test_alignment_parity.py -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/lightfall/plugins/agents/alignment.py tests/plugins/agents/test_alignment_parity.py tests/plugins/agents/__init__.py
git commit -m "Migrate alignment skill to AgentPlugin"
```

### Task 2.3: Migrate `plan_design`, `scan_planning`, `panel_design` (pure-prompt)

These three pure-prompt skills migrate identically. The class-name mapping is:

| Old skill (in `lightfall/plugins/skills/`) | Old class | New class |
|---|---|---|
| `plan_design.py` | `PlanDesignSkill` | `PlanDesignAgent` |
| `scan_planning.py` | `ScanPlanningSkill` | `ScanPlanningAgent` |
| `panel_design.py` | `PanelDesignSkill` | `PanelDesignAgent` |

Repeat the following sequence **for each** of the three. `<n>` is the module name (e.g., `plan_design`); `<OldClass>` is the old class name (e.g., `PlanDesignSkill`); `<NewClass>` is the new class name (e.g., `PlanDesignAgent`).

**Files (per plugin):**
- Create: `src/lightfall/plugins/agents/<n>.py`
- Test: `tests/plugins/agents/test_<n>_parity.py`

- [ ] **Step 1: Read the existing skill source**

Run: `cat src/lightfall/plugins/skills/<n>.py`

Note the values of `name`, `display_name`, `description`, `category`, `enabled_by_default`, `priority` — you'll need them for the parity test. Note the `get_system_prompt()` body — it must be copied verbatim.

- [ ] **Step 2: Write the parity test**

Create `tests/plugins/agents/test_<n>_parity.py`:

```python
"""Parity test: migrated <n> agent matches old skill content."""
from __future__ import annotations


def test_migrated_prompt_matches_legacy():
    from lightfall.plugins.agents.<n> import <NewClass>
    from lightfall.plugins.skills.<n> import <OldClass>

    assert <NewClass>().get_system_prompt() == <OldClass>().get_system_prompt()


def test_metadata_preserved():
    from lightfall.plugins.agents.<n> import <NewClass>

    p = <NewClass>()
    assert p.name == "<n>"                    # exact match to old skill's name
    assert p.display_name == "<DisplayName>"  # from step 1
    assert p.category == "<category>"         # from step 1
    assert p.enabled_by_default is True       # confirm against step 1
    assert p.priority == <priority>           # from step 1
```

Substitute the angle-bracketed placeholders with the values noted in step 1.

- [ ] **Step 3: Run test to verify it fails**

Run: `pytest tests/plugins/agents/test_<n>_parity.py -v`
Expected: FAIL — `ModuleNotFoundError`.

- [ ] **Step 4: Migrate the module**

Create `src/lightfall/plugins/agents/<n>.py` modeled on `src/lightfall/plugins/agents/alignment.py` (Task 2.2). Specifically:

- Copy the full source of `src/lightfall/plugins/skills/<n>.py`.
- Replace `from lightfall.plugins.skill_plugin import SkillPlugin` with `from lightfall.plugins.agent_plugin import AgentPlugin`.
- Rename class `<OldClass>` → `<NewClass>`. Change base class to `AgentPlugin`.
- Copy `get_system_prompt()` body verbatim. To be safe:

  ```bash
  sed -n '/def get_system_prompt/,/^    [^ ]/p' src/lightfall/plugins/skills/<n>.py
  ```

  …and paste into the new module's `get_system_prompt()`.

- [ ] **Step 5: Run test to verify it passes**

Run: `pytest tests/plugins/agents/test_<n>_parity.py -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/lightfall/plugins/agents/<n>.py tests/plugins/agents/test_<n>_parity.py
git commit -m "Migrate <n> skill to AgentPlugin"
```

### Task 2.4: Migrate `panel_builder` (prompt + tools)

`panel_builder` is the only existing skill that contributes both prompt and tools. The migration carries both into one `AgentPlugin` class, plus moves docs to `references/`.

**Files:**
- Create: `src/lightfall/plugins/agents/panel_builder.py`
- Move (if exists): `src/lightfall/plugins/skills/docs/panel_builder.md` → `src/lightfall/plugins/agents/panel_builder/references/panel_builder.md`
- Test: `tests/plugins/agents/test_panel_builder_parity.py`

- [ ] **Step 1: Inspect existing skill source**

Run: `cat src/lightfall/plugins/skills/panel_builder.py`
Expected: full `PanelBuilderSkill(SkillPlugin)` with both `get_system_prompt()` and `create_tools()` returning 5 tools (`ncs_create_user_plugin`, `ncs_list_user_plugins`, `ncs_reload_plugin`, `ncs_unload_plugin`, `ncs_create_temp_plugin`).

- [ ] **Step 2: Write parity test**

Create `tests/plugins/agents/test_panel_builder_parity.py`:

```python
"""Parity test for the migrated panel_builder agent."""
from __future__ import annotations


def test_prompt_matches_legacy():
    from lightfall.plugins.agents.panel_builder import PanelBuilderAgent
    from lightfall.plugins.skills.panel_builder import PanelBuilderSkill

    assert PanelBuilderAgent().get_system_prompt() == PanelBuilderSkill().get_system_prompt()


def test_tool_names_match_legacy():
    """The 5 tools have the same names as before."""
    from lightfall.plugins.agents.panel_builder import PanelBuilderAgent
    from lightfall.plugins.skills.panel_builder import PanelBuilderSkill

    new_tools = PanelBuilderAgent().create_tools()
    old_tools = PanelBuilderSkill().create_tools()
    new_names = sorted(getattr(t, "name", None) or t.__name__ for t in new_tools)
    old_names = sorted(getattr(t, "name", None) or t.__name__ for t in old_tools)
    assert new_names == old_names
    assert len(new_tools) == 5


def test_metadata_preserved():
    from lightfall.plugins.agents.panel_builder import PanelBuilderAgent

    p = PanelBuilderAgent()
    assert p.name == "panel_builder"
    assert p.display_name == "Panel Builder"
    assert p.category == "development"
    assert p.enabled_by_default is True
    assert p.priority == 25
```

- [ ] **Step 3: Run test to verify it fails**

Run: `pytest tests/plugins/agents/test_panel_builder_parity.py -v`
Expected: FAIL — module missing.

- [ ] **Step 4: Migrate the module**

Create `src/lightfall/plugins/agents/panel_builder.py`. Copy the body of `src/lightfall/plugins/skills/panel_builder.py`. Changes:

- Replace `from lightfall.plugins.skill_plugin import SkillPlugin` with `from lightfall.plugins.agent_plugin import AgentPlugin`.
- Rename class `PanelBuilderSkill` → `PanelBuilderAgent`. Change base class.
- Override `get_references_dir()` if a docs file is being relocated (see step 5):

```python
    from pathlib import Path

    def get_references_dir(self) -> Path | None:
        return Path(__file__).parent / "panel_builder" / "references"
```

- All five `@tool`-decorated functions inside `create_tools()`: copy verbatim.
- Internal helper methods (`_validate_plugin_code`, etc.): copy verbatim.

- [ ] **Step 5: Move references doc if it exists**

Run: `ls src/lightfall/plugins/skills/docs/`
If `panel_builder.md` exists:

```bash
mkdir -p src/lightfall/plugins/agents/panel_builder/references
git mv src/lightfall/plugins/skills/docs/panel_builder.md src/lightfall/plugins/agents/panel_builder/references/panel_builder.md
```

- [ ] **Step 6: Run parity tests**

Run: `pytest tests/plugins/agents/test_panel_builder_parity.py -v`
Expected: PASS — all 3 tests.

- [ ] **Step 7: Commit**

```bash
git add src/lightfall/plugins/agents/panel_builder.py src/lightfall/plugins/agents/panel_builder/ tests/plugins/agents/test_panel_builder_parity.py src/lightfall/plugins/skills/docs/
git commit -m "Migrate panel_builder skill (prompt + tools) to AgentPlugin"
```

### Task 2.5: Migrate `device_tools` (pure-tools)

**Files:**
- Create: `src/lightfall/plugins/agents/device_tools.py`
- Test: `tests/plugins/agents/test_device_tools_parity.py`

- [ ] **Step 1: Inspect**

Run: `cat src/lightfall/plugins/tools/device_tools.py`
Expected: `DeviceToolPlugin(MCPToolPlugin)` with 9 `@tool` definitions inside `create_tools()`.

- [ ] **Step 2: Write parity test**

Create `tests/plugins/agents/test_device_tools_parity.py`:

```python
def test_tool_names_match_legacy():
    from lightfall.plugins.agents.device_tools import DeviceToolsAgent
    from lightfall.plugins.tools.device_tools import DeviceToolPlugin

    new_names = sorted(getattr(t, "name", None) or t.__name__ for t in DeviceToolsAgent().create_tools())
    old_names = sorted(getattr(t, "name", None) or t.__name__ for t in DeviceToolPlugin().create_tools())
    assert new_names == old_names
    assert len(new_names) == 9


def test_metadata_preserved():
    from lightfall.plugins.agents.device_tools import DeviceToolsAgent
    p = DeviceToolsAgent()
    assert p.name == "device_tools"
    assert p.category == "devices"


def test_get_system_prompt_empty():
    """Pure-tools plugin contributes no skill prompt."""
    from lightfall.plugins.agents.device_tools import DeviceToolsAgent
    assert DeviceToolsAgent().get_system_prompt() == ""
```

- [ ] **Step 3: Run test, see fail, migrate, see pass.**

Migrate the module: `src/lightfall/plugins/agents/device_tools.py` is the body of `src/lightfall/plugins/tools/device_tools.py` with:
- `from lightfall.plugins.mcp_tool import MCPToolPlugin` → `from lightfall.plugins.agent_plugin import AgentPlugin`
- `class DeviceToolPlugin(MCPToolPlugin)` → `class DeviceToolsAgent(AgentPlugin)`
- All 9 `@tool` definitions: verbatim.
- Helper methods (`_get_catalog`, `_check_device_control_permission`, etc.): verbatim.

Run: `pytest tests/plugins/agents/test_device_tools_parity.py -v`
Expected: PASS.

- [ ] **Step 4: Commit**

```bash
git add src/lightfall/plugins/agents/device_tools.py tests/plugins/agents/test_device_tools_parity.py
git commit -m "Migrate device_tools plugin to AgentPlugin"
```

### Task 2.6: Migrate `plan_tools`, `engine_tools`, `ipython_tools` (pure-tools)

Three pure-tools plugins migrate identically. Class-name mapping:

| Old plugin (in `lightfall/plugins/tools/`) | Old class | New class |
|---|---|---|
| `plan_tools.py` | `PlanToolPlugin` | `PlanToolsAgent` |
| `engine_tools.py` | `EngineToolPlugin` | `EngineToolsAgent` |
| `ipython_tools.py` | `IPythonToolPlugin` | `IPythonToolsAgent` |

Repeat the sequence **for each**. `<n>` = module name; `<OldClass>` = old class name; `<NewClass>` = new class name; `<N>` = the expected tool count (count `@tool` decorators in the old file via `grep -c "@tool" src/lightfall/plugins/tools/<n>.py`).

**Files (per plugin):**
- Create: `src/lightfall/plugins/agents/<n>.py`
- Test: `tests/plugins/agents/test_<n>_parity.py`

- [ ] **Step 1: Inspect**

Run: `cat src/lightfall/plugins/tools/<n>.py` and `grep -c "@tool" src/lightfall/plugins/tools/<n>.py`. Note `<OldClass>`, `category`, and `<N>` (tool count).

- [ ] **Step 2: Write the parity test**

Create `tests/plugins/agents/test_<n>_parity.py`:

```python
"""Parity test: migrated <n> agent matches old plugin's tools."""
from __future__ import annotations


def test_tool_names_match_legacy():
    from lightfall.plugins.agents.<n> import <NewClass>
    from lightfall.plugins.tools.<n> import <OldClass>

    new_names = sorted(getattr(t, "name", None) or t.__name__ for t in <NewClass>().create_tools())
    old_names = sorted(getattr(t, "name", None) or t.__name__ for t in <OldClass>().create_tools())
    assert new_names == old_names
    assert len(new_names) == <N>


def test_metadata_preserved():
    from lightfall.plugins.agents.<n> import <NewClass>

    p = <NewClass>()
    assert p.name == "<n>"
    assert p.category == "<category>"


def test_get_system_prompt_empty():
    """Pure-tools plugin contributes no skill prompt."""
    from lightfall.plugins.agents.<n> import <NewClass>
    assert <NewClass>().get_system_prompt() == ""
```

- [ ] **Step 3: Run test to verify it fails**

Run: `pytest tests/plugins/agents/test_<n>_parity.py -v`
Expected: FAIL — `ModuleNotFoundError`.

- [ ] **Step 4: Migrate the module**

Create `src/lightfall/plugins/agents/<n>.py` modeled on `src/lightfall/plugins/agents/device_tools.py` (Task 2.5). Specifically:

- Copy the full source of `src/lightfall/plugins/tools/<n>.py`.
- Replace `from lightfall.plugins.mcp_tool import MCPToolPlugin` with `from lightfall.plugins.agent_plugin import AgentPlugin`.
- Replace `from lightfall.plugins.tools._mcp_helpers import mcp_result` with `from lightfall.plugins.agents._mcp_helpers import mcp_result`.
- Rename class `<OldClass>` → `<NewClass>`. Change base class to `AgentPlugin`.
- All `@tool` definitions inside `create_tools()` and any helper methods (e.g., `_get_engine`, `_check_permission`, etc.): copy verbatim.

- [ ] **Step 5: Run test to verify it passes**

Run: `pytest tests/plugins/agents/test_<n>_parity.py -v`
Expected: PASS — all 3 tests.

- [ ] **Step 6: Commit**

```bash
git add src/lightfall/plugins/agents/<n>.py tests/plugins/agents/test_<n>_parity.py
git commit -m "Migrate <n> plugin to AgentPlugin"
```

### Task 2.7: Update `builtin_manifest.py`

After this task, the manifest declares the 9 new agent plugins via `type_name="agent"` and removes the `skill_docs` entry entirely.

**Files:**
- Modify: `src/lightfall/plugins/builtin_manifest.py`
- Test: `tests/plugins/test_builtin_manifest_agents.py`

- [ ] **Step 1: Write a verification test**

Create `tests/plugins/test_builtin_manifest_agents.py`:

```python
"""Verify the builtin manifest exposes 9 agent entries (no skill/mcp_tool entries)."""
from __future__ import annotations


def test_manifest_has_9_agent_entries_and_no_skill_or_mcp_tool_entries():
    from lightfall.plugins.builtin_manifest import builtin_manifest

    type_counts: dict[str, int] = {}
    for entry in builtin_manifest.plugins:
        type_counts.setdefault(entry.type_name, 0)
        type_counts[entry.type_name] += 1

    assert type_counts.get("agent") == 9
    assert type_counts.get("skill", 0) == 0
    assert type_counts.get("mcp_tool", 0) == 0


def test_manifest_lists_expected_agent_names():
    from lightfall.plugins.builtin_manifest import builtin_manifest

    agent_names = {e.name for e in builtin_manifest.plugins if e.type_name == "agent"}
    assert agent_names == {
        "alignment", "plan_design", "scan_planning", "panel_design", "panel_builder",
        "device_tools", "plan_tools", "engine_tools", "ipython_tools",
    }
    # skill_docs is GONE
    assert "skill_docs" not in agent_names


def test_manifest_agent_import_paths_resolve():
    """Each agent's import_path must be importable and yield an AgentPlugin subclass."""
    import importlib

    from lightfall.plugins.agent_plugin import AgentPlugin
    from lightfall.plugins.builtin_manifest import builtin_manifest

    for entry in builtin_manifest.plugins:
        if entry.type_name != "agent":
            continue
        module_path, class_name = entry.import_path.split(":")
        mod = importlib.import_module(module_path)
        cls = getattr(mod, class_name)
        assert issubclass(cls, AgentPlugin), f"{entry.import_path} is not an AgentPlugin"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/plugins/test_builtin_manifest_agents.py -v`
Expected: FAIL — manifest still has `skill` / `mcp_tool` entries.

- [ ] **Step 3: Edit `builtin_manifest.py`**

Open `src/lightfall/plugins/builtin_manifest.py`. Replace the existing `mcp_tool` and `skill` blocks (lines roughly 158-211 based on earlier exploration) with a single agent block:

```python
        # Agent plugins (skill prompts and/or MCP tool bags).
        # Each contributes via AgentRegistry; per-plugin MCP servers are
        # assembled at agent-construction time in lightfall/claude/agent.py.
        PluginEntry(
            type_name="agent",
            name="alignment",
            import_path="lightfall.plugins.agents.alignment:BeamlineAlignmentAgent",
        ),
        PluginEntry(
            type_name="agent",
            name="plan_design",
            import_path="lightfall.plugins.agents.plan_design:PlanDesignAgent",
        ),
        PluginEntry(
            type_name="agent",
            name="scan_planning",
            import_path="lightfall.plugins.agents.scan_planning:ScanPlanningAgent",
        ),
        PluginEntry(
            type_name="agent",
            name="panel_design",
            import_path="lightfall.plugins.agents.panel_design:PanelDesignAgent",
        ),
        PluginEntry(
            type_name="agent",
            name="panel_builder",
            import_path="lightfall.plugins.agents.panel_builder:PanelBuilderAgent",
        ),
        PluginEntry(
            type_name="agent",
            name="device_tools",
            import_path="lightfall.plugins.agents.device_tools:DeviceToolsAgent",
        ),
        PluginEntry(
            type_name="agent",
            name="plan_tools",
            import_path="lightfall.plugins.agents.plan_tools:PlanToolsAgent",
        ),
        PluginEntry(
            type_name="agent",
            name="engine_tools",
            import_path="lightfall.plugins.agents.engine_tools:EngineToolsAgent",
        ),
        PluginEntry(
            type_name="agent",
            name="ipython_tools",
            import_path="lightfall.plugins.agents.ipython_tools:IPythonToolsAgent",
        ),
```

The `skill_docs` entry is **deleted** entirely (no replacement). Other plugin types (theme, settings, panel, statusbar, controller, engine) are untouched.

- [ ] **Step 4: Run all tests**

Run: `pytest tests/ -q`
Expected: All passing. The new manifest test passes; other tests still pass because `SkillPlugin` and `MCPToolPlugin` still exist (just unused).

- [ ] **Step 5: Commit**

```bash
git add src/lightfall/plugins/builtin_manifest.py tests/plugins/test_builtin_manifest_agents.py
git commit -m "Update builtin_manifest to use type_name='agent'; drop skill_docs"
```

---

## Phase 3: Wire session-time SDK assembly

After this phase, the embedded Claude agent uses SDK-native skills (via `plugins=[…]`) and per-plugin MCP servers. The `additional_system_prompt` skill aggregation block is gone. Old base classes, registries, and `skill_docs_tool.py` are deleted.

### Task 3.1: Add `_materialize_skill` helper

**Files:**
- Create: `src/lightfall/claude/_session_assembly.py`
- Test: `tests/claude/test_session_assembly.py`

- [ ] **Step 1: Write failing tests**

Create `tests/claude/__init__.py` if missing.

Create `tests/claude/test_session_assembly.py`:

```python
"""Tests for session-time SDK plugin-dir + MCP server assembly."""
from __future__ import annotations

from pathlib import Path

import pytest

from lightfall.plugins.agent_plugin import AgentPlugin


class _PromptOnly(AgentPlugin):
    @property
    def name(self): return "prompt_only"
    @property
    def description(self): return "prompt only plugin"
    def get_system_prompt(self): return "## Prompt body\n\nText here."


class _ToolsOnly(AgentPlugin):
    @property
    def name(self): return "tools_only"
    @property
    def description(self): return "tools only plugin"
    def create_tools(self): return [object()]


class _Both(AgentPlugin):
    @property
    def name(self): return "both"
    @property
    def description(self): return "x" * 1500
    def get_system_prompt(self): return "Both prompt"
    def create_tools(self): return [object()]


class _WithRefs(AgentPlugin):
    def __init__(self, refs_dir):
        self._refs = refs_dir
    @property
    def name(self): return "with_refs"
    @property
    def description(self): return "with refs"
    def get_system_prompt(self): return "with refs body"
    def get_references_dir(self): return self._refs


def test_prompt_only_plugin_writes_skill_md(tmp_path):
    from lightfall.claude._session_assembly import materialize_skill, init_session_plugin_dir

    plugin_dir = init_session_plugin_dir(tmp_path / "session")
    materialize_skill(_PromptOnly(), plugin_dir)

    skill_md = plugin_dir / "skills" / "prompt_only" / "SKILL.md"
    assert skill_md.exists()
    content = skill_md.read_text(encoding="utf-8")
    assert content.startswith("---\n")
    assert "name: prompt_only" in content
    assert "description: prompt only plugin" in content
    assert "## Prompt body" in content


def test_tools_only_plugin_does_not_write_skill_md(tmp_path):
    from lightfall.claude._session_assembly import materialize_skill, init_session_plugin_dir

    plugin_dir = init_session_plugin_dir(tmp_path / "session")
    materialize_skill(_ToolsOnly(), plugin_dir)

    assert not (plugin_dir / "skills" / "tools_only").exists()


def test_long_description_truncates_with_warning(tmp_path, caplog):
    from lightfall.claude._session_assembly import materialize_skill, init_session_plugin_dir

    plugin_dir = init_session_plugin_dir(tmp_path / "session")
    materialize_skill(_Both(), plugin_dir)

    skill_md = (plugin_dir / "skills" / "both" / "SKILL.md").read_text()
    desc_line = next(line for line in skill_md.splitlines() if line.startswith("description:"))
    # 1024-char limit; "description: " prefix = 13 chars; total = 13 + 1024 = 1037
    assert len(desc_line) <= 13 + 1024


def test_references_dir_copied(tmp_path):
    from lightfall.claude._session_assembly import materialize_skill, init_session_plugin_dir

    src_refs = tmp_path / "src_refs"
    src_refs.mkdir()
    (src_refs / "guide.md").write_text("# Guide", encoding="utf-8")

    plugin_dir = init_session_plugin_dir(tmp_path / "session")
    materialize_skill(_WithRefs(src_refs), plugin_dir)

    copied = plugin_dir / "skills" / "with_refs" / "references" / "guide.md"
    assert copied.exists()
    assert copied.read_text() == "# Guide"


def test_init_session_plugin_dir_writes_plugin_json(tmp_path):
    from lightfall.claude._session_assembly import init_session_plugin_dir

    plugin_dir = init_session_plugin_dir(tmp_path / "session")
    plugin_json = plugin_dir / ".claude-plugin" / "plugin.json"
    assert plugin_json.exists()
    import json
    data = json.loads(plugin_json.read_text())
    assert data["name"] == "lightfall-session"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/claude/test_session_assembly.py -v`
Expected: FAIL — module missing.

- [ ] **Step 3: Implement `_session_assembly.py`**

Create `src/lightfall/claude/_session_assembly.py`:

```python
"""Session-time assembly of the SDK plugin directory + per-plugin MCP servers.

Called by lightfall.claude.agent (QtClaudeAgent.__init__) at agent-construction
time. Translates AgentRegistry's enabled plugins into the inputs needed for
ClaudeAgentOptions.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import TYPE_CHECKING, Any

from lightfall.utils.logging import logger

if TYPE_CHECKING:
    from lightfall.plugins.agent_plugin import AgentPlugin


# Per-SDK constraints
_SKILL_DESCRIPTION_LIMIT = 1024
_SKILL_NAME_LIMIT = 64


def init_session_plugin_dir(path: Path) -> Path:
    """Create a fresh SDK plugin dir at `path` with a minimal plugin.json.

    Returns `path` for convenience.
    """
    path.mkdir(parents=True, exist_ok=True)
    claude_plugin = path / ".claude-plugin"
    claude_plugin.mkdir(exist_ok=True)
    (claude_plugin / "plugin.json").write_text(
        json.dumps({"name": "lightfall-session", "version": "0.0.0"}),
        encoding="utf-8",
    )
    (path / "skills").mkdir(exist_ok=True)
    return path


def materialize_skill(plugin: "AgentPlugin", plugin_dir: Path) -> None:
    """Write `<plugin_dir>/skills/<name>/SKILL.md` for `plugin`, if it has a prompt.

    No-op for plugins where `get_system_prompt()` is empty/whitespace.
    Truncates description to the SDK's 1024-char limit; logs a warning if so.
    Converts `_` → `-` in the SKILL.md frontmatter `name` (per spec Open Q).
    Copies `get_references_dir()` (if set) to `references/` alongside.
    """
    body = plugin.get_system_prompt().strip()
    if not body:
        return

    skill_dir = plugin_dir / "skills" / plugin.name
    skill_dir.mkdir(parents=True, exist_ok=True)

    skill_name = plugin.name.replace("_", "-")
    if len(skill_name) > _SKILL_NAME_LIMIT:
        logger.warning(
            "agent '{}': SKILL.md frontmatter name truncated from {} to {} chars",
            plugin.name, len(skill_name), _SKILL_NAME_LIMIT,
        )
        skill_name = skill_name[:_SKILL_NAME_LIMIT]

    description = plugin.description
    if len(description) > _SKILL_DESCRIPTION_LIMIT:
        logger.warning(
            "agent '{}': SKILL.md description truncated from {} to {} chars",
            plugin.name, len(description), _SKILL_DESCRIPTION_LIMIT,
        )
        description = description[:_SKILL_DESCRIPTION_LIMIT]

    frontmatter = (
        f"---\n"
        f"name: {skill_name}\n"
        f"description: {description}\n"
        f"---\n\n"
    )
    (skill_dir / "SKILL.md").write_text(frontmatter + body, encoding="utf-8")

    refs = plugin.get_references_dir()
    if refs is not None:
        target = skill_dir / "references"
        if target.exists():
            shutil.rmtree(target)
        shutil.copytree(refs, target)


def assemble_mcp_servers(
    enabled_plugins: list["AgentPlugin"],
) -> tuple[dict[str, Any], list[str]]:
    """Build (mcp_servers, allowed_tools) from the enabled AgentPlugins.

    The returned dict has one server per plugin that has tools, keyed by
    plugin.name. Server names follow the SDK convention: tools become
    `mcp__<plugin.name>__<tool_name>` in `allowed_tools`.

    Caller is expected to merge this with the always-on `qt` server and
    its allowed_tools entries.
    """
    from claude_agent_sdk import create_sdk_mcp_server

    mcp_servers: dict[str, Any] = {}
    allowed_tools: list[str] = []

    for plugin in enabled_plugins:
        tools = plugin.create_tools()
        if not tools:
            continue
        server = create_sdk_mcp_server(
            name=plugin.name, version="1.0.0", tools=tools,
        )
        mcp_servers[plugin.name] = server
        for tool in tools:
            tool_name = getattr(tool, "name", None) or getattr(tool, "__name__", None)
            if tool_name is None:
                logger.warning(
                    "agent '{}': tool object {!r} has no name; skipping allowed_tools entry",
                    plugin.name, tool,
                )
                continue
            allowed_tools.append(f"mcp__{plugin.name}__{tool_name}")

    return mcp_servers, allowed_tools
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/claude/test_session_assembly.py -v`
Expected: PASS — all 5 tests.

- [ ] **Step 5: Commit**

```bash
git add src/lightfall/claude/_session_assembly.py tests/claude/test_session_assembly.py
git commit -m "Add _session_assembly helpers (materialize_skill, assemble_mcp_servers)"
```

### Task 3.2: Wire `QtClaudeAgent.__init__` to use new assembly

**Files:**
- Modify: `src/lightfall/claude/agent.py`
- Test: `tests/claude/test_agent_session_wiring.py`

- [ ] **Step 1: Re-read the relevant section of `agent.py`**

Run: `sed -n '230,330p' src/lightfall/claude/agent.py`
Expected: shows the section that builds `mcp_servers`, `allowed_tools`, and `ClaudeAgentOptions`.

- [ ] **Step 2: Write integration test**

Create `tests/claude/test_agent_session_wiring.py`:

```python
"""Lightweight test that QtClaudeAgent constructs ClaudeAgentOptions correctly.

We don't actually connect to the SDK — we patch ClaudeSDKClient and inspect
the options dict that QtClaudeAgent built.
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from lightfall.plugins.agent_plugin import AgentPlugin
from lightfall.ui.panels.claude.agent_registry import AgentRegistry


class _PromptAgent(AgentPlugin):
    @property
    def name(self): return "prompt_agent"
    @property
    def description(self): return "prompt agent for tests"
    def get_system_prompt(self): return "## Prompt body"


class _ToolAgent(AgentPlugin):
    @property
    def name(self): return "tool_agent"
    @property
    def description(self): return "tool agent for tests"
    def create_tools(self):
        # Stub: claude_agent_sdk.tool decorator produces a tool object with .name attr
        from claude_agent_sdk import tool

        @tool(name="my_tool", description="x", input_schema={"type": "object", "properties": {}})
        async def t(args): return {"content": [{"type": "text", "text": "ok"}]}
        return [t]


@pytest.fixture(autouse=True)
def reset_registry():
    AgentRegistry.reset_instance()
    yield
    AgentRegistry.reset_instance()


@pytest.fixture
def mock_sdk(monkeypatch):
    """Replace ClaudeSDKClient with a MagicMock so __init__ doesn't connect."""
    monkeypatch.setattr("lightfall.claude.agent.ClaudeSDKClient", MagicMock())


def test_qtclaudeagent_uses_per_plugin_servers_and_plugins_param(mock_sdk, qtbot, monkeypatch):
    AgentRegistry.get_instance().register(_PromptAgent())
    AgentRegistry.get_instance().register(_ToolAgent())
    monkeypatch.setattr(
        "lightfall.ui.panels.claude.agent_registry.AgentRegistry._get_enabled_pref",
        lambda self: ["prompt_agent", "tool_agent"],
    )

    from PySide6.QtWidgets import QWidget

    from lightfall.claude.agent import QtClaudeAgent

    target = QWidget()
    qtbot.addWidget(target)
    agent = QtClaudeAgent(target_window=target, require_approval=False)

    options = agent.options
    # qt server is always present
    assert "qt" in options.mcp_servers
    # tool_agent gets its own server (per-plugin split)
    assert "tool_agent" in options.mcp_servers
    # prompt_agent has no tools so no server
    assert "prompt_agent" not in options.mcp_servers
    # No "additional" mega-bag anymore
    assert "additional" not in options.mcp_servers
    # plugins= is set with the synthesized session plugin dir
    assert isinstance(options.plugins, list)
    assert len(options.plugins) == 1
    plugin_path = options.plugins[0]["path"]
    from pathlib import Path
    assert (Path(plugin_path) / "skills" / "prompt_agent" / "SKILL.md").exists()
    # No skill content baked into system_prompt
    assert "## Prompt body" not in options.system_prompt
    # allowed_tools includes per-plugin namespace
    assert any(t.startswith("mcp__tool_agent__") for t in options.allowed_tools)
```

- [ ] **Step 3: Run test to verify it fails**

Run: `pytest tests/claude/test_agent_session_wiring.py -v`
Expected: FAIL — current code paths use the `additional` server and inject skills via `additional_system_prompt`.

- [ ] **Step 4: Modify `QtClaudeAgent.__init__`**

Open `src/lightfall/claude/agent.py`. Locate the section building `mcp_servers` (around lines 235-280 from earlier exploration). Replace:

```python
        # OLD: bundle additional_tools into one "additional" server
        self._additional_tools = additional_tools or []
        mcp_servers = {"qt": self.qt_tools}

        if self._additional_tools:
            from claude_agent_sdk import create_sdk_mcp_server
            additional_server = create_sdk_mcp_server(
                name="additional",
                version="1.0.0",
                tools=self._additional_tools,
            )
            mcp_servers["additional"] = additional_server
            for tool_func in self._additional_tools:
                ...
                allowed_tools.append(f"mcp__additional__{tool_name}")
```

with:

```python
        # NEW: per-plugin MCP servers + plugins= for skills
        self._additional_tools = additional_tools or []  # legacy callers
        mcp_servers: dict[str, Any] = {"qt": self.qt_tools}

        # Per-plugin server assembly from AgentRegistry
        from lightfall.claude._session_assembly import (
            assemble_mcp_servers,
            init_session_plugin_dir,
            materialize_skill,
        )
        from lightfall.ui.panels.claude.agent_registry import AgentRegistry

        enabled = AgentRegistry.get_instance().enabled_plugins()
        agent_servers, agent_allowed = assemble_mcp_servers(enabled)
        mcp_servers.update(agent_servers)
        allowed_tools.extend(agent_allowed)

        # Synthesize per-session SDK plugin dir
        self._session_plugin_dir = Path(tempfile.mkdtemp(prefix="lightfall_claude_"))
        init_session_plugin_dir(self._session_plugin_dir)
        for plugin in enabled:
            materialize_skill(plugin, self._session_plugin_dir)

        # Legacy additional_tools support (deprecated; warn but keep working
        # for any direct API consumers)
        if self._additional_tools:
            from claude_agent_sdk import create_sdk_mcp_server
            logger.warning(
                "QtClaudeAgent.additional_tools is deprecated; contribute via AgentPlugin instead"
            )
            legacy_server = create_sdk_mcp_server(
                name="legacy_additional",
                version="1.0.0",
                tools=self._additional_tools,
            )
            mcp_servers["legacy_additional"] = legacy_server
            for tool_func in self._additional_tools:
                tool_name = getattr(tool_func, "name", None) or getattr(tool_func, "__name__", None)
                if tool_name:
                    allowed_tools.append(f"mcp__legacy_additional__{tool_name}")
```

Update the `ClaudeAgentOptions` construction (around line 277-285) to add `plugins=`:

```python
        options_dict = {
            "plugins": [{"type": "local", "path": str(self._session_plugin_dir)}],
            "mcp_servers": mcp_servers,
            "allowed_tools": allowed_tools,
            "system_prompt": system_prompt,
            "permission_mode": permission_mode,
            "max_turns": max_turns,
        }
```

Add `Path` and `tempfile` imports at the top of the file if not already present.

Add cleanup in `QtClaudeAgent.stop()`:

```python
    def stop(self) -> None:
        # ... existing worker stop logic ...
        if hasattr(self, "_session_plugin_dir") and self._session_plugin_dir.exists():
            import shutil
            shutil.rmtree(self._session_plugin_dir, ignore_errors=True)
```

- [ ] **Step 5: Run test to verify it passes**

Run: `pytest tests/claude/test_agent_session_wiring.py -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/lightfall/claude/agent.py tests/claude/test_agent_session_wiring.py
git commit -m "Wire QtClaudeAgent to per-plugin MCP servers + plugins= for skills"
```

### Task 3.3: Remove skill aggregation from `claude_panel.py`

**Files:**
- Modify: `src/lightfall/ui/panels/claude_panel.py`

- [ ] **Step 1: Read the affected block**

Run: `sed -n '648,672p' src/lightfall/ui/panels/claude_panel.py`
Expected: shows the `# Append skill prompts from enabled skills` block (lines ~651-669).

- [ ] **Step 2: Delete the block**

Open `src/lightfall/ui/panels/claude_panel.py`. Remove the block that begins with `# Append skill prompts from enabled skills` and ends with the closing `except Exception as e:` for that try-block. Specifically: delete lines 651-669 (or wherever they are after intervening edits). Leave the `return base_prompt` line in place.

- [ ] **Step 3: Run full test suite**

Run: `pytest tests/ -q`
Expected: All passing. The skills are no longer aggregated into the system prompt; they're instead loaded by the SDK from the synthesized plugin dir (Task 3.2).

- [ ] **Step 4: Commit**

```bash
git add src/lightfall/ui/panels/claude_panel.py
git commit -m "Remove skill aggregation from system prompt (skills now SDK-native)"
```

### Task 3.4: Delete obsolete plugin types, registries, and `skill_docs_tool.py`

**Files:**
- Delete: `src/lightfall/plugins/skill_plugin.py`
- Delete: `src/lightfall/plugins/mcp_tool.py`
- Delete: `src/lightfall/ui/panels/claude/skill_registry.py`
- Delete: `src/lightfall/ui/panels/claude/tool_registry.py`
- Delete: `src/lightfall/plugins/tools/skill_docs_tool.py`
- Delete: `src/lightfall/plugins/skills/` (after files moved in Phase 2)
- Delete: `src/lightfall/plugins/tools/` (after files moved in Phase 2)
- Modify: `src/lightfall/plugins/loader.py` (remove `skill` and `mcp_tool` branches)

- [ ] **Step 1: Run a grep to find any remaining references to the to-be-deleted modules**

Run: `grep -rln 'skill_plugin\|mcp_tool\|SkillRegistry\|MCPToolRegistry\|skill_docs_tool' src/ tests/`
Expected: lists files. Make a list of remaining usages.

- [ ] **Step 2: Update each remaining reference to use AgentPlugin / AgentRegistry**

For each file in step 1's list:
- If it imports `SkillPlugin` or `MCPToolPlugin`: change to `AgentPlugin`. (Inside `agents/` modules, there should be no remaining imports — already done in Phase 2.)
- If it imports `SkillRegistry` or `MCPToolRegistry`: change to `AgentRegistry`. Update method calls — `register_plugin(p)` → `register(p)`, `get_aggregated_system_prompt()` → no replacement (delete the call), etc.
- If it imports `skill_docs_tool`: delete the call/import.

Specific sites known from the spec:
- `src/lightfall/plugins/loader.py` — remove the `elif plugin_info.type_name == "skill":` and `elif plugin_info.type_name == "mcp_tool":` branches entirely. Verify no other code references those types.
- `src/lightfall/plugins/user_plugins.py` — `RegistrationTracker` references both registries; this is removed in Phase 4. For now, swap to `AgentRegistry.register` for the skill/mcp_tool case (or leave a TODO if Phase 4 will rewrite this entirely — see Task 4.1).
- `src/lightfall/ui/preferences/tool_settings.py` — references `MCPToolRegistry`. Update to `AgentRegistry.get_plugins()`. This is also revisited in Phase 5.

- [ ] **Step 3: Delete the files**

```bash
git rm src/lightfall/plugins/skill_plugin.py
git rm src/lightfall/plugins/mcp_tool.py
git rm src/lightfall/ui/panels/claude/skill_registry.py
git rm src/lightfall/ui/panels/claude/tool_registry.py
git rm src/lightfall/plugins/tools/skill_docs_tool.py
git rm -rf src/lightfall/plugins/skills/
git rm -rf src/lightfall/plugins/tools/
```

(If `lightfall/plugins/skills/__init__.py` and `lightfall/plugins/tools/__init__.py` only contain pass-through, this is safe; otherwise inspect first.)

- [ ] **Step 4: Run all tests**

Run: `pytest tests/ -q`
Expected: All passing. Parity tests in `tests/plugins/agents/` will fail because they import the old skill modules — DELETE those parity tests too at this point (they served their purpose during Phase 2 and now reference removed code):

Run: `git rm tests/plugins/agents/test_alignment_parity.py tests/plugins/agents/test_plan_design_parity.py tests/plugins/agents/test_scan_planning_parity.py tests/plugins/agents/test_panel_design_parity.py tests/plugins/agents/test_panel_builder_parity.py tests/plugins/agents/test_device_tools_parity.py tests/plugins/agents/test_plan_tools_parity.py tests/plugins/agents/test_engine_tools_parity.py tests/plugins/agents/test_ipython_tools_parity.py`

Re-run: `pytest tests/ -q`
Expected: All passing.

- [ ] **Step 5: Commit**

```bash
git add -A
git commit -m "Delete obsolete SkillPlugin / MCPToolPlugin / SkillRegistry / skill_docs"
```

---

## Phase 4: Simplify `UserPluginService` via `__init_subclass__`

After this phase, `RegistrationTracker` is gone and user plugins auto-register through `PluginType.__init_subclass__`. Existing user plugin files keep working (explicit `Registry.register()` calls become no-ops with a deprecation warning).

### Task 4.1: Replace `RegistrationTracker` with `__init_subclass__`-driven tracking

**Files:**
- Modify: `src/lightfall/plugins/user_plugins.py`
- Test: `tests/plugins/test_user_plugin_service.py`

- [ ] **Step 1: Inspect current `UserPluginService` end-to-end**

Run: `wc -l src/lightfall/plugins/user_plugins.py && cat src/lightfall/plugins/user_plugins.py | head -200`
Expected: shows the file structure. Note `RegistrationTracker`, `_patch_*_registry` methods, `load_file`, `unload_file`.

- [ ] **Step 2: Write integration tests for the new flow**

Create `tests/plugins/test_user_plugin_service.py`:

```python
"""Tests for the simplified UserPluginService."""
from __future__ import annotations

from pathlib import Path

import pytest

from lightfall.plugins.user_plugins import UserPluginService
from lightfall.ui.panels.claude.agent_registry import AgentRegistry


@pytest.fixture(autouse=True)
def reset():
    UserPluginService.reset_instance()
    AgentRegistry.reset_instance()
    yield
    UserPluginService.reset_instance()
    AgentRegistry.reset_instance()


@pytest.fixture
def fake_user_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "lightfall.plugins.types._user_plugin_roots",
        lambda: [tmp_path.resolve()],
    )
    return tmp_path


def _write_user_agent(dir_: Path, name: str) -> Path:
    path = dir_ / f"{name}.py"
    path.write_text(f'''
from lightfall.plugins.agent_plugin import AgentPlugin

class {name.title()}Agent(AgentPlugin):
    @property
    def name(self): return "{name}"
    @property
    def description(self): return "user-contributed {name}"
    def get_system_prompt(self): return "## {name} prompt"
''', encoding="utf-8")
    return path


def test_load_file_registers_with_agent_registry(fake_user_dir):
    path = _write_user_agent(fake_user_dir, "user_alpha")
    service = UserPluginService.get_instance()
    info = service.load_file(path)
    assert any(r.registry_type == "agent" and r.key == "user_alpha" for r in info.registrations)
    assert AgentRegistry.get_instance().get_plugin("user_alpha") is not None


def test_unload_file_removes_from_registry(fake_user_dir):
    path = _write_user_agent(fake_user_dir, "user_beta")
    service = UserPluginService.get_instance()
    service.load_file(path)
    assert AgentRegistry.get_instance().get_plugin("user_beta") is not None
    service.unload_file(path)
    assert AgentRegistry.get_instance().get_plugin("user_beta") is None


def test_hot_reload_replaces_old_registration(fake_user_dir):
    path = _write_user_agent(fake_user_dir, "user_gamma")
    service = UserPluginService.get_instance()
    service.load_file(path)
    first_instance = AgentRegistry.get_instance().get_plugin("user_gamma")

    # Modify the file: change the description
    path.write_text(path.read_text().replace("user-contributed", "edited"), encoding="utf-8")
    service.load_file(path)  # equivalent to a hot-reload trigger
    second_instance = AgentRegistry.get_instance().get_plugin("user_gamma")

    assert second_instance is not None
    assert second_instance is not first_instance
    assert "edited" in second_instance.description
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `pytest tests/plugins/test_user_plugin_service.py -v`
Expected: FAIL — current `UserPluginService` doesn't auto-register through `__init_subclass__`; user files would need to call `Registry.register()` explicitly.

- [ ] **Step 4: Rewrite `UserPluginService`**

Open `src/lightfall/plugins/user_plugins.py`. Replace the module contents (preserve other helpful comments/docstrings) with:

```python
"""Service for loading and managing user-defined plugins with hot-reload.

User plugins are Python files in ~/lightfall/plugins/. Plugin classes auto-register
via PluginType.__init_subclass__ when defined; UserPluginService tracks
which (registry_type, key) pairs were registered for each file so unload can
clean up properly.

Hot-reload: file change → unload (unregister tracked entries) → re-exec
(new class definitions auto-enqueue and register).
"""

from __future__ import annotations

import importlib
import importlib.util
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from PySide6.QtCore import QFileSystemWatcher, QObject, Signal

from lightfall.utils.logging import logger

if TYPE_CHECKING:
    from lightfall.plugins.types import PluginType


@dataclass
class PluginRegistration:
    registry_type: str   # "panel", "agent", "theme", etc.
    key: str             # plugin identifier within that registry


@dataclass
class PluginInfo:
    file_path: Path
    module_name: str
    registrations: list[PluginRegistration] = field(default_factory=list)
    is_temp: bool = False
    load_error: str | None = None


class UserPluginService(QObject):
    """Singleton that loads user plugins and tracks their registrations."""

    plugin_loaded = Signal(str)
    plugin_unloaded = Signal(str)
    plugin_load_failed = Signal(str, str)

    _instance: "UserPluginService | None" = None
    _lock = threading.Lock()

    def __init__(self) -> None:
        super().__init__()
        self._plugins_by_path: dict[Path, PluginInfo] = {}
        self._current_load: PluginInfo | None = None
        self._watcher: QFileSystemWatcher | None = None

    @classmethod
    def get_instance(cls) -> "UserPluginService":
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    @classmethod
    def reset_instance(cls) -> None:
        with cls._lock:
            cls._instance = None

    def enqueue(self, cls: type["PluginType"], file_path: Path) -> None:
        """Auto-register a PluginType subclass discovered via __init_subclass__.

        Routes the class to its type-specific registry via the existing
        PluginLoader._register_plugin machinery, and records the registration
        for later unload.
        """
        from lightfall.plugins.loader import PluginInfo as LoaderPluginInfo, PluginLoader

        try:
            instance = cls()  # PluginType subclasses are singleton-shaped
        except Exception as e:  # noqa: BLE001
            logger.error("instantiating user plugin {} from {} failed: {}", cls.__name__, file_path, e)
            return

        loader_info = LoaderPluginInfo(
            type_name=cls.type_name,
            name=getattr(instance, "name", cls.__name__),
            instance=instance,
        )
        try:
            PluginLoader.get_instance()._register_plugin(loader_info)
        except Exception as e:  # noqa: BLE001
            logger.error("registering user plugin {} failed: {}", loader_info.name, e)
            return

        if self._current_load is not None:
            self._current_load.registrations.append(
                PluginRegistration(registry_type=cls.type_name, key=loader_info.name)
            )

    def load_file(self, path: Path) -> PluginInfo:
        """Exec `path` in an isolated module. __init_subclass__ fires for each
        PluginType subclass defined in the file; enqueue() records each registration.
        """
        path = path.resolve()
        if path in self._plugins_by_path:
            self.unload_file(path)

        info = PluginInfo(
            file_path=path,
            module_name=f"lightfall_user_plugins.{path.stem}_{int(time.time() * 1000)}",
        )
        self._current_load = info
        try:
            spec = importlib.util.spec_from_file_location(info.module_name, path)
            if spec is None or spec.loader is None:
                info.load_error = "could not create module spec"
                return info
            module = importlib.util.module_from_spec(spec)
            sys.modules[info.module_name] = module
            try:
                spec.loader.exec_module(module)
            except Exception as e:  # noqa: BLE001
                info.load_error = repr(e)
                logger.error("user plugin {} exec failed: {}", path, e)
                self.plugin_load_failed.emit(str(path), repr(e))
                return info
        finally:
            self._current_load = None

        self._plugins_by_path[path] = info
        self.plugin_loaded.emit(str(path))
        logger.info("loaded user plugin {} ({} registrations)", path, len(info.registrations))
        return info

    def unload_file(self, path: Path) -> None:
        """Unregister all entries this file contributed."""
        path = path.resolve()
        info = self._plugins_by_path.pop(path, None)
        if info is None:
            return
        for reg in info.registrations:
            self._unregister(reg)
        sys.modules.pop(info.module_name, None)
        self.plugin_unloaded.emit(str(path))
        logger.info("unloaded user plugin {}", path)

    def _unregister(self, reg: PluginRegistration) -> None:
        try:
            if reg.registry_type == "agent":
                from lightfall.ui.panels.claude.agent_registry import AgentRegistry
                AgentRegistry.get_instance().unregister(reg.key)
            elif reg.registry_type == "panel":
                from lightfall.ui.panels.registry import PanelRegistry
                PanelRegistry.get_instance().unregister(reg.key)
            else:
                logger.warning("don't know how to unregister type={} key={}", reg.registry_type, reg.key)
        except Exception as e:  # noqa: BLE001
            logger.error("unregister {} {} failed: {}", reg.registry_type, reg.key, e)

    def loaded_plugins(self) -> list[PluginInfo]:
        return list(self._plugins_by_path.values())
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest tests/plugins/test_user_plugin_service.py -v`
Expected: PASS — all 3 tests.

- [ ] **Step 6: Run full test suite**

Run: `pytest tests/ -q`
Expected: All passing.

- [ ] **Step 7: Commit**

```bash
git add src/lightfall/plugins/user_plugins.py tests/plugins/test_user_plugin_service.py
git commit -m "Rewrite UserPluginService around __init_subclass__; remove RegistrationTracker"
```

### Task 4.2: Update `panel_builder` agent's `ncs_create_user_plugin` to be kindless

**Files:**
- Modify: `src/lightfall/plugins/agents/panel_builder.py`

- [ ] **Step 1: Inspect the current tool**

Run: `grep -n 'create_user_plugin\|panel_metadata' src/lightfall/plugins/agents/panel_builder.py`
Expected: shows the tool definition site and any panel-specific validation.

- [ ] **Step 2: Update validation to accept any `PluginType` subclass**

Open `src/lightfall/plugins/agents/panel_builder.py`. Find `_validate_plugin_code`. Currently it checks for a `PanelPlugin` subclass with `panel_metadata`. Update so that the function accepts content that defines any concrete `PluginType` subclass (`PanelPlugin`, `AgentPlugin`, future kinds), and the kind is reported back via the result rather than required as input:

```python
    def _validate_plugin_code(
        self,
        code: str,
        name: str,
    ) -> tuple[bool, str | None, list[str]]:
        """Validate user plugin code. Returns (is_valid, error, found_kinds).

        found_kinds is a list of type_names ("panel", "agent", ...) for
        each concrete PluginType subclass discovered.
        """
        # 1. Syntax
        try:
            compile(code, f"{name}.py", "exec")
        except SyntaxError as e:
            return False, f"Syntax error at line {e.lineno}: {e.msg}", []

        # 2. Isolated exec
        namespace: dict[str, Any] = {"__name__": f"lightfall_user_plugins.{name}"}
        try:
            exec(code, namespace)
        except Exception as e:  # noqa: BLE001
            return False, f"Import/exec error: {e}", []

        # 3. Find PluginType subclasses
        from lightfall.plugins.types import PluginType

        found_kinds: list[str] = []
        for v in namespace.values():
            if (
                isinstance(v, type)
                and issubclass(v, PluginType)
                and v is not PluginType
                and not getattr(v, "__abstractmethods__", None)
            ):
                found_kinds.append(v.type_name)

        if not found_kinds:
            return False, "No concrete PluginType subclass found", []

        return True, None, found_kinds
```

Update the `@tool` definition. Remove any `kind` parameter from its input_schema. Update success message to report what was created:

```python
        @tool(
            name="ncs_create_user_plugin",
            description=(
                "Create a user plugin file in ~/lightfall/plugins/<name>.py. The "
                "class hierarchy in `content` determines the plugin kind:\n"
                "  AgentPlugin → extends the embedded Claude agent (skill prompt + MCP tools)\n"
                "  PanelPlugin → registers a new dock panel\n"
                "Use persistent=False to write to a temp dir for prototyping."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "content": {"type": "string"},
                    "persistent": {"type": "boolean", "default": True},
                },
                "required": ["name", "content"],
            },
        )
        async def create_user_plugin(args: dict) -> dict:
            name = args["name"]
            content = args["content"]
            persistent = args.get("persistent", True)
            ok, err, kinds = self._validate_plugin_code(content, name)
            if not ok:
                return mcp_result(f"Validation failed: {err}")
            from pathlib import Path
            target_dir = Path.home() / "lightfall" / "plugins" if persistent else self._temp_plugin_dir()
            target_dir.mkdir(parents=True, exist_ok=True)
            path = target_dir / f"{name}.py"
            path.write_text(content, encoding="utf-8")
            from lightfall.plugins.user_plugins import UserPluginService
            info = UserPluginService.get_instance().load_file(path)
            kinds_str = ", ".join(sorted(set(r.registry_type for r in info.registrations)))
            return mcp_result(f"Created plugin '{name}' at {path}. Registered: {kinds_str}.")
```

(`self._temp_plugin_dir()` is whatever method already exists for resolving the temp dir; if not present, add a simple one returning `Path(tempfile.gettempdir()) / "lightfall_temp_plugins"`.)

- [ ] **Step 3: Update the agent's `get_system_prompt()` to teach the new contract**

In the same file, update the prompt body to describe class-hierarchy-determines-kind. Remove any references to a `kind` parameter or to the old `ncs_create_temp_plugin` (deprecate by inlining it as `persistent=False`).

- [ ] **Step 4: Run tests** (existing tests should still pass; new tests cover the validation)

Run: `pytest tests/ -q`
Expected: All passing.

- [ ] **Step 5: Commit**

```bash
git add src/lightfall/plugins/agents/panel_builder.py
git commit -m "Update ncs_create_user_plugin to infer kind from class hierarchy"
```

---

## Phase 5: Settings UI updates

After this phase, the Claude tools settings panel reads from `AgentRegistry`, drops the "Type" column, and the optional rename of `tool_settings` to `agent_settings` is applied (skip the rename if not worth the effort).

### Task 5.1: Wire `tool_settings.py` to `AgentRegistry`

**Files:**
- Modify: `src/lightfall/ui/preferences/tool_settings.py`

- [ ] **Step 1: Inspect**

Run: `grep -n 'MCPToolRegistry\|column' src/lightfall/ui/preferences/tool_settings.py | head -30`
Expected: shows where the "Type" column is defined and where the registry is queried.

- [ ] **Step 2: Update column definitions and refresh logic**

Open `src/lightfall/ui/preferences/tool_settings.py`. Find `ToolPluginTableModel.COLUMNS` — change from:

```python
    COLUMNS = ["Plugin", "Type", "Category", "Description"]
```

to:

```python
    COLUMNS = ["Plugin", "Category", "Description"]
```

In `refresh()`, change:

```python
            from lightfall.ui.panels.claude.tool_registry import MCPToolRegistry
            registry = MCPToolRegistry.get_instance()
            self._plugins = registry.get_plugins()
            self._plugins.sort(
                key=lambda p: (
                    0 if p.type_name == "mcp_tool" else 1,
                    p.category,
                    p.display_name,
                )
            )
```

to:

```python
            from lightfall.ui.panels.claude.agent_registry import AgentRegistry
            registry = AgentRegistry.get_instance()
            self._plugins = registry.get_plugins()
            self._plugins.sort(key=lambda p: (p.category, p.display_name))
```

In `data()`, find any switch on column index that handles the old "Type" column (column 1) and remove that branch. Adjust subsequent column indices:
- Column 0: Plugin name (display_name)
- Column 1 (was 2): Category
- Column 2 (was 3): Description

- [ ] **Step 3: Run full test suite + smoke-launch the app**

Run: `pytest tests/ -q`
Expected: All passing.

(Manual: launch lightfall, open Settings → Claude Tools, verify the table renders 9 rows without a "Type" column.)

- [ ] **Step 4: Commit**

```bash
git add src/lightfall/ui/preferences/tool_settings.py
git commit -m "Settings UI: drop Type column; read from AgentRegistry"
```

### Task 5.2: (Optional) Rename `tool_settings.py` → `agent_settings.py`

Skip this task if not worth the rename churn. If proceeding:

- [ ] **Step 1: Rename**

```bash
git mv src/lightfall/ui/preferences/tool_settings.py src/lightfall/ui/preferences/agent_settings.py
```

- [ ] **Step 2: Update class name** in the new file: `ClaudeToolsSettingsPlugin` → `ClaudeAgentsSettingsPlugin`. Update any internal docstrings.

- [ ] **Step 3: Update `builtin_manifest.py`** — change the entry's `import_path` and `name`:

```python
        PluginEntry(
            type_name="settings",
            name="claude_agents",
            import_path="lightfall.ui.preferences.agent_settings:ClaudeAgentsSettingsPlugin",
        ),
```

- [ ] **Step 4: Find any other references** — `grep -rln 'tool_settings\|ClaudeToolsSettings' src/ tests/` — and update.

- [ ] **Step 5: Run full test suite**

Run: `pytest tests/ -q`
Expected: All passing.

- [ ] **Step 6: Commit**

```bash
git add -A
git commit -m "Rename tool_settings → agent_settings"
```

---

## Phase 6: Test suite consolidation + manual smoke

### Task 6.1: Add an end-to-end agent-construction test

**Files:**
- Create: `tests/claude/test_full_session_construction.py`

- [ ] **Step 1: Write the integration test**

Create `tests/claude/test_full_session_construction.py`:

```python
"""End-to-end: load builtin manifest → construct QtClaudeAgent → verify options."""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest


@pytest.fixture
def loaded_builtins(monkeypatch):
    """Load the real builtin manifest into AgentRegistry, with SDK mocked."""
    monkeypatch.setattr("lightfall.claude.agent.ClaudeSDKClient", MagicMock())
    from lightfall.plugins.builtin_manifest import builtin_manifest
    from lightfall.plugins.loader import PluginLoader
    from lightfall.ui.panels.claude.agent_registry import AgentRegistry

    AgentRegistry.reset_instance()
    loader = PluginLoader()
    loader.load_manifest(builtin_manifest)
    yield
    AgentRegistry.reset_instance()


def test_construct_agent_with_all_builtins_enabled(loaded_builtins, qtbot, monkeypatch):
    monkeypatch.setattr(
        "lightfall.ui.panels.claude.agent_registry.AgentRegistry._get_enabled_pref",
        lambda self: None,  # default-enabled set
    )
    from PySide6.QtWidgets import QWidget
    from lightfall.claude.agent import QtClaudeAgent

    target = QWidget()
    qtbot.addWidget(target)
    agent = QtClaudeAgent(target_window=target, require_approval=False)

    options = agent.options
    # qt server always present
    assert "qt" in options.mcp_servers
    # 5 tool-bearing plugins each get their own server
    expected_tool_servers = {"device_tools", "plan_tools", "engine_tools", "ipython_tools", "panel_builder"}
    assert expected_tool_servers.issubset(options.mcp_servers.keys())
    # No legacy "additional" server
    assert "additional" not in options.mcp_servers
    # plugins= present, points at a real on-disk dir with skills
    from pathlib import Path
    plugin_dir = Path(options.plugins[0]["path"])
    assert plugin_dir.exists()
    assert (plugin_dir / ".claude-plugin" / "plugin.json").exists()
    # Each prompt-bearing plugin has a SKILL.md
    expected_skills = {"alignment", "plan_design", "scan_planning", "panel_design", "panel_builder"}
    for skill_name in expected_skills:
        assert (plugin_dir / "skills" / skill_name / "SKILL.md").exists(), f"missing {skill_name}"
    # No skill content baked into the system prompt
    assert "## Beamline Alignment Expertise" not in options.system_prompt
```

- [ ] **Step 2: Run test**

Run: `pytest tests/claude/test_full_session_construction.py -v`
Expected: PASS.

- [ ] **Step 3: Commit**

```bash
git add tests/claude/test_full_session_construction.py
git commit -m "Add end-to-end agent-construction integration test"
```

### Task 6.2: Manual smoke test (gate before merging the branch)

This is a manual checklist; not automated. Execute against a real lightfall app instance.

- [ ] **Smoke 1: Settings UI renders 9 rows.**
  Start lightfall → Settings → Claude Tools. Verify 9 entries: alignment, plan_design, scan_planning, panel_design, panel_builder, device_tools, plan_tools, engine_tools, ipython_tools. No "Type" column. Defaults: all 9 enabled (since each `enabled_by_default=True`).

- [ ] **Smoke 2: Per-plugin tool routing.**
  Open Claude panel. Send: *"List the devices."* Verify the agent invokes `mcp__device_tools__ncs_list_devices` (or similar — exact tool name depends on the existing tool definitions). Check the namespace prefix is `mcp__device_tools__`, not `mcp__additional__`.

- [ ] **Smoke 3: Skill via deferred Skill tool.**
  Send: *"Help me align this motor — start with a coarse alignment."* Verify the SDK's deferred `Skill` tool fires for the `alignment` skill (visible in the agent's tool-call stream). The `alignment` SKILL.md body should appear as the loaded skill content.

- [ ] **Smoke 4: Disable + reconnect.**
  Settings → Claude Tools → uncheck `panel_builder` → Apply. Reconnect agent. Verify `mcp__panel_builder__*` tools are no longer in the agent's tool list (use the agent's introspection or attempt to invoke a panel_builder tool — should fail).

- [ ] **Smoke 5: User plugin auto-register.**
  Create `~/lightfall/plugins/test_user_agent.py`:

  ```python
  from lightfall.plugins.agent_plugin import AgentPlugin

  class TestUserAgent(AgentPlugin):
      @property
      def name(self): return "test_user"
      @property
      def description(self): return "smoke-test user agent"
      def get_system_prompt(self): return "## Test User Skill\n\nThis is a smoke-test skill."
  ```

  Reload (or restart lightfall). Settings → Claude Tools → verify `test_user` appears. Reconnect agent. Send a query that should invoke it (*"Use the test user skill."*) and verify the deferred `Skill` tool fires.

  Cleanup: delete `~/lightfall/plugins/test_user_agent.py`.

- [ ] **Smoke 6: Hot-reload.**
  Edit `~/lightfall/plugins/test_user_agent.py` (e.g., change the description). Verify lightfall logs the unload + reload, the settings panel updates, and the next agent reconnect uses the new content.

If any smoke test fails, fix in-place and add an automated test capturing the gap before merging.

### Task 6.3: Merge the branch

- [ ] **Step 1: Final pre-merge checks**

```bash
cd ~/PycharmProjects/ncs/ncs
pytest tests/ -q                # All passing
git log --oneline master..HEAD  # Review the commit list
```

- [ ] **Step 2: Squash-merge or merge as-is**

Per Ron's preference (option C from brainstorm: "merge once after end-to-end validation"), the branch is meant to land as one cohesive change. Use a regular merge to preserve the per-commit story:

```bash
git checkout master
git merge --no-ff feature/sdk-native-agents
git push origin master  # only if Ron confirms
```

- [ ] **Step 3: Cleanup**

```bash
git branch -d feature/sdk-native-agents
```

---

## Spec coverage check

Cross-reference each section of the spec to a task above:

- **Goals:** AgentPlugin (Task 1.1) ✓; SDK-native skills via plugins= (Task 3.2) ✓; per-plugin MCP servers (Task 3.1, 3.2) ✓; AgentPlugin unification (Task 1.1) ✓; user-plugin generalization (Tasks 1.2, 4.1) ✓; manifest-as-source-of-truth (Task 1.4, 2.7) ✓.
- **Non-goals:** explicitly preserved — no tool-taxonomy reorg, no per-tool toggles, no migration of other plugin types, no Spec B work, no bundle-coupling.
- **Architecture diagram:** every flow has a corresponding task.
- **AgentPlugin contract:** Task 1.1.
- **Loader registration:** Task 1.4.
- **Session-time SDK assembly:** Tasks 3.1, 3.2.
- **Generalized user-plugin path:** Tasks 1.2, 4.1, 4.2.
- **Built-in plugin map:** Tasks 2.2-2.6.
- **Files added/deleted/modified:** Tasks 1.1, 1.3, 1.4, 2.1, 3.1-3.4, 4.1, 4.2, 5.1, 5.2.
- **Branch commit shape:** the 6-phase task structure of this plan.
- **Breaking changes:** Tasks 4.1 (`__init_subclass__` makes explicit `register()` calls redundant), 3.4 (skill_docs deletion), 4.2 (`ncs_create_user_plugin` no longer takes `kind`).
- **Settings UI:** Task 5.1.
- **Testing strategy:** unit tests in Tasks 1.1, 1.2, 1.3, 1.4, 3.1, 3.2; integration in Task 6.1; smoke checklist in Task 6.2.
- **Open questions:**
  - Plugin name format: `materialize_skill` converts `_` → `-` for SKILL.md frontmatter (Task 3.1, in the implementation), keeps `plugin.name` as canonical identifier with underscores. Open Q (b) implementation.
  - References packaging: Task 2.4 (panel_builder) exercises the path; if more skills had docs, similar overrides would apply.
  - Backward-compat shim: not implemented; no `SkillPlugin = AgentPlugin` alias. Add one if Phase 4 surfaces user-plugin breakage in the wild.

No spec gaps detected.
