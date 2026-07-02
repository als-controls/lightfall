# Proactive Monitor — Plan B-core (lightfall side) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development. Steps use checkbox (`- [ ]`) syntax. Builds on Plan A (already implemented on this branch: src/lightfall/monitor/{models,data_window,feed,monitor_plugin,registry,rate_limiter,buffer,scheduler,service,context_provider}.py + feeds/acquisition_health.py + ui/panels/monitor_panel.py).

**Goal:** Add the lightfall-side pieces Plan B needs: (1) the scheduler's `derived("xpcs")` provider that reads xpcs_live's recorded Tiled snapshot, (2) a lean off-by-default `MonitorAdvisor` (LLM fuses a batch of observations into one message), (3) advisor integration in `MonitorService`, and (4) the `MonitorSettingsPlugin` (per-feed table + advisor switch + tick interval). The XPCS feed itself is a separate plan in `lightfall-endstation-7011`.

**Architecture:** The scheduler sets `window.derived_provider` to a function that reads `client[uid]["xpcs"]`'s latest `snapshot_NNN` (off the eval thread). The advisor is a small wrapper over a minimal `ClaudeSDKClient` (no tools, `max_turns=1`, own session/cwd, same auth), invoked by `MonitorService` on a debounced batch of surfaced observations when `monitor_advisor_enabled` is set. Settings mirror `ClaudeToolsSettingsPlugin`.

**Tech Stack:** PySide6, claude_agent_sdk (`ClaudeAgentOptions`, `ClaudeSDKClient`), Tiled (`TiledService`), existing monitor package from Plan A.

## Global Constraints

- **No data analysis in the Lightfall process.** The XPCS provider only *reads* xpcs_live's already-computed g₂/metrics arrays — it does not correlate or fit. (Any β/decay judgment lives in the feed, Plan B-xpcs, and is threshold logic on the provided curve, not fitting.)
- **Never block the engine.** The `derived` provider runs on the `QThreadFuture` eval thread (already off-engine, off-UI); it must tolerate Tiled being down / the stream not yet written and return `None`.
- **Advisor is off by default** (`monitor_advisor_enabled`, default `False`) and **never instantiated** unless enabled. It has **no tools** (`allowed_tools=[]`, `mcp_servers={}`), uses a **separate cwd**, and must **not** write the reactive agent's session id.
- **Advisory only.** No hardware actions.
- **Run tests** from the worktree: `QT_QPA_PLATFORM=offscreen PYTHONPATH=src "C:/Users/rp/PycharmProjects/ncs/lightfall/.venv/Scripts/python.exe" -m pytest <path> -v` (drop QT_QPA_PLATFORM for non-Qt tests).

## File Structure

- Modify: `src/lightfall/monitor/scheduler.py` (add `_derived`, wire `window.derived_provider`, accept `tick_granularity_s` from a setter for live interval).
- Create: `src/lightfall/monitor/advisor.py` (`format_advisor_prompt`, `MonitorAdvisor`).
- Modify: `src/lightfall/monitor/service.py` (advisor batching/gating).
- Create: `src/lightfall/ui/preferences/monitor_settings.py` (`MonitorPluginTableModel`, `MonitorSettingsPlugin`).
- Modify: `src/lightfall/plugins/builtin_manifest.py` (settings entry).
- Modify: `src/lightfall/ui/preferences/manager.py` (`GLOBAL_ONLY_PREFS`).
- Tests under `tests/monitor/` and `tests/ui/`.

---

### Task 1: Scheduler `derived("xpcs")` provider

**Files:**
- Modify: `src/lightfall/monitor/scheduler.py`
- Test: `tests/monitor/test_scheduler_derived.py`

**Interfaces:**
- Consumes: `RollingBuffer.active_uid` (Plan A, `buffer.py`), `TiledService.get_instance()` / `.client` / `.is_connected` (`services/tiled_service.py`).
- Produces: `MonitorScheduler._derived(name: str) -> dict | None`; `_tick` sets `window.derived_provider = self._derived` after building the window. The returned dict shape for `"xpcs"`: `{"tau", "g2": {"average", "<roi_id>"...}, "frames_count", "metrics": {...}, "intensity_average", "snapshot"}` or `None`.

- [ ] **Step 1: Write the failing test**

```python
# tests/monitor/test_scheduler_derived.py
import pytest
from PySide6.QtWidgets import QApplication

from lightfall.monitor.registry import MonitorRegistry
from lightfall.monitor.scheduler import MonitorScheduler


@pytest.fixture(scope="module")
def _app():
    return QApplication.instance() or QApplication([])


class _FakeEngine:
    def __init__(self):
        self._cb = None
        self.sigAbort = _Sig(); self.sigException = _Sig()
    def subscribe(self, cb): self._cb = cb; return 1
    def unsubscribe(self, t): self._cb = None
    def emit(self, name, doc):
        if self._cb: self._cb(name, doc)


class _Sig:
    def connect(self, *a, **k): pass


class _Arr:
    def __init__(self, v): self._v = v
    def read(self): return self._v


class _Snap:
    def __init__(self, d): self._d = d
    def keys(self): return list(self._d.keys())
    def __getitem__(self, k): return _Arr(self._d[k])


class _Xpcs:
    def __init__(self, snaps): self._snaps = snaps  # {name: _Snap}
    def keys(self): return ["config", *self._snaps.keys()]
    def __getitem__(self, k):
        if k in self._snaps: return self._snaps[k]
        raise KeyError(k)


class _Run:
    def __init__(self, xpcs): self._xpcs = xpcs
    def __getitem__(self, k):
        if k == "xpcs" and self._xpcs is not None: return self._xpcs
        raise KeyError(k)


class _Client:
    def __init__(self, runs): self._runs = runs
    def __getitem__(self, uid):
        if uid in self._runs: return self._runs[uid]
        raise KeyError(uid)


class _Svc:
    def __init__(self, client): self._client = client
    @property
    def client(self): return self._client
    @property
    def is_connected(self): return self._client is not None


@pytest.fixture
def _registry():
    MonitorRegistry.reset_instance()
    reg = MonitorRegistry.get_instance()
    reg._read_list_pref = lambda key: []
    yield reg
    MonitorRegistry.reset_instance()


def _arm(sched, eng, uid):
    eng.emit("start", {"uid": uid, "time": 0.0})


def test_derived_returns_latest_snapshot(_app, _registry, monkeypatch):
    snap = _Snap({"tau": [1, 2], "g2_average": [1.3, 1.1], "g2_roi_0": [1.3, 1.1],
                  "frames_count": 50, "metrics_rms": [0.2], "intensity_average": [9.0]})
    client = _Client({"u1": _Run(_Xpcs({"snapshot_001": _Snap({}), "snapshot_002": snap}))})
    monkeypatch.setattr(
        "lightfall.services.tiled_service.TiledService.get_instance",
        staticmethod(lambda: _Svc(client)),
    )
    eng = _FakeEngine()
    sched = MonitorScheduler(eng, registry=_registry, eval_async=False)
    sched.start(); _arm(sched, eng, "u1")
    d = sched._derived("xpcs")
    assert d is not None
    assert d["snapshot"] == "snapshot_002"
    assert d["g2"]["average"] == [1.3, 1.1]
    assert d["g2"]["0"] == [1.3, 1.1]
    assert d["frames_count"] == 50


def test_derived_none_when_no_xpcs_stream(_app, _registry, monkeypatch):
    client = _Client({"u1": _Run(None)})  # run exists, no xpcs stream yet
    monkeypatch.setattr(
        "lightfall.services.tiled_service.TiledService.get_instance",
        staticmethod(lambda: _Svc(client)),
    )
    eng = _FakeEngine()
    sched = MonitorScheduler(eng, registry=_registry, eval_async=False)
    sched.start(); _arm(sched, eng, "u1")
    assert sched._derived("xpcs") is None


def test_derived_none_when_disconnected(_app, _registry, monkeypatch):
    monkeypatch.setattr(
        "lightfall.services.tiled_service.TiledService.get_instance",
        staticmethod(lambda: _Svc(None)),
    )
    eng = _FakeEngine()
    sched = MonitorScheduler(eng, registry=_registry, eval_async=False)
    sched.start(); _arm(sched, eng, "u1")
    assert sched._derived("xpcs") is None


def test_derived_non_xpcs_name_returns_none(_app, _registry):
    eng = _FakeEngine()
    sched = MonitorScheduler(eng, registry=_registry, eval_async=False)
    assert sched._derived("something_else") is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `QT_QPA_PLATFORM=offscreen PYTHONPATH=src .venv/Scripts/python.exe -m pytest tests/monitor/test_scheduler_derived.py -v`
Expected: FAIL — `MonitorScheduler` has no `_derived`.

- [ ] **Step 3: Implement `_derived` and wire it in `_tick`**

In `src/lightfall/monitor/scheduler.py`, add the method (place near `_tick`):

```python
    def _derived(self, name: str) -> dict | None:
        """Provider for DataWindow.derived(name). For "xpcs": read xpcs_live's
        latest recorded Tiled snapshot for the active run. Reduced metrics only
        (no analysis). Runs on the eval thread; degrades to None on any miss."""
        if name != "xpcs":
            return None
        uid = self._buffer.active_uid
        if not uid:
            return None
        try:
            from lightfall.services.tiled_service import TiledService
            svc = TiledService.get_instance()
            client = svc.client
            if client is None or not svc.is_connected:
                return None
            run = client[uid]                 # KeyError if writer still lagging
            xpcs = run["xpcs"]                # KeyError until first snapshot
            snaps = sorted(k for k in xpcs.keys() if k.startswith("snapshot_"))
            if not snaps:
                return None
            snap = xpcs[snaps[-1]]
        except KeyError:
            return None
        except Exception:  # noqa: BLE001 — advisory; never crash the tick
            logger.debug("monitor _derived('xpcs') read failed for {}", uid)
            return None

        def arr(k):
            try:
                return snap[k].read()
            except Exception:  # noqa: BLE001
                return None

        keys = list(snap.keys())
        g2 = {"average": arr("g2_average")}
        for k in keys:
            if k.startswith("g2_roi_"):
                g2[k[len("g2_roi_"):]] = arr(k)
        fc = arr("frames_count")
        try:
            frames_count = int(fc) if fc is not None else 0
        except (TypeError, ValueError):
            frames_count = 0
        return {
            "tau": arr("tau"),
            "g2": g2,
            "frames_count": frames_count,
            "metrics": {k: arr(k) for k in keys if k.startswith("metrics_")},
            "intensity_average": arr("intensity_average"),
            "snapshot": snaps[-1],
        }
```

In `_tick`, right after the `window = self._buffer.snapshot(...)` line, add:

```python
        window.derived_provider = self._derived
```

- [ ] **Step 4: Run test to verify it passes**

Run: `QT_QPA_PLATFORM=offscreen PYTHONPATH=src .venv/Scripts/python.exe -m pytest tests/monitor/test_scheduler_derived.py -v`
Expected: PASS (4 tests).

- [ ] **Step 5: Commit**

```bash
git add src/lightfall/monitor/scheduler.py tests/monitor/test_scheduler_derived.py
git commit -m "feat(monitor): scheduler derived('xpcs') provider reads xpcs_live snapshot"
```

---

### Task 2: `MonitorAdvisor` (lean, off-by-default)

**Files:**
- Create: `src/lightfall/monitor/advisor.py`
- Test: `tests/monitor/test_advisor.py`

**Interfaces:**
- Consumes: `Observation` (Plan A); `claude_agent_sdk` (`ClaudeAgentOptions`, `ClaudeSDKClient`, `types.AssistantMessage/TextBlock/ResultMessage`).
- Produces:
  - `format_advisor_prompt(observations: list[Observation]) -> str` (pure).
  - `async def collect_reply(client, prompt: str) -> str` (drives one SDK turn; testable with a fake async client).
  - `MonitorAdvisor(query_fn: Callable[[str], str] | None = None, model: str | None = None)` with `advise(observations: list[Observation]) -> str` (returns "" if no observations). `query_fn` defaults to the real SDK-backed `_sdk_query`; tests inject a fake.

- [ ] **Step 1: Write the failing test**

```python
# tests/monitor/test_advisor.py
import asyncio

import pytest

from lightfall.monitor.advisor import MonitorAdvisor, collect_reply, format_advisor_prompt
from lightfall.monitor.models import Observation


def _obs(title, sev="warn"):
    return Observation(severity=sev, feed_name="f", run_uid="u",
                       title=title, message="m", state_key=f"f:{title}",
                       metrics={"x": 1.0}, recommendation="do y")


def test_format_prompt_includes_each_observation():
    p = format_advisor_prompt([_obs("A"), _obs("B")])
    assert "A" in p and "B" in p and "do y" in p


def test_advise_returns_empty_for_no_observations():
    adv = MonitorAdvisor(query_fn=lambda prompt: "should not be called")
    assert adv.advise([]) == ""


def test_advise_calls_query_fn_with_prompt():
    seen = {}
    adv = MonitorAdvisor(query_fn=lambda prompt: seen.setdefault("p", prompt) or "FUSED")
    out = adv.advise([_obs("A")])
    assert out == "FUSED"
    assert "A" in seen["p"]


class _FakeBlock:
    def __init__(self, text): self.text = text


class _FakeAssistant:
    def __init__(self, blocks): self.content = blocks


class _FakeResult:
    pass


class _FakeClient:
    def __init__(self, msgs): self._msgs = msgs
    async def query(self, prompt): self._prompt = prompt
    async def receive_response(self):
        for m in self._msgs:
            yield m


def test_collect_reply_joins_textblocks_until_result(monkeypatch):
    # Patch the SDK type-checks collect_reply uses to our fakes.
    import lightfall.monitor.advisor as mod
    monkeypatch.setattr(mod, "_AssistantMessage", _FakeAssistant)
    monkeypatch.setattr(mod, "_TextBlock", _FakeBlock)
    monkeypatch.setattr(mod, "_ResultMessage", _FakeResult)
    client = _FakeClient([_FakeAssistant([_FakeBlock("Hello "), _FakeBlock("world")]),
                          _FakeResult()])
    out = asyncio.get_event_loop().run_until_complete(collect_reply(client, "p"))
    assert out == "Hello world"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=src .venv/Scripts/python.exe -m pytest tests/monitor/test_advisor.py -v`
Expected: FAIL — `ModuleNotFoundError`.

- [ ] **Step 3: Implement the advisor**

```python
# src/lightfall/monitor/advisor.py
"""Optional LLM advisor: fuses a batch of deterministic Observations into one
plain-language message. Off by default. A lean wrapper over a minimal
ClaudeSDKClient — no tools, max_turns=1, its own session/cwd, same auth. It
does NOT sense data; it only voices/triages what the feeds already found."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from pathlib import Path

from lightfall.monitor.models import Observation
from lightfall.utils.logging import logger

# Imported at module load so tests can monkeypatch these names with fakes.
try:  # pragma: no cover - exercised indirectly
    from claude_agent_sdk.types import (
        AssistantMessage as _AssistantMessage,
        ResultMessage as _ResultMessage,
        TextBlock as _TextBlock,
    )
except Exception:  # noqa: BLE001 - SDK optional at import time
    _AssistantMessage = _ResultMessage = _TextBlock = tuple()  # type: ignore[assignment]

ADVISOR_SYSTEM_PROMPT = (
    "You are a measurement-quality advisor for a synchrotron beamline. You receive "
    "a batch of structured observations produced by deterministic monitors during a "
    "running measurement. Fuse them into ONE short, plain-language message for the "
    "scientist: say whether anything needs attention and, if so, the single most "
    "useful next action. If nothing is worth interrupting for, reply exactly "
    "'nothing to report'. Be concise (1-3 sentences). Do not invent data."
)


def format_advisor_prompt(observations: list[Observation]) -> str:
    lines = ["Observations this interval:"]
    for o in observations:
        rec = f" | suggested: {o.recommendation}" if o.recommendation else ""
        lines.append(
            f"- [{o.severity}] {o.feed_name}: {o.title} — {o.message} "
            f"| metrics={o.metrics}{rec}"
        )
    lines.append("\nFuse into one short message, or 'nothing to report'.")
    return "\n".join(lines)


async def collect_reply(client, prompt: str) -> str:
    """Drive one SDK turn and return the joined assistant text."""
    await client.query(prompt)
    parts: list[str] = []
    async for msg in client.receive_response():
        if isinstance(msg, _AssistantMessage):
            for block in msg.content:
                if isinstance(block, _TextBlock):
                    parts.append(block.text or "")
        elif isinstance(msg, _ResultMessage):
            break
    return "".join(parts).strip()


class MonitorAdvisor:
    def __init__(
        self,
        query_fn: Callable[[str], str] | None = None,
        model: str | None = None,
    ) -> None:
        self._query_fn = query_fn or self._sdk_query
        self._model = model

    def advise(self, observations: list[Observation]) -> str:
        if not observations:
            return ""
        prompt = format_advisor_prompt(observations)
        try:
            return self._query_fn(prompt).strip()
        except Exception:  # noqa: BLE001 - advisory; never crash the monitor
            logger.exception("monitor advisor query failed")
            return ""

    def _sdk_query(self, prompt: str) -> str:
        """Real SDK-backed one-shot query. Runs its own event loop + client.
        Integration path (the response-collection logic is unit-tested via
        collect_reply with a fake client)."""
        from claude_agent_sdk import ClaudeAgentOptions, ClaudeSDKClient

        cwd = (Path.home() / "lightfall" / "advisor")
        cwd.mkdir(parents=True, exist_ok=True)
        opts = ClaudeAgentOptions(
            cwd=str(cwd.resolve()),
            mcp_servers={},
            allowed_tools=[],
            system_prompt=ADVISOR_SYSTEM_PROMPT,
            permission_mode="bypassPermissions",
            max_turns=1,
            include_partial_messages=False,
            **({"model": self._model} if self._model else {}),
        )
        loop = asyncio.new_event_loop()
        try:
            asyncio.set_event_loop(loop)
            client = ClaudeSDKClient(options=opts)
            loop.run_until_complete(client.connect())
            try:
                return loop.run_until_complete(collect_reply(client, prompt))
            finally:
                try:
                    loop.run_until_complete(client.disconnect())
                except Exception:  # noqa: BLE001
                    pass
        finally:
            try:
                loop.close()
            except Exception:  # noqa: BLE001
                pass
```

> Implementer note: confirm `ClaudeSDKClient` exposes `disconnect()` (it's used in the reactive worker's shutdown path; if the method name differs, match it — the connect call is `await client.connect()` per `claude/_internal/worker.py:141`). If there is no disconnect, drop that block.

- [ ] **Step 4: Run test to verify it passes**

Run: `PYTHONPATH=src .venv/Scripts/python.exe -m pytest tests/monitor/test_advisor.py -v`
Expected: PASS (4 tests).

- [ ] **Step 5: Commit**

```bash
git add src/lightfall/monitor/advisor.py tests/monitor/test_advisor.py
git commit -m "feat(monitor): lean off-by-default MonitorAdvisor"
```

---

### Task 3: Advisor integration in `MonitorService`

**Files:**
- Modify: `src/lightfall/monitor/service.py`
- Test: `tests/monitor/test_service_advisor.py`

**Interfaces:**
- Consumes: `MonitorAdvisor` (Task 2), `Observation`, `PreferencesManager` (`monitor_advisor_enabled`), `QTimer`, `QThreadFuture`.
- Produces: `MonitorService` gains: pref-gated advisor batching. On each surfaced observation whose `feed_name != "advisor"`, append to a pending batch and (re)start a single-shot debounce timer; on timeout, if enabled and batch non-empty, run `MonitorAdvisor.advise(batch)` off-thread and, on a non-empty reply, surface an advisor `Observation` (severity `info`, `feed_name="advisor"`). New methods: `set_advisor(advisor)` (for tests/wiring), `_flush_advisor()`.

- [ ] **Step 1: Write the failing test**

```python
# tests/monitor/test_service_advisor.py
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
    monkeypatch.setattr(MonitorService, "_build_scheduler", lambda self: None)
    svc = MonitorService.get_instance()
    yield svc
    MonitorService.reset_instance()


class _FakeAdvisor:
    def __init__(self, reply): self.reply = reply; self.seen = None
    def advise(self, observations):
        self.seen = list(observations)
        return self.reply


def _obs(title):
    return Observation(severity="warn", feed_name="health", run_uid="u",
                       title=title, message="m", state_key=f"health:{title}")


def test_flush_runs_advisor_and_surfaces_reply_when_enabled(_svc, monkeypatch):
    monkeypatch.setattr(_svc, "_advisor_enabled", lambda: True)
    adv = _FakeAdvisor("FUSED MESSAGE")
    _svc.set_advisor(adv)
    # Force synchronous advisor execution for the test.
    _svc._advise_async = False

    surfaced = []
    _svc.observation.connect(surfaced.append)

    _svc._on_observation(_obs("A"))
    _svc._on_observation(_obs("B"))
    _svc._flush_advisor()  # the debounce timer would call this

    assert adv.seen is not None and len(adv.seen) == 2
    advisor_obs = [o for o in surfaced if o.feed_name == "advisor"]
    assert len(advisor_obs) == 1
    assert advisor_obs[0].message == "FUSED MESSAGE"
    assert advisor_obs[0].severity == "info"


def test_advisor_observations_are_not_rebatched(_svc, monkeypatch):
    monkeypatch.setattr(_svc, "_advisor_enabled", lambda: True)
    adv = _FakeAdvisor("X")
    _svc.set_advisor(adv); _svc._advise_async = False
    advisor_only = Observation(severity="info", feed_name="advisor", run_uid="u",
                               title="t", message="m", state_key="advisor:x")
    _svc._on_observation(advisor_only)
    _svc._flush_advisor()
    assert adv.seen in (None, [])  # advisor's own output never re-batched


def test_no_advisor_when_disabled(_svc, monkeypatch):
    monkeypatch.setattr(_svc, "_advisor_enabled", lambda: False)
    adv = _FakeAdvisor("X"); _svc.set_advisor(adv); _svc._advise_async = False
    _svc._on_observation(_obs("A"))
    _svc._flush_advisor()
    assert adv.seen is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `QT_QPA_PLATFORM=offscreen PYTHONPATH=src .venv/Scripts/python.exe -m pytest tests/monitor/test_service_advisor.py -v`
Expected: FAIL — no `set_advisor`/`_flush_advisor`.

- [ ] **Step 3: Implement advisor batching in `MonitorService`**

Add to `MonitorService.__init__` (after the existing fields):

```python
        from PySide6.QtCore import QTimer
        self._advisor = None
        self._advisor_batch: list[Observation] = []
        self._advise_async = True
        self._advisor_timer = QTimer(self)
        self._advisor_timer.setSingleShot(True)
        self._advisor_timer.setInterval(5000)  # debounce window (ms)
        self._advisor_timer.timeout.connect(self._flush_advisor)
```

Add methods:

```python
    def set_advisor(self, advisor) -> None:
        self._advisor = advisor

    def _advisor_enabled(self) -> bool:
        try:
            from lightfall.ui.preferences.manager import PreferencesManager
            return bool(PreferencesManager.get_instance().get("monitor_advisor_enabled", False))
        except Exception:  # noqa: BLE001
            return False

    def _ensure_advisor(self):
        if self._advisor is None:
            from lightfall.monitor.advisor import MonitorAdvisor
            self._advisor = MonitorAdvisor()
        return self._advisor

    def _flush_advisor(self) -> None:
        batch, self._advisor_batch = self._advisor_batch, []
        if not batch or not self._advisor_enabled():
            return
        advisor = self._ensure_advisor()
        if self._advise_async:
            from lightfall.utils.threads import QThreadFuture
            QThreadFuture(advisor.advise, batch,
                          callback_slot=self._on_advisor_reply,
                          key="monitor:advisor").start()
        else:
            self._on_advisor_reply(advisor.advise(batch))

    def _on_advisor_reply(self, reply: str) -> None:
        reply = (reply or "").strip()
        if not reply or reply.lower() == "nothing to report":
            return
        import time
        from lightfall.monitor.models import Observation
        obs = Observation(
            severity="info", feed_name="advisor",
            run_uid=self._recent[-1].run_uid if self._recent else "",
            title="Advisor", message=reply, state_key="advisor:summary",
            ts=time.time(),
        )
        self._recent.append(obs)
        self.observation.emit(obs)
```

Modify `_on_observation` so non-advisor observations feed the batch (add at the end of the method, after the existing emit):

```python
        if obs.feed_name != "advisor" and self._advisor_enabled():
            self._advisor_batch.append(obs)
            self._advisor_timer.start()  # (re)arm debounce
```

- [ ] **Step 4: Run test to verify it passes**

Run: `QT_QPA_PLATFORM=offscreen PYTHONPATH=src .venv/Scripts/python.exe -m pytest tests/monitor/test_service_advisor.py -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add src/lightfall/monitor/service.py tests/monitor/test_service_advisor.py
git commit -m "feat(monitor): pref-gated debounced advisor batching in MonitorService"
```

---

### Task 4: `MonitorSettingsPlugin` + tick-interval wiring

**Files:**
- Create: `src/lightfall/ui/preferences/monitor_settings.py`
- Modify: `src/lightfall/plugins/builtin_manifest.py`
- Modify: `src/lightfall/ui/preferences/manager.py` (`GLOBAL_ONLY_PREFS`)
- Modify: `src/lightfall/monitor/service.py` (apply `monitor_tick_interval` to the scheduler)
- Modify: `src/lightfall/monitor/scheduler.py` (add `set_tick_interval_s`)
- Test: `tests/ui/test_monitor_settings.py`, `tests/monitor/test_scheduler_interval_setter.py`

**Interfaces:**
- Consumes: `SettingsPlugin` (`plugins/settings_plugin.py`), `MonitorRegistry` + its pref constants, `PreferencesManager`.
- Produces: `MonitorPluginTableModel` (mirror of `ToolPluginTableModel`), `MonitorSettingsPlugin` (name `"monitor"`, category `"advanced"`), prefs `monitor_advisor_enabled` (bool, default False) + `monitor_tick_interval` (int seconds, default 60); `MonitorScheduler.set_tick_interval_s(s)`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/monitor/test_scheduler_interval_setter.py
import pytest
from PySide6.QtWidgets import QApplication
from lightfall.monitor.scheduler import MonitorScheduler


@pytest.fixture(scope="module")
def _app():
    return QApplication.instance() or QApplication([])


class _Eng:
    sigAbort = type("S", (), {"connect": lambda *a, **k: None})()
    sigException = type("S", (), {"connect": lambda *a, **k: None})()
    def subscribe(self, cb): return 1
    def unsubscribe(self, t): pass


def test_set_tick_interval_updates_timer(_app):
    sched = MonitorScheduler(_Eng(), eval_async=False, tick_granularity_s=5.0)
    sched.set_tick_interval_s(30.0)
    assert sched._timer.interval() == 30000
```

```python
# tests/ui/test_monitor_settings.py
import pytest
from PySide6.QtWidgets import QApplication

from lightfall.monitor.registry import (
    DISABLED_MONITORS_PREF, MonitorRegistry,
)
from lightfall.ui.preferences.manager import PreferencesManager
from lightfall.ui.preferences.monitor_settings import (
    ADVISOR_ENABLED_PREF, TICK_INTERVAL_PREF, MonitorSettingsPlugin,
)


@pytest.fixture(scope="module")
def _app():
    return QApplication.instance() or QApplication([])


def test_settings_roundtrip(_app):
    MonitorRegistry.reset_instance()
    plugin = MonitorSettingsPlugin()
    plugin.create_widget()
    plugin.load_settings()
    plugin._advisor_check.setChecked(True)
    plugin._interval_spin.setValue(45)
    plugin.save_settings()
    prefs = PreferencesManager.get_instance()
    assert prefs.get(ADVISOR_ENABLED_PREF) is True
    assert prefs.get(TICK_INTERVAL_PREF) == 45
    # cleanup
    prefs.remove(ADVISOR_ENABLED_PREF); prefs.remove(TICK_INTERVAL_PREF)
    MonitorRegistry.reset_instance()
```

- [ ] **Step 2: Run to verify they fail**

Run: `QT_QPA_PLATFORM=offscreen PYTHONPATH=src .venv/Scripts/python.exe -m pytest tests/monitor/test_scheduler_interval_setter.py tests/ui/test_monitor_settings.py -v`
Expected: FAIL — missing `set_tick_interval_s` / module.

- [ ] **Step 3: Add `set_tick_interval_s` to the scheduler**

In `src/lightfall/monitor/scheduler.py`:

```python
    def set_tick_interval_s(self, seconds: float) -> None:
        self._timer.setInterval(int(max(1.0, seconds) * 1000))
```

- [ ] **Step 4: Implement `monitor_settings.py`**

Create `src/lightfall/ui/preferences/monitor_settings.py`. Build `MonitorPluginTableModel` by mirroring `ToolPluginTableModel` (`src/lightfall/ui/preferences/tool_settings.py:38-238`): copy its `__init__`, `set_overrides`, `get_overrides`, `has_changes`, `rowCount`, `columnCount`, `headerData`, `data`, `flags`, `setData` verbatim, with these substitutions — (a) swap `AgentRegistry` → `MonitorRegistry`; (b) in `refresh()` use `MonitorRegistry.get_instance().get_plugins()` and do **not** call any legacy-migration method (MonitorRegistry has none); (c) drop any tooltip line that calls `create_tools()` / `get_system_prompt()` (MonitorPlugin has neither). Then add the plugin:

```python
ADVISOR_ENABLED_PREF = "monitor_advisor_enabled"   # bool, default False
TICK_INTERVAL_PREF = "monitor_tick_interval"        # int seconds, default 60


class MonitorSettingsPlugin(SettingsPlugin):
    """Monitor settings: per-feed enable table + advisor switch + tick interval.
    Mirrors ClaudeToolsSettingsPlugin (tool_settings.py:241)."""

    def __init__(self) -> None:
        self._widget = None
        self._table_view = None
        self._model = None
        self._advisor_check = None
        self._interval_spin = None

    @property
    def name(self) -> str: return "monitor"
    @property
    def display_name(self) -> str: return "Monitor"
    @property
    def category(self) -> str: return "advanced"
    @property
    def priority(self) -> int: return 95

    def create_widget(self, parent=None):
        from PySide6.QtWidgets import (
            QCheckBox, QFormLayout, QHeaderView, QLabel, QSpinBox,
            QTableView, QVBoxLayout, QWidget,
        )
        widget = QWidget(parent)
        layout = QVBoxLayout(widget)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(QLabel(
            "Enable or disable monitor feeds, the advisor, and the tick interval."))
        form = QFormLayout()
        self._advisor_check = QCheckBox("Enable monitor advisor (LLM)")
        self._interval_spin = QSpinBox()
        self._interval_spin.setRange(1, 86400)
        self._interval_spin.setSuffix(" s")
        form.addRow("Advisor:", self._advisor_check)
        form.addRow("Tick interval:", self._interval_spin)
        layout.addLayout(form)
        self._model = MonitorPluginTableModel(widget)
        self._table_view = QTableView(widget)
        self._table_view.setModel(self._model)
        self._table_view.setSelectionBehavior(QTableView.SelectionBehavior.SelectRows)
        self._table_view.setAlternatingRowColors(True)
        self._table_view.verticalHeader().setVisible(False)
        header = self._table_view.horizontalHeader()
        if header:
            header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
            header.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
            header.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        layout.addWidget(self._table_view, stretch=1)
        self._widget = widget
        return widget

    def load_settings(self) -> None:
        if not self._model:
            return
        self._model.refresh()
        prefs = PreferencesManager.get_instance()
        disabled = prefs.get(DISABLED_MONITORS_PREF)
        forced = prefs.get(FORCED_ENABLED_MONITORS_PREF)
        self._model.set_overrides(
            set(disabled) if isinstance(disabled, list) else set(),
            set(forced) if isinstance(forced, list) else set(),
        )
        self._advisor_check.setChecked(bool(prefs.get(ADVISOR_ENABLED_PREF, False)))
        self._interval_spin.setValue(int(prefs.get(TICK_INTERVAL_PREF, 60)))

    def save_settings(self) -> None:
        if not self._model:
            return
        disabled, forced_enabled = self._model.get_overrides()
        prefs = PreferencesManager.get_instance()
        prefs.set(DISABLED_MONITORS_PREF, sorted(disabled))
        prefs.set(FORCED_ENABLED_MONITORS_PREF, sorted(forced_enabled))
        prefs.set(ADVISOR_ENABLED_PREF, self._advisor_check.isChecked())
        prefs.set(TICK_INTERVAL_PREF, self._interval_spin.value())

    def validate(self) -> list[str]:
        return []
```

Imports at top: `from lightfall.monitor.registry import DISABLED_MONITORS_PREF, FORCED_ENABLED_MONITORS_PREF, MonitorRegistry`, `from lightfall.plugins.settings_plugin import SettingsPlugin`, `from lightfall.ui.preferences.manager import PreferencesManager`, `from lightfall.utils.logging import logger`, plus the Qt model imports for the table model.

- [ ] **Step 5: Register + route prefs**

In `src/lightfall/plugins/builtin_manifest.py`, beside the `claude_tools` settings entry, add:

```python
        PluginEntry(
            type_name="settings",
            name="monitor",
            import_path="lightfall.ui.preferences.monitor_settings:MonitorSettingsPlugin",
        ),
```

In `src/lightfall/ui/preferences/manager.py`, add to `GLOBAL_ONLY_PREFS` (next to `disabled_tool_plugins`/`forced_enabled_tool_plugins`): `"monitor_advisor_enabled"`, `"monitor_tick_interval"`, `"disabled_monitor_plugins"`, `"forced_enabled_monitor_plugins"`.

- [ ] **Step 6: Apply the tick interval in `MonitorService`**

In `MonitorService.start()` (after `self._scheduler.start()` if scheduler exists), apply the saved interval and subscribe to changes:

```python
    def start(self) -> None:
        if self._scheduler is not None:
            self._scheduler.start()
            self._apply_tick_interval()
            try:
                from lightfall.ui.preferences.manager import PreferencesManager
                PreferencesManager.get_instance().subscribe(
                    "monitor_tick_interval", lambda _v: self._apply_tick_interval())
            except Exception:  # noqa: BLE001
                pass

    def _apply_tick_interval(self) -> None:
        if self._scheduler is None:
            return
        try:
            from lightfall.ui.preferences.manager import PreferencesManager
            secs = int(PreferencesManager.get_instance().get("monitor_tick_interval", 60))
        except Exception:  # noqa: BLE001
            secs = 60
        self._scheduler.set_tick_interval_s(secs)
```

- [ ] **Step 7: Run tests to verify they pass**

Run: `QT_QPA_PLATFORM=offscreen PYTHONPATH=src .venv/Scripts/python.exe -m pytest tests/monitor/test_scheduler_interval_setter.py tests/ui/test_monitor_settings.py -v`
Expected: PASS.

- [ ] **Step 8: Commit**

```bash
git add src/lightfall/ui/preferences/monitor_settings.py src/lightfall/plugins/builtin_manifest.py src/lightfall/ui/preferences/manager.py src/lightfall/monitor/service.py src/lightfall/monitor/scheduler.py tests/ui/test_monitor_settings.py tests/monitor/test_scheduler_interval_setter.py
git commit -m "feat(monitor): MonitorSettingsPlugin + live tick-interval wiring"
```

---

## Final verification

- [ ] Full monitor + ui suite:
  `QT_QPA_PLATFORM=offscreen PYTHONPATH=src .venv/Scripts/python.exe -m pytest tests/monitor tests/ui/test_monitor_panel.py tests/ui/test_monitor_settings.py -q`
- [ ] Import smoke: `PYTHONPATH=src .venv/Scripts/python.exe -c "import lightfall.main"`
- [ ] Plugin regression: `QT_QPA_PLATFORM=offscreen PYTHONPATH=src .venv/Scripts/python.exe -m pytest tests/plugins -q`

## Self-Review (plan author)

- **Spec coverage:** derived-xpcs provider (T1, the prerequisite the endstation feed consumes), advisor (T2/T3, off by default), settings page incl. advisor switch + interval (T4). The XPCS *feed* is intentionally out of scope here — it ships in `lightfall-endstation-7011` (Plan B-xpcs), per the placement decision.
- **No placeholders:** all code present. Two "implementer note"s ask to confirm an SDK method name (`disconnect`) and to mirror an existing table model with explicit substitutions — both reference real, citable code.
- **Type consistency:** `Observation`/`DataWindow.derived_provider`/`MonitorScheduler`/`MonitorService` usages match Plan A's implemented signatures; `_advisor_enabled`/`set_advisor`/`_flush_advisor`/`set_tick_interval_s` are defined where referenced.

## Notes for Plan B-xpcs (endstation, separate cycle)

- Lives in `lightfall-endstation-7011`; imports `from lightfall.monitor.feed import MonitorFeed` / `monitor_plugin import MonitorPlugin` (needs feature-branch lightfall on path to build/test).
- Feed reads `window.derived("xpcs")` (the dict T1 produces): `β = g2["average"][0] - 1` (contrast; warn if `< contrast_warn`), "dynamics captured" = `g2["average"]` decayed below `1 + β/e` within the measured τ; gate on `frames_count >= min_frames` else "waiting for live g₂". No fitting (xpcs_live provides no fit).
- Needs grounding: the endstation's plugin-registration mechanism (manifest/entry-points) and its test env's lightfall resolution.
