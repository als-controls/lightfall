# Proactive Monitor — Framework (Plan A) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the deterministic proactive-monitor framework — pluggable `MonitorFeed`s evaluated on a timer during a run, surfaced via toasts + a new Monitor dock panel — plus one real feed (acquisition-health) and the launch-time `ExperimentContext` injection. (XPCS feed + LLM advisor are Plan B.)

**Architecture:** A `MonitorService` singleton owns a `MonitorScheduler` that subscribes to the Bluesky engine's document stream (worker thread, non-blocking), maintains a thread-safe `RollingBuffer`, and on a Qt timer (GUI thread) evaluates each enabled feed off-thread via `QThreadFuture`. Feeds are deterministic and judge **already-reduced signals only** — they never analyse raw data. Observations are rate-limited (surface once per state change) and pushed to toasts + the Monitor panel, with a "discuss in assistant" hand-off into the existing reactive Claude agent. Plugin rails (`MonitorPlugin`/`MonitorRegistry`) mirror the existing `AgentPlugin`/`AgentRegistry` exactly.

**Tech Stack:** Python 3.11, PySide6 (Qt), Bluesky engine wrapper (`lightfall.acquire.engine`), existing plugin loader/registry, `lightfall.utils.threads` (`QThreadFuture`, `invoke_in_main_thread`), `lightfall.ui.toast`.

## Global Constraints

- **No data analysis in the Lightfall process.** Feeds judge reduced signals (IOC stats, external-service metrics); they never correlate/FFT/fit raw data. (Spec §Goal.)
- **Advisory only.** No hardware actions; never gate an abort on a feed.
- **Never destabilise a run.** Engine-thread callbacks only enqueue/append and return; feed evaluation runs off-thread and is exception-wrapped.
- **Mirror the agent rails** for plugin type + registry + opt-out prefs.
- **Run tests from the worktree** with: `PYTHONPATH=src .venv/Scripts/python -m pytest <path> -v` (the editable install resolves to the main checkout; `PYTHONPATH=src` makes pytest test the worktree code). The venv is the main checkout's: `C:/Users/rp/PycharmProjects/ncs/lightfall/.venv/Scripts/python.exe`.
- **Qt tests** need a `QApplication`; if the suite's conftest doesn't provide one, create it in-test via `QApplication.instance() or QApplication([])`, and run headless with `QT_QPA_PLATFORM=offscreen`.

## File Structure

New package `src/lightfall/monitor/`:
- `models.py` — `Severity`, `Observation`, `ExperimentContext` (pure data).
- `data_window.py` — `DataWindow` (reduced-signal view a feed reads).
- `feed.py` — `MonitorFeed` ABC.
- `monitor_plugin.py` — `MonitorPlugin` (mirrors `AgentPlugin`).
- `registry.py` — `MonitorRegistry` (mirrors `AgentRegistry`) + `enabled_feeds()`.
- `rate_limiter.py` — `RateLimiter` (state-change dedupe).
- `buffer.py` — `RollingBuffer` (thread-safe, subscribe-compatible, `snapshot()`).
- `scheduler.py` — `MonitorScheduler` (arm/disarm, tick, eval dispatch).
- `service.py` — `MonitorService` (singleton; toasts; recent log; discuss hand-off).
- `context_provider.py` — `ExperimentContextProvider` (current context for launch injection).
- `feeds/__init__.py`, `feeds/acquisition_health.py` — first real feed + its `MonitorPlugin`.

New UI:
- `src/lightfall/ui/panels/monitor_panel.py` — `MonitorPanel(BasePanel)`.
- `src/lightfall/ui/panels/plugins/monitor_panel_plugin.py` — `MonitorPanelPlugin(PanelPlugin)`.

Modified:
- `src/lightfall/main.py` — register the `"monitor"` plugin type (`:589` block) + add `_setup_monitor(...)` after the main window is created.
- `src/lightfall/plugins/loader.py` — add a `"monitor"` dispatch branch (after the `"agent"` branch, `:644`).
- `src/lightfall/plugins/builtin_manifest.py` — add the monitor plugin + Monitor panel entries.
- `src/lightfall/ui/panels/claude_panel.py` — add `submit_external_prompt(text)`.
- `src/lightfall/ui/panels/bluesky_panel.py` — register the ExperimentContext pre-submit hook.

Tests under `tests/monitor/` and `tests/ui/`.

---

### Task 1: Monitor data model (`Severity`, `Observation`, `ExperimentContext`)

**Files:**
- Create: `src/lightfall/monitor/__init__.py` (empty)
- Create: `src/lightfall/monitor/models.py`
- Test: `tests/monitor/test_models.py`

**Interfaces:**
- Produces: `Severity` (Literal `"info"|"warn"|"critical"`); `Observation` dataclass with fields `severity, feed_name, run_uid, title, message, state_key, metrics: dict[str,float]={}, recommendation: str|None=None, ts: float=0.0` and `.to_dict()`; `ExperimentContext` dataclass with `experiment_type="generic", intent="", feed_config: dict[str,dict]={}` and classmethods `default()`, `from_dict(d)`, `from_start_doc(doc)`, instance methods `to_dict()`, `for_feed(name)->dict`.

- [ ] **Step 1: Write the failing test**

```python
# tests/monitor/test_models.py
from lightfall.monitor.models import Observation, ExperimentContext


def test_observation_to_dict_roundtrips_core_fields():
    obs = Observation(
        severity="warn", feed_name="health", run_uid="abc",
        title="Low count rate", message="rate=0.1", state_key="health:low_rate",
        metrics={"rate": 0.1}, recommendation="check shutter", ts=123.0,
    )
    d = obs.to_dict()
    assert d["severity"] == "warn"
    assert d["state_key"] == "health:low_rate"
    assert d["metrics"] == {"rate": 0.1}


def test_experiment_context_from_start_doc_reads_injected_key():
    doc = {"uid": "u1", "experiment_context": {
        "experiment_type": "xpcs", "intent": "slow dynamics",
        "feed_config": {"acquisition_health": {"min_rate": 1.0}},
    }}
    ctx = ExperimentContext.from_start_doc(doc)
    assert ctx.experiment_type == "xpcs"
    assert ctx.for_feed("acquisition_health") == {"min_rate": 1.0}
    assert ctx.for_feed("missing") == {}


def test_experiment_context_from_start_doc_defaults_when_absent():
    ctx = ExperimentContext.from_start_doc({"uid": "u1"})
    assert ctx.experiment_type == "generic"
    assert ctx.feed_config == {}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=src .venv/Scripts/python -m pytest tests/monitor/test_models.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'lightfall.monitor'`.

- [ ] **Step 3: Write minimal implementation**

```python
# src/lightfall/monitor/models.py
"""Pure data types for the proactive monitor.

Observation is what a feed emits. ExperimentContext is the launch-time
"what is this measurement trying to do" object front-loaded into the run
start document. Both are JSON-serializable.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

Severity = Literal["info", "warn", "critical"]


@dataclass
class Observation:
    """A single judgment emitted by a MonitorFeed."""

    severity: Severity
    feed_name: str
    run_uid: str
    title: str
    message: str
    state_key: str  # identity of the *condition*, for rate-limiting
    metrics: dict[str, float] = field(default_factory=dict)
    recommendation: str | None = None
    ts: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "severity": self.severity,
            "feed_name": self.feed_name,
            "run_uid": self.run_uid,
            "title": self.title,
            "message": self.message,
            "state_key": self.state_key,
            "metrics": dict(self.metrics),
            "recommendation": self.recommendation,
            "ts": self.ts,
        }


@dataclass
class ExperimentContext:
    """Declared intent for a measurement, read by feeds (and, in Plan B,
    the advisor). Front-loaded into the start doc under key
    ``experiment_context``."""

    experiment_type: str = "generic"
    intent: str = ""
    feed_config: dict[str, dict[str, Any]] = field(default_factory=dict)

    @classmethod
    def default(cls) -> ExperimentContext:
        return cls()

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> ExperimentContext:
        return cls(
            experiment_type=str(d.get("experiment_type", "generic")),
            intent=str(d.get("intent", "")),
            feed_config=dict(d.get("feed_config", {}) or {}),
        )

    @classmethod
    def from_start_doc(cls, doc: dict[str, Any]) -> ExperimentContext:
        blob = doc.get("experiment_context")
        if isinstance(blob, dict):
            return cls.from_dict(blob)
        return cls.default()

    def to_dict(self) -> dict[str, Any]:
        return {
            "experiment_type": self.experiment_type,
            "intent": self.intent,
            "feed_config": dict(self.feed_config),
        }

    def for_feed(self, name: str) -> dict[str, Any]:
        cfg = self.feed_config.get(name)
        return dict(cfg) if isinstance(cfg, dict) else {}
```

Also create empty `src/lightfall/monitor/__init__.py`.

- [ ] **Step 4: Run test to verify it passes**

Run: `PYTHONPATH=src .venv/Scripts/python -m pytest tests/monitor/test_models.py -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add src/lightfall/monitor/__init__.py src/lightfall/monitor/models.py tests/monitor/test_models.py
git commit -m "feat(monitor): Observation + ExperimentContext data model"
```

---

### Task 2: `DataWindow`

**Files:**
- Create: `src/lightfall/monitor/data_window.py`
- Test: `tests/monitor/test_data_window.py`

**Interfaces:**
- Produces: `DataWindow` dataclass — `run_uid: str`, `events: dict[str, list]`, `seq_nums: list[int]`, `timestamps: list[float]`, `event_count: int`, `age_s: float|None=None`, `derived_provider: Callable[[str], dict|None]|None=None`, `pv_getter: Callable[[str], Any]|None=None`. Methods: `latest(field)->Any|None`, `series(field, last_k=None)->list`, `derived(name)->dict|None`, `pv_get(pv)->Any`.
- Consumes: nothing.

- [ ] **Step 1: Write the failing test**

```python
# tests/monitor/test_data_window.py
import pytest
from lightfall.monitor.data_window import DataWindow


def _win():
    return DataWindow(
        run_uid="u1",
        events={"det": [1.0, 2.0, 3.0]},
        seq_nums=[1, 2, 3],
        timestamps=[10.0, 11.0, 12.0],
        event_count=3,
        age_s=4.0,
    )


def test_latest_and_series():
    w = _win()
    assert w.latest("det") == 3.0
    assert w.latest("missing") is None
    assert w.series("det", last_k=2) == [2.0, 3.0]
    assert w.series("det") == [1.0, 2.0, 3.0]


def test_derived_defaults_none_and_pv_get_raises_without_hooks():
    w = _win()
    assert w.derived("xpcs") is None
    with pytest.raises(NotImplementedError):
        w.pv_get("BL:PV")


def test_hooks_are_used_when_set():
    w = _win()
    w.derived_provider = lambda name: {"name": name}
    w.pv_getter = lambda pv: f"val:{pv}"
    assert w.derived("xpcs") == {"name": "xpcs"}
    assert w.pv_get("BL:PV") == "val:BL:PV"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=src .venv/Scripts/python -m pytest tests/monitor/test_data_window.py -v`
Expected: FAIL — `ModuleNotFoundError`.

- [ ] **Step 3: Write minimal implementation**

```python
# src/lightfall/monitor/data_window.py
"""Reduced-signal view passed to a MonitorFeed each tick.

Holds ONLY already-reduced signals: inline scalar event columns from the
rolling buffer, plus optional hooks for externally-computed metrics
(`derived`) and a rare direct PV read (`pv_get`). There is deliberately
no raw-frame facet — feeds never analyse raw data (see spec §Goal)."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any


@dataclass
class DataWindow:
    run_uid: str
    events: dict[str, list[Any]] = field(default_factory=dict)
    seq_nums: list[int] = field(default_factory=list)
    timestamps: list[float] = field(default_factory=list)
    event_count: int = 0
    age_s: float | None = None  # seconds since the last event (None if no events)
    derived_provider: Callable[[str], dict | None] | None = None
    pv_getter: Callable[[str], Any] | None = None

    def latest(self, field_name: str) -> Any | None:
        seq = self.events.get(field_name)
        return seq[-1] if seq else None

    def series(self, field_name: str, last_k: int | None = None) -> list[Any]:
        seq = list(self.events.get(field_name, []))
        return seq[-last_k:] if last_k else seq

    def derived(self, name: str) -> dict | None:
        if self.derived_provider is not None:
            return self.derived_provider(name)
        return None

    def pv_get(self, pv: str) -> Any:
        if self.pv_getter is not None:
            return self.pv_getter(pv)
        raise NotImplementedError("pv_get is not wired in this DataWindow")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `PYTHONPATH=src .venv/Scripts/python -m pytest tests/monitor/test_data_window.py -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add src/lightfall/monitor/data_window.py tests/monitor/test_data_window.py
git commit -m "feat(monitor): DataWindow reduced-signal view"
```

---

### Task 3: `MonitorFeed` ABC

**Files:**
- Create: `src/lightfall/monitor/feed.py`
- Test: `tests/monitor/test_feed.py`

**Interfaces:**
- Consumes: `ExperimentContext`, `DataWindow`, `Observation` (Tasks 1–2).
- Produces: `MonitorFeed` ABC — class attrs `name: str`, `default_interval_s: float = 30.0`; abstract `evaluate(self, ctx: ExperimentContext, window: DataWindow, prior: list[Observation]) -> Observation | None`.

- [ ] **Step 1: Write the failing test**

```python
# tests/monitor/test_feed.py
from lightfall.monitor.data_window import DataWindow
from lightfall.monitor.feed import MonitorFeed
from lightfall.monitor.models import ExperimentContext, Observation


class _AlwaysWarn(MonitorFeed):
    name = "always_warn"
    default_interval_s = 15.0

    def evaluate(self, ctx, window, prior):
        return Observation(
            severity="warn", feed_name=self.name, run_uid=window.run_uid,
            title="t", message="m", state_key=f"{self.name}:x",
        )


def test_feed_subclass_evaluates():
    feed = _AlwaysWarn()
    obs = feed.evaluate(ExperimentContext.default(), DataWindow(run_uid="u1"), [])
    assert obs is not None and obs.feed_name == "always_warn"
    assert feed.default_interval_s == 15.0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=src .venv/Scripts/python -m pytest tests/monitor/test_feed.py -v`
Expected: FAIL — `ModuleNotFoundError`.

- [ ] **Step 3: Write minimal implementation**

```python
# src/lightfall/monitor/feed.py
"""MonitorFeed: the pluggable unit of judgment.

A feed is deterministic and cheap — it JUDGES reduced signals against
experiment intent. It must not perform data reduction (see spec §Goal).
``prior`` is the run's already-surfaced observations, so a feed can express
"low and not improving" without ad-hoc state."""

from __future__ import annotations

from abc import ABC, abstractmethod

from lightfall.monitor.data_window import DataWindow
from lightfall.monitor.models import ExperimentContext, Observation


class MonitorFeed(ABC):
    name: str = "feed"
    default_interval_s: float = 30.0

    @abstractmethod
    def evaluate(
        self,
        ctx: ExperimentContext,
        window: DataWindow,
        prior: list[Observation],
    ) -> Observation | None:
        """Return an Observation to surface, or None if nothing to report."""
        ...
```

- [ ] **Step 4: Run test to verify it passes**

Run: `PYTHONPATH=src .venv/Scripts/python -m pytest tests/monitor/test_feed.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/lightfall/monitor/feed.py tests/monitor/test_feed.py
git commit -m "feat(monitor): MonitorFeed ABC"
```

---

### Task 4: `MonitorPlugin` (mirror `AgentPlugin`)

**Files:**
- Create: `src/lightfall/monitor/monitor_plugin.py`
- Test: `tests/monitor/test_monitor_plugin.py`

**Interfaces:**
- Consumes: `PluginType` (`lightfall.plugins.types`), `MonitorFeed`.
- Produces: `MonitorPlugin(PluginType)` — `type_name="monitor"`, `is_singleton=True`; abstract `name`, `description`; properties `display_name`, `category="general"`, `enabled_by_default=True`, `priority=100`; `create_feeds() -> list[MonitorFeed]`; `get_introspection_data()`.

- [ ] **Step 1: Write the failing test**

```python
# tests/monitor/test_monitor_plugin.py
from lightfall.monitor.feed import MonitorFeed
from lightfall.monitor.monitor_plugin import MonitorPlugin


class _Feed(MonitorFeed):
    name = "f1"
    def evaluate(self, ctx, window, prior):
        return None


class _Plugin(MonitorPlugin):
    @property
    def name(self): return "demo"
    @property
    def description(self): return "demo monitor"
    def create_feeds(self): return [_Feed()]


def test_monitor_plugin_defaults_and_feeds():
    p = _Plugin()
    assert p.type_name == "monitor"
    assert p.is_singleton is True
    assert p.enabled_by_default is True
    assert p.priority == 100
    assert p.display_name == "Demo"
    feeds = p.create_feeds()
    assert len(feeds) == 1 and feeds[0].name == "f1"
    intro = p.get_introspection_data()
    assert intro["type"] == "monitor" and intro["feed_count"] == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=src .venv/Scripts/python -m pytest tests/monitor/test_monitor_plugin.py -v`
Expected: FAIL — `ModuleNotFoundError`.

- [ ] **Step 3: Write minimal implementation**

```python
# src/lightfall/monitor/monitor_plugin.py
"""Plugin type for contributing MonitorFeeds. Mirrors AgentPlugin so
behaviour and settings are predictable (see src/lightfall/plugins/agent_plugin.py)."""

from __future__ import annotations

from abc import abstractmethod
from typing import Any, ClassVar

from lightfall.monitor.feed import MonitorFeed
from lightfall.plugins.types import PluginType


class MonitorPlugin(PluginType):
    """Contributes one or more MonitorFeeds when enabled."""

    type_name: ClassVar[str] = "monitor"
    is_singleton: ClassVar[bool] = True

    @property
    @abstractmethod
    def name(self) -> str:
        """Unique plugin identifier (≤64 chars, lowercase + _/-)."""
        ...

    @property
    @abstractmethod
    def description(self) -> str:
        """One-line description shown in the settings UI."""
        ...

    @property
    def display_name(self) -> str:
        return self.name.replace("_", " ").title()

    @property
    def category(self) -> str:
        return "general"

    @property
    def enabled_by_default(self) -> bool:
        return True

    @property
    def priority(self) -> int:
        return 100

    @abstractmethod
    def create_feeds(self) -> list[MonitorFeed]:
        """Return the MonitorFeed instances this plugin contributes."""
        ...

    def get_introspection_data(self) -> dict[str, Any]:
        return {
            "type": self.type_name,
            "name": self.name,
            "display_name": self.display_name,
            "description": self.description,
            "category": self.category,
            "enabled_by_default": self.enabled_by_default,
            "priority": self.priority,
            "feed_count": len(self.create_feeds()),
            "class": self.__class__.__name__,
            "module": self.__class__.__module__,
        }
```

- [ ] **Step 4: Run test to verify it passes**

Run: `PYTHONPATH=src .venv/Scripts/python -m pytest tests/monitor/test_monitor_plugin.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/lightfall/monitor/monitor_plugin.py tests/monitor/test_monitor_plugin.py
git commit -m "feat(monitor): MonitorPlugin type (mirrors AgentPlugin)"
```

---

### Task 5: `MonitorRegistry` (mirror `AgentRegistry`)

**Files:**
- Create: `src/lightfall/monitor/registry.py`
- Test: `tests/monitor/test_registry.py`

**Interfaces:**
- Consumes: `MonitorPlugin`, `MonitorFeed`, `PreferencesManager` (read-only).
- Produces: `MonitorRegistry` singleton — `get_instance()`, `reset_instance()`, `register(plugin)`, `get_plugins()`, `enabled_plugins()` (opt-out via prefs `disabled_monitor_plugins` / `forced_enabled_monitor_plugins`, sorted by priority), `enabled_feeds()` (cached flatten of enabled plugins' `create_feeds()`).

- [ ] **Step 1: Write the failing test**

```python
# tests/monitor/test_registry.py
import pytest
from lightfall.monitor.feed import MonitorFeed
from lightfall.monitor.monitor_plugin import MonitorPlugin
from lightfall.monitor.registry import (
    DISABLED_MONITORS_PREF, MonitorRegistry,
)


class _Feed(MonitorFeed):
    def __init__(self, name): self.name = name
    def evaluate(self, ctx, window, prior): return None


def _plugin(name, enabled=True):
    class _P(MonitorPlugin):
        @property
        def name(self): return name
        @property
        def description(self): return name
        @property
        def enabled_by_default(self): return enabled
        def create_feeds(self): return [_Feed(f"{name}_feed")]
    return _P()


@pytest.fixture(autouse=True)
def _reset():
    MonitorRegistry.reset_instance()
    yield
    MonitorRegistry.reset_instance()


def test_enabled_plugins_respects_opt_out(monkeypatch):
    reg = MonitorRegistry.get_instance()
    reg.register(_plugin("a"))
    reg.register(_plugin("b"))
    # Pretend "a" is user-disabled.
    monkeypatch.setattr(reg, "_read_list_pref",
                        lambda key: ["a"] if key == DISABLED_MONITORS_PREF else [])
    names = [p.name for p in reg.enabled_plugins()]
    assert names == ["b"]


def test_enabled_feeds_flattens(monkeypatch):
    reg = MonitorRegistry.get_instance()
    reg.register(_plugin("a"))
    monkeypatch.setattr(reg, "_read_list_pref", lambda key: [])
    feeds = reg.enabled_feeds()
    assert [f.name for f in feeds] == ["a_feed"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=src .venv/Scripts/python -m pytest tests/monitor/test_registry.py -v`
Expected: FAIL — `ModuleNotFoundError`.

- [ ] **Step 3: Write minimal implementation**

```python
# src/lightfall/monitor/registry.py
"""Singleton registry of MonitorPlugins. Mirrors
src/lightfall/ui/panels/claude/agent_registry.py (opt-out preference model)."""

from __future__ import annotations

import threading
from typing import TYPE_CHECKING

from lightfall.utils.logging import logger

if TYPE_CHECKING:
    from lightfall.monitor.feed import MonitorFeed
    from lightfall.monitor.monitor_plugin import MonitorPlugin

DISABLED_MONITORS_PREF = "disabled_monitor_plugins"
FORCED_ENABLED_MONITORS_PREF = "forced_enabled_monitor_plugins"


class MonitorRegistry:
    _instance: MonitorRegistry | None = None
    _lock = threading.Lock()

    def __init__(self) -> None:
        self._plugins: dict[str, MonitorPlugin] = {}
        self._feed_cache: dict[str, list[MonitorFeed]] = {}

    @classmethod
    def get_instance(cls) -> MonitorRegistry:
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    @classmethod
    def reset_instance(cls) -> None:
        with cls._lock:
            cls._instance = None

    def register(self, plugin: MonitorPlugin) -> None:
        if plugin.name in self._plugins:
            logger.warning("monitor plugin '{}' already registered, replacing", plugin.name)
        self._plugins[plugin.name] = plugin
        self._feed_cache.pop(plugin.name, None)
        logger.debug("Registered monitor plugin: {} (priority={})", plugin.name, plugin.priority)

    def unregister(self, name: str) -> bool:
        self._feed_cache.pop(name, None)
        return self._plugins.pop(name, None) is not None

    def get_plugins(self) -> list[MonitorPlugin]:
        return list(self._plugins.values())

    def _read_list_pref(self, key: str) -> list[str]:
        try:
            from lightfall.ui.preferences.manager import PreferencesManager
            value = PreferencesManager.get_instance().get(key)
            if isinstance(value, list):
                return value
        except Exception as e:  # noqa: BLE001
            logger.debug("Could not load {}: {}", key, e)
        return []

    def enabled_plugins(self) -> list[MonitorPlugin]:
        disabled = set(self._read_list_pref(DISABLED_MONITORS_PREF))
        forced = set(self._read_list_pref(FORCED_ENABLED_MONITORS_PREF))
        result = [
            p for p in self._plugins.values()
            if p.name not in disabled and (p.enabled_by_default or p.name in forced)
        ]
        result.sort(key=lambda p: p.priority)
        return result

    def enabled_feeds(self) -> list[MonitorFeed]:
        feeds: list[MonitorFeed] = []
        for plugin in self.enabled_plugins():
            cached = self._feed_cache.get(plugin.name)
            if cached is None:
                cached = plugin.create_feeds()
                self._feed_cache[plugin.name] = cached
            feeds.extend(cached)
        return feeds
```

- [ ] **Step 4: Run test to verify it passes**

Run: `PYTHONPATH=src .venv/Scripts/python -m pytest tests/monitor/test_registry.py -v`
Expected: PASS (2 tests).

- [ ] **Step 5: Commit**

```bash
git add src/lightfall/monitor/registry.py tests/monitor/test_registry.py
git commit -m "feat(monitor): MonitorRegistry (mirrors AgentRegistry)"
```

---

### Task 6: Loader + manifest wiring for the `"monitor"` type

**Files:**
- Modify: `src/lightfall/main.py:589` (type registration block)
- Modify: `src/lightfall/plugins/loader.py` (dispatch branch after `:644`)
- Test: `tests/monitor/test_loader_wiring.py`

**Interfaces:**
- Consumes: `MonitorPlugin`, `MonitorRegistry`, `PluginLoader`.
- Produces: the loader registers any `type_name="monitor"` plugin instance into `MonitorRegistry`.

- [ ] **Step 1: Write the failing test**

```python
# tests/monitor/test_loader_wiring.py
from lightfall.monitor.feed import MonitorFeed
from lightfall.monitor.monitor_plugin import MonitorPlugin
from lightfall.monitor.registry import MonitorRegistry
from lightfall.plugins.loader import PluginLoader
from lightfall.plugins.registry import PluginRegistry


class _Feed(MonitorFeed):
    name = "wf"
    def evaluate(self, ctx, window, prior): return None


class _Plugin(MonitorPlugin):
    @property
    def name(self): return "wired"
    @property
    def description(self): return "wired"
    def create_feeds(self): return [_Feed()]


def test_loader_registers_monitor_into_monitor_registry():
    MonitorRegistry.reset_instance()
    loader = PluginLoader(PluginRegistry())
    loader.register_plugin_type("monitor", MonitorPlugin)
    # Simulate a loaded plugin instance going through type-registry dispatch.
    from lightfall.plugins.loader import PluginInfo  # dataclass holding instance + type_name
    info = PluginInfo(name="wired", type_name="monitor", instance=_Plugin())
    loader._register_with_type_registry(info)
    assert MonitorRegistry.get_instance().get_plugins()[0].name == "wired"
```

> Note for implementer: confirm the exact constructor/fields of `PluginInfo` in `loader.py` (it already exists; mirror how the `"agent"` branch test or existing loader tests build it). If `PluginInfo` requires more fields, set them; only `name`, `type_name`, `instance` are used by the dispatch branch.

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=src .venv/Scripts/python -m pytest tests/monitor/test_loader_wiring.py -v`
Expected: FAIL — the dispatch has no `"monitor"` branch, so `MonitorRegistry` stays empty.

- [ ] **Step 3: Add the dispatch branch in `loader.py`**

After the `"agent"` branch (which ends at `loader.py:644`), add:

```python
        elif plugin_info.type_name == "monitor":
            try:
                from lightfall.monitor.monitor_plugin import MonitorPlugin
                from lightfall.monitor.registry import MonitorRegistry

                instance = plugin_info.instance
                if not isinstance(instance, MonitorPlugin):
                    logger.error(
                        "Monitor plugin '{}' class {} is not a MonitorPlugin subclass; skipping",
                        plugin_info.name, type(instance).__name__,
                    )
                else:
                    MonitorRegistry.get_instance().register(instance)
                    logger.debug("Registered monitor plugin '{}' with MonitorRegistry", instance.name)
            except ImportError:
                logger.debug("MonitorRegistry not available, skipping monitor registration")
```

- [ ] **Step 4: Register the type in `main.py`**

In `main.py`, add after the `"agent"` registration (`main.py:589`):

```python
    from lightfall.monitor.monitor_plugin import MonitorPlugin
    loader.register_plugin_type("monitor", MonitorPlugin)
```

(Place the import with the other plugin-type imports near the top of that function, matching the existing style.)

- [ ] **Step 5: Run test to verify it passes**

Run: `PYTHONPATH=src .venv/Scripts/python -m pytest tests/monitor/test_loader_wiring.py -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/lightfall/plugins/loader.py src/lightfall/main.py tests/monitor/test_loader_wiring.py
git commit -m "feat(monitor): wire monitor plugin type into loader + main"
```

---

### Task 7: `RateLimiter`

**Files:**
- Create: `src/lightfall/monitor/rate_limiter.py`
- Test: `tests/monitor/test_rate_limiter.py`

**Interfaces:**
- Consumes: `Observation`.
- Produces: `RateLimiter` — `should_surface(obs) -> bool` (True only when `(state_key -> severity)` changes), `reset()`.

- [ ] **Step 1: Write the failing test**

```python
# tests/monitor/test_rate_limiter.py
from lightfall.monitor.models import Observation
from lightfall.monitor.rate_limiter import RateLimiter


def _obs(state_key, severity="warn"):
    return Observation(severity=severity, feed_name="f", run_uid="u",
                       title="t", message="m", state_key=state_key)


def test_surfaces_once_per_state_then_suppresses():
    rl = RateLimiter()
    assert rl.should_surface(_obs("low")) is True
    assert rl.should_surface(_obs("low")) is False           # same condition + severity
    assert rl.should_surface(_obs("low", "critical")) is True  # severity escalated
    assert rl.should_surface(_obs("other")) is True            # different condition


def test_reset_clears_state():
    rl = RateLimiter()
    rl.should_surface(_obs("low"))
    rl.reset()
    assert rl.should_surface(_obs("low")) is True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=src .venv/Scripts/python -m pytest tests/monitor/test_rate_limiter.py -v`
Expected: FAIL — `ModuleNotFoundError`.

- [ ] **Step 3: Write minimal implementation**

```python
# src/lightfall/monitor/rate_limiter.py
"""Surface an observation only when its (state_key, severity) changes, so a
standing condition is announced once, not every tick."""

from __future__ import annotations

from lightfall.monitor.models import Observation


class RateLimiter:
    def __init__(self) -> None:
        self._state: dict[str, str] = {}

    def should_surface(self, obs: Observation) -> bool:
        if self._state.get(obs.state_key) == obs.severity:
            return False
        self._state[obs.state_key] = obs.severity
        return True

    def reset(self) -> None:
        self._state.clear()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `PYTHONPATH=src .venv/Scripts/python -m pytest tests/monitor/test_rate_limiter.py -v`
Expected: PASS (2 tests).

- [ ] **Step 5: Commit**

```bash
git add src/lightfall/monitor/rate_limiter.py tests/monitor/test_rate_limiter.py
git commit -m "feat(monitor): RateLimiter state-change dedupe"
```

---

### Task 8: `RollingBuffer`

**Files:**
- Create: `src/lightfall/monitor/buffer.py`
- Test: `tests/monitor/test_buffer.py`

**Interfaces:**
- Consumes: `DataWindow`.
- Produces: `RollingBuffer` — `__call__(name, doc)` (thread-safe Bluesky callback), `snapshot(now: float) -> DataWindow`, `active_uid: str`, `stopped: bool`. Resets on `start`; computes `age_s = now - last_timestamp`.

- [ ] **Step 1: Write the failing test**

```python
# tests/monitor/test_buffer.py
from lightfall.monitor.buffer import RollingBuffer


def test_buffer_accumulates_and_snapshots():
    buf = RollingBuffer()
    buf("start", {"uid": "u1", "time": 100.0})
    buf("descriptor", {"name": "primary", "data_keys": {"det": {"shape": []}}})
    buf("event", {"seq_num": 1, "time": 101.0, "data": {"det": 5.0}})
    buf("event", {"seq_num": 2, "time": 102.0, "data": {"det": 6.0}})

    win = buf.snapshot(now=105.0)
    assert win.run_uid == "u1"
    assert win.event_count == 2
    assert win.series("det") == [5.0, 6.0]
    assert win.latest("det") == 6.0
    assert win.age_s == 3.0  # 105 - 102


def test_start_resets_previous_run():
    buf = RollingBuffer()
    buf("start", {"uid": "u1", "time": 0.0})
    buf("event", {"seq_num": 1, "time": 1.0, "data": {"det": 1.0}})
    buf("start", {"uid": "u2", "time": 10.0})
    win = buf.snapshot(now=10.0)
    assert win.run_uid == "u2"
    assert win.event_count == 0


def test_snapshot_with_no_events_has_none_age():
    buf = RollingBuffer()
    buf("start", {"uid": "u1", "time": 0.0})
    assert buf.snapshot(now=5.0).age_s is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=src .venv/Scripts/python -m pytest tests/monitor/test_buffer.py -v`
Expected: FAIL — `ModuleNotFoundError`.

- [ ] **Step 3: Write minimal implementation**

```python
# src/lightfall/monitor/buffer.py
"""Thread-safe rolling buffer of inline event scalars for the monitor.

Subscribe-compatible (``__call__(name, doc)``) like LiveDataBuffer
(src/lightfall/acquire/buffer.py), but lock-guarded so the GUI thread can
take a consistent snapshot while the engine worker thread appends. Holds
only inline scalar columns — never external image assets."""

from __future__ import annotations

import threading
from collections import deque
from typing import Any

from lightfall.monitor.data_window import DataWindow


class RollingBuffer:
    def __init__(self, max_points: int = 10000) -> None:
        self._max = max_points
        self._lock = threading.Lock()
        self._uid: str = ""
        self._stopped: bool = False
        self._fields: dict[str, deque[Any]] = {}
        self._seq: deque[int] = deque(maxlen=max_points)
        self._ts: deque[float] = deque(maxlen=max_points)

    def __call__(self, name: str, doc: dict[str, Any]) -> None:
        with self._lock:
            if name == "start":
                self._reset(doc)
            elif name == "event":
                self._append_event(doc)
            elif name == "stop":
                self._stopped = True

    def _reset(self, doc: dict[str, Any]) -> None:
        self._uid = doc.get("uid", "")
        self._stopped = False
        self._fields.clear()
        self._seq.clear()
        self._ts.clear()

    def _append_event(self, doc: dict[str, Any]) -> None:
        self._seq.append(int(doc.get("seq_num", 0)))
        self._ts.append(float(doc.get("time", 0.0)))
        for key, value in (doc.get("data") or {}).items():
            buf = self._fields.get(key)
            if buf is None:
                buf = deque(maxlen=self._max)
                self._fields[key] = buf
            buf.append(value)

    def snapshot(self, now: float) -> DataWindow:
        with self._lock:
            ts = list(self._ts)
            age = (now - ts[-1]) if ts else None
            return DataWindow(
                run_uid=self._uid,
                events={k: list(v) for k, v in self._fields.items()},
                seq_nums=list(self._seq),
                timestamps=ts,
                event_count=len(ts),
                age_s=age,
            )

    @property
    def active_uid(self) -> str:
        return self._uid

    @property
    def stopped(self) -> bool:
        return self._stopped
```

- [ ] **Step 4: Run test to verify it passes**

Run: `PYTHONPATH=src .venv/Scripts/python -m pytest tests/monitor/test_buffer.py -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add src/lightfall/monitor/buffer.py tests/monitor/test_buffer.py
git commit -m "feat(monitor): thread-safe RollingBuffer with snapshot"
```

---

### Task 9: `MonitorScheduler`

**Files:**
- Create: `src/lightfall/monitor/scheduler.py`
- Test: `tests/monitor/test_scheduler.py`

**Interfaces:**
- Consumes: an engine with `subscribe(cb)->int` / `unsubscribe(token)` / `sigAbort` / `sigException` (real: `lightfall.acquire.engine.get_engine()`); `MonitorRegistry`, `RollingBuffer`, `RateLimiter`, `ExperimentContext`, `Observation`; `QThreadFuture`, `invoke_in_main_thread`.
- Produces: `MonitorScheduler(QObject)` — Qt signal `observation = Signal(object)`; `__init__(engine, registry=None, tick_granularity_s=5.0, clock=time.monotonic, eval_async=True, parent=None)`; `start()`, `stop()`; internal `_on_document(name, doc)`, `_arm(uid, ctx)`, `_disarm()`, `_tick()`, `_on_observation(obs)`.

> Testability: `eval_async=False` runs feeds inline (no QThread); `clock` is injectable for deterministic due-checks. `_on_document`/`_tick` are exercised directly in tests with a fake engine.

- [ ] **Step 1: Write the failing test**

```python
# tests/monitor/test_scheduler.py
import pytest
from PySide6.QtWidgets import QApplication

from lightfall.monitor.feed import MonitorFeed
from lightfall.monitor.models import Observation
from lightfall.monitor.registry import MonitorRegistry
from lightfall.monitor.scheduler import MonitorScheduler


@pytest.fixture(scope="module")
def _app():
    return QApplication.instance() or QApplication([])


class _FakeEngine:
    def __init__(self):
        self._cb = None
        self.sigAbort = _Sig(); self.sigException = _Sig()
    def subscribe(self, cb):
        self._cb = cb; return 1
    def unsubscribe(self, token): self._cb = None
    def emit(self, name, doc):
        if self._cb: self._cb(name, doc)


class _Sig:  # minimal stand-in for a Qt signal
    def connect(self, *_a, **_k): pass


class _CountFeed(MonitorFeed):
    name = "count"
    default_interval_s = 0.0  # always due
    def evaluate(self, ctx, window, prior):
        if window.latest("det") == 0:
            return Observation(severity="warn", feed_name=self.name,
                               run_uid=window.run_uid, title="zero",
                               message="det=0", state_key="count:zero")
        return None


@pytest.fixture
def _registry():
    MonitorRegistry.reset_instance()
    reg = MonitorRegistry.get_instance()

    class _P(  # noqa: N801
        __import__("lightfall.monitor.monitor_plugin", fromlist=["MonitorPlugin"]).MonitorPlugin
    ):
        @property
        def name(self): return "count_plugin"
        @property
        def description(self): return "d"
        def create_feeds(self): return [_CountFeed()]

    reg.register(_P())
    reg._read_list_pref = lambda key: []  # no prefs in test
    yield reg
    MonitorRegistry.reset_instance()


def test_scheduler_emits_rate_limited_observation(_app, _registry):
    eng = _FakeEngine()
    t = [0.0]
    sched = MonitorScheduler(eng, registry=_registry, clock=lambda: t[0], eval_async=False)
    received = []
    sched.observation.connect(received.append)
    sched.start()

    eng.emit("start", {"uid": "u1", "time": 0.0})
    eng.emit("event", {"seq_num": 1, "time": 0.0, "data": {"det": 0}})
    sched._tick()  # would be the QTimer; called directly here
    sched._tick()  # second tick: same condition -> suppressed by rate limiter

    assert len(received) == 1
    assert received[0].state_key == "count:zero"


def test_disarm_on_stop_stops_emitting(_app, _registry):
    eng = _FakeEngine()
    sched = MonitorScheduler(eng, registry=_registry, clock=lambda: 0.0, eval_async=False)
    received = []
    sched.observation.connect(received.append)
    sched.start()
    eng.emit("start", {"uid": "u1", "time": 0.0})
    eng.emit("stop", {"run_start": "u1"})
    sched._tick()
    assert received == []  # disarmed
```

> Implementer note: `_arm`/`_disarm` are invoked via `invoke_in_main_thread` in production (the document callback runs on the engine worker thread). In tests we call `_tick` directly, and `_on_document` runs inline on the test thread, so `invoke_in_main_thread` executes synchronously (it runs inline when already on the main thread — see `utils/threads.py:830`).

- [ ] **Step 2: Run test to verify it fails**

Run: `QT_QPA_PLATFORM=offscreen PYTHONPATH=src .venv/Scripts/python -m pytest tests/monitor/test_scheduler.py -v`
Expected: FAIL — `ModuleNotFoundError`.

- [ ] **Step 3: Write minimal implementation**

```python
# src/lightfall/monitor/scheduler.py
"""Drives MonitorFeed evaluation during a run.

- Subscribes to the engine document stream (worker thread) and feeds a
  thread-safe RollingBuffer; arms on 'start' (uid from the start doc, since
  sigStart is payload-less), disarms on 'stop'/abort.
- On a QTimer (GUI thread) evaluates each due enabled feed OFF the UI thread
  via QThreadFuture, rate-limits results, and emits `observation`.
Never blocks the engine: the document callback only appends + marshals
arm/disarm to the GUI thread."""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any

from PySide6.QtCore import QObject, QTimer, Signal

from lightfall.monitor.buffer import RollingBuffer
from lightfall.monitor.models import ExperimentContext, Observation
from lightfall.monitor.rate_limiter import RateLimiter
from lightfall.monitor.registry import MonitorRegistry
from lightfall.utils.logging import logger
from lightfall.utils.threads import QThreadFuture, invoke_in_main_thread


class MonitorScheduler(QObject):
    observation = Signal(object)  # Observation

    def __init__(
        self,
        engine: Any,
        registry: MonitorRegistry | None = None,
        tick_granularity_s: float = 5.0,
        clock: Callable[[], float] = time.monotonic,
        eval_async: bool = True,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._engine = engine
        self._registry = registry or MonitorRegistry.get_instance()
        self._clock = clock
        self._eval_async = eval_async
        self._buffer = RollingBuffer()
        self._rate = RateLimiter()
        self._prior: list[Observation] = []
        self._ctx: ExperimentContext = ExperimentContext.default()
        self._last_eval: dict[str, float] = {}
        self._active = False
        self._token: int | None = None
        self._timer = QTimer(self)
        self._timer.setInterval(int(tick_granularity_s * 1000))
        self._timer.timeout.connect(self._tick)

    def start(self) -> None:
        if self._token is None:
            self._token = self._engine.subscribe(self._on_document)
        self._engine.sigAbort.connect(self._disarm)
        self._engine.sigException.connect(lambda _e: self._disarm())

    def stop(self) -> None:
        if self._token is not None:
            self._engine.unsubscribe(self._token)
            self._token = None
        self._timer.stop()

    # --- engine worker thread ---
    def _on_document(self, name: str, doc: dict[str, Any]) -> None:
        self._buffer(name, doc)
        if name == "start":
            ctx = ExperimentContext.from_start_doc(doc)
            invoke_in_main_thread(self._arm, doc.get("uid", ""), ctx)
        elif name == "stop":
            invoke_in_main_thread(self._disarm)

    # --- GUI thread ---
    def _arm(self, uid: str, ctx: ExperimentContext) -> None:
        self._ctx = ctx
        self._prior = []
        self._rate.reset()
        self._last_eval = {}
        self._active = True
        self._timer.start()

    def _disarm(self) -> None:
        self._active = False
        self._timer.stop()

    def _tick(self) -> None:
        if not self._active:
            return
        now = self._clock()
        window = self._buffer.snapshot(now=time.time())
        for feed in self._registry.enabled_feeds():
            last = self._last_eval.get(feed.name, float("-inf"))
            if now - last < feed.default_interval_s:
                continue
            self._last_eval[feed.name] = now
            self._dispatch(feed, window)

    def _dispatch(self, feed: Any, window: Any) -> None:
        prior = list(self._prior)
        if self._eval_async:
            QThreadFuture(
                self._safe_eval, feed, window, prior,
                callback_slot=self._on_observation,
                key=f"monitor:{feed.name}",
            ).start()
        else:
            self._on_observation(self._safe_eval(feed, window, prior))

    def _safe_eval(self, feed: Any, window: Any, prior: list[Observation]) -> Observation | None:
        try:
            return feed.evaluate(self._ctx, window, prior)
        except Exception:  # noqa: BLE001 — advisory; never crash the app
            logger.exception("monitor feed '{}' raised during evaluate", getattr(feed, "name", "?"))
            return None

    def _on_observation(self, obs: Observation | None) -> None:
        if obs is None:
            return
        if not obs.ts:
            obs.ts = time.time()
        if self._rate.should_surface(obs):
            self._prior.append(obs)
            self.observation.emit(obs)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `QT_QPA_PLATFORM=offscreen PYTHONPATH=src .venv/Scripts/python -m pytest tests/monitor/test_scheduler.py -v`
Expected: PASS (2 tests).

- [ ] **Step 5: Commit**

```bash
git add src/lightfall/monitor/scheduler.py tests/monitor/test_scheduler.py
git commit -m "feat(monitor): MonitorScheduler (arm/disarm, tick, rate-limited emit)"
```

---

### Task 10: Acquisition-health feed + plugin + manifest entry

**Files:**
- Create: `src/lightfall/monitor/feeds/__init__.py` (empty)
- Create: `src/lightfall/monitor/feeds/acquisition_health.py`
- Modify: `src/lightfall/plugins/builtin_manifest.py` (add monitor entry)
- Test: `tests/monitor/test_acquisition_health.py`

**Interfaces:**
- Consumes: `MonitorFeed`, `MonitorPlugin`, `ExperimentContext`, `DataWindow`, `Observation`.
- Produces: `AcquisitionHealthFeed(MonitorFeed)` (`name="acquisition_health"`); `AcquisitionHealthMonitorPlugin(MonitorPlugin)` (`name="acquisition_health"`). Config (via `ctx.for_feed("acquisition_health")`): `count_field: str`, `min_rate: float=0.0`, `min_samples: int=3`, `stall_after_s: float=60.0`.

- [ ] **Step 1: Write the failing test**

```python
# tests/monitor/test_acquisition_health.py
from lightfall.monitor.data_window import DataWindow
from lightfall.monitor.feeds.acquisition_health import AcquisitionHealthFeed
from lightfall.monitor.models import ExperimentContext


def _ctx():
    return ExperimentContext(feed_config={"acquisition_health": {
        "count_field": "det", "min_rate": 1.0, "min_samples": 3, "stall_after_s": 60.0,
    }})


def test_warns_on_count_rate_collapse():
    win = DataWindow(run_uid="u", events={"det": [0.0, 0.0, 0.0]},
                     event_count=3, age_s=1.0)
    obs = AcquisitionHealthFeed().evaluate(_ctx(), win, [])
    assert obs is not None and obs.state_key == "acquisition_health:low_rate"
    assert obs.severity == "warn"


def test_warns_on_stall():
    win = DataWindow(run_uid="u", events={"det": [10.0, 10.0, 10.0]},
                     event_count=3, age_s=120.0)
    obs = AcquisitionHealthFeed().evaluate(_ctx(), win, [])
    assert obs is not None and obs.state_key == "acquisition_health:stalled"


def test_healthy_returns_none():
    win = DataWindow(run_uid="u", events={"det": [10.0, 11.0, 12.0]},
                     event_count=3, age_s=1.0)
    assert AcquisitionHealthFeed().evaluate(_ctx(), win, []) is None


def test_insufficient_samples_returns_none():
    win = DataWindow(run_uid="u", events={"det": [0.0]}, event_count=1, age_s=1.0)
    assert AcquisitionHealthFeed().evaluate(_ctx(), win, []) is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=src .venv/Scripts/python -m pytest tests/monitor/test_acquisition_health.py -v`
Expected: FAIL — `ModuleNotFoundError`.

- [ ] **Step 3: Write minimal implementation**

```python
# src/lightfall/monitor/feeds/acquisition_health.py
"""Beamline-agnostic acquisition-health feed.

Judges IOC-provided inline scalars only (no asset reads, no reduction):
detects a stalled run and count-rate collapse. Config via
ctx.for_feed("acquisition_health"): count_field, min_rate, min_samples,
stall_after_s."""

from __future__ import annotations

from lightfall.monitor.data_window import DataWindow
from lightfall.monitor.feed import MonitorFeed
from lightfall.monitor.models import ExperimentContext, Observation
from lightfall.monitor.monitor_plugin import MonitorPlugin

FEED_NAME = "acquisition_health"


class AcquisitionHealthFeed(MonitorFeed):
    name = FEED_NAME
    default_interval_s = 30.0

    def evaluate(
        self, ctx: ExperimentContext, window: DataWindow, prior: list[Observation]
    ) -> Observation | None:
        cfg = ctx.for_feed(FEED_NAME)
        stall_after = float(cfg.get("stall_after_s", 60.0))
        # Stall: events have stopped arriving while the run is active.
        if window.event_count > 0 and window.age_s is not None and window.age_s > stall_after:
            return Observation(
                severity="warn", feed_name=FEED_NAME, run_uid=window.run_uid,
                title="Acquisition stalled",
                message=f"No new events for {window.age_s:.0f}s (> {stall_after:.0f}s).",
                state_key=f"{FEED_NAME}:stalled",
                metrics={"age_s": float(window.age_s)},
                recommendation="Check the detector / shutter / plan progress.",
            )
        # Count-rate collapse.
        count_field = cfg.get("count_field")
        min_rate = float(cfg.get("min_rate", 0.0))
        min_samples = int(cfg.get("min_samples", 3))
        if count_field:
            series = [float(v) for v in window.series(count_field) if isinstance(v, (int, float))]
            if len(series) >= min_samples:
                recent = series[-min_samples:]
                mean = sum(recent) / len(recent)
                if mean < min_rate:
                    return Observation(
                        severity="warn", feed_name=FEED_NAME, run_uid=window.run_uid,
                        title="Count rate collapsed",
                        message=f"Mean of last {min_samples} '{count_field}' = "
                                f"{mean:.3g} < {min_rate:.3g}.",
                        state_key=f"{FEED_NAME}:low_rate",
                        metrics={"mean_rate": mean, "min_rate": min_rate},
                        recommendation="Check beam / shutter / sample alignment.",
                    )
        return None


class AcquisitionHealthMonitorPlugin(MonitorPlugin):
    @property
    def name(self) -> str:
        return FEED_NAME

    @property
    def description(self) -> str:
        return "Warns on stalled acquisition or count-rate collapse."

    @property
    def category(self) -> str:
        return "acquisition"

    def create_feeds(self) -> list[MonitorFeed]:
        return [AcquisitionHealthFeed()]
```

- [ ] **Step 4: Add the manifest entry**

In `src/lightfall/plugins/builtin_manifest.py`, after the agent entries block (`:257`) and before the panel entries (`:258`), add:

```python
        # Monitor plugins (proactive measurement feeds).
        PluginEntry(
            type_name="monitor",
            name="acquisition_health",
            import_path=(
                "lightfall.monitor.feeds.acquisition_health:"
                "AcquisitionHealthMonitorPlugin"
            ),
        ),
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `PYTHONPATH=src .venv/Scripts/python -m pytest tests/monitor/test_acquisition_health.py -v`
Expected: PASS (4 tests).

- [ ] **Step 6: Commit**

```bash
git add src/lightfall/monitor/feeds tests/monitor/test_acquisition_health.py src/lightfall/plugins/builtin_manifest.py
git commit -m "feat(monitor): acquisition-health feed + manifest entry"
```

---

### Task 11: `MonitorService` + Monitor panel + app wiring + Claude hand-off

This task delivers the user-visible end-to-end path. It has three deliverables that ship together (service, panel, wiring) because none is independently useful.

**Files:**
- Create: `src/lightfall/monitor/service.py`
- Create: `src/lightfall/ui/panels/monitor_panel.py`
- Create: `src/lightfall/ui/panels/plugins/monitor_panel_plugin.py`
- Modify: `src/lightfall/plugins/builtin_manifest.py` (Monitor panel entry)
- Modify: `src/lightfall/main.py` (`_setup_monitor`)
- Modify: `src/lightfall/ui/panels/claude_panel.py` (`submit_external_prompt`)
- Test: `tests/monitor/test_service.py`, `tests/ui/test_monitor_panel.py`

**Interfaces:**
- Produces:
  - `MonitorService(QObject)` singleton — `get_instance()`, `reset_instance()`, signal `observation = Signal(object)`, `set_window(win)`, `start()`, `recent_observations() -> list[Observation]`, `discuss_observation(obs)`, internal `_on_observation`, `_toast`.
  - `format_observation(obs) -> str` (pure, in `monitor_panel.py`).
  - `MonitorPanel(BasePanel)` — `add_observation(obs)`; `panel_metadata.id = "lightfall.panels.monitor"`.
  - `MonitorPanelPlugin(PanelPlugin)` — `name="monitor"`.
  - `ClaudePanel.submit_external_prompt(text: str) -> bool`.

- [ ] **Step 1: Write the failing service test**

```python
# tests/monitor/test_service.py
import pytest
from PySide6.QtWidgets import QApplication

from lightfall.monitor.models import Observation
from lightfall.monitor.service import MonitorService


@pytest.fixture(scope="module")
def _app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def _svc(_app, monkeypatch):
    MonitorService.reset_instance()
    # Avoid constructing a real scheduler/engine in the unit test.
    monkeypatch.setattr(MonitorService, "_build_scheduler", lambda self: None)
    svc = MonitorService.get_instance()
    yield svc
    MonitorService.reset_instance()


def test_recent_and_signal_on_observation(_svc):
    seen = []
    _svc.observation.connect(seen.append)
    obs = Observation(severity="info", feed_name="f", run_uid="u",
                      title="t", message="m", state_key="f:k")
    _svc._on_observation(obs)
    assert _svc.recent_observations()[-1] is obs
    assert seen == [obs]


def test_warn_triggers_toast(_svc, monkeypatch):
    calls = []
    monkeypatch.setattr(_svc, "_toast", lambda obs: calls.append(obs))
    warn = Observation(severity="warn", feed_name="f", run_uid="u",
                       title="t", message="m", state_key="f:k")
    _svc._on_observation(warn)
    assert calls == [warn]
```

- [ ] **Step 2: Run to verify it fails**

Run: `QT_QPA_PLATFORM=offscreen PYTHONPATH=src .venv/Scripts/python -m pytest tests/monitor/test_service.py -v`
Expected: FAIL — `ModuleNotFoundError`.

- [ ] **Step 3: Implement `MonitorService`**

```python
# src/lightfall/monitor/service.py
"""Always-on monitor service: owns the scheduler, keeps a recent-observation
log, raises toasts for warn/critical, and routes the "discuss in assistant"
hand-off to the reactive Claude agent."""

from __future__ import annotations

import threading
from collections import deque
from typing import TYPE_CHECKING

from PySide6.QtCore import QObject, Signal

from lightfall.monitor.models import Observation
from lightfall.utils.logging import logger

if TYPE_CHECKING:
    from lightfall.ui.mainwindow import LFMainWindow


class MonitorService(QObject):
    observation = Signal(object)  # Observation

    _instance: MonitorService | None = None
    _lock = threading.Lock()

    def __init__(self) -> None:
        super().__init__()
        self._recent: deque[Observation] = deque(maxlen=200)
        self._window: LFMainWindow | None = None
        self._scheduler = self._build_scheduler()
        if self._scheduler is not None:
            self._scheduler.observation.connect(self._on_observation)

    def _build_scheduler(self):
        from lightfall.acquire.engine import get_engine
        from lightfall.monitor.scheduler import MonitorScheduler
        return MonitorScheduler(get_engine(), parent=self)

    @classmethod
    def get_instance(cls) -> MonitorService:
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    @classmethod
    def reset_instance(cls) -> None:
        with cls._lock:
            cls._instance = None

    def set_window(self, window: LFMainWindow) -> None:
        self._window = window

    def start(self) -> None:
        if self._scheduler is not None:
            self._scheduler.start()

    def recent_observations(self) -> list[Observation]:
        return list(self._recent)

    def _on_observation(self, obs: Observation) -> None:
        self._recent.append(obs)
        if obs.severity in ("warn", "critical"):
            self._toast(obs)
        self.observation.emit(obs)

    def _toast(self, obs: Observation) -> None:
        try:
            from lightfall.ui.toast import ToastManager
            mgr = ToastManager.get_instance()
            if obs.severity == "critical":
                mgr.error(obs.title, obs.message)
            else:
                mgr.warning(obs.title, obs.message)
        except Exception:  # noqa: BLE001 — never let a toast failure break the run
            logger.exception("monitor toast failed")

    def discuss_observation(self, obs: Observation) -> None:
        win = self._window
        if win is None:
            logger.warning("discuss_observation: no main window")
            return
        try:
            win.activate_panel("lightfall.panels.claude")
            claude = win.get_panel("lightfall.panels.claude")
            if claude is not None and hasattr(claude, "submit_external_prompt"):
                claude.submit_external_prompt(self._discuss_prompt(obs))
        except Exception:  # noqa: BLE001
            logger.exception("discuss_observation hand-off failed")

    @staticmethod
    def _discuss_prompt(obs: Observation) -> str:
        rec = f"\nSuggested: {obs.recommendation}" if obs.recommendation else ""
        return (
            f"The proactive monitor flagged a [{obs.severity}] from "
            f"'{obs.feed_name}' on run {obs.run_uid}:\n"
            f"{obs.title} — {obs.message}\nMetrics: {obs.metrics}{rec}\n"
            f"Help me investigate this."
        )
```

- [ ] **Step 4: Run service test to verify it passes**

Run: `QT_QPA_PLATFORM=offscreen PYTHONPATH=src .venv/Scripts/python -m pytest tests/monitor/test_service.py -v`
Expected: PASS (2 tests).

- [ ] **Step 5: Write the failing panel test**

```python
# tests/ui/test_monitor_panel.py
import pytest
from PySide6.QtWidgets import QApplication

from lightfall.monitor.models import Observation
from lightfall.monitor.service import MonitorService


@pytest.fixture(scope="module")
def _app():
    return QApplication.instance() or QApplication([])


def test_format_observation_includes_title_and_message():
    from lightfall.ui.panels.monitor_panel import format_observation
    obs = Observation(severity="warn", feed_name="health", run_uid="u",
                      title="Stalled", message="no events", state_key="k",
                      recommendation="check shutter")
    text = format_observation(obs)
    assert "Stalled" in text and "no events" in text and "check shutter" in text


def test_panel_adds_observation_row(_app, monkeypatch):
    monkeypatch.setattr(MonitorService, "_build_scheduler", lambda self: None)
    MonitorService.reset_instance()
    from lightfall.ui.panels.monitor_panel import MonitorPanel
    panel = MonitorPanel()
    before = panel.row_count()
    panel.add_observation(Observation(severity="info", feed_name="f", run_uid="u",
                                      title="t", message="m", state_key="k"))
    assert panel.row_count() == before + 1
    MonitorService.reset_instance()
```

- [ ] **Step 6: Run to verify it fails**

Run: `QT_QPA_PLATFORM=offscreen PYTHONPATH=src .venv/Scripts/python -m pytest tests/ui/test_monitor_panel.py -v`
Expected: FAIL — `ModuleNotFoundError`.

- [ ] **Step 7: Implement the Monitor panel**

```python
# src/lightfall/ui/panels/monitor_panel.py
"""Monitor panel: per-run, severity-coded log of monitor observations, with
a 'Discuss in assistant' hand-off to the reactive Claude agent."""

from __future__ import annotations

from typing import ClassVar

from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

from lightfall.monitor.models import Observation
from lightfall.monitor.service import MonitorService
from lightfall.ui.panels.base import BasePanel, PanelMetadata

_SEVERITY_COLOR = {"info": "#6b7280", "warn": "#d97706", "critical": "#dc2626"}


def format_observation(obs: Observation) -> str:
    rec = f"  ·  {obs.recommendation}" if obs.recommendation else ""
    return f"[{obs.severity.upper()}] {obs.title} — {obs.message}{rec}"


class MonitorPanel(BasePanel):
    panel_metadata: ClassVar[PanelMetadata] = PanelMetadata(
        id="lightfall.panels.monitor",
        name="Monitor",
        description="Proactive feedback about the running measurement.",
        icon="activity",
        category="Data",
        default_area="right",
        proactive_init=False,  # stay lazy until opened
    )

    def _setup_ui(self) -> None:
        self._rows = QVBoxLayout()
        self._rows.addStretch(1)
        container = QWidget()
        container.setLayout(self._rows)
        self._layout.addWidget(container)

        svc = MonitorService.get_instance()
        for obs in svc.recent_observations():
            self.add_observation(obs)
        svc.observation.connect(self.add_observation)

    def add_observation(self, obs: Observation) -> None:
        svc = MonitorService.get_instance()
        row = QFrame()
        row.setObjectName("monitorRow")
        hl = QHBoxLayout(row)
        label = QLabel(format_observation(obs))
        label.setWordWrap(True)
        label.setStyleSheet(f"color: {_SEVERITY_COLOR.get(obs.severity, '#6b7280')};")
        hl.addWidget(label, 1)
        btn = QPushButton("Discuss in assistant")
        btn.clicked.connect(lambda _checked=False, o=obs: svc.discuss_observation(o))
        hl.addWidget(btn, 0)
        # Insert above the trailing stretch.
        self._rows.insertWidget(self._rows.count() - 1, row)

    def row_count(self) -> int:
        # Number of observation rows (excludes the trailing stretch item).
        return max(0, self._rows.count() - 1)
```

- [ ] **Step 8: Implement the panel plugin + manifest entry**

```python
# src/lightfall/ui/panels/plugins/monitor_panel_plugin.py
"""Panel plugin that provides the Monitor panel."""

from __future__ import annotations

from typing import TYPE_CHECKING

from lightfall.plugins.panel_plugin import PanelPlugin

if TYPE_CHECKING:
    from lightfall.ui.panels.base import BasePanel


class MonitorPanelPlugin(PanelPlugin):
    @property
    def name(self) -> str:
        return "monitor"

    def get_panel_class(self) -> type[BasePanel]:
        from lightfall.ui.panels.monitor_panel import MonitorPanel
        return MonitorPanel
```

In `builtin_manifest.py`, add after the claude panel entry (`:300`):

```python
        PluginEntry(
            type_name="panel",
            name="monitor",
            import_path="lightfall.ui.panels.plugins.monitor_panel_plugin:MonitorPanelPlugin",
            preload=True,  # register metadata; panel instantiated lazily (proactive_init=False)
        ),
```

- [ ] **Step 9: Add `ClaudePanel.submit_external_prompt`**

In `src/lightfall/ui/panels/claude_panel.py`, add a public method (mirrors the existing `action_send_message` at `:1153`, but raises the panel first):

```python
    def submit_external_prompt(self, text: str) -> bool:
        """Raise the Claude panel and submit a programmatic user prompt to
        the reactive agent. Returns False if the agent widget isn't built yet."""
        win = self._get_main_window()
        if win is not None:
            win.activate_panel(self.panel_metadata.id)
        if self._claude_widget is None:
            return False
        self._claude_widget.input_field.setText(text)
        self._claude_widget._send_query()  # auto-connects via agent.query_sync
        return True
```

- [ ] **Step 10: Add `_setup_monitor` in `main.py`**

In `src/lightfall/main.py`, after the main window is created and `window.set_engine(engine)` is called (`:954`), add a call `_setup_monitor(app, window)`, and define:

```python
def _setup_monitor(app, window) -> None:
    """Start the proactive monitor service (subscribes to the engine, surfaces
    observations to toasts + the Monitor panel + the Claude hand-off)."""
    from lightfall.monitor.service import MonitorService
    svc = MonitorService.get_instance()
    svc.set_window(window)
    svc.start()
    try:
        app.services.register(MonitorService, MonitorService.get_instance)
    except Exception:  # noqa: BLE001 — service registry is best-effort
        logger.debug("could not register MonitorService with app.services")
```

> Implementer note: match the surrounding `_setup_*` style for the `app`/`window` parameters and the services-registry call (cf. `_setup_tiled` at `main.py:504-560`). `logger` is already imported in `main.py`.

- [ ] **Step 11: Run panel + service tests**

Run: `QT_QPA_PLATFORM=offscreen PYTHONPATH=src .venv/Scripts/python -m pytest tests/ui/test_monitor_panel.py tests/monitor/test_service.py -v`
Expected: PASS.

- [ ] **Step 12: Manual smoke (optional but recommended)**

Launch the app, start any scan, and confirm the Monitor panel appears in the right sidebar and (with a configured `acquisition_health.count_field`) a warn toast + row appears when the count rate is low. Confirm "Discuss in assistant" opens the Claude panel with a pre-filled prompt.

- [ ] **Step 13: Commit**

```bash
git add src/lightfall/monitor/service.py src/lightfall/ui/panels/monitor_panel.py \
  src/lightfall/ui/panels/plugins/monitor_panel_plugin.py \
  src/lightfall/plugins/builtin_manifest.py src/lightfall/main.py \
  src/lightfall/ui/panels/claude_panel.py \
  tests/monitor/test_service.py tests/ui/test_monitor_panel.py
git commit -m "feat(monitor): MonitorService + Monitor panel + app wiring + Claude hand-off"
```

---

### Task 12: `ExperimentContext` launch injection (pre-submit hook)

**Files:**
- Create: `src/lightfall/monitor/context_provider.py`
- Modify: `src/lightfall/ui/panels/bluesky_panel.py` (register the pre-submit hook)
- Test: `tests/monitor/test_context_injection.py`

**Interfaces:**
- Produces: `ExperimentContextProvider` singleton — `get_instance()`, `reset_instance()`, `set_context(ctx)`, `current() -> ExperimentContext`; `experiment_context_pre_submit(plan_name, kwargs) -> dict | None` (returns `{"experiment_context": <dict>}`).
- Consumes: `BaseEngine.register_pre_submit` (`acquire/engine/base.py:480`), `ExperimentContext`.

> v1 scope: this delivers the *plumbing* — the current `ExperimentContext` is injected into every run's start doc. The default is `generic` with empty config; richer per-experiment population (the XPCS fields) is Plan B. The hook only adds the key if it isn't already present, so an explicit per-plan context wins.

- [ ] **Step 1: Write the failing test**

```python
# tests/monitor/test_context_injection.py
from lightfall.monitor.context_provider import (
    ExperimentContextProvider, experiment_context_pre_submit,
)
from lightfall.monitor.models import ExperimentContext


def test_pre_submit_injects_current_context():
    ExperimentContextProvider.reset_instance()
    ExperimentContextProvider.get_instance().set_context(
        ExperimentContext(experiment_type="xpcs", intent="slow")
    )
    out = experiment_context_pre_submit("count", {})
    assert out["experiment_context"]["experiment_type"] == "xpcs"
    # Round-trips through the model.
    ctx = ExperimentContext.from_start_doc(out)
    assert ctx.intent == "slow"
    ExperimentContextProvider.reset_instance()


def test_pre_submit_does_not_overwrite_explicit_context():
    ExperimentContextProvider.reset_instance()
    kwargs = {"experiment_context": {"experiment_type": "explicit"}}
    out = experiment_context_pre_submit("count", kwargs)
    assert out is None  # nothing to merge; explicit value preserved
    ExperimentContextProvider.reset_instance()
```

- [ ] **Step 2: Run to verify it fails**

Run: `PYTHONPATH=src .venv/Scripts/python -m pytest tests/monitor/test_context_injection.py -v`
Expected: FAIL — `ModuleNotFoundError`.

- [ ] **Step 3: Implement the provider + hook**

```python
# src/lightfall/monitor/context_provider.py
"""Holds the current ExperimentContext and injects it into run start docs via
a BaseEngine pre-submit hook (same mechanism as the sample-metadata dialog)."""

from __future__ import annotations

import threading
from typing import Any

from lightfall.monitor.models import ExperimentContext


class ExperimentContextProvider:
    _instance: ExperimentContextProvider | None = None
    _lock = threading.Lock()

    def __init__(self) -> None:
        self._ctx = ExperimentContext.default()

    @classmethod
    def get_instance(cls) -> ExperimentContextProvider:
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    @classmethod
    def reset_instance(cls) -> None:
        with cls._lock:
            cls._instance = None

    def set_context(self, ctx: ExperimentContext) -> None:
        self._ctx = ctx

    def current(self) -> ExperimentContext:
        return self._ctx


def experiment_context_pre_submit(plan_name: str, kwargs: dict[str, Any]) -> dict | None:
    """Pre-submit hook: merge the current ExperimentContext into the start doc.

    Returns a dict merged into plan kwargs (and thus the start doc), or None to
    change nothing. Does not overwrite an explicit per-plan context."""
    if "experiment_context" in kwargs:
        return None
    ctx = ExperimentContextProvider.get_instance().current()
    return {"experiment_context": ctx.to_dict()}
```

- [ ] **Step 4: Register the hook in `bluesky_panel.py`**

In `src/lightfall/ui/panels/bluesky_panel.py`, in `_auto_configure` (where `register_pre_submit(_sample_metadata_pre_submit)` is wired, `:291-299`), add:

```python
        from lightfall.monitor.context_provider import experiment_context_pre_submit
        engine.register_pre_submit(experiment_context_pre_submit)
```

> Implementer note: confirm `engine` is the local variable in `_auto_configure` (it is — `engine = get_engine()`), and that registering two pre-submit hooks is supported (it is — `_run_pre_submit_hooks` runs them in registration order, `base.py:209-221`).

- [ ] **Step 5: Run to verify it passes**

Run: `PYTHONPATH=src .venv/Scripts/python -m pytest tests/monitor/test_context_injection.py -v`
Expected: PASS (2 tests).

- [ ] **Step 6: Commit**

```bash
git add src/lightfall/monitor/context_provider.py src/lightfall/ui/panels/bluesky_panel.py tests/monitor/test_context_injection.py
git commit -m "feat(monitor): ExperimentContext launch injection (pre-submit hook)"
```

---

## Final verification

- [ ] Run the full monitor suite:
  `QT_QPA_PLATFORM=offscreen PYTHONPATH=src .venv/Scripts/python -m pytest tests/monitor tests/ui/test_monitor_panel.py -v`
  Expected: all PASS.
- [ ] Run a broad import/smoke to catch wiring regressions:
  `PYTHONPATH=src .venv/Scripts/python -c "import lightfall.main"`
  Expected: no error.
- [ ] Manual: launch app, run a scan with `acquisition_health.count_field` configured (via an injected ExperimentContext), confirm toast + Monitor panel row + "Discuss in assistant" hand-off.

## Self-Review (completed by plan author)

- **Spec coverage:** MonitorFeed (T3), MonitorPlugin/Registry + opt-out prefs (T4/T5/T6), MonitorScheduler arm/disarm/tick/rate-limit non-blocking (T7/T8/T9), DataWindow reduced-signals-only (T2), ExperimentContext + Observation (T1), toast + Monitor panel + discuss hand-off (T11), acquisition-health feed (T10), launch injection via pre-submit hook (T12). Deferred to Plan B (explicitly): XPCS speckle/dynamics feed, the optional LLM advisor, and the settings pages — all listed in the spec's Plan B split.
- **Placeholder scan:** none — every code/test step contains real content. Two "implementer note" callouts ask for confirmation of an existing constructor (`PluginInfo`) and a local variable (`engine`); these are verification of already-read code, not missing content.
- **Type consistency:** `Observation`/`ExperimentContext`/`DataWindow` field and method names are used identically across T9/T10/T11/T12; `enabled_feeds()`, `should_surface()`, `snapshot(now=)`, `submit_external_prompt()`, `discuss_observation()` match their definitions.

## Notes for Plan B

- XPCS feed reads `xpcs_live`'s recorded g₂/metrics via `DataWindow.derived("xpcs")` (wire `derived_provider` in the scheduler to read the run's `xpcs` Tiled stream); β = g₂(τ→0)−1; dynamics-captured from provided `tau_c`/fit. No in-process correlation. (Spec §"XPCS speckle / dynamics".)
- LLM advisor = a second headless `QtClaudeAgent` consuming a batch of `Observation`s; off by default. (Spec §"LLM advisor".)
- Settings pages (per-feed enable table + advisor switch + interval) copy `ClaudeToolsSettingsPlugin`. (Spec §"Settings / toggles".)
