# lucid-deck Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a new repo `lucid-deck` (sibling of `~/PycharmProjects/ncs/ncs`) that makes Bitfocus Companion drive Ophyd positioners exposed by LUCID. Two deliverables in one repo: a LUCID-side plugin (`MotorsSettingsPlugin` + NATS action handlers + readback publisher) and a `companion-bridge` console-script (FastAPI ↔ NATS ↔ Companion HTTP).

**Architecture:** LUCID hosts NATS request/reply actions (`lucid.motors.action.*`) and publishes readback events (`lucid.motors.readback.<name>`) using its existing `IPCService`. The bridge process subscribes to NATS, exposes HTTP for Companion's Generic HTTP module, and pushes Companion custom variables via Companion's REST API. Bridge owns selection state in memory; LUCID side is stateless about selection.

**Tech Stack:** Python 3.11+, hatch + hatch-vcs, pytest, ruff, PySide6 (LUCID side), FastAPI + uvicorn + httpx + nats-py (bridge side), Ophyd (LUCID positioners). Spec: `docs/superpowers/specs/2026-05-05-lucid-deck-design.md` (commit `261ece1`).

**Out of scope** (per spec): AgentPlugin, hold-to-jog, ControllerPlugin, multi-Companion, auth on bridge, hardware-in-the-loop CI.

**Subject/prefix conventions used throughout:** Plugin registers action suffixes via `IPCService.register_action("motors.action.<verb>", ...)`. Assumes LUCID configures `IPCService` with topic prefix `lucid` (the default in current deployments), so the full wire subjects are `lucid.motors.action.<verb>` and `lucid.motors.readback.<name>`. Bridge connects with the full prefix hard-coded as `lucid.motors`. If a deployment uses a different prefix this becomes a bridge config flag (`--lucid-prefix`); ship the default and the flag together in Task 7.

---

## Files

**Repo root:** `~/PycharmProjects/ncs/lucid-deck/`

```
lucid-deck/
├── pyproject.toml
├── README.md
├── src/
│   └── lucid_deck/
│       ├── __init__.py
│       ├── manifest.py              # PluginManifest registration
│       ├── motor_actions.py         # NATS action handlers + readback publisher
│       ├── settings_plugin.py       # MotorsSettingsPlugin (Qt UI + lifecycle)
│       └── bridge/
│           ├── __init__.py
│           ├── __main__.py          # `python -m lucid_deck.bridge`
│           ├── cli.py               # console-script entry point
│           ├── config.py            # env var / CLI flag parsing
│           ├── companion_client.py  # httpx wrapper for Companion variable push
│           ├── nats_client.py       # nats-py wrapper (request/reply + subscribe)
│           ├── app.py               # FastAPI app factory + endpoint handlers
│           └── readback.py          # NATS subscriber → Companion variable push
├── tests/
│   ├── conftest.py                  # shared fixtures (mock positioner)
│   ├── test_motor_actions.py
│   ├── test_readback_publisher.py
│   ├── test_settings_plugin.py
│   ├── test_manifest.py
│   ├── test_bridge_config.py
│   ├── test_bridge_companion_client.py
│   ├── test_bridge_app.py
│   ├── test_bridge_readback.py
│   └── test_integration_e2e.py      # requires real nats-server
└── .gitignore
```

**File responsibilities:**
- `motor_actions.py`: pure functions and a `MotorActionHandlers` class. Owns Ophyd → NATS translation. Has no Qt or FastAPI dependencies.
- `settings_plugin.py`: only the `SettingsPlugin` subclass and its Qt widget. Imports `motor_actions` and wires it up via `on_loaded()`.
- `bridge/`: standalone subpackage. Must NOT import lucid (the bridge runs in its own process and venv if desired).
- `tests/test_integration_e2e.py`: integration-only; gated behind a marker so unit-test runs stay fast.

**Dependency rule:** `lucid_deck.bridge.*` imports nothing from `lucid` or `lucid_deck` (outside `bridge/`). Verified in Task 9.

---

## Task 1: Repo scaffolding

**Files:**
- Create: `~/PycharmProjects/ncs/lucid-deck/pyproject.toml`
- Create: `~/PycharmProjects/ncs/lucid-deck/README.md`
- Create: `~/PycharmProjects/ncs/lucid-deck/.gitignore`
- Create: `~/PycharmProjects/ncs/lucid-deck/src/lucid_deck/__init__.py`
- Create: `~/PycharmProjects/ncs/lucid-deck/tests/__init__.py`

- [ ] **Step 1: Create directory layout**

```bash
mkdir -p ~/PycharmProjects/ncs/lucid-deck/src/lucid_deck/bridge
mkdir -p ~/PycharmProjects/ncs/lucid-deck/tests
cd ~/PycharmProjects/ncs/lucid-deck
git init
```

- [ ] **Step 2: Write `pyproject.toml`**

```toml
[build-system]
requires = ["hatchling", "hatch-vcs"]
build-backend = "hatchling.build"

[project]
name = "lucid-deck"
description = "Companion-driven motor control surface for LUCID"
readme = "README.md"
license = "MIT"
requires-python = ">=3.11"
dynamic = ["version"]
authors = [{ name = "ALS Beamline Controls" }]
classifiers = [
    "Development Status :: 3 - Alpha",
    "License :: OSI Approved :: MIT License",
    "Programming Language :: Python :: 3.11",
    "Programming Language :: Python :: 3.12",
]
dependencies = [
    "lucid",
    "fastapi>=0.110",
    "uvicorn[standard]>=0.27",
    "httpx>=0.27",
    "nats-py>=2.6",
]

[project.optional-dependencies]
test = [
    "pytest>=7",
    "pytest-asyncio>=0.23",
    "pytest-qt>=4.2",
    "respx>=0.20",
]

[project.scripts]
companion-bridge = "lucid_deck.bridge.cli:main"

[project.entry-points."lucid.plugins"]
lucid_deck = "lucid_deck.manifest:manifest"

[tool.hatch.version]
source = "vcs"

[tool.hatch.build.hooks.vcs]
version-file = "src/lucid_deck/_version.py"

[tool.hatch.build.targets.wheel]
packages = ["src/lucid_deck"]

[tool.pytest.ini_options]
markers = ["integration: requires running nats-server"]
asyncio_mode = "auto"
```

- [ ] **Step 3: Write `README.md`**

```markdown
# lucid-deck

Companion-driven motor control surface for LUCID. See `docs/superpowers/specs/2026-05-05-lucid-deck-design.md` in the lucid repo for full design.

## Components

- `lucid_deck` — LUCID plugin (`MotorsSettingsPlugin` + NATS actions + readback publisher).
- `lucid_deck.bridge` — `companion-bridge` console-script (FastAPI ↔ NATS ↔ Companion HTTP).

## Quick start

```bash
# Install editable with test extras
pip install -e ".[test]"

# Run unit tests
.venv/Scripts/python -m pytest

# Run integration tests (requires nats-server)
.venv/Scripts/python -m pytest -m integration

# Run the bridge
COMPANION_URL=http://localhost:8000 NATS_URL=nats://localhost:4222 companion-bridge
```
```

- [ ] **Step 4: Write `.gitignore`**

```
__pycache__/
*.py[cod]
*.egg-info/
.venv/
.pytest_cache/
.ruff_cache/
build/
dist/
src/lucid_deck/_version.py
```

- [ ] **Step 5: Create empty package files**

`src/lucid_deck/__init__.py`:
```python
"""lucid-deck: Companion-driven motor control for LUCID."""
```

`src/lucid_deck/bridge/__init__.py`:
```python
"""companion-bridge: FastAPI ↔ NATS translation for Companion."""
```

`tests/__init__.py`: empty file.

- [ ] **Step 6: Create venv and install editable**

```bash
cd ~/PycharmProjects/ncs/lucid-deck
python -m venv .venv
.venv/Scripts/python -m pip install --upgrade pip
.venv/Scripts/python -m pip install -e "../ncs"  # lucid editable
.venv/Scripts/python -m pip install -e ".[test]"
```

Expected: `pip install` succeeds. Some warnings about lucid's many deps are normal.

- [ ] **Step 7: Baseline test run**

Run: `.venv/Scripts/python -m pytest`
Expected: `no tests ran in 0.0Xs` — confirms test collection works.

- [ ] **Step 8: Initial commit**

```bash
cd ~/PycharmProjects/ncs/lucid-deck
git add pyproject.toml README.md .gitignore src tests
git commit -m "chore: scaffold lucid-deck repo"
```

---

## Task 2: Mock positioner fixture

**Files:**
- Create: `~/PycharmProjects/ncs/lucid-deck/tests/conftest.py`
- Create: `~/PycharmProjects/ncs/lucid-deck/tests/test_conftest.py`

**Why this comes first:** Tasks 3 and 4 both need a controllable positioner stand-in. Putting it in a fixture removes ~50 lines of setup from each test module.

- [ ] **Step 1: Write the fixture self-test (TDD on the fixture itself)**

`tests/test_conftest.py`:
```python
"""Sanity check for the mock_positioner fixture."""
from __future__ import annotations


def test_mock_positioner_has_expected_attrs(mock_positioner):
    p = mock_positioner("samx")
    assert p.name == "samx"
    assert p.position == 0.0
    assert p.high_limit == 100.0
    assert p.low_limit == -100.0
    assert p.units == "mm"
    assert p.moving is False


def test_mock_positioner_move_updates_position(mock_positioner):
    p = mock_positioner("samx")
    status = p.move(5.0)
    assert p.position == 5.0
    assert status.done


def test_mock_positioner_move_at_high_limit_raises(mock_positioner):
    p = mock_positioner("samx", high_limit=10.0)
    p.move(10.0)  # to limit is OK
    try:
        p.move(11.0)  # past limit raises
    except ValueError as exc:
        assert "limit" in str(exc).lower()
    else:
        raise AssertionError("expected ValueError for past-limit move")


def test_mock_positioner_subscribe_fires_callback(mock_positioner):
    p = mock_positioner("samx")
    seen: list[float] = []
    p.subscribe(lambda value, **kw: seen.append(value))
    p.move(3.0)
    assert seen[-1] == 3.0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/python -m pytest tests/test_conftest.py -v`
Expected: FAIL — `fixture 'mock_positioner' not found`.

- [ ] **Step 3: Write the conftest fixture**

`tests/conftest.py`:
```python
"""Shared test fixtures."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

import pytest


@dataclass
class _Status:
    done: bool = True
    success: bool = True


class MockPositioner:
    """Minimal Ophyd-shaped positioner for unit tests.

    Implements the surface lucid_deck.motor_actions actually uses:
      - .name, .position, .high_limit, .low_limit, .units, .moving
      - .move(target) -> Status (synchronous; updates position immediately)
      - .stop()
      - .subscribe(callback) — callback fires on every state change
    """

    def __init__(
        self,
        name: str,
        position: float = 0.0,
        high_limit: float = 100.0,
        low_limit: float = -100.0,
        units: str = "mm",
    ) -> None:
        self.name = name
        self._position = position
        self.high_limit = high_limit
        self.low_limit = low_limit
        self.units = units
        self.moving = False
        self._subscribers: list[Callable] = []

    @property
    def position(self) -> float:
        return self._position

    def move(self, target: float) -> _Status:
        if target > self.high_limit:
            raise ValueError(f"target {target} above high limit {self.high_limit}")
        if target < self.low_limit:
            raise ValueError(f"target {target} below low limit {self.low_limit}")
        self.moving = True
        self._fire_subscribers()
        self._position = target
        self.moving = False
        self._fire_subscribers()
        return _Status(done=True, success=True)

    def stop(self) -> None:
        self.moving = False
        self._fire_subscribers()

    def subscribe(self, callback: Callable, **kwargs) -> int:
        self._subscribers.append(callback)
        return len(self._subscribers)

    def _fire_subscribers(self) -> None:
        for cb in self._subscribers:
            cb(value=self._position, obj=self, timestamp=0.0)


@pytest.fixture
def mock_positioner():
    """Factory fixture: ``mock_positioner('samx', high_limit=10.0)``."""
    def _make(name: str, **kwargs) -> MockPositioner:
        return MockPositioner(name=name, **kwargs)
    return _make
```

- [ ] **Step 4: Run test to verify pass**

Run: `.venv/Scripts/python -m pytest tests/test_conftest.py -v`
Expected: 4 PASS.

- [ ] **Step 5: Commit**

```bash
git add tests/conftest.py tests/test_conftest.py
git commit -m "test: add mock_positioner fixture"
```

---

## Task 3: motor_actions module

**Files:**
- Create: `~/PycharmProjects/ncs/lucid-deck/src/lucid_deck/motor_actions.py`
- Create: `~/PycharmProjects/ncs/lucid-deck/tests/test_motor_actions.py`

This task is pure logic (no NATS yet). NATS registration is deferred to Task 5. The module exposes a `MotorActionHandlers` class with one method per action; each method takes a `dict` payload and returns a `dict` reply, matching the action contract from the spec.

- [ ] **Step 1: Write failing tests**

`tests/test_motor_actions.py`:
```python
"""Tests for motor_actions handlers (pure dict-in/dict-out)."""
from __future__ import annotations

from lucid_deck.motor_actions import MotorActionHandlers


def _make_handlers(positioners):
    """Build handlers with a static positioner registry."""
    return MotorActionHandlers(registry=lambda: dict(positioners))


def test_list_returns_configured_motor_names(mock_positioner):
    h = _make_handlers({"samx": mock_positioner("samx"), "samy": mock_positioner("samy")})
    reply = h.list({})
    assert reply["ok"] is True
    assert sorted(reply["motors"]) == ["samx", "samy"]


def test_status_returns_full_state(mock_positioner):
    p = mock_positioner("samx", position=2.5, high_limit=10.0, low_limit=-10.0, units="mm")
    h = _make_handlers({"samx": p})
    reply = h.status({"name": "samx"})
    assert reply["ok"] is True
    s = reply["status"]
    assert s["position"] == 2.5
    assert s["high_limit"] == 10.0
    assert s["low_limit"] == -10.0
    assert s["units"] == "mm"
    assert s["at_high"] is False
    assert s["at_low"] is False
    assert s["moving"] is False


def test_status_unknown_motor(mock_positioner):
    h = _make_handlers({})
    reply = h.status({"name": "nope"})
    assert reply == {"ok": False, "code": "unknown_motor", "msg": "Motor 'nope' not configured"}


def test_select_returns_status(mock_positioner):
    p = mock_positioner("samx", position=1.0)
    h = _make_handlers({"samx": p})
    reply = h.select({"name": "samx"})
    assert reply["ok"] is True
    assert reply["status"]["position"] == 1.0


def test_jog_does_relative_move(mock_positioner):
    p = mock_positioner("samx", position=5.0)
    h = _make_handlers({"samx": p})
    reply = h.jog({"name": "samx", "delta": 0.5})
    assert reply == {"ok": True, "expected_setpoint": 5.5}
    assert p.position == 5.5


def test_jog_at_limit_returns_at_limit(mock_positioner):
    p = mock_positioner("samx", position=10.0, high_limit=10.0)
    h = _make_handlers({"samx": p})
    reply = h.jog({"name": "samx", "delta": 1.0})
    assert reply["ok"] is False
    assert reply["code"] == "at_limit"


def test_move_absolute(mock_positioner):
    p = mock_positioner("samx")
    h = _make_handlers({"samx": p})
    reply = h.move({"name": "samx", "position": 3.0})
    assert reply == {"ok": True, "expected_setpoint": 3.0}
    assert p.position == 3.0


def test_stop(mock_positioner):
    p = mock_positioner("samx")
    p.moving = True
    h = _make_handlers({"samx": p})
    reply = h.stop({"name": "samx"})
    assert reply == {"ok": True}
    assert p.moving is False


def test_hardware_error_caught(mock_positioner):
    class ExplodingPositioner:
        name = "kaboom"
        @property
        def position(self): raise RuntimeError("CA disconnect")
    h = _make_handlers({"kaboom": ExplodingPositioner()})
    reply = h.status({"name": "kaboom"})
    assert reply["ok"] is False
    assert reply["code"] == "hardware_error"
    assert "CA disconnect" in reply["msg"]
```

- [ ] **Step 2: Run tests, expect failure**

Run: `.venv/Scripts/python -m pytest tests/test_motor_actions.py -v`
Expected: ImportError on `lucid_deck.motor_actions`.

- [ ] **Step 3: Write the module**

`src/lucid_deck/motor_actions.py`:
```python
"""NATS action handlers for motor control.

Pure dict-in/dict-out logic — no NATS or Qt imports here. The
``register_with_ipc`` helper wires handlers to an :class:`IPCService`
in :mod:`lucid_deck.settings_plugin.on_loaded`.
"""
from __future__ import annotations

from typing import Any, Callable, Protocol


class _Positioner(Protocol):
    """The Ophyd surface motor_actions actually uses."""
    name: str
    position: float
    high_limit: float
    low_limit: float
    units: str
    moving: bool

    def move(self, target: float) -> Any: ...
    def stop(self) -> None: ...
    def subscribe(self, callback: Callable, **kwargs) -> int: ...


PositionerRegistry = Callable[[], dict[str, _Positioner]]


def _status_dict(p: _Positioner) -> dict[str, Any]:
    """Snapshot a positioner's full state as a JSON-serialisable dict."""
    pos = p.position
    return {
        "position": pos,
        "units": p.units,
        "high_limit": p.high_limit,
        "low_limit": p.low_limit,
        "at_high": pos >= p.high_limit,
        "at_low": pos <= p.low_limit,
        "moving": p.moving,
    }


def _wrap_errors(fn):
    """Convert any exception into a structured error reply."""
    def wrapper(self, payload):
        try:
            return fn(self, payload)
        except KeyError as exc:
            return {"ok": False, "code": "unknown_motor", "msg": str(exc)}
        except ValueError as exc:
            # Limits raise ValueError in our positioner contract.
            return {"ok": False, "code": "at_limit", "msg": str(exc)}
        except Exception as exc:
            return {"ok": False, "code": "hardware_error", "msg": f"{type(exc).__name__}: {exc}"}
    return wrapper


class MotorActionHandlers:
    """Stateless action handlers backed by a positioner registry.

    The registry is a zero-arg callable returning ``{name: positioner}``
    so the SettingsPlugin can swap motors without rebuilding handlers.
    """

    def __init__(self, registry: PositionerRegistry) -> None:
        self._registry = registry

    def _get(self, name: str) -> _Positioner:
        motors = self._registry()
        try:
            return motors[name]
        except KeyError:
            raise KeyError(f"Motor '{name}' not configured")

    def list(self, payload: dict) -> dict:
        return {"ok": True, "motors": sorted(self._registry().keys())}

    @_wrap_errors
    def status(self, payload: dict) -> dict:
        p = self._get(payload["name"])
        return {"ok": True, "status": _status_dict(p)}

    @_wrap_errors
    def select(self, payload: dict) -> dict:
        # `select` is just `status` with semantic intent — the bridge
        # tracks selection. Mirroring keeps the LUCID side stateless.
        p = self._get(payload["name"])
        return {"ok": True, "status": _status_dict(p)}

    @_wrap_errors
    def jog(self, payload: dict) -> dict:
        p = self._get(payload["name"])
        target = p.position + float(payload["delta"])
        p.move(target)
        return {"ok": True, "expected_setpoint": target}

    @_wrap_errors
    def move(self, payload: dict) -> dict:
        p = self._get(payload["name"])
        target = float(payload["position"])
        p.move(target)
        return {"ok": True, "expected_setpoint": target}

    @_wrap_errors
    def stop(self, payload: dict) -> dict:
        p = self._get(payload["name"])
        p.stop()
        return {"ok": True}
```

- [ ] **Step 4: Run tests**

Run: `.venv/Scripts/python -m pytest tests/test_motor_actions.py -v`
Expected: 9 PASS.

Note: `test_status_unknown_motor` expects the exact error message `"Motor 'nope' not configured"`. The KeyError message in `_get` is wrapped by `_wrap_errors` as `str(exc)`, which on KeyError gives `"\"Motor 'nope' not configured\""` (with surrounding quotes). If that test fails on string equality, change `_wrap_errors` to use `exc.args[0] if exc.args else str(exc)` for KeyError specifically. Verify and adjust.

- [ ] **Step 5: Commit**

```bash
git add src/lucid_deck/motor_actions.py tests/test_motor_actions.py
git commit -m "feat: motor_actions handlers with structured errors"
```

---

## Task 4: Readback publisher

**Files:**
- Create: `~/PycharmProjects/ncs/lucid-deck/src/lucid_deck/motor_actions.py` (modify — add `ReadbackPublisher`)
- Create: `~/PycharmProjects/ncs/lucid-deck/tests/test_readback_publisher.py`

The publisher subscribes to each positioner's Ophyd `subscribe` callback and forwards state changes onto the NATS bus via a `publish_fn` injected by the caller. Decoupling `publish_fn` from `IPCService` keeps the unit test pure.

- [ ] **Step 1: Write failing test**

`tests/test_readback_publisher.py`:
```python
from __future__ import annotations

from lucid_deck.motor_actions import ReadbackPublisher


def test_publisher_emits_on_motor_move(mock_positioner):
    captured: list[tuple[str, dict]] = []
    def publish(subject, data):
        captured.append((subject, data))

    p = mock_positioner("samx", position=0.0)
    pub = ReadbackPublisher(publish_fn=publish)
    pub.add_motor("samx", p)

    p.move(2.5)

    # MockPositioner fires subscribers twice per move (start + end).
    # Both should land on lucid.motors.readback.samx.
    subjects = [s for s, _ in captured]
    assert subjects == ["lucid.motors.readback.samx"] * 2
    assert captured[-1][1]["position"] == 2.5
    assert captured[-1][1]["moving"] is False


def test_publisher_remove_motor_stops_callbacks(mock_positioner):
    captured: list[tuple[str, dict]] = []
    p = mock_positioner("samx")
    pub = ReadbackPublisher(publish_fn=lambda s, d: captured.append((s, d)))
    pub.add_motor("samx", p)
    pub.remove_motor("samx")

    p.move(1.0)
    assert captured == []  # no callbacks fired after remove


def test_publisher_replaces_motor_on_re_add(mock_positioner):
    captured: list[tuple[str, dict]] = []
    p1 = mock_positioner("samx", position=0.0)
    p2 = mock_positioner("samx", position=10.0)
    pub = ReadbackPublisher(publish_fn=lambda s, d: captured.append((s, d)))
    pub.add_motor("samx", p1)
    pub.add_motor("samx", p2)  # replace

    p1.move(5.0)  # should NOT publish — p1 was replaced
    assert captured == []
    p2.move(11.0)  # should publish
    assert captured[-1][1]["position"] == 11.0
```

- [ ] **Step 2: Run, expect failure**

Run: `.venv/Scripts/python -m pytest tests/test_readback_publisher.py -v`
Expected: ImportError on `ReadbackPublisher`.

- [ ] **Step 3: Add `ReadbackPublisher` to motor_actions.py**

Append to `src/lucid_deck/motor_actions.py`:
```python
class ReadbackPublisher:
    """Bridges Ophyd subscribe() callbacks to NATS publish().

    Owns one Ophyd subscription per registered motor. ``publish_fn`` is
    injected so unit tests don't need NATS; in production it's
    ``IPCService.publish``-shaped (takes ``subject, data``).
    """

    SUBJECT_FMT = "lucid.motors.readback.{name}"

    def __init__(self, publish_fn: Callable[[str, dict], None]) -> None:
        self._publish = publish_fn
        # name -> (positioner, subscription_id)
        self._subs: dict[str, tuple[_Positioner, int]] = {}

    def add_motor(self, name: str, positioner: _Positioner) -> None:
        # Drop prior registration if any (idempotent re-add).
        if name in self._subs:
            self.remove_motor(name)

        def callback(value=None, obj=None, **kwargs) -> None:
            # Re-snapshot — Ophyd callback args are unreliable across classes.
            data = _status_dict(positioner)
            self._publish(self.SUBJECT_FMT.format(name=name), data)

        sub_id = positioner.subscribe(callback)
        self._subs[name] = (positioner, sub_id)

    def remove_motor(self, name: str) -> None:
        entry = self._subs.pop(name, None)
        if entry is None:
            return
        p, sub_id = entry
        # Best-effort unsubscribe; not all positioners support it identically.
        unsubscribe = getattr(p, "unsubscribe", None)
        if callable(unsubscribe):
            try:
                unsubscribe(sub_id)
            except Exception:
                pass

    def clear(self) -> None:
        for name in list(self._subs):
            self.remove_motor(name)
```

Note: `MockPositioner` in conftest doesn't implement `unsubscribe`. The third test (`test_publisher_remove_motor_stops_callbacks`) will fail because `remove_motor` can't actually detach. Fix the fixture: add an `unsubscribe(sub_id)` method that pops the callback from `_subscribers` by index. Update `subscribe` to return a stable id (the index of the callback) and `unsubscribe` to remove that callback. Add the test for replacement (`test_publisher_replaces_motor_on_re_add`) only after this works.

Patch `tests/conftest.py` `MockPositioner`:
```python
    def subscribe(self, callback: Callable, **kwargs) -> int:
        self._subscribers.append(callback)
        return len(self._subscribers) - 1  # index as id

    def unsubscribe(self, sub_id: int) -> None:
        if 0 <= sub_id < len(self._subscribers):
            self._subscribers[sub_id] = lambda **kw: None  # neuter, keep indices stable
```

- [ ] **Step 4: Run tests**

Run: `.venv/Scripts/python -m pytest tests/test_readback_publisher.py tests/test_conftest.py -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add src/lucid_deck/motor_actions.py tests/test_readback_publisher.py tests/conftest.py
git commit -m "feat: ReadbackPublisher for motor state events"
```

---

## Task 5: MotorsSettingsPlugin

**Files:**
- Create: `~/PycharmProjects/ncs/lucid-deck/src/lucid_deck/settings_plugin.py`
- Create: `~/PycharmProjects/ncs/lucid-deck/tests/test_settings_plugin.py`

The plugin owns lifecycle: builds the Qt UI, persists settings to `PreferencesManager`, and on `on_loaded()` registers NATS actions and starts the readback publisher. Catalog access goes through `lucid.devices.catalog`.

**Tests use `pytest-qt` and skip if a `QApplication` cannot start (headless CI).**

- [ ] **Step 1: Write failing tests**

`tests/test_settings_plugin.py`:
```python
"""Tests for MotorsSettingsPlugin.

Most tests focus on settings persistence and motor-list resolution,
not the Qt widget itself (covered by smoke test only).
"""
from __future__ import annotations

import pytest

pytest.importorskip("PySide6")

from unittest.mock import MagicMock, patch

from lucid_deck.settings_plugin import (
    MotorsSettingsPlugin,
    PREF_SELECTED_MOTORS,
    PREF_STEP_VALUES,
    DEFAULT_STEP_VALUES,
)


def test_plugin_metadata():
    plugin = MotorsSettingsPlugin()
    assert plugin.name == "motor_deck"
    assert plugin.display_name == "Motor Deck"
    assert plugin.category == "devices"


def test_resolve_motors_from_catalog(mock_positioner):
    """Plugin should fetch positioners from lucid's catalog by name."""
    plugin = MotorsSettingsPlugin()
    fake_catalog = MagicMock()
    samx = mock_positioner("samx")
    fake_catalog.get_device.side_effect = lambda n: {"samx": samx}.get(n)

    with patch("lucid_deck.settings_plugin._get_catalog", return_value=fake_catalog):
        result = plugin._resolve_motors(["samx", "missing"])

    assert "samx" in result
    assert "missing" not in result  # missing motors silently dropped + logged


def test_default_step_values_seed_companion_step():
    """The plugin exposes default step list for Companion seeding."""
    plugin = MotorsSettingsPlugin()
    assert DEFAULT_STEP_VALUES == [0.001, 0.01, 0.1, 1.0, 10.0]
    # Initial step is the middle of the list.
    assert plugin.initial_step == 0.1


def test_save_settings_persists_to_preferences(qtbot):
    plugin = MotorsSettingsPlugin()
    widget = plugin.create_widget()
    qtbot.addWidget(widget)

    plugin._motor_list_widget.set_selected(["samx", "samy"])
    plugin._step_values_widget.set_values([0.1, 1.0])

    fake_prefs = MagicMock()
    with patch("lucid_deck.settings_plugin.PreferencesManager.get_instance", return_value=fake_prefs):
        plugin.save_settings()

    fake_prefs.set.assert_any_call(PREF_SELECTED_MOTORS, ["samx", "samy"])
    fake_prefs.set.assert_any_call(PREF_STEP_VALUES, [0.1, 1.0])


def test_on_loaded_registers_actions_and_publisher(mock_positioner):
    plugin = MotorsSettingsPlugin()
    fake_ipc = MagicMock()
    fake_prefs = MagicMock()
    fake_prefs.get.side_effect = lambda key, default=None: {
        PREF_SELECTED_MOTORS: ["samx"],
        PREF_STEP_VALUES: [0.1, 1.0],
    }.get(key, default)

    with patch("lucid_deck.settings_plugin.get_ipc_service", return_value=fake_ipc), \
         patch("lucid_deck.settings_plugin.PreferencesManager.get_instance", return_value=fake_prefs), \
         patch("lucid_deck.settings_plugin._get_catalog") as fake_get_catalog:
        fake_get_catalog.return_value.get_device.return_value = mock_positioner("samx")
        plugin.on_loaded()

    # Six actions registered (list, status, select, jog, move, stop)
    assert fake_ipc.register_action.call_count == 6
    suffixes = [c.args[0] for c in fake_ipc.register_action.call_args_list]
    assert sorted(suffixes) == sorted([
        "motors.action.list",
        "motors.action.status",
        "motors.action.select",
        "motors.action.jog",
        "motors.action.move",
        "motors.action.stop",
    ])
```

- [ ] **Step 2: Run, expect failure**

Run: `.venv/Scripts/python -m pytest tests/test_settings_plugin.py -v`
Expected: ImportError.

- [ ] **Step 3: Implement the plugin**

`src/lucid_deck/settings_plugin.py`:
```python
"""Motors settings plugin.

UI for picking which catalogued motors are exposed to Companion and the
step value list. On ``on_loaded()`` registers NATS action handlers and
starts the readback publisher.
"""
from __future__ import annotations

from typing import Any

from loguru import logger
from PySide6.QtWidgets import (
    QDoubleSpinBox,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from lucid.devices.catalog import get_catalog
from lucid.devices.model import DeviceCategory
from lucid.ipc.service import get_ipc_service
from lucid.plugins.settings_plugin import SettingsPlugin
from lucid.ui.preferences.manager import PreferencesManager

from lucid_deck.motor_actions import (
    MotorActionHandlers,
    ReadbackPublisher,
    PositionerRegistry,
)


PREF_SELECTED_MOTORS = "lucid_deck.selected_motors"
PREF_STEP_VALUES = "lucid_deck.step_values"
DEFAULT_STEP_VALUES = [0.001, 0.01, 0.1, 1.0, 10.0]


def _get_catalog():
    """Indirection so tests can patch the catalog accessor."""
    return get_catalog()


class _MotorListWidget(QWidget):
    """Multi-pick list of catalogued motors."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("Motors exposed to Companion:"))
        self._list = QListWidget()
        layout.addWidget(self._list)

    def populate(self, available: list[str]) -> None:
        self._list.clear()
        for name in available:
            item = QListWidgetItem(name)
            item.setFlags(item.flags() | 0x10)  # ItemIsUserCheckable
            item.setCheckState(0)  # Unchecked
            self._list.addItem(item)

    def set_selected(self, names: list[str]) -> None:
        for i in range(self._list.count()):
            item = self._list.item(i)
            item.setCheckState(2 if item.text() in names else 0)

    def selected(self) -> list[str]:
        return [
            self._list.item(i).text()
            for i in range(self._list.count())
            if self._list.item(i).checkState() == 2
        ]


class _StepValuesWidget(QWidget):
    """Editable list of jog step values."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("Step values (jog deltas):"))
        self._list = QListWidget()
        layout.addWidget(self._list)
        button_row = QHBoxLayout()
        add_btn = QPushButton("Add…")
        rm_btn = QPushButton("Remove")
        add_btn.clicked.connect(self._add)
        rm_btn.clicked.connect(self._remove)
        button_row.addWidget(add_btn)
        button_row.addWidget(rm_btn)
        layout.addLayout(button_row)

    def set_values(self, values: list[float]) -> None:
        self._list.clear()
        for v in values:
            self._list.addItem(str(v))

    def values(self) -> list[float]:
        return [float(self._list.item(i).text()) for i in range(self._list.count())]

    def _add(self) -> None:
        spin = QDoubleSpinBox()
        spin.setDecimals(6)
        spin.setRange(-1e6, 1e6)
        spin.setValue(0.1)
        # Simplified: just append a default entry. Full edit dialog is out of scope.
        self._list.addItem(str(spin.value()))

    def _remove(self) -> None:
        for item in self._list.selectedItems():
            self._list.takeItem(self._list.row(item))


class MotorsSettingsPlugin(SettingsPlugin):
    """Settings page + lifecycle for lucid-deck."""

    @property
    def name(self) -> str:
        return "motor_deck"

    @property
    def display_name(self) -> str:
        return "Motor Deck"

    @property
    def category(self) -> str:
        return "devices"

    @property
    def initial_step(self) -> float:
        """Middle-of-list default for Companion's motor_step seed."""
        return DEFAULT_STEP_VALUES[len(DEFAULT_STEP_VALUES) // 2]

    def __init__(self) -> None:
        super().__init__()
        self._widget: QWidget | None = None
        self._motor_list_widget: _MotorListWidget | None = None
        self._step_values_widget: _StepValuesWidget | None = None
        self._action_handles: list[Any] = []
        self._publisher: ReadbackPublisher | None = None
        # Cached resolved positioners; refreshed on save and on_loaded.
        self._motors: dict = {}

    # ---------------- SettingsPlugin lifecycle ----------------

    def create_widget(self, parent: QWidget | None = None) -> QWidget:
        if self._widget is not None:
            return self._widget
        widget = QWidget(parent)
        layout = QVBoxLayout(widget)
        self._motor_list_widget = _MotorListWidget(widget)
        self._step_values_widget = _StepValuesWidget(widget)
        layout.addWidget(self._motor_list_widget)
        layout.addWidget(self._step_values_widget)
        self._widget = widget
        return widget

    def load_settings(self) -> None:
        prefs = PreferencesManager.get_instance()
        # Populate available motors from catalog.
        try:
            catalog = _get_catalog()
            available = [d.name for d in catalog.list_devices(category=DeviceCategory.MOTOR)]
        except Exception as exc:
            logger.warning("lucid-deck: failed to list catalog motors: {}", exc)
            available = []
        self._motor_list_widget.populate(available)
        self._motor_list_widget.set_selected(prefs.get(PREF_SELECTED_MOTORS, []))
        self._step_values_widget.set_values(prefs.get(PREF_STEP_VALUES, DEFAULT_STEP_VALUES))

    def save_settings(self) -> None:
        prefs = PreferencesManager.get_instance()
        prefs.set(PREF_SELECTED_MOTORS, self._motor_list_widget.selected())
        prefs.set(PREF_STEP_VALUES, self._step_values_widget.values())
        # Refresh the live registry so action handlers see the new list.
        self._refresh_motors()

    # ---------------- preload init ----------------

    def on_loaded(self) -> None:
        """Called once at LUCID startup for preload plugins.

        Registers NATS actions and starts readback publisher using the
        currently saved selection. If there is no saved selection yet
        (first-run) this is a no-op until the user saves prefs.
        """
        self._refresh_motors()

        ipc = get_ipc_service()
        if ipc is None:
            logger.warning("lucid-deck: IPCService not available; skipping action registration")
            return

        handlers = MotorActionHandlers(registry=lambda: self._motors)

        def _wrap(handler):
            def _cb(subject: str, data: dict, reply: str | None) -> None:
                if reply is None:
                    return
                ipc.reply(reply, handler(data or {}))
            return _cb

        action_map = {
            "motors.action.list": handlers.list,
            "motors.action.status": handlers.status,
            "motors.action.select": handlers.select,
            "motors.action.jog": handlers.jog,
            "motors.action.move": handlers.move,
            "motors.action.stop": handlers.stop,
        }
        for suffix, handler in action_map.items():
            handle = ipc.register_action(
                suffix,
                _wrap(handler),
                description=f"lucid-deck {suffix}",
            )
            self._action_handles.append(handle)

        self._publisher = ReadbackPublisher(publish_fn=ipc.publish)
        for name, positioner in self._motors.items():
            self._publisher.add_motor(name, positioner)

    def teardown(self) -> None:
        for h in self._action_handles:
            try:
                h.unregister()
            except Exception:
                pass
        self._action_handles.clear()
        if self._publisher is not None:
            self._publisher.clear()
            self._publisher = None

    # ---------------- internals ----------------

    def _resolve_motors(self, names: list[str]) -> dict:
        catalog = _get_catalog()
        result: dict = {}
        for name in names:
            try:
                device = catalog.get_device(name)
            except Exception as exc:
                logger.warning("lucid-deck: catalog.get_device({}) failed: {}", name, exc)
                device = None
            if device is None:
                logger.warning("lucid-deck: motor '{}' not found in catalog; skipping", name)
                continue
            result[name] = device
        return result

    def _refresh_motors(self) -> None:
        prefs = PreferencesManager.get_instance()
        selected = prefs.get(PREF_SELECTED_MOTORS, [])
        new_motors = self._resolve_motors(selected)

        # Update publisher subscriptions to match new selection.
        if self._publisher is not None:
            old = set(self._motors.keys())
            new = set(new_motors.keys())
            for name in old - new:
                self._publisher.remove_motor(name)
            for name in new - old:
                self._publisher.add_motor(name, new_motors[name])
            # Replacement (same name, different object): refresh.
            for name in old & new:
                if self._motors[name] is not new_motors[name]:
                    self._publisher.add_motor(name, new_motors[name])

        self._motors = new_motors
```

- [ ] **Step 4: Run tests**

Run: `.venv/Scripts/python -m pytest tests/test_settings_plugin.py -v`
Expected: all PASS. If `lucid.devices.catalog.get_catalog` or `get_device` does not exist with that signature, adjust the import in `_get_catalog` and the patch target in tests to match the real API. Verify by inspecting `~/PycharmProjects/ncs/ncs/src/lucid/devices/catalog.py` before writing tests.

- [ ] **Step 5: Commit**

```bash
git add src/lucid_deck/settings_plugin.py tests/test_settings_plugin.py
git commit -m "feat: MotorsSettingsPlugin with NATS action wiring"
```

---

## Task 6: Plugin manifest

**Files:**
- Create: `~/PycharmProjects/ncs/lucid-deck/src/lucid_deck/manifest.py`
- Create: `~/PycharmProjects/ncs/lucid-deck/tests/test_manifest.py`

- [ ] **Step 1: Write failing test**

`tests/test_manifest.py`:
```python
from __future__ import annotations


def test_manifest_exposes_motor_deck_settings_plugin():
    from lucid_deck.manifest import manifest
    assert manifest.name == "lucid-deck"
    settings_entries = [p for p in manifest.plugins if p.type_name == "settings"]
    assert len(settings_entries) == 1
    assert settings_entries[0].name == "motor_deck"
    assert settings_entries[0].import_path == "lucid_deck.settings_plugin:MotorsSettingsPlugin"


def test_manifest_settings_entry_preloads():
    """Preload=True is required so on_loaded() runs at LUCID startup."""
    from lucid_deck.manifest import manifest
    entry = next(p for p in manifest.plugins if p.name == "motor_deck")
    assert entry.preload is True


def test_manifest_loadable_via_entry_point():
    """The pyproject entry-point points at this manifest object."""
    import importlib
    mod = importlib.import_module("lucid_deck.manifest")
    assert hasattr(mod, "manifest")
```

- [ ] **Step 2: Run, expect failure**

Run: `.venv/Scripts/python -m pytest tests/test_manifest.py -v`

- [ ] **Step 3: Write the manifest**

`src/lucid_deck/manifest.py`:
```python
"""Plugin manifest for lucid-deck."""
from __future__ import annotations

from lucid.plugins.manifest import PluginEntry, PluginManifest


manifest = PluginManifest(
    name="lucid-deck",
    version="0.1.0",
    description="Companion-driven motor control surface for LUCID",
    plugins=[
        PluginEntry(
            type_name="settings",
            name="motor_deck",
            import_path="lucid_deck.settings_plugin:MotorsSettingsPlugin",
            preload=True,
        ),
    ],
)
```

- [ ] **Step 4: Run tests**

Run: `.venv/Scripts/python -m pytest tests/test_manifest.py -v`
Expected: 3 PASS.

- [ ] **Step 5: Commit**

```bash
git add src/lucid_deck/manifest.py tests/test_manifest.py
git commit -m "feat: register lucid-deck plugin manifest"
```

---

## Task 7: Bridge config + CLI entry point

**Files:**
- Create: `~/PycharmProjects/ncs/lucid-deck/src/lucid_deck/bridge/config.py`
- Create: `~/PycharmProjects/ncs/lucid-deck/src/lucid_deck/bridge/cli.py`
- Create: `~/PycharmProjects/ncs/lucid-deck/src/lucid_deck/bridge/__main__.py`
- Create: `~/PycharmProjects/ncs/lucid-deck/tests/test_bridge_config.py`

- [ ] **Step 1: Write failing test**

`tests/test_bridge_config.py`:
```python
"""Bridge config: env var + CLI flag parsing."""
from __future__ import annotations

import pytest

from lucid_deck.bridge.config import BridgeConfig, load_config


def test_defaults():
    cfg = BridgeConfig()
    assert cfg.bind_host == "127.0.0.1"
    assert cfg.bind_port == 8765
    assert cfg.lucid_prefix == "lucid"


def test_env_vars(monkeypatch):
    monkeypatch.setenv("NATS_URL", "nats://other:4222")
    monkeypatch.setenv("COMPANION_URL", "http://comp:8000")
    monkeypatch.setenv("BIND_HOST", "0.0.0.0")
    monkeypatch.setenv("BIND_PORT", "9000")
    monkeypatch.setenv("LUCID_PREFIX", "ncs")
    cfg = load_config(argv=[])
    assert cfg.nats_url == "nats://other:4222"
    assert cfg.companion_url == "http://comp:8000"
    assert cfg.bind_host == "0.0.0.0"
    assert cfg.bind_port == 9000
    assert cfg.lucid_prefix == "ncs"


def test_cli_flags_override_env(monkeypatch):
    monkeypatch.setenv("BIND_PORT", "9000")
    cfg = load_config(argv=["--bind-port", "12345"])
    assert cfg.bind_port == 12345


def test_required_companion_url_missing_raises(monkeypatch):
    monkeypatch.delenv("COMPANION_URL", raising=False)
    with pytest.raises(SystemExit):
        load_config(argv=[])
```

- [ ] **Step 2: Run, expect failure**

Run: `.venv/Scripts/python -m pytest tests/test_bridge_config.py -v`

- [ ] **Step 3: Implement config**

`src/lucid_deck/bridge/config.py`:
```python
"""Bridge configuration via env vars + CLI flags."""
from __future__ import annotations

import argparse
import os
import sys
from dataclasses import dataclass


@dataclass
class BridgeConfig:
    nats_url: str = "nats://localhost:4222"
    companion_url: str = ""
    bind_host: str = "127.0.0.1"
    bind_port: int = 8765
    lucid_prefix: str = "lucid"


def load_config(argv: list[str] | None = None) -> BridgeConfig:
    parser = argparse.ArgumentParser(prog="companion-bridge")
    parser.add_argument("--nats-url", default=os.environ.get("NATS_URL", "nats://localhost:4222"))
    parser.add_argument("--companion-url", default=os.environ.get("COMPANION_URL", ""))
    parser.add_argument("--bind-host", default=os.environ.get("BIND_HOST", "127.0.0.1"))
    parser.add_argument("--bind-port", type=int, default=int(os.environ.get("BIND_PORT", "8765")))
    parser.add_argument("--lucid-prefix", default=os.environ.get("LUCID_PREFIX", "lucid"))
    ns = parser.parse_args(argv)

    if not ns.companion_url:
        print("ERROR: COMPANION_URL or --companion-url is required", file=sys.stderr)
        raise SystemExit(2)

    return BridgeConfig(
        nats_url=ns.nats_url,
        companion_url=ns.companion_url.rstrip("/"),
        bind_host=ns.bind_host,
        bind_port=ns.bind_port,
        lucid_prefix=ns.lucid_prefix,
    )
```

- [ ] **Step 4: Implement CLI entry point + `__main__`**

`src/lucid_deck/bridge/cli.py`:
```python
"""Console-script entry point: ``companion-bridge``."""
from __future__ import annotations

import sys

import uvicorn

from lucid_deck.bridge.app import create_app
from lucid_deck.bridge.config import load_config


def main() -> int:
    cfg = load_config(argv=sys.argv[1:])
    app = create_app(cfg)
    uvicorn.run(app, host=cfg.bind_host, port=cfg.bind_port)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

`src/lucid_deck/bridge/__main__.py`:
```python
from lucid_deck.bridge.cli import main
raise SystemExit(main())
```

- [ ] **Step 5: Run tests**

Run: `.venv/Scripts/python -m pytest tests/test_bridge_config.py -v`
Expected: 4 PASS.

- [ ] **Step 6: Commit**

```bash
git add src/lucid_deck/bridge/config.py src/lucid_deck/bridge/cli.py src/lucid_deck/bridge/__main__.py tests/test_bridge_config.py
git commit -m "feat: bridge config + CLI entry point"
```

---

## Task 8: Companion HTTP client

**Files:**
- Create: `~/PycharmProjects/ncs/lucid-deck/src/lucid_deck/bridge/companion_client.py`
- Create: `~/PycharmProjects/ncs/lucid-deck/tests/test_bridge_companion_client.py`

- [ ] **Step 1: Write failing test**

`tests/test_bridge_companion_client.py`:
```python
from __future__ import annotations

import httpx
import pytest
import respx

from lucid_deck.bridge.companion_client import CompanionClient


@pytest.mark.asyncio
async def test_set_variable_posts_to_companion():
    async with respx.mock(base_url="http://comp:8000") as mock:
        route = mock.post("/api/custom-variable/motor_position/value").mock(
            return_value=httpx.Response(200)
        )
        client = CompanionClient("http://comp:8000")
        await client.set_variable("motor_position", "5.3")
        await client.aclose()

        assert route.called
        assert route.calls.last.request.content == b"5.3"


@pytest.mark.asyncio
async def test_set_variables_batches_pushes():
    async with respx.mock(base_url="http://comp:8000") as mock:
        mock.post().mock(return_value=httpx.Response(200))
        client = CompanionClient("http://comp:8000")
        await client.set_variables({"motor_position": "5", "motor_units": "mm"})
        await client.aclose()
        assert mock.calls.call_count == 2


@pytest.mark.asyncio
async def test_set_variable_swallows_failures():
    """Push is fire-and-forget; bridge logs and continues on failure."""
    async with respx.mock(base_url="http://comp:8000") as mock:
        mock.post().mock(side_effect=httpx.ConnectError("nope"))
        client = CompanionClient("http://comp:8000")
        # Should NOT raise.
        await client.set_variable("motor_position", "5")
        await client.aclose()
```

- [ ] **Step 2: Run, expect failure**

Run: `.venv/Scripts/python -m pytest tests/test_bridge_companion_client.py -v`

- [ ] **Step 3: Implement**

`src/lucid_deck/bridge/companion_client.py`:
```python
"""Async HTTP client for pushing Companion custom variables."""
from __future__ import annotations

import logging

import httpx


logger = logging.getLogger(__name__)


class CompanionClient:
    """Posts variable updates to a single Companion instance.

    Fire-and-forget: failures are logged and swallowed (the next readback
    supersedes the missed update).
    """

    def __init__(self, base_url: str, timeout: float = 2.0) -> None:
        self._base_url = base_url.rstrip("/")
        self._client = httpx.AsyncClient(base_url=self._base_url, timeout=timeout)

    async def aclose(self) -> None:
        await self._client.aclose()

    async def set_variable(self, name: str, value: str) -> None:
        url = f"/api/custom-variable/{name}/value"
        try:
            await self._client.post(url, content=str(value).encode())
        except Exception as exc:
            logger.warning("companion-bridge: failed to push %s=%r: %s", name, value, exc)

    async def set_variables(self, mapping: dict[str, str]) -> None:
        for name, value in mapping.items():
            await self.set_variable(name, value)
```

- [ ] **Step 4: Run tests**

Run: `.venv/Scripts/python -m pytest tests/test_bridge_companion_client.py -v`
Expected: 3 PASS.

- [ ] **Step 5: Commit**

```bash
git add src/lucid_deck/bridge/companion_client.py tests/test_bridge_companion_client.py
git commit -m "feat: bridge CompanionClient for variable push"
```

---

## Task 9: Bridge FastAPI app + NATS client

**Files:**
- Create: `~/PycharmProjects/ncs/lucid-deck/src/lucid_deck/bridge/nats_client.py`
- Create: `~/PycharmProjects/ncs/lucid-deck/src/lucid_deck/bridge/app.py`
- Create: `~/PycharmProjects/ncs/lucid-deck/tests/test_bridge_app.py`

The NATS client is wrapped behind a small abstraction (`NatsLink`) so tests don't need a running NATS server. The FastAPI app receives `NatsLink` and `CompanionClient` as dependencies, so they can be replaced with fakes.

- [ ] **Step 1: Write failing test**

`tests/test_bridge_app.py`:
```python
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from lucid_deck.bridge.app import create_app, BridgeState
from lucid_deck.bridge.config import BridgeConfig


class FakeNats:
    """In-memory NATS double for tests."""
    def __init__(self):
        self.requests: list[tuple[str, dict]] = []
        self.replies: dict[str, dict] = {}

    async def request(self, subject: str, payload: dict, timeout: float = 2.0) -> dict:
        self.requests.append((subject, payload))
        if subject in self.replies:
            return self.replies[subject]
        return {"ok": True, "status": {"position": 1.0, "units": "mm",
                                        "high_limit": 10.0, "low_limit": -10.0,
                                        "at_high": False, "at_low": False, "moving": False}}


class FakeCompanion:
    def __init__(self):
        self.pushes: list[tuple[str, str]] = []
    async def set_variable(self, name, value):
        self.pushes.append((name, str(value)))
    async def set_variables(self, mapping):
        for k, v in mapping.items():
            await self.set_variable(k, v)
    async def aclose(self):
        pass


@pytest.fixture
def app_pair():
    cfg = BridgeConfig(nats_url="", companion_url="http://x", lucid_prefix="lucid")
    nats = FakeNats()
    comp = FakeCompanion()
    state = BridgeState(config=cfg, nats=nats, companion=comp)
    app = create_app(cfg, state=state)
    return TestClient(app), state


def test_select_calls_nats_and_pushes_variables(app_pair):
    client, state = app_pair
    response = client.post("/select", params={"name": "samx"})
    assert response.status_code == 200
    assert state.nats.requests[0] == ("lucid.motors.action.select", {"name": "samx"})
    pushes = dict(state.companion.pushes)
    assert pushes["motor_selected"] == "samx"
    assert pushes["motor_position"] == "1.0"
    assert pushes["motor_units"] == "mm"
    assert pushes["motor_status_msg"] == ""
    assert state.selected_motor == "samx"


def test_jog_uses_selected_motor(app_pair):
    client, state = app_pair
    client.post("/select", params={"name": "samx"})
    state.nats.requests.clear()
    response = client.post("/jog", params={"delta": "0.5"})
    assert response.status_code == 200
    assert state.nats.requests[-1] == ("lucid.motors.action.jog", {"name": "samx", "delta": 0.5})


def test_jog_with_no_selection_returns_400(app_pair):
    client, _ = app_pair
    response = client.post("/jog", params={"delta": "0.5"})
    assert response.status_code == 400


def test_jog_at_limit_returns_409_and_pushes_status_msg(app_pair):
    client, state = app_pair
    client.post("/select", params={"name": "samx"})
    state.nats.replies["lucid.motors.action.jog"] = {
        "ok": False, "code": "at_limit", "msg": "above high limit",
    }
    state.companion.pushes.clear()
    response = client.post("/jog", params={"delta": "5.0"})
    assert response.status_code == 409
    pushes = dict(state.companion.pushes)
    assert "above high limit" in pushes["motor_status_msg"]


def test_unknown_motor_returns_404(app_pair):
    client, state = app_pair
    state.nats.replies["lucid.motors.action.select"] = {
        "ok": False, "code": "unknown_motor", "msg": "Motor 'nope' not configured",
    }
    response = client.post("/select", params={"name": "nope"})
    assert response.status_code == 404


def test_status_endpoint_returns_bridge_view(app_pair):
    client, state = app_pair
    client.post("/select", params={"name": "samx"})
    response = client.get("/status")
    assert response.status_code == 200
    body = response.json()
    assert body["selected_motor"] == "samx"
    assert "last_readback" in body


def test_bridge_does_not_import_lucid():
    """The bridge subpackage must be runnable without lucid installed."""
    import sys
    bridge_modules = [n for n in sys.modules if n.startswith("lucid_deck.bridge")]
    for mod_name in bridge_modules:
        mod = sys.modules[mod_name]
        for attr in dir(mod):
            value = getattr(mod, attr)
            if hasattr(value, "__module__"):
                origin = value.__module__
                assert not origin.startswith("lucid."), \
                    f"{mod_name}.{attr} originates in {origin}"
```

- [ ] **Step 2: Run, expect failure**

Run: `.venv/Scripts/python -m pytest tests/test_bridge_app.py -v`

- [ ] **Step 3: Implement nats_client**

`src/lucid_deck/bridge/nats_client.py`:
```python
"""Thin async wrapper around nats-py for the bridge."""
from __future__ import annotations

import json
from typing import Any, Callable, Protocol

import nats


class NatsLink(Protocol):
    async def request(self, subject: str, payload: dict, timeout: float = 2.0) -> dict: ...


class NatsClient:
    """Real nats-py implementation of NatsLink with reconnect."""

    def __init__(self, url: str) -> None:
        self._url = url
        self._nc: Any = None

    async def connect(self) -> None:
        self._nc = await nats.connect(self._url, reconnect_time_wait=1.0, max_reconnect_attempts=-1)

    async def close(self) -> None:
        if self._nc is not None:
            await self._nc.drain()

    async def request(self, subject: str, payload: dict, timeout: float = 2.0) -> dict:
        if self._nc is None:
            raise RuntimeError("NatsClient not connected")
        msg = await self._nc.request(subject, json.dumps(payload).encode(), timeout=timeout)
        return json.loads(msg.data.decode())

    async def subscribe(self, subject: str, callback: Callable[[str, dict], Any]) -> Any:
        if self._nc is None:
            raise RuntimeError("NatsClient not connected")
        async def _cb(msg):
            data = json.loads(msg.data.decode())
            await callback(msg.subject, data)
        return await self._nc.subscribe(subject, cb=_cb)
```

- [ ] **Step 4: Implement FastAPI app**

`src/lucid_deck/bridge/app.py`:
```python
"""FastAPI app for the companion-bridge."""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Any

from fastapi import FastAPI, HTTPException, Query

from lucid_deck.bridge.companion_client import CompanionClient
from lucid_deck.bridge.config import BridgeConfig
from lucid_deck.bridge.nats_client import NatsClient, NatsLink


logger = logging.getLogger(__name__)


@dataclass
class BridgeState:
    """Mutable runtime state, injected so tests can substitute fakes."""
    config: BridgeConfig
    nats: NatsLink
    companion: Any  # CompanionClient-shaped
    selected_motor: str | None = None
    last_readback: dict = field(default_factory=dict)


# Map LUCID error codes → HTTP status.
_CODE_TO_HTTP = {
    "unknown_motor": 404,
    "at_limit": 409,
    "hardware_error": 502,
    "internal": 500,
}


def _action_subject(state: BridgeState, verb: str) -> str:
    return f"{state.config.lucid_prefix}.motors.action.{verb}"


async def _push_status_to_companion(state: BridgeState, status: dict, name: str) -> None:
    state.last_readback = status
    await state.companion.set_variables({
        "motor_selected": name,
        "motor_position": str(status["position"]),
        "motor_units": str(status["units"]),
        "motor_high_limit": str(status["high_limit"]),
        "motor_low_limit": str(status["low_limit"]),
        "motor_at_high": "1" if status["at_high"] else "0",
        "motor_at_low": "1" if status["at_low"] else "0",
        "motor_moving": "1" if status["moving"] else "0",
        "motor_status_msg": "",
    })


async def _push_error(state: BridgeState, msg: str) -> None:
    await state.companion.set_variable("motor_status_msg", msg)


def _raise_for_reply(reply: dict) -> None:
    if reply.get("ok"):
        return
    code = reply.get("code", "internal")
    raise HTTPException(status_code=_CODE_TO_HTTP.get(code, 500), detail=reply.get("msg", code))


def _require_selection(state: BridgeState) -> str:
    if state.selected_motor is None:
        raise HTTPException(status_code=400, detail="no motor selected")
    return state.selected_motor


def create_app(cfg: BridgeConfig, *, state: BridgeState | None = None) -> FastAPI:
    if state is None:
        # Real wiring at production startup.
        state = BridgeState(
            config=cfg,
            nats=NatsClient(cfg.nats_url),
            companion=CompanionClient(cfg.companion_url),
        )

    app = FastAPI(title="companion-bridge")
    app.state.bridge = state

    @app.on_event("startup")
    async def _startup():
        nats = state.nats
        if hasattr(nats, "connect"):
            await nats.connect()

    @app.on_event("shutdown")
    async def _shutdown():
        nats = state.nats
        if hasattr(nats, "close"):
            await nats.close()
        if hasattr(state.companion, "aclose"):
            await state.companion.aclose()

    @app.post("/select")
    async def select(name: str = Query(...)):
        try:
            reply = await state.nats.request(_action_subject(state, "select"), {"name": name})
        except asyncio.TimeoutError:
            await _push_error(state, "LUCID not responding")
            raise HTTPException(status_code=504, detail="LUCID not responding")
        if not reply.get("ok"):
            await _push_error(state, reply.get("msg", reply.get("code", "error")))
            _raise_for_reply(reply)
        state.selected_motor = name
        await _push_status_to_companion(state, reply["status"], name)
        return {"ok": True}

    @app.post("/jog")
    async def jog(delta: float = Query(...)):
        name = _require_selection(state)
        try:
            reply = await state.nats.request(
                _action_subject(state, "jog"), {"name": name, "delta": delta}
            )
        except asyncio.TimeoutError:
            await _push_error(state, "LUCID not responding")
            raise HTTPException(status_code=504, detail="LUCID not responding")
        if not reply.get("ok"):
            await _push_error(state, reply.get("msg", reply.get("code", "error")))
            _raise_for_reply(reply)
        return {"ok": True, "expected_setpoint": reply.get("expected_setpoint")}

    @app.post("/move")
    async def move(position: float = Query(...)):
        name = _require_selection(state)
        try:
            reply = await state.nats.request(
                _action_subject(state, "move"), {"name": name, "position": position}
            )
        except asyncio.TimeoutError:
            await _push_error(state, "LUCID not responding")
            raise HTTPException(status_code=504, detail="LUCID not responding")
        if not reply.get("ok"):
            await _push_error(state, reply.get("msg", reply.get("code", "error")))
            _raise_for_reply(reply)
        return {"ok": True, "expected_setpoint": reply.get("expected_setpoint")}

    @app.post("/stop")
    async def stop():
        name = _require_selection(state)
        try:
            reply = await state.nats.request(_action_subject(state, "stop"), {"name": name})
        except asyncio.TimeoutError:
            await _push_error(state, "LUCID not responding")
            raise HTTPException(status_code=504, detail="LUCID not responding")
        if not reply.get("ok"):
            await _push_error(state, reply.get("msg", reply.get("code", "error")))
            _raise_for_reply(reply)
        return {"ok": True}

    @app.get("/status")
    async def status_endpoint():
        return {
            "selected_motor": state.selected_motor,
            "last_readback": state.last_readback,
        }

    return app
```

- [ ] **Step 5: Run tests**

Run: `.venv/Scripts/python -m pytest tests/test_bridge_app.py -v`
Expected: 7 PASS.

- [ ] **Step 6: Commit**

```bash
git add src/lucid_deck/bridge/nats_client.py src/lucid_deck/bridge/app.py tests/test_bridge_app.py
git commit -m "feat: bridge FastAPI app + NATS client wrapper"
```

---

## Task 10: Bridge readback subscriber

**Files:**
- Create: `~/PycharmProjects/ncs/lucid-deck/src/lucid_deck/bridge/readback.py`
- Create: `~/PycharmProjects/ncs/lucid-deck/tests/test_bridge_readback.py`
- Modify: `~/PycharmProjects/ncs/lucid-deck/src/lucid_deck/bridge/app.py` (wire `ReadbackForwarder` into startup)

The forwarder subscribes to `<prefix>.motors.readback.*` and, when an event is for the bridge's currently-selected motor, calls `_push_status_to_companion` (re-using the helper from `app.py`).

- [ ] **Step 1: Write failing test**

`tests/test_bridge_readback.py`:
```python
from __future__ import annotations

import pytest

from lucid_deck.bridge.app import BridgeState
from lucid_deck.bridge.config import BridgeConfig
from lucid_deck.bridge.readback import ReadbackForwarder


class FakeNatsSubscribable:
    def __init__(self):
        self.subject: str | None = None
        self.callback = None
    async def subscribe(self, subject, callback):
        self.subject = subject
        self.callback = callback
        return self


class FakeCompanion:
    def __init__(self): self.pushes: list[tuple[str, str]] = []
    async def set_variable(self, n, v): self.pushes.append((n, str(v)))
    async def set_variables(self, m):
        for k, v in m.items():
            self.pushes.append((k, str(v)))


def _state():
    cfg = BridgeConfig(nats_url="", companion_url="http://x", lucid_prefix="lucid")
    return BridgeState(config=cfg, nats=FakeNatsSubscribable(), companion=FakeCompanion())


@pytest.mark.asyncio
async def test_subscribe_on_correct_subject():
    state = _state()
    fwd = ReadbackForwarder(state)
    await fwd.start()
    assert state.nats.subject == "lucid.motors.readback.*"


@pytest.mark.asyncio
async def test_event_for_selected_motor_pushes_to_companion():
    state = _state()
    state.selected_motor = "samx"
    fwd = ReadbackForwarder(state)
    await fwd.start()

    await state.nats.callback("lucid.motors.readback.samx", {
        "position": 7.0, "units": "mm", "high_limit": 10.0, "low_limit": -10.0,
        "at_high": False, "at_low": False, "moving": False,
    })

    pushes = dict(state.companion.pushes)
    assert pushes["motor_position"] == "7.0"
    assert pushes["motor_selected"] == "samx"


@pytest.mark.asyncio
async def test_event_for_other_motor_ignored():
    state = _state()
    state.selected_motor = "samx"
    fwd = ReadbackForwarder(state)
    await fwd.start()

    await state.nats.callback("lucid.motors.readback.samy", {
        "position": 99.0, "units": "mm", "high_limit": 10.0, "low_limit": -10.0,
        "at_high": False, "at_low": False, "moving": False,
    })
    assert state.companion.pushes == []
```

- [ ] **Step 2: Run, expect failure**

Run: `.venv/Scripts/python -m pytest tests/test_bridge_readback.py -v`

- [ ] **Step 3: Implement**

`src/lucid_deck/bridge/readback.py`:
```python
"""Forwards NATS readback events into Companion variable pushes."""
from __future__ import annotations

import logging

from lucid_deck.bridge.app import BridgeState, _push_status_to_companion


logger = logging.getLogger(__name__)


class ReadbackForwarder:
    """Subscribes to readback subjects, pushes to Companion when relevant."""

    def __init__(self, state: BridgeState) -> None:
        self._state = state
        self._sub = None

    async def start(self) -> None:
        subject = f"{self._state.config.lucid_prefix}.motors.readback.*"
        self._sub = await self._state.nats.subscribe(subject, self._on_event)

    async def _on_event(self, subject: str, data: dict) -> None:
        # Subject format: <prefix>.motors.readback.<name>
        try:
            name = subject.rsplit(".", 1)[-1]
        except IndexError:
            return
        if name != self._state.selected_motor:
            return
        try:
            await _push_status_to_companion(self._state, data, name)
        except Exception as exc:
            logger.warning("readback push failed: %s", exc)
```

- [ ] **Step 4: Wire into app startup**

Edit `src/lucid_deck/bridge/app.py`. After the `_startup` function body, before `_shutdown`, add:

```python
        # Subscribe to readback events.
        from lucid_deck.bridge.readback import ReadbackForwarder
        state._forwarder = ReadbackForwarder(state)
        if hasattr(nats, "subscribe"):
            await state._forwarder.start()
```

(The `BridgeState` dataclass uses `field(default_factory=dict)`; assigning `_forwarder` as an extra attr at runtime is fine since dataclasses tolerate it unless `frozen=True`. We're not frozen.)

- [ ] **Step 5: Run all bridge tests**

Run: `.venv/Scripts/python -m pytest tests/test_bridge_*.py -v`
Expected: all PASS, no regressions.

- [ ] **Step 6: Commit**

```bash
git add src/lucid_deck/bridge/readback.py src/lucid_deck/bridge/app.py tests/test_bridge_readback.py
git commit -m "feat: bridge readback forwarder"
```

---

## Task 11: Integration test (real nats-server)

**Files:**
- Create: `~/PycharmProjects/ncs/lucid-deck/tests/test_integration_e2e.py`

This test exercises the full bridge against a real nats-server, with a fake LUCID-side responder and a fake Companion. It is gated behind `pytest -m integration` and skipped by default. The test catches whether `NatsClient.request` actually round-trips, which the in-memory tests can't.

**Prerequisite for the runner:** `nats-server` must be available on `$PATH` and reachable on `localhost:4222`. If absent, the test is auto-skipped.

- [ ] **Step 1: Write the test**

`tests/test_integration_e2e.py`:
```python
"""End-to-end: real nats-server + bridge + fake LUCID responder + fake Companion."""
from __future__ import annotations

import asyncio
import json
import os
import shutil
import socket
import subprocess
import time

import httpx
import nats
import pytest
import respx
from fastapi.testclient import TestClient

from lucid_deck.bridge.app import create_app, BridgeState
from lucid_deck.bridge.companion_client import CompanionClient
from lucid_deck.bridge.config import BridgeConfig
from lucid_deck.bridge.nats_client import NatsClient


pytestmark = pytest.mark.integration


def _nats_reachable(host="127.0.0.1", port=4222) -> bool:
    if shutil.which("nats-server") is None:
        return False
    try:
        with socket.create_connection((host, port), timeout=0.2):
            return True
    except OSError:
        return False


@pytest.fixture(scope="module")
def nats_server():
    if not shutil.which("nats-server"):
        pytest.skip("nats-server binary not found on $PATH")
    if _nats_reachable():
        yield "nats://127.0.0.1:4222"
        return
    proc = subprocess.Popen(["nats-server", "-p", "4222"],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    # wait for port to open
    for _ in range(50):
        if _nats_reachable():
            break
        time.sleep(0.1)
    else:
        proc.kill()
        pytest.skip("nats-server did not start within 5s")
    try:
        yield "nats://127.0.0.1:4222"
    finally:
        proc.terminate()
        proc.wait(timeout=5)


@pytest.mark.asyncio
async def test_select_round_trips_through_real_nats(nats_server):
    # Stand up a fake LUCID-side responder.
    nc = await nats.connect(nats_server)
    async def lucid_handler(msg):
        await nc.publish(msg.reply, json.dumps({
            "ok": True,
            "status": {"position": 1.5, "units": "mm",
                       "high_limit": 10.0, "low_limit": -10.0,
                       "at_high": False, "at_low": False, "moving": False},
        }).encode())
    sub = await nc.subscribe("lucid.motors.action.select", cb=lucid_handler)

    # Build a real bridge wired to real NATS but a mocked Companion.
    cfg = BridgeConfig(
        nats_url=nats_server,
        companion_url="http://comp:8000",
        lucid_prefix="lucid",
    )
    nats_link = NatsClient(cfg.nats_url)
    await nats_link.connect()
    companion = CompanionClient(cfg.companion_url)
    state = BridgeState(config=cfg, nats=nats_link, companion=companion)

    try:
        async with respx.mock(base_url="http://comp:8000") as mock:
            mock.post().mock(return_value=httpx.Response(200))
            app = create_app(cfg, state=state)
            with TestClient(app) as client:
                response = client.post("/select", params={"name": "samx"})
                assert response.status_code == 200
            # Confirm at least one Companion variable push happened.
            assert mock.calls.call_count >= 5
    finally:
        await sub.unsubscribe()
        await nc.drain()
        await nats_link.close()
        await companion.aclose()
```

- [ ] **Step 2: Run integration suite (skipped if no nats-server)**

Run: `.venv/Scripts/python -m pytest -m integration -v`

If `nats-server` is on `$PATH`: PASS.
If not: SKIPPED with message about `nats-server` not found — that's the expected behavior when running on a developer machine without it.

- [ ] **Step 3: Confirm unit tests still pass**

Run: `.venv/Scripts/python -m pytest -v`
Expected: all unit tests PASS, integration test SKIPPED unless nats-server is available.

- [ ] **Step 4: Commit**

```bash
git add tests/test_integration_e2e.py
git commit -m "test: e2e integration test against real nats-server"
```

---

## Task 12: Final verification

- [ ] **Step 1: Run full unit test suite**

```bash
cd ~/PycharmProjects/ncs/lucid-deck
.venv/Scripts/python -m pytest -v
```

Expected: all unit tests PASS, integration test SKIPPED unless nats-server is available.

- [ ] **Step 2: Verify LUCID can discover the plugin**

```bash
cd ~/PycharmProjects/ncs/lucid-deck
.venv/Scripts/python -c "from importlib.metadata import entry_points; print([e for e in entry_points(group='lucid.plugins') if e.name=='lucid_deck'])"
```

Expected: a non-empty list containing the `lucid_deck` entry-point.

- [ ] **Step 3: Verify the bridge subpackage imports cleanly without lucid**

```bash
cd ~/PycharmProjects/ncs/lucid-deck
.venv/Scripts/python -c "import lucid_deck.bridge.app, lucid_deck.bridge.cli, lucid_deck.bridge.config, lucid_deck.bridge.companion_client, lucid_deck.bridge.nats_client, lucid_deck.bridge.readback; print('bridge imports OK')"
```

Expected: `bridge imports OK`.

- [ ] **Step 4: Tag v0.1.0**

```bash
cd ~/PycharmProjects/ncs/lucid-deck
git tag -a v0.1.0 -m "v0.1.0: initial release per spec 2026-05-05-lucid-deck-design.md"
```

---

## Self-review (against spec)

- **Goals: top page motors → drill into single shared Motor Control page** — addressed in spec; the layout itself is data, authored via the existing companion-mcp-server. Plan delivers the bridge endpoints + LUCID actions that back the layout.
- **Step-on-press jog with global step + ×10/÷10** — bridge `/jog` accepts an explicit delta; step state lives in Companion. Plan does not need to ship Companion-side variable arithmetic (deferred per spec).
- **Live readback push** — Tasks 4 (LUCID readback publisher) and 10 (bridge readback forwarder) cover it.
- **Match LUCID's "headless service over NATS" pattern** — Task 5 wires actions via `IPCService`; Task 9 wires the bridge as a separate process consuming the same bus.
- **Companion variables: `motor_*`** — pushed in Tasks 9 and 10. All fields from the spec table covered.
- **Error → `motor_status_msg`** — Tasks 9 and 10 push it on error and clear it on success.
- **Tests** — three surfaces (LUCID handlers, bridge, e2e) per spec.
- **YAGNI**: no AgentPlugin, no hold-to-jog, no ControllerPlugin, no auth, no multi-Companion, no CI — all explicitly deferred.
- **Type consistency** — `BridgeConfig`, `BridgeState`, `MotorActionHandlers`, `ReadbackPublisher`, `ReadbackForwarder`, `CompanionClient`, `NatsClient`/`NatsLink` names appear consistently across tasks. Action subjects (`motors.action.list/status/select/jog/move/stop`) appear identically in Tasks 3, 5, 9. Companion variable names appear identically in Tasks 9, 10 and the spec.
- **No placeholders** — every code block is complete and runnable; the only `TODO`-shaped item is the QDoubleSpinBox-based step-add UI which has a working minimal implementation noted as "full edit dialog out of scope" (acceptable).

---

## Execution

This plan is ready for **subagent-driven-development**. Tasks have these dependencies:

- Task 1 (scaffolding) — first.
- Task 2 (mock_positioner) — must run before 3, 4, 5.
- Tasks 3, 4 (motor_actions, readback publisher) — can run after 2; 4 depends on 3 (same module).
- Task 5 (settings_plugin) — depends on 3, 4.
- Task 6 (manifest) — depends on 5.
- **Tasks 7, 8 (bridge config, companion_client)** — independent of LUCID side; can run in parallel with 3–6 after Task 1.
- Task 9 (bridge app) — depends on 7, 8.
- Task 10 (bridge readback) — depends on 9.
- Task 11 (integration) — depends on 9, 10.
- Task 12 (verification) — last.

**Parallel-able groups:**
1. After Task 1: dispatch Task 2 alone (everything depends on it).
2. After Task 2: dispatch Tasks 3 and 7 in parallel (different modules).
3. After Task 3: dispatch Tasks 4 and 8 in parallel.
4. Sequential after that: 5 → 6 (plugin chain), 9 → 10 (bridge chain). The two chains are independent so 5/6 and 9/10 can also run in parallel.
5. Task 11 after both chains complete. Task 12 last.
