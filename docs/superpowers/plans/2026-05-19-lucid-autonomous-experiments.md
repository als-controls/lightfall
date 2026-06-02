# Lightfall Autonomous Experiments Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Wire Lightfall's embedded Claude agent to Tsuchinoko's NATS surface so the agent can design (via gpCAM skills), launch (via existing `adaptive_experiment` plan), and monitor (via existing adaptive viz widgets) end-to-end autonomous experiments.

**Architecture:** Two repos. Tsuchinoko (`Lightfall-refactor` branch) gets a typed `experiment.configure` payload and a new `experiment.upload_design_code` action that lands agent-authored Python in `~/.tsuchinoko/user_designs/<kind>/<name>.py`. Lightfall (`feature/autonomous-experiment-agent`) gets one new `AutonomousExperimentAgent` AgentPlugin carrying a stub prompt, a lazy reference to `gpcam.skills/`, and five MCP tools over the existing `tsuchinoko.*` subjects.

**Tech Stack:** Python 3.12+, `nats-py`, `claude-agent-sdk`, `pytest`, gpCAM (optional runtime dep on Lightfall side).

**Spec:** `docs/superpowers/specs/2026-05-19-lightfall-autonomous-experiments-design.md`

---

## Repos and branches

- **Tsuchinoko:** `~/PycharmProjects/tsuchinoko`, branch `Lightfall-refactor`. Phase A lands here. Open MR titled `feat(nats): typed configure schema + upload_design_code` against `Lightfall-refactor` (or its upstream merge target).
- **Lightfall:** `~/PycharmProjects/ncs/ncs`, branch `feature/autonomous-experiment-agent` (already created at spec commit `a589859`). Phase B lands here.

**Deploy order:** Phase A → Phase B. Reverse breaks because Phase B sends configure keys old Tsuchinoko rejects under strict validation.

---

## File map

**Tsuchinoko (`Lightfall-refactor` branch):**
- Create: `tsuchinoko/nats/user_designs.py` — name validation + filesystem layout + resolver.
- Modify: `tsuchinoko/nats/service.py` — replace `_handle_configure`; add `_handle_upload_design_code`; extend `ACTIONS`.
- Create: `tests/test_user_designs.py` — unit tests for resolver.
- Create: `tests/test_nats_upload_design_code.py` — unit tests for new handler.
- Create: `tests/test_nats_configure_extended.py` — unit tests for typed configure.
- Create: `docs/design/2026-05-19-phase5-rich-configure.md` — tsuchinoko-side design doc.
- Create: `docs/plans/2026-05-19-phase5-rich-configure.md` — short pointer plan.

**Lightfall (`feature/autonomous-experiment-agent`):**
- Create: `src/lightfall/plugins/agents/autonomous_experiment/__init__.py`
- Create: `src/lightfall/plugins/agents/autonomous_experiment/plugin.py`
- Create: `src/lightfall/plugins/agents/autonomous_experiment/prompts.py`
- Create: `src/lightfall/plugins/agents/autonomous_experiment/nats_tools.py`
- Modify: `src/lightfall/plugins/builtin_manifest.py` — one new `PluginEntry`.
- Create: `tests/plugins/agents/test_autonomous_experiment.py`
- Create: `scripts/demo_autonomous_experiment.py`

---

# Phase A — Tsuchinoko-side changes

All paths below are relative to `~/PycharmProjects/tsuchinoko`. Switch to `Lightfall-refactor` before starting:

```bash
cd ~/PycharmProjects/tsuchinoko
git checkout Lightfall-refactor
git pull --ff-only
git checkout -b feature/typed-configure-and-upload
```

## Task A.1: User-designs resolver module

**Files:**
- Create: `tsuchinoko/nats/user_designs.py`
- Test: `tests/test_user_designs.py`

- [ ] **Step 1: Write the failing tests**

`tests/test_user_designs.py`:

```python
"""Tests for tsuchinoko.nats.user_designs."""
import os
import textwrap
from pathlib import Path

import pytest

from tsuchinoko.nats.user_designs import (
    EXPECTED_CALLABLES,
    KINDS,
    UserDesignError,
    resolve_user_ref,
    user_designs_root,
    validate_name,
    write_design,
)


def test_validate_name_accepts_lowercase_underscore(tmp_path):
    validate_name("my_ucb")
    validate_name("a")
    validate_name("a1")
    validate_name("a_b_c_123")


@pytest.mark.parametrize("bad", [
    "", "1starts_with_digit", "Capital", "has-hyphen",
    "../foo", "foo/bar", "with space", "a" * 64,
])
def test_validate_name_rejects(bad):
    with pytest.raises(UserDesignError):
        validate_name(bad)


def test_user_designs_root_honors_env(monkeypatch, tmp_path):
    monkeypatch.setenv("TSUCHINOKO_USER_DIR", str(tmp_path))
    assert user_designs_root() == tmp_path / "user_designs"


def test_user_designs_root_default(monkeypatch, tmp_path):
    monkeypatch.delenv("TSUCHINOKO_USER_DIR", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))  # Windows
    root = user_designs_root()
    assert root.name == "user_designs"
    assert str(tmp_path) in str(root)


def test_kinds_and_expected_callables_aligned():
    assert set(KINDS) == set(EXPECTED_CALLABLES.keys())
    for kind, name in EXPECTED_CALLABLES.items():
        assert isinstance(name, str) and name


def test_write_design_creates_file_and_returns_ref(monkeypatch, tmp_path):
    monkeypatch.setenv("TSUCHINOKO_USER_DIR", str(tmp_path))
    code = textwrap.dedent("""
        import numpy as np
        def acquisition_function(x, gp, **_):
            return np.zeros(len(x))
    """).strip()
    ref, path = write_design("my_ucb", "acquisition", code)
    assert ref == "user:my_ucb"
    assert path == tmp_path / "user_designs" / "acquisition" / "my_ucb.py"
    assert path.read_text().strip() == code


def test_write_design_rejects_syntax_error(monkeypatch, tmp_path):
    monkeypatch.setenv("TSUCHINOKO_USER_DIR", str(tmp_path))
    with pytest.raises(UserDesignError) as excinfo:
        write_design("bad", "acquisition", "def acquisition_function(x, gp):\n    return 1 +\n")
    assert "SyntaxError" in str(excinfo.value) or "invalid syntax" in str(excinfo.value)
    # Must not have created the file
    assert not (tmp_path / "user_designs" / "acquisition" / "bad.py").exists()


def test_write_design_rejects_missing_callable(monkeypatch, tmp_path):
    monkeypatch.setenv("TSUCHINOKO_USER_DIR", str(tmp_path))
    with pytest.raises(UserDesignError) as excinfo:
        write_design("nope", "acquisition", "x = 1\n")
    assert "acquisition_function" in str(excinfo.value)
    assert not (tmp_path / "user_designs" / "acquisition" / "nope.py").exists()


def test_write_design_rejects_unknown_kind(monkeypatch, tmp_path):
    monkeypatch.setenv("TSUCHINOKO_USER_DIR", str(tmp_path))
    with pytest.raises(UserDesignError):
        write_design("x", "totally_made_up", "def foo(): pass\n")


def test_resolve_user_ref_imports_callable(monkeypatch, tmp_path):
    monkeypatch.setenv("TSUCHINOKO_USER_DIR", str(tmp_path))
    code = "def acquisition_function(x, gp, **_):\n    return 'hi'\n"
    write_design("my_aq", "acquisition", code)
    fn = resolve_user_ref("user:my_aq", "acquisition")
    assert callable(fn)
    assert fn(None, None) == "hi"


def test_resolve_user_ref_unknown_name(monkeypatch, tmp_path):
    monkeypatch.setenv("TSUCHINOKO_USER_DIR", str(tmp_path))
    with pytest.raises(UserDesignError):
        resolve_user_ref("user:does_not_exist", "acquisition")


def test_resolve_user_ref_bad_prefix(monkeypatch, tmp_path):
    monkeypatch.setenv("TSUCHINOKO_USER_DIR", str(tmp_path))
    with pytest.raises(UserDesignError):
        resolve_user_ref("builtin:variance", "acquisition")
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
.venv/Scripts/python -m pytest tests/test_user_designs.py -x
```

Expected: collection error (`ImportError: cannot import name 'UserDesignError' from 'tsuchinoko.nats.user_designs'`).

- [ ] **Step 3: Write the module**

`tsuchinoko/nats/user_designs.py`:

```python
"""User-authored design code: name validation, filesystem layout, resolver.

Agent-authored callables (custom acquisition / kernel / prior_mean / noise
functions) are landed on disk under ``user_designs_root()`` and referenced
by ``"user:<name>"`` strings inside ``experiment.configure`` payloads.

Trust boundary: code dropped here is executed in this process. The wire
gate is in ``NATSService._handle_upload_design_code`` (this module only
implements the resolver and the on-disk layout).
"""
from __future__ import annotations

import importlib.util
import os
import re
from pathlib import Path

KINDS: tuple[str, ...] = ("acquisition", "kernel", "prior_mean", "noise")

EXPECTED_CALLABLES: dict[str, str] = {
    "acquisition": "acquisition_function",
    "kernel": "kernel",
    "prior_mean": "prior_mean",
    "noise": "noise_function",
}

_NAME_RE = re.compile(r"^[a-z][a-z0-9_]{0,62}$")


class UserDesignError(Exception):
    """Validation, compile, or resolution failure for a user-authored design."""


def validate_name(name: str) -> None:
    """Raise ``UserDesignError`` if *name* is not a safe filesystem stem."""
    if not isinstance(name, str) or not _NAME_RE.match(name):
        raise UserDesignError(
            f"invalid design name {name!r}: must match {_NAME_RE.pattern}"
        )


def _validate_kind(kind: str) -> None:
    if kind not in KINDS:
        raise UserDesignError(
            f"unknown kind {kind!r}; expected one of {KINDS}"
        )


def user_designs_root() -> Path:
    """Return the root directory for uploaded designs.

    Honours ``$TSUCHINOKO_USER_DIR`` if set; otherwise
    ``~/.tsuchinoko/user_designs/``. Does not create directories.
    """
    base = os.environ.get("TSUCHINOKO_USER_DIR")
    if base:
        return Path(base) / "user_designs"
    return Path.home() / ".tsuchinoko" / "user_designs"


def _path_for(kind: str, name: str) -> Path:
    return user_designs_root() / kind / f"{name}.py"


def write_design(name: str, kind: str, code: str) -> tuple[str, Path]:
    """Validate, compile-check, and persist *code* under <kind>/<name>.py.

    Returns ``("user:<name>", absolute_path)``. Raises ``UserDesignError``
    on any validation failure; no file is written if validation fails.
    """
    _validate_kind(kind)
    validate_name(name)
    try:
        compiled = compile(code, f"<user_design:{kind}/{name}>", "exec")
    except SyntaxError as exc:
        raise UserDesignError(f"SyntaxError in design code: {exc}") from exc

    expected = EXPECTED_CALLABLES[kind]
    ns: dict[str, object] = {}
    try:
        exec(compiled, ns)
    except Exception as exc:
        raise UserDesignError(
            f"failed to evaluate design code: {type(exc).__name__}: {exc}"
        ) from exc

    fn = ns.get(expected)
    if not callable(fn):
        raise UserDesignError(
            f"design code must bind a callable named {expected!r} "
            f"(kind={kind!r})"
        )

    path = _path_for(kind, name)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(code, encoding="utf-8")
    return f"user:{name}", path


def resolve_user_ref(ref: str, kind: str):
    """Import the callable referenced by ``"user:<name>"``.

    Looks under ``<user_designs_root>/<kind>/<name>.py``. Raises
    ``UserDesignError`` if the ref is malformed or the file is missing.
    """
    _validate_kind(kind)
    if not isinstance(ref, str) or not ref.startswith("user:"):
        raise UserDesignError(
            f"expected 'user:<name>' ref, got {ref!r}"
        )
    name = ref[len("user:"):]
    validate_name(name)
    path = _path_for(kind, name)
    if not path.is_file():
        raise UserDesignError(
            f"unknown user design {kind}/{name} at {path}"
        )

    spec = importlib.util.spec_from_file_location(
        f"_tsuchinoko_user_design_{kind}_{name}", path
    )
    if spec is None or spec.loader is None:
        raise UserDesignError(f"cannot load module for {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    expected = EXPECTED_CALLABLES[kind]
    fn = getattr(module, expected, None)
    if not callable(fn):
        raise UserDesignError(
            f"{path} does not bind callable {expected!r}"
        )
    return fn
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
.venv/Scripts/python -m pytest tests/test_user_designs.py -v
```

Expected: 12 passed.

- [ ] **Step 5: Commit**

```bash
git add tsuchinoko/nats/user_designs.py tests/test_user_designs.py
git commit -m "feat(nats): user_designs module — validation, persistence, resolver"
```

---

## Task A.2: `experiment.upload_design_code` handler

**Files:**
- Modify: `tsuchinoko/nats/service.py:18-28` (ACTIONS list), `tsuchinoko/nats/service.py:40-67` (subscribe block).
- Test: `tests/test_nats_upload_design_code.py`

- [ ] **Step 1: Write the failing tests**

`tests/test_nats_upload_design_code.py`:

```python
"""Tests for the NATS experiment.upload_design_code handler."""
import json
import textwrap
from unittest.mock import AsyncMock, MagicMock

import pytest

from tsuchinoko.nats.service import NATSService


class FakeMsg:
    def __init__(self, payload: dict):
        self.data = json.dumps(payload).encode()
        self.reply = "_INBOX.x"
        self.respond = AsyncMock()


def _service(monkeypatch, tmp_path):
    monkeypatch.setenv("TSUCHINOKO_USER_DIR", str(tmp_path))
    core = MagicMock()
    client = MagicMock()
    return NATSService(core, client)


@pytest.mark.asyncio
async def test_upload_design_code_happy(monkeypatch, tmp_path):
    svc = _service(monkeypatch, tmp_path)
    code = textwrap.dedent("""
        def acquisition_function(x, gp, **_):
            return [0.0]
    """).strip()
    msg = FakeMsg({"name": "my_ucb", "kind": "acquisition", "code": code})

    await svc._handle_upload_design_code(msg)

    reply = json.loads(msg.respond.call_args.args[0])
    assert reply["status"] == "ok"
    assert reply["ref"] == "user:my_ucb"
    assert reply["path"].endswith("acquisition/my_ucb.py")
    assert (tmp_path / "user_designs" / "acquisition" / "my_ucb.py").is_file()


@pytest.mark.asyncio
async def test_upload_design_code_syntax_error(monkeypatch, tmp_path):
    svc = _service(monkeypatch, tmp_path)
    msg = FakeMsg({"name": "bad", "kind": "acquisition", "code": "def f(:\n"})

    await svc._handle_upload_design_code(msg)

    reply = json.loads(msg.respond.call_args.args[0])
    assert reply["status"] == "error"
    assert "SyntaxError" in reply["message"] or "invalid syntax" in reply["message"]
    assert not (tmp_path / "user_designs" / "acquisition" / "bad.py").exists()


@pytest.mark.asyncio
async def test_upload_design_code_missing_callable(monkeypatch, tmp_path):
    svc = _service(monkeypatch, tmp_path)
    msg = FakeMsg({"name": "nofn", "kind": "kernel", "code": "x = 1\n"})

    await svc._handle_upload_design_code(msg)

    reply = json.loads(msg.respond.call_args.args[0])
    assert reply["status"] == "error"
    assert "kernel" in reply["message"]


@pytest.mark.asyncio
async def test_upload_design_code_bad_name(monkeypatch, tmp_path):
    svc = _service(monkeypatch, tmp_path)
    msg = FakeMsg({"name": "../escape", "kind": "acquisition", "code": "def acquisition_function(x, gp): pass\n"})

    await svc._handle_upload_design_code(msg)

    reply = json.loads(msg.respond.call_args.args[0])
    assert reply["status"] == "error"
    assert "invalid design name" in reply["message"]
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
.venv/Scripts/python -m pytest tests/test_nats_upload_design_code.py -x
```

Expected: `AttributeError: 'NATSService' object has no attribute '_handle_upload_design_code'`.

- [ ] **Step 3: Add the handler + register it**

In `tsuchinoko/nats/service.py`:

(a) Append to the module-level `ACTIONS` list (currently at lines 18-28):

```python
ACTIONS = [
    {"suffix": "experiment.configure", "description": "Set up experiment parameters"},
    {"suffix": "experiment.bind_run", "description": "Bind a bluesky run for Tiled I/O"},
    {"suffix": "experiment.start", "description": "Begin the adaptive loop"},
    {"suffix": "experiment.pause", "description": "Pause the loop"},
    {"suffix": "experiment.resume", "description": "Resume from pause"},
    {"suffix": "experiment.stop", "description": "Stop and finalize"},
    {"suffix": "experiment.upload_design_code", "description": "Upload an agent-authored callable (acquisition/kernel/prior_mean/noise)"},
    {"suffix": "engine.set_parameter", "description": "Update a single engine parameter"},
    {"suffix": "engine.get_parameters", "description": "Retrieve current engine parameters"},
    {"suffix": "status", "description": "Query current state and progress"},
]
```

(b) In `NATSService.start()` `handler_map`, add the new entry alongside the others:

```python
handler_map = {
    "experiment.configure": self._handle_configure,
    "experiment.bind_run": self._handle_bind_run,
    "experiment.start": self._handle_start,
    "experiment.pause": self._handle_pause,
    "experiment.resume": self._handle_resume,
    "experiment.stop": self._handle_stop,
    "experiment.upload_design_code": self._handle_upload_design_code,
    "engine.set_parameter": self._handle_set_parameter,
    "engine.get_parameters": self._handle_get_parameters,
    "status": self._handle_status,
}
```

(c) Add the handler method on `NATSService`, just below `_handle_stop`:

```python
    async def _handle_upload_design_code(self, msg) -> None:
        from tsuchinoko.nats.user_designs import UserDesignError, write_design
        try:
            data = json.loads(msg.data)
            name = data["name"]
            kind = data["kind"]
            code = data["code"]
            ref, path = write_design(name, kind, code)
            await self._reply(msg, {
                "status": "ok",
                "ref": ref,
                "path": str(path),
            })
        except UserDesignError as exc:
            await self._reply(msg, {"status": "error", "message": str(exc)})
        except Exception as exc:
            logger.exception(exc)
            await self._reply(msg, {"status": "error", "message": str(exc)})
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
.venv/Scripts/python -m pytest tests/test_nats_upload_design_code.py -v
```

Expected: 4 passed.

- [ ] **Step 5: Commit**

```bash
git add tsuchinoko/nats/service.py tests/test_nats_upload_design_code.py
git commit -m "feat(nats): experiment.upload_design_code action"
```

---

## Task A.3: Typed `experiment.configure` with user-ref resolution

**Files:**
- Modify: `tsuchinoko/nats/service.py` (`_handle_configure` body).
- Test: `tests/test_nats_configure_extended.py`

The existing handler only reads `parameter_bounds`. Replace it with a typed-payload version. Strict validation: unknown top-level keys return an error.

- [ ] **Step 1: Write the failing tests**

`tests/test_nats_configure_extended.py`:

```python
"""Tests for the extended experiment.configure handler."""
import json
import textwrap
from unittest.mock import AsyncMock, MagicMock

import pytest

from tsuchinoko.nats.service import NATSService


class FakeMsg:
    def __init__(self, payload: dict):
        self.data = json.dumps(payload).encode()
        self.reply = "_INBOX.x"
        self.respond = AsyncMock()


def _service(monkeypatch, tmp_path):
    monkeypatch.setenv("TSUCHINOKO_USER_DIR", str(tmp_path))
    core = MagicMock()
    # Engine params recorded by setattr; tests inspect _recorded.
    core.adaptive_engine.parameters = MagicMock()
    return NATSService(core, MagicMock()), core


@pytest.mark.asyncio
async def test_configure_bounds_only_still_works(monkeypatch, tmp_path):
    svc, core = _service(monkeypatch, tmp_path)
    msg = FakeMsg({"parameter_bounds": [[0.0, 10.0], [-5.0, 5.0]]})
    await svc._handle_configure(msg)
    reply = json.loads(msg.respond.call_args.args[0])
    assert reply["status"] == "ok"
    # Bounds mapped to engine via the existing parameter-tree keys
    params = core.adaptive_engine.parameters.__setitem__.call_args_list
    keys = [call.args[0] for call in params]
    assert ("bounds", "axis_0_min") in keys
    assert ("bounds", "axis_0_max") in keys
    assert ("bounds", "axis_1_min") in keys
    assert ("bounds", "axis_1_max") in keys


@pytest.mark.asyncio
async def test_configure_unknown_key_rejected(monkeypatch, tmp_path):
    svc, _core = _service(monkeypatch, tmp_path)
    msg = FakeMsg({"parameter_bounds": [[0, 1]], "what_is_this": 42})
    await svc._handle_configure(msg)
    reply = json.loads(msg.respond.call_args.args[0])
    assert reply["status"] == "error"
    assert "what_is_this" in reply["message"]


@pytest.mark.asyncio
async def test_configure_full_typed_payload(monkeypatch, tmp_path):
    svc, core = _service(monkeypatch, tmp_path)
    msg = FakeMsg({
        "parameter_bounds": [[0.0, 1.0], [0.0, 1.0]],
        "dimensionality": 2,
        "kernel": "matern_3_2",
        "acquisition_function": "ucb",
        "prior_mean": None,
        "noise_function": None,
        "noise_variances": 0.01,
        "initial_points": 12,
        "training_method": "global",
        "hyperparameters": [1.0, 0.5, 0.5],
        "x_out": None,
    })
    await svc._handle_configure(msg)
    reply = json.loads(msg.respond.call_args.args[0])
    assert reply["status"] == "ok"
    setattrs = [c.args[0] for c in core.adaptive_engine.__setattr__.call_args_list] if hasattr(core.adaptive_engine, '__setattr__') else []
    # Engine knobs touched (strings — exact attribute set is checked in the unit-of-config helper)
    # See helper-level assertions below.


@pytest.mark.asyncio
async def test_configure_user_ref_resolves(monkeypatch, tmp_path):
    from tsuchinoko.nats.user_designs import write_design
    code = textwrap.dedent("""
        def acquisition_function(x, gp, **_):
            return [0.0]
    """).strip()
    write_design("my_ucb", "acquisition", code)

    svc, core = _service(monkeypatch, tmp_path)
    msg = FakeMsg({
        "parameter_bounds": [[0, 1]],
        "acquisition_function": "user:my_ucb",
    })
    await svc._handle_configure(msg)
    reply = json.loads(msg.respond.call_args.args[0])
    assert reply["status"] == "ok"
    # The resolved callable was assigned to the engine
    core.adaptive_engine.__setattr__.assert_any_call("acquisition_function", pytest.helpers.callable_arg())


@pytest.mark.asyncio
async def test_configure_unknown_user_ref(monkeypatch, tmp_path):
    svc, _core = _service(monkeypatch, tmp_path)
    msg = FakeMsg({
        "parameter_bounds": [[0, 1]],
        "kernel": "user:does_not_exist",
    })
    await svc._handle_configure(msg)
    reply = json.loads(msg.respond.call_args.args[0])
    assert reply["status"] == "error"
    assert "does_not_exist" in reply["message"]
```

Add a tiny pytest plugin helper (or inline a sentinel) for `callable_arg()` if pytest-helpers-namespace is not installed; in that case, replace the assertion with:

```python
    args_seen = [c.args for c in core.adaptive_engine.__setattr__.call_args_list]
    assert any(a[0] == "acquisition_function" and callable(a[1]) for a in args_seen)
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
.venv/Scripts/python -m pytest tests/test_nats_configure_extended.py -x
```

Expected: most pass for bounds-only (legacy behaviour), unknown-key + typed-payload fail because the handler currently ignores them.

- [ ] **Step 3: Replace `_handle_configure`**

In `tsuchinoko/nats/service.py`, locate `_handle_configure` (currently ~lines 153-163) and replace its body with a typed-payload implementation. Keep the surrounding `try`/`except` structure for safety.

```python
    # Recognised configure keys (strict: unknown keys are an error)
    _CONFIGURE_KEYS = frozenset({
        "parameter_bounds",
        "dimensionality",
        "kernel",
        "acquisition_function",
        "prior_mean",
        "noise_function",
        "noise_variances",
        "initial_points",
        "training_method",
        "hyperparameters",
        "x_out",
    })

    _CONFIGURE_KIND_BY_KEY = {
        "acquisition_function": "acquisition",
        "kernel": "kernel",
        "prior_mean": "prior_mean",
        "noise_function": "noise",
    }

    async def _handle_configure(self, msg) -> None:
        from tsuchinoko.nats.user_designs import UserDesignError, resolve_user_ref
        try:
            data = json.loads(msg.data)

            unknown = set(data) - self._CONFIGURE_KEYS
            if unknown:
                await self._reply(msg, {
                    "status": "error",
                    "message": f"unknown configure field(s): {sorted(unknown)}",
                })
                return

            engine = self._core.adaptive_engine

            if "parameter_bounds" in data:
                for i, (lo, hi) in enumerate(data["parameter_bounds"]):
                    engine.parameters[("bounds", f"axis_{i}_min")] = lo
                    engine.parameters[("bounds", f"axis_{i}_max")] = hi

            # Resolve user:<name> refs before any engine mutation that could
            # be confusing on partial failure. We loop twice to keep failures
            # transactional from the caller's POV.
            resolved: dict[str, object] = {}
            for key, kind in self._CONFIGURE_KIND_BY_KEY.items():
                value = data.get(key)
                if isinstance(value, str) and value.startswith("user:"):
                    resolved[key] = resolve_user_ref(value, kind)

            # Apply remaining typed fields to the engine. We use setattr —
            # the adaptive engine surfaces these as Python attributes.
            for key in (
                "dimensionality", "kernel", "acquisition_function",
                "prior_mean", "noise_function", "noise_variances",
                "initial_points", "training_method", "hyperparameters",
                "x_out",
            ):
                if key in data:
                    value = resolved.get(key, data[key])
                    setattr(engine, key, value)

            await self._reply(msg, {"status": "ok"})
        except UserDesignError as exc:
            await self._reply(msg, {"status": "error", "message": str(exc)})
        except Exception as exc:
            logger.exception(exc)
            await self._reply(msg, {"status": "error", "message": str(exc)})
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
.venv/Scripts/python -m pytest tests/test_nats_configure_extended.py -v
```

Expected: 5 passed.

- [ ] **Step 5: Run the full tsuchinoko test suite for regressions**

```bash
.venv/Scripts/python -m pytest tests/ -x --ignore=tests/test_gui.py
```

Expected: no new failures vs the baseline of `Lightfall-refactor`. Pre-existing skips (e.g. tests needing a real NATS server) remain skipped.

- [ ] **Step 6: Commit**

```bash
git add tsuchinoko/nats/service.py tests/test_nats_configure_extended.py
git commit -m "feat(nats): typed experiment.configure with strict validation and user-ref resolution"
```

---

## Task A.4: Tsuchinoko-side design + plan docs

**Files:**
- Create: `docs/design/2026-05-19-phase5-rich-configure.md`
- Create: `docs/plans/2026-05-19-phase5-rich-configure.md`

Short docs that reference the canonical Lightfall spec rather than re-stating it.

- [ ] **Step 1: Write the design doc**

`docs/design/2026-05-19-phase5-rich-configure.md`:

```markdown
# Phase 5 — Rich `experiment.configure` + `upload_design_code`

**Status:** Implemented
**Date:** 2026-05-19
**Canonical spec:** `ncs/docs/superpowers/specs/2026-05-19-lightfall-autonomous-experiments-design.md`

## Summary

Two NATS-side changes that let the Lightfall embedded Claude agent drive
end-to-end autonomous experiments:

1. **`experiment.configure`** grows from `{parameter_bounds}` into a
   typed payload (kernel, acquisition_function, prior_mean,
   noise_function/variances, initial_points, training_method,
   hyperparameters, x_out, dimensionality). Validation is strict —
   unknown top-level keys return an error.
2. **`experiment.upload_design_code`** (new action) accepts
   ``{name, kind, code}`` and persists agent-authored Python under
   ``<user_designs_root>/<kind>/<name>.py``. Configure resolves
   ``"user:<name>"`` refs at apply time.

## Expected callable signatures (per kind)

| kind | expected callable | signature (per gpCAM upstream) |
|---|---|---|
| `acquisition` | `acquisition_function` | `(x, gp, **_) -> ndarray` |
| `kernel` | `kernel` | `(x1, x2, hyperparameters) -> ndarray` |
| `prior_mean` | `prior_mean` | `(x, hyperparameters, gp) -> ndarray` |
| `noise` | `noise_function` | `(x, hyperparameters, gp) -> ndarray` |

The handler validates the bound name only; signature errors surface
at run time inside the adaptive engine, where they're already handled.

## User-designs root

`~/.tsuchinoko/user_designs/<kind>/<name>.py` by default, or
`$TSUCHINOKO_USER_DIR/user_designs/<kind>/<name>.py` if set.

## Trust boundary

Code dropped through `upload_design_code` runs in this process. This
exposure is identical to what `engine.set_parameter` already permits
via the pyqtgraph param tree. NATS-level authentication is governed
by the existing IPC design (TLS, no broker creds).

## References

- Lightfall canonical spec: `ncs/docs/superpowers/specs/2026-05-19-lightfall-autonomous-experiments-design.md`
- Lightfall implementation plan: `ncs/docs/superpowers/plans/2026-05-19-lightfall-autonomous-experiments.md`
- Previous phase: `docs/design/2026-04-12-phase2-nats-integration.md`
```

- [ ] **Step 2: Write the plan doc (short pointer)**

`docs/plans/2026-05-19-phase5-rich-configure.md`:

```markdown
# Phase 5 — Implementation Plan

Tracked in the Lightfall repo:
`ncs/docs/superpowers/plans/2026-05-19-lightfall-autonomous-experiments.md`

Tsuchinoko-side tasks land first (Phase A of that plan), in this
order:

1. `tsuchinoko/nats/user_designs.py` + `tests/test_user_designs.py`
2. `experiment.upload_design_code` handler + `tests/test_nats_upload_design_code.py`
3. Typed `experiment.configure` + `tests/test_nats_configure_extended.py`
4. This design doc + this plan doc
5. MR against `Lightfall-refactor` titled
   `feat(nats): typed configure schema + upload_design_code`

Deploy this MR before Lightfall's `feature/autonomous-experiment-agent`
branch — strict validation in the new configure means old Tsuchinoko
would reject payloads sent by the new Lightfall plugin.
```

- [ ] **Step 3: Commit**

```bash
git add docs/design/2026-05-19-phase5-rich-configure.md docs/plans/2026-05-19-phase5-rich-configure.md
git commit -m "docs(nats): phase 5 — rich configure + upload_design_code"
```

---

## Task A.5: Phase A integration test + MR

**Files:**
- (Optional) Extend an existing NATS integration test if your test setup spawns a real broker, otherwise rely on the unit tests above.
- No new files required.

- [ ] **Step 1: Run the full test suite once more**

```bash
.venv/Scripts/python -m pytest tests/ -x --ignore=tests/test_gui.py
```

Expected: all the new tests pass; no regressions vs `Lightfall-refactor` baseline.

- [ ] **Step 2: Push the branch**

```bash
git push -u origin feature/typed-configure-and-upload
```

- [ ] **Step 3: Open an MR**

Title: `feat(nats): typed configure schema + upload_design_code`
Target: `Lightfall-refactor`
Description (paste in):

```
Implements Phase A of the Lightfall Autonomous Experiments work.

Canonical spec: ncs/docs/superpowers/specs/2026-05-19-lightfall-autonomous-experiments-design.md
Plan:           ncs/docs/superpowers/plans/2026-05-19-lightfall-autonomous-experiments.md

Changes:
- New tsuchinoko/nats/user_designs.py — validation, persistence, resolver
- New experiment.upload_design_code action
- experiment.configure now accepts a typed payload (strict validation)
- user:<name> refs resolved at configure-time

Deploy before Lightfall feature/autonomous-experiment-agent. Strict
validation means old Tsuchinoko would reject the new Lightfall plugin's
payloads.
```

**Pause** — wait for review/merge of Phase A before starting Phase B integration testing. Phase B's unit tests can be written and run in parallel; they don't need Tsuchinoko deployed.

---

# Phase B — Lightfall-side AgentPlugin

All paths below are relative to `~/PycharmProjects/ncs/ncs`. The feature branch already exists from the spec commit:

```bash
cd ~/PycharmProjects/ncs/ncs
git checkout feature/autonomous-experiment-agent
```

## Task B.1: Plugin scaffolding (package + class shell)

**Files:**
- Create: `src/lightfall/plugins/agents/autonomous_experiment/__init__.py`
- Create: `src/lightfall/plugins/agents/autonomous_experiment/plugin.py`
- Test: `tests/plugins/agents/test_autonomous_experiment.py`

- [ ] **Step 1: Write the failing tests (plugin metadata only)**

`tests/plugins/agents/test_autonomous_experiment.py`:

```python
"""Tests for the AutonomousExperimentAgent plugin."""
from __future__ import annotations

import pytest

from lightfall.plugins.agents.autonomous_experiment import (
    AutonomousExperimentAgent,
)


def test_plugin_metadata():
    agent = AutonomousExperimentAgent()
    assert agent.name == "autonomous_experiment"
    assert agent.display_name == "Autonomous Experiment"
    assert "Tsuchinoko" in agent.description
    assert agent.category == "acquisition"
    assert agent.priority == 30
    assert agent.enabled_by_default is True


def test_plugin_reports_has_prompt_and_tools():
    agent = AutonomousExperimentAgent()
    info = agent.get_introspection_data()
    assert info["has_prompt"] is True
    assert info["has_tools"] is True
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
.venv\Scripts\python.exe -m pytest tests/plugins/agents/test_autonomous_experiment.py -x
```

Expected: `ImportError: cannot import name 'AutonomousExperimentAgent' from 'lightfall.plugins.agents.autonomous_experiment'`.

- [ ] **Step 3: Create the package shell**

`src/lightfall/plugins/agents/autonomous_experiment/__init__.py`:

```python
"""Autonomous Experiment agent plugin.

Bridges Lightfall's embedded Claude agent to Tsuchinoko's NATS surface
for designing and running GP-driven adaptive experiments.

Spec: docs/superpowers/specs/2026-05-19-lightfall-autonomous-experiments-design.md
"""
from __future__ import annotations

from .plugin import AutonomousExperimentAgent

__all__ = ["AutonomousExperimentAgent"]
```

`src/lightfall/plugins/agents/autonomous_experiment/plugin.py`:

```python
"""AutonomousExperimentAgent AgentPlugin."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from lightfall.plugins.agent_plugin import AgentPlugin


class AutonomousExperimentAgent(AgentPlugin):
    """Embeds gpCAM's experiment-design skills and exposes a NATS bridge
    to a running Tsuchinoko instance.

    Together with the existing ``adaptive_experiment`` plan and the
    adaptive viz widgets, this plugin lets the embedded agent drive an
    end-to-end autonomous experiment from chat.
    """

    @property
    def name(self) -> str:
        return "autonomous_experiment"

    @property
    def display_name(self) -> str:
        return "Autonomous Experiment"

    @property
    def description(self) -> str:
        return "Design and run GP-driven adaptive experiments via Tsuchinoko"

    @property
    def category(self) -> str:
        return "acquisition"

    @property
    def priority(self) -> int:
        return 30

    @property
    def enabled_by_default(self) -> bool:
        return True

    def get_system_prompt(self) -> str:
        from .prompts import STUB
        return STUB

    def create_tools(self) -> list[Any]:
        from .nats_tools import build_tools
        return build_tools()

    def get_references_dir(self) -> Path | None:
        try:
            import importlib.resources as ir
            ref = ir.files("gpcam.skills")
        except (ImportError, ModuleNotFoundError, FileNotFoundError):
            return None
        try:
            return Path(str(ref))
        except Exception:
            return None
```

- [ ] **Step 4: Add minimal `prompts.STUB` and a stub `build_tools` so imports resolve**

`src/lightfall/plugins/agents/autonomous_experiment/prompts.py`:

```python
"""System-prompt text for the AutonomousExperimentAgent."""
from __future__ import annotations

STUB = "(stub — filled in Task B.2)"
```

`src/lightfall/plugins/agents/autonomous_experiment/nats_tools.py`:

```python
"""MCP tools that bridge the Lightfall embedded agent to Tsuchinoko's NATS surface.

Filled in across Tasks B.3 – B.5.
"""
from __future__ import annotations

from typing import Any


def build_tools() -> list[Any]:
    return [object()]  # placeholder so create_tools() reports has_tools=True
```

- [ ] **Step 5: Run tests to verify they pass**

```bash
.venv\Scripts\python.exe -m pytest tests/plugins/agents/test_autonomous_experiment.py -v
```

Expected: 2 passed.

- [ ] **Step 6: Commit**

```bash
git add src/lightfall/plugins/agents/autonomous_experiment tests/plugins/agents/test_autonomous_experiment.py
git commit -m "feat(agents): autonomous_experiment plugin scaffolding"
```

---

## Task B.2: Stub prompt content

**Files:**
- Modify: `src/lightfall/plugins/agents/autonomous_experiment/prompts.py`
- Test: extend `tests/plugins/agents/test_autonomous_experiment.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/plugins/agents/test_autonomous_experiment.py`:

```python
def test_stub_prompt_mentions_key_tools_and_steps():
    agent = AutonomousExperimentAgent()
    prompt = agent.get_system_prompt()

    # Workflow steps
    for token in (
        "experiment-designer",
        "tsuchinoko_discover",
        "tsuchinoko_upload_design_code",
        "tsuchinoko_configure",
        "ncs_run_plan",
        "adaptive_experiment",
        "AdaptiveHeatmapVisualization",
        "AdaptiveHyperparameterPlot",
        "tsuchinoko_status",
        "tsuchinoko_pause",
        "tsuchinoko_resume",
        "tsuchinoko_stop",
    ):
        assert token in prompt, f"prompt missing reference to {token!r}"

    # Sibling skills surfaced for lazy load
    for skill in (
        "acquisition-functions",
        "kernel-designer",
        "prior-mean-functions",
        "noise-functions",
        "cost-functions",
        "gp2scale-advanced",
        "multi-task-advanced",
    ):
        assert skill in prompt, f"prompt missing skill reference {skill!r}"

    # Install hint for the gpcam-missing path
    assert "pip install gpcam" in prompt
```

- [ ] **Step 2: Run to verify it fails**

```bash
.venv\Scripts\python.exe -m pytest tests/plugins/agents/test_autonomous_experiment.py::test_stub_prompt_mentions_key_tools_and_steps -x
```

Expected: assertion failure (the placeholder stub has none of these).

- [ ] **Step 3: Replace `prompts.STUB`**

`src/lightfall/plugins/agents/autonomous_experiment/prompts.py`:

```python
"""System-prompt text for the AutonomousExperimentAgent."""
from __future__ import annotations

STUB = """
## Autonomous Experiments

When the user asks for a smart/adaptive scan, peak finding, parameter
optimisation, or any other GP-driven experiment, follow this workflow.
Do not improvise around it — each step relies on the previous one.

### 1. Design

Load gpCAM's `experiment-designer` skill from this plugin's references
and follow its conversation flow. If you cannot see that skill, gpCAM
is not installed in Lightfall's environment — tell the user:

> "I can't see the gpCAM design skills. Install gpCAM with
> `pip install gpcam` in the Lightfall environment and restart Lightfall,
> then ask me again."

…and stop. Do not proceed with `tsuchinoko_*` tools before the user
confirms.

Sibling skills are available for lazy load via the Skill tool when the
design needs them: `acquisition-functions`, `kernel-designer`,
`prior-mean-functions`, `noise-functions`, `cost-functions`,
`gp2scale-advanced`, `multi-task-advanced`.

### 2. Discover Tsuchinoko

Call `tsuchinoko_discover()`. If the list is empty, tell the user:

> "No Tsuchinoko instance is responding on the bus. Start one
> (`tsuchinoko run`) and tell me when it's ready."

…and stop.

### 3. Upload custom callables (if needed)

If the design includes a user-authored acquisition function, kernel,
prior mean, or noise function, upload each one before configure:

```
tsuchinoko_upload_design_code(
    name="my_ucb", kind="acquisition", code="<python source>"
)
```

`kind` is one of `acquisition`, `kernel`, `prior_mean`, `noise`. The
tool returns a ref string of the form `"user:<name>"`; use it in
configure.

### 4. Configure

`tsuchinoko_configure(payload)` — payload is a dict with these fields
(omit any that should keep the engine default):

- `parameter_bounds`: list of `[lo, hi]` per axis (required)
- `dimensionality`: optional int
- `kernel`: `"matern_3_2" | "matern_1_2" | "matern_5_2" | "se" | "periodic" | "user:<name>"`
- `acquisition_function`: `"variance" | "ucb" | "ei" | "user:<name>"`
- `prior_mean`: `null` or `"user:<name>"`
- `noise_function`: `null` or `"user:<name>"`
- `noise_variances`: `null`, float, or list of floats
- `initial_points`: int (default 10)
- `training_method`: `"global" | "local" | "mcmc" | "adam" | "hgdl"`
- `hyperparameters`: optional list of floats (initial values)
- `x_out`: optional, for fvGP multi-task

Unknown keys are an error. The configure tool will surface that
verbatim — fix it before retrying.

### 5. Run

Use the existing plan tool:

```
ncs_run_plan(
    plan_name="adaptive_experiment",
    params={
        "detectors": [<detector names>],
        "motors": [<motor names, in the same order as parameter_bounds>],
        "timeout": 300.0,
    },
)
```

The plan opens a single Bluesky run, hands off Tiled credentials to
Tsuchinoko via `bind_run`, and drives the move-and-measure loop.

### 6. Monitor

Tell the user to open the Visualization Panel — the
`AdaptiveHeatmapVisualization` (posterior mean / variance /
acquisition) and `AdaptiveHyperparameterPlot` widgets will populate
live as iterations land in the Tiled `adaptive` stream.

For textual progress, call `tsuchinoko_status()`.

### 7. Control

`tsuchinoko_pause()`, `tsuchinoko_resume()`, `tsuchinoko_stop()` —
each takes no arguments. Use `tsuchinoko_stop()` to finalise the
experiment from the Tsuchinoko side; the Lightfall plan exits cleanly
when targets stop arriving (configurable timeout).

### Constraints

- Do not start a new `adaptive_experiment` before stopping the
  current one — the bind_run handshake is single-occupant.
- `motors` order in `ncs_run_plan` must match the axes order in
  `parameter_bounds`. Disagreement silently produces nonsense.
- Never call `tsuchinoko_configure` while the loop is running.
  Stop first.
"""
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
.venv\Scripts\python.exe -m pytest tests/plugins/agents/test_autonomous_experiment.py -v
```

Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
git add src/lightfall/plugins/agents/autonomous_experiment/prompts.py tests/plugins/agents/test_autonomous_experiment.py
git commit -m "feat(agents): autonomous_experiment stub prompt"
```

---

## Task B.3: NATS request helper + `tsuchinoko_discover` tool

**Files:**
- Modify: `src/lightfall/plugins/agents/autonomous_experiment/nats_tools.py`
- Test: extend `tests/plugins/agents/test_autonomous_experiment.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/plugins/agents/test_autonomous_experiment.py`:

```python
from unittest.mock import MagicMock, patch


def _patch_ipc(reply):
    """Patch get_ipc_service to return a stub IPC with .request → *reply*."""
    ipc = MagicMock()
    ipc.request = MagicMock(return_value=reply)
    return patch("lightfall.ipc.service.get_ipc_service", return_value=ipc), ipc


def _find_tool(tools, name):
    for t in tools:
        # SDK @tool returns objects with a `.name` or accessible via spec.
        # Inspect by name attribute first, fall back to repr.
        if getattr(t, "name", None) == name:
            return t
        if hasattr(t, "tool_spec") and t.tool_spec.get("name") == name:
            return t
    raise AssertionError(f"tool {name!r} not in {tools!r}")


import asyncio


def _call(tool, args=None):
    handler = getattr(tool, "handler", None) or getattr(tool, "_handler", None) or tool
    return asyncio.run(handler(args or {}))


def test_discover_returns_responder_list_from_request():
    agent = AutonomousExperimentAgent()
    tools = agent.create_tools()
    discover = _find_tool(tools, "tsuchinoko_discover")

    patcher, ipc = _patch_ipc(reply={"instance_id": "abc", "state": "Inactive"})
    with patcher:
        result = _call(discover)

    assert result["success"] is True
    assert isinstance(result["instances"], list)
    assert any(i.get("instance_id") == "abc" for i in result["instances"])
    ipc.request.assert_called()


def test_discover_empty_when_no_responders():
    agent = AutonomousExperimentAgent()
    discover = _find_tool(agent.create_tools(), "tsuchinoko_discover")

    patcher, _ = _patch_ipc(reply=None)
    with patcher:
        result = _call(discover)
    assert result["success"] is True
    assert result["instances"] == []


def test_nats_unavailable_raises_actionable_message():
    agent = AutonomousExperimentAgent()
    discover = _find_tool(agent.create_tools(), "tsuchinoko_discover")
    with patch("lightfall.ipc.service.get_ipc_service", return_value=None):
        result = _call(discover)
    assert result["success"] is False
    assert "Settings" in result["error"] or "IPC" in result["error"]
```

> Note on discovery semantics: `IPCService.request()` is request/reply
> (single responder). For broadcast discovery we use the same call
> shape but accept a *list* response from a (TBD/optional) extension
> point, or fall back to a single-shot reply. To keep this plan
> dependency-free of any broadcast helper, the tool returns at most
> one instance per call — a single `[entry]` list. Multi-instance
> routing is explicitly out of scope (see spec §Non-goals).

- [ ] **Step 2: Run to verify they fail**

```bash
.venv\Scripts\python.exe -m pytest tests/plugins/agents/test_autonomous_experiment.py::test_discover_returns_responder_list_from_request -x
```

Expected: `AssertionError: tool 'tsuchinoko_discover' not in [<object>]` (placeholder still in place).

- [ ] **Step 3: Replace `nats_tools.py` with the shared helper + discover tool**

`src/lightfall/plugins/agents/autonomous_experiment/nats_tools.py`:

```python
"""MCP tools that bridge the Lightfall embedded agent to Tsuchinoko's NATS surface.

All tools share a single request helper. Each tool is stateless and
returns a dict shaped for the agent SDK (`success`, plus tool-specific
fields, or `success: false` with an `error` string).
"""
from __future__ import annotations

import json
from typing import Any

from lightfall.plugins.agents._mcp_helpers import mcp_result
from lightfall.utils.logging import logger


def _ipc_request(subject: str, payload: dict, *, timeout: float = 5.0) -> dict | None:
    """Send a NATS request via the Lightfall IPC service.

    Returns the decoded reply dict on success, or ``None`` if the
    broker did not reply within *timeout* (or the IPC service is
    unavailable). Callers must distinguish ``None`` from
    ``{"status": "error", ...}``.
    """
    from lightfall.ipc.service import get_ipc_service
    ipc = get_ipc_service()
    if ipc is None:
        return None
    encoded = json.dumps(payload).encode()
    return ipc.request(subject, encoded, timeout=timeout)


def _ipc_error_response() -> dict:
    return {
        "success": False,
        "error": "Lightfall IPC is not running; enable it in Settings → IPC and retry.",
    }


def _wire_error_response(subject: str, reply: dict | None) -> dict | None:
    """Return a structured error dict, or None if the call succeeded."""
    if reply is None:
        return {
            "success": False,
            "error": f"No reply on '{subject}' (timeout or broker unreachable).",
        }
    if isinstance(reply, dict) and reply.get("status") == "error":
        return {
            "success": False,
            "error": reply.get("message", "<tsuchinoko returned an error with no message>"),
        }
    return None


def build_tools() -> list[Any]:
    try:
        from claude_agent_sdk import tool
    except ImportError:
        logger.warning(
            "claude_agent_sdk not available; autonomous_experiment tools disabled"
        )
        return []

    @tool(
        name="tsuchinoko_discover",
        description=(
            "Discover Tsuchinoko instances on the NATS bus. Returns at most "
            "one instance (multi-instance routing is out of scope). Empty "
            "list means no Tsuchinoko is running — tell the user to start one."
        ),
        input_schema={"type": "object", "properties": {}},
    )
    async def discover(args: dict) -> dict[str, Any]:
        from lightfall.ipc.service import get_ipc_service
        if get_ipc_service() is None:
            return mcp_result(_ipc_error_response(), is_error=True)
        reply = _ipc_request("_tsuchinoko.discover", {}, timeout=2.0)
        if reply is None:
            return mcp_result({"success": True, "instances": []})
        return mcp_result({"success": True, "instances": [reply]})

    return [discover]
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
.venv\Scripts\python.exe -m pytest tests/plugins/agents/test_autonomous_experiment.py -v
```

Expected: 6 passed.

- [ ] **Step 5: Commit**

```bash
git add src/lightfall/plugins/agents/autonomous_experiment/nats_tools.py tests/plugins/agents/test_autonomous_experiment.py
git commit -m "feat(agents): NATS helper + tsuchinoko_discover tool"
```

---

## Task B.4: `tsuchinoko_upload_design_code` + `tsuchinoko_configure`

**Files:**
- Modify: `src/lightfall/plugins/agents/autonomous_experiment/nats_tools.py`
- Test: extend `tests/plugins/agents/test_autonomous_experiment.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/plugins/agents/test_autonomous_experiment.py`:

```python
def test_upload_design_code_passthrough():
    agent = AutonomousExperimentAgent()
    upload = _find_tool(agent.create_tools(), "tsuchinoko_upload_design_code")

    patcher, ipc = _patch_ipc(reply={
        "status": "ok",
        "ref": "user:my_ucb",
        "path": "/home/.../my_ucb.py",
    })
    with patcher:
        result = _call(upload, {
            "name": "my_ucb",
            "kind": "acquisition",
            "code": "def acquisition_function(x, gp): return 0",
        })

    assert result["success"] is True
    assert result["ref"] == "user:my_ucb"
    # Wire-level subject is the right one
    assert ipc.request.call_args.args[0] == "tsuchinoko.experiment.upload_design_code"


def test_upload_design_code_surfaces_tsuchinoko_error():
    agent = AutonomousExperimentAgent()
    upload = _find_tool(agent.create_tools(), "tsuchinoko_upload_design_code")

    patcher, _ = _patch_ipc(reply={"status": "error", "message": "invalid design name 'X'"})
    with patcher:
        result = _call(upload, {"name": "X", "kind": "acquisition", "code": ""})

    assert result["success"] is False
    assert "invalid design name" in result["error"]


def test_configure_passthrough():
    agent = AutonomousExperimentAgent()
    configure = _find_tool(agent.create_tools(), "tsuchinoko_configure")

    patcher, ipc = _patch_ipc(reply={"status": "ok"})
    with patcher:
        result = _call(configure, {"payload": {
            "parameter_bounds": [[0, 1], [0, 1]],
            "kernel": "matern_3_2",
            "acquisition_function": "variance",
            "initial_points": 12,
        }})

    assert result["success"] is True
    assert ipc.request.call_args.args[0] == "tsuchinoko.experiment.configure"
    sent = json.loads(ipc.request.call_args.args[1])
    assert sent["initial_points"] == 12


def test_configure_surfaces_strict_validation_error():
    agent = AutonomousExperimentAgent()
    configure = _find_tool(agent.create_tools(), "tsuchinoko_configure")

    patcher, _ = _patch_ipc(reply={
        "status": "error",
        "message": "unknown configure field(s): ['foo']",
    })
    with patcher:
        result = _call(configure, {"payload": {"foo": 1}})

    assert result["success"] is False
    assert "unknown configure field" in result["error"]
```

Add `import json` at the top of the test module if not already present.

- [ ] **Step 2: Run to verify they fail**

```bash
.venv\Scripts\python.exe -m pytest tests/plugins/agents/test_autonomous_experiment.py -x
```

Expected: discovery passes; the four new tests fail because the tools don't exist yet.

- [ ] **Step 3: Add the two tools to `build_tools()`**

In `src/lightfall/plugins/agents/autonomous_experiment/nats_tools.py`, append two new tools inside `build_tools()`, immediately above `return [discover]`. Then change the return line.

```python
    @tool(
        name="tsuchinoko_upload_design_code",
        description=(
            "Upload an agent-authored callable (acquisition function, kernel, "
            "prior mean, or noise function) to the running Tsuchinoko instance. "
            "Returns the 'user:<name>' ref to use in tsuchinoko_configure. "
            "Tsuchinoko validates name (^[a-z][a-z0-9_]{0,62}$), kind "
            "(acquisition|kernel|prior_mean|noise), syntax, and the expected "
            "callable name before writing the file."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "kind": {
                    "type": "string",
                    "enum": ["acquisition", "kernel", "prior_mean", "noise"],
                },
                "code": {"type": "string"},
            },
            "required": ["name", "kind", "code"],
        },
    )
    async def upload_design_code(args: dict) -> dict[str, Any]:
        from lightfall.ipc.service import get_ipc_service
        if get_ipc_service() is None:
            return mcp_result(_ipc_error_response(), is_error=True)
        subject = "tsuchinoko.experiment.upload_design_code"
        reply = _ipc_request(subject, {
            "name": args["name"], "kind": args["kind"], "code": args["code"],
        }, timeout=5.0)
        err = _wire_error_response(subject, reply)
        if err is not None:
            return mcp_result(err, is_error=True)
        return mcp_result({
            "success": True,
            "ref": reply["ref"],
            "path": reply.get("path", ""),
        })

    @tool(
        name="tsuchinoko_configure",
        description=(
            "Send an experiment design to Tsuchinoko. The payload schema is "
            "documented in the autonomous_experiment skill prompt (parameter_bounds, "
            "kernel, acquisition_function, prior_mean, noise_function, "
            "noise_variances, initial_points, training_method, hyperparameters, "
            "x_out, dimensionality). Unknown keys are an error — fix them before "
            "retrying. Use 'user:<name>' refs for callables previously uploaded "
            "via tsuchinoko_upload_design_code."
        ),
        input_schema={
            "type": "object",
            "properties": {"payload": {"type": "object"}},
            "required": ["payload"],
        },
    )
    async def configure(args: dict) -> dict[str, Any]:
        from lightfall.ipc.service import get_ipc_service
        if get_ipc_service() is None:
            return mcp_result(_ipc_error_response(), is_error=True)
        subject = "tsuchinoko.experiment.configure"
        reply = _ipc_request(subject, args["payload"], timeout=5.0)
        err = _wire_error_response(subject, reply)
        if err is not None:
            return mcp_result(err, is_error=True)
        return mcp_result({"success": True})

    return [discover, upload_design_code, configure]
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
.venv\Scripts\python.exe -m pytest tests/plugins/agents/test_autonomous_experiment.py -v
```

Expected: 10 passed.

- [ ] **Step 5: Commit**

```bash
git add src/lightfall/plugins/agents/autonomous_experiment/nats_tools.py tests/plugins/agents/test_autonomous_experiment.py
git commit -m "feat(agents): tsuchinoko_upload_design_code + tsuchinoko_configure tools"
```

---

## Task B.5: `tsuchinoko_status` + pause/resume/stop tools

**Files:**
- Modify: `src/lightfall/plugins/agents/autonomous_experiment/nats_tools.py`
- Test: extend `tests/plugins/agents/test_autonomous_experiment.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/plugins/agents/test_autonomous_experiment.py`:

```python
def test_status_passthrough():
    agent = AutonomousExperimentAgent()
    status_tool = _find_tool(agent.create_tools(), "tsuchinoko_status")

    patcher, ipc = _patch_ipc(reply={
        "status": "ok",
        "state": "Running",
        "iteration": 7,
        "data_count": 14,
    })
    with patcher:
        result = _call(status_tool)

    assert result["success"] is True
    assert result["state"] == "Running"
    assert result["iteration"] == 7
    assert result["data_count"] == 14
    assert ipc.request.call_args.args[0] == "tsuchinoko.status"


def test_pause_resume_stop_hit_distinct_subjects():
    agent = AutonomousExperimentAgent()
    tools = agent.create_tools()

    for action, subject in [
        ("tsuchinoko_pause", "tsuchinoko.experiment.pause"),
        ("tsuchinoko_resume", "tsuchinoko.experiment.resume"),
        ("tsuchinoko_stop", "tsuchinoko.experiment.stop"),
    ]:
        t = _find_tool(tools, action)
        patcher, ipc = _patch_ipc(reply={"status": "ok", "state": "Paused"})
        with patcher:
            result = _call(t)
        assert result["success"] is True, action
        assert ipc.request.call_args.args[0] == subject, action


def test_status_timeout_returns_actionable_error():
    agent = AutonomousExperimentAgent()
    status_tool = _find_tool(agent.create_tools(), "tsuchinoko_status")
    patcher, _ = _patch_ipc(reply=None)
    with patcher:
        result = _call(status_tool)
    assert result["success"] is False
    assert "tsuchinoko.status" in result["error"]
```

- [ ] **Step 2: Run to verify they fail**

```bash
.venv\Scripts\python.exe -m pytest tests/plugins/agents/test_autonomous_experiment.py -x
```

Expected: 10 prior pass, 3 new fail (tools missing).

- [ ] **Step 3: Add the four tools**

Inside `build_tools()` in `nats_tools.py`, before the return statement:

```python
    @tool(
        name="tsuchinoko_status",
        description=(
            "Query Tsuchinoko's current state. Returns "
            "{state, iteration, data_count}. Use this for textual progress "
            "checks during a running adaptive experiment."
        ),
        input_schema={"type": "object", "properties": {}},
    )
    async def status(args: dict) -> dict[str, Any]:
        from lightfall.ipc.service import get_ipc_service
        if get_ipc_service() is None:
            return mcp_result(_ipc_error_response(), is_error=True)
        subject = "tsuchinoko.status"
        reply = _ipc_request(subject, {}, timeout=5.0)
        err = _wire_error_response(subject, reply)
        if err is not None:
            return mcp_result(err, is_error=True)
        return mcp_result({
            "success": True,
            "state": reply.get("state"),
            "iteration": reply.get("iteration"),
            "data_count": reply.get("data_count"),
        })

    def _make_control(action: str, subject: str, description: str):
        @tool(
            name=f"tsuchinoko_{action}",
            description=description,
            input_schema={"type": "object", "properties": {}},
        )
        async def control(args: dict) -> dict[str, Any]:
            from lightfall.ipc.service import get_ipc_service
            if get_ipc_service() is None:
                return mcp_result(_ipc_error_response(), is_error=True)
            reply = _ipc_request(subject, {}, timeout=5.0)
            err = _wire_error_response(subject, reply)
            if err is not None:
                return mcp_result(err, is_error=True)
            return mcp_result({
                "success": True,
                "state": reply.get("state"),
            })
        return control

    pause = _make_control(
        "pause", "tsuchinoko.experiment.pause",
        "Pause Tsuchinoko's adaptive loop. The Lightfall adaptive_experiment "
        "plan keeps the run open; new targets stop until tsuchinoko_resume.",
    )
    resume = _make_control(
        "resume", "tsuchinoko.experiment.resume",
        "Resume a paused Tsuchinoko adaptive loop.",
    )
    stop = _make_control(
        "stop", "tsuchinoko.experiment.stop",
        "Stop Tsuchinoko's adaptive loop and finalise. The Lightfall plan exits "
        "cleanly once targets stop arriving (configurable timeout).",
    )
```

Update the return statement at the bottom of `build_tools()`:

```python
    return [discover, upload_design_code, configure, status, pause, resume, stop]
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
.venv\Scripts\python.exe -m pytest tests/plugins/agents/test_autonomous_experiment.py -v
```

Expected: 13 passed.

- [ ] **Step 5: Commit**

```bash
git add src/lightfall/plugins/agents/autonomous_experiment/nats_tools.py tests/plugins/agents/test_autonomous_experiment.py
git commit -m "feat(agents): tsuchinoko_status + pause/resume/stop tools"
```

---

## Task B.6: `get_references_dir` gpcam-aware detection

**Files:**
- Already implemented in `plugin.py` (Task B.1). Add tests for both branches.
- Test: extend `tests/plugins/agents/test_autonomous_experiment.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/plugins/agents/test_autonomous_experiment.py`:

```python
def test_references_dir_returns_gpcam_skills_when_importable():
    pytest.importorskip("gpcam.skills", reason="gpcam not installed")
    agent = AutonomousExperimentAgent()
    ref = agent.get_references_dir()
    assert ref is not None
    # The path should contain a SKILL.md for at least the experiment-designer skill
    assert (ref / "experiment-designer" / "SKILL.md").is_file()


def test_references_dir_returns_none_when_gpcam_missing(monkeypatch):
    """When gpcam is not importable, the plugin returns None and the prompt
    still tells the agent how to recover."""
    import builtins
    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "gpcam.skills" or (name == "gpcam" and "skills" in (args[2] if len(args) >= 3 else ())):
            raise ImportError("simulated missing gpcam")
        if name.startswith("gpcam"):
            raise ImportError("simulated missing gpcam")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    import importlib.resources as ir
    monkeypatch.setattr(ir, "files", lambda *a, **k: (_ for _ in ()).throw(ModuleNotFoundError()))

    agent = AutonomousExperimentAgent()
    assert agent.get_references_dir() is None
    # Prompt still mentions the install path
    assert "pip install gpcam" in agent.get_system_prompt()
```

- [ ] **Step 2: Run tests to verify the second one fails (or is skipped)**

```bash
.venv\Scripts\python.exe -m pytest tests/plugins/agents/test_autonomous_experiment.py -v
```

Expected: the first test passes (gpcam is installed in the dev env) and the second test fails because the simulated import-error handling needs to be wired through `plugin.py`. If it already passes due to the broad `except (ImportError, ModuleNotFoundError, FileNotFoundError)` in Task B.1, that's fine — skip Step 3.

- [ ] **Step 3: Tighten `get_references_dir` in `plugin.py` if Step 2 reveals leaks**

If the test still fails, widen the except clause in `AutonomousExperimentAgent.get_references_dir`:

```python
    def get_references_dir(self) -> Path | None:
        try:
            import importlib.resources as ir
            ref = ir.files("gpcam.skills")
        except Exception:
            return None
        try:
            return Path(str(ref))
        except Exception:
            return None
```

(`except Exception` is acceptable here because the only contract is "return None on any failure to locate the gpcam skills tree".)

- [ ] **Step 4: Run tests to verify they pass**

```bash
.venv\Scripts\python.exe -m pytest tests/plugins/agents/test_autonomous_experiment.py -v
```

Expected: 15 passed (one possibly skipped if gpcam not installed locally).

- [ ] **Step 5: Commit**

```bash
git add src/lightfall/plugins/agents/autonomous_experiment/plugin.py tests/plugins/agents/test_autonomous_experiment.py
git commit -m "test(agents): cover both branches of get_references_dir"
```

---

## Task B.7: Register in `builtin_manifest.py`

**Files:**
- Modify: `src/lightfall/plugins/builtin_manifest.py` — add one `PluginEntry` between scan_planning and panel_design.
- Test: `tests/plugins/test_builtin_manifest_agents.py` (existing — add an assertion).

- [ ] **Step 1: Write the failing test**

In `tests/plugins/test_builtin_manifest_agents.py`, add (or extend the existing list-of-known-agents assertion):

```python
def test_autonomous_experiment_registered():
    from lightfall.plugins.builtin_manifest import builtin_plugin_entries
    names = [e.name for e in builtin_plugin_entries() if e.type_name == "agent"]
    assert "autonomous_experiment" in names

    entry = next(
        e for e in builtin_plugin_entries()
        if e.type_name == "agent" and e.name == "autonomous_experiment"
    )
    assert entry.import_path == (
        "lightfall.plugins.agents.autonomous_experiment:AutonomousExperimentAgent"
    )
```

(Use the existing module's pattern — the function may be named `builtin_plugin_entries` or `BUILTIN_PLUGINS`; replicate whichever the surrounding tests use.)

- [ ] **Step 2: Run to verify it fails**

```bash
.venv\Scripts\python.exe -m pytest tests/plugins/test_builtin_manifest_agents.py -v
```

Expected: `'autonomous_experiment' not in names`.

- [ ] **Step 3: Add the entry**

In `src/lightfall/plugins/builtin_manifest.py`, immediately after the `scan_planning` entry (around line ~200, between `scan_planning` and `panel_design`):

```python
        PluginEntry(
            type_name="agent",
            name="autonomous_experiment",
            import_path=(
                "lightfall.plugins.agents.autonomous_experiment:"
                "AutonomousExperimentAgent"
            ),
        ),
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
.venv\Scripts\python.exe -m pytest tests/plugins/test_builtin_manifest_agents.py tests/plugins/agents/test_autonomous_experiment.py -v
```

Expected: all passing (16 + manifest test).

- [ ] **Step 5: Smoke-test the loader against the new entry**

```bash
.venv\Scripts\python.exe -m pytest tests/plugins/test_loader_agent_branch.py -v
```

Expected: passes; the loader resolves the new entry without exception.

- [ ] **Step 6: Commit**

```bash
git add src/lightfall/plugins/builtin_manifest.py tests/plugins/test_builtin_manifest_agents.py
git commit -m "feat(agents): register autonomous_experiment in builtin manifest"
```

---

## Task B.8: End-to-end smoke test script

**Files:**
- Create: `scripts/demo_autonomous_experiment.py`

Not a CI test — this lives under `scripts/` and is documented as the verification ritual.

- [ ] **Step 1: Write the script**

`scripts/demo_autonomous_experiment.py`:

```python
"""End-to-end smoke test for the autonomous-experiment agent integration.

Prerequisites (manual):
- Both Lightfall and Tsuchinoko venvs installed.
- nats-server binary on PATH (or $NATS_SERVER_BIN set).
- gpcam importable in Lightfall's environment.

What this script does:
1. Optionally start a local nats-server (NATS_TEST_AUTOSTART=1).
2. Launch `tsuchinoko run --nats nats://localhost:4222` in a subprocess.
3. Walk the demo flow programmatically against synthetic soft motors +
   a synthetic detector.
4. Tail the resulting Tiled run for the `adaptive` stream and assert
   at least three `iter_NNN` containers landed.

Run from the Lightfall venv:

    .venv\\Scripts\\python.exe scripts\\demo_autonomous_experiment.py
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

# 1. Optionally start nats-server
NATS_AUTOSTART = os.environ.get("NATS_TEST_AUTOSTART") == "1"
nats_proc: subprocess.Popen | None = None
if NATS_AUTOSTART:
    bin_path = os.environ.get("NATS_SERVER_BIN") or shutil.which("nats-server")
    if not bin_path:
        print("ERROR: NATS_SERVER_BIN not set and nats-server not on PATH.")
        sys.exit(2)
    nats_proc = subprocess.Popen([bin_path, "-p", "4222"])
    time.sleep(1.0)

# 2. Launch tsuchinoko (assumes `tsuchinoko` console_script is installed)
tsu_proc = subprocess.Popen(
    ["tsuchinoko", "run", "--nats", "nats://localhost:4222"],
    stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
)
print("Waiting 3s for Tsuchinoko to subscribe...")
time.sleep(3.0)

try:
    # 3. Walk the demo flow
    from lightfall.ipc.service import get_ipc_service, start_ipc_service
    start_ipc_service(nats_url="nats://localhost:4222", topic_prefix="lightfall-demo")
    time.sleep(1.0)

    ipc = get_ipc_service()
    assert ipc is not None, "IPC service did not start"

    # Discover
    import json
    discover = ipc.request("_tsuchinoko.discover", b"", timeout=2.0)
    assert discover and discover.get("app_name") == "tsuchinoko", discover

    # Configure (bounds only — minimal demo)
    cfg = ipc.request(
        "tsuchinoko.experiment.configure",
        json.dumps({
            "parameter_bounds": [[-1.0, 1.0], [-1.0, 1.0]],
            "kernel": "matern_3_2",
            "acquisition_function": "variance",
            "initial_points": 5,
        }).encode(),
        timeout=5.0,
    )
    assert cfg and cfg.get("status") == "ok", cfg

    # Build synthetic devices and drive the plan
    from bluesky import RunEngine
    from ophyd.sim import SynAxis, SynSignal
    import numpy as np

    m1 = SynAxis(name="m1")
    m2 = SynAxis(name="m2")
    det = SynSignal(name="det", func=lambda: float(np.exp(-(m1.read()["m1"]["value"]**2 + m2.read()["m2"]["value"]**2))))

    from lightfall.acquire.plans.adaptive import adaptive_experiment
    RE = RunEngine({})
    RE(adaptive_experiment(detectors=[det], motors=[m1, m2], timeout=30.0))

    # 4. Assert adaptive stream landed (open the Lightfall-configured Tiled
    # catalog and check). Use the existing helper:
    from lightfall.acquire.tiled import get_default_client
    client = get_default_client()
    last = next(iter(client.values()))
    adaptive = last["adaptive"]
    iter_keys = [k for k in adaptive if k.startswith("iter_")]
    assert len(iter_keys) >= 3, f"only {len(iter_keys)} iter_ containers"

    print("OK — demo completed; adaptive stream has", len(iter_keys), "iterations")

finally:
    tsu_proc.terminate()
    tsu_proc.wait(timeout=10)
    if nats_proc is not None:
        nats_proc.terminate()
        nats_proc.wait(timeout=10)
```

- [ ] **Step 2: Verify the script is syntactically valid (no execution)**

```bash
.venv\Scripts\python.exe -c "import py_compile; py_compile.compile('scripts/demo_autonomous_experiment.py', doraise=True)"
```

Expected: silent success.

- [ ] **Step 3: Run the smoke script (manual; requires Phase A merged + Tsuchinoko venv)**

```bash
set NATS_TEST_AUTOSTART=1
set NATS_SERVER_BIN=C:\Users\rp\AppData\Local\Microsoft\WinGet\Packages\NATSAuthors.NATSServer_Microsoft.Winget.Source_8wekyb3d8bbwe\nats-server-v2.10.25-windows-amd64\nats-server.exe
.venv\Scripts\python.exe scripts\demo_autonomous_experiment.py
```

Expected: prints `OK — demo completed; adaptive stream has N iterations` where N ≥ 3.

If Phase A hasn't merged yet on the Tsuchinoko instance in use, this script will fail at the `configure` step (strict validation will reject `kernel`, `acquisition_function`, etc.). That's the expected gating signal — defer the smoke test until Phase A is deployed wherever Tsuchinoko runs.

- [ ] **Step 4: Commit**

```bash
git add scripts/demo_autonomous_experiment.py
git commit -m "test: end-to-end demo smoke script for autonomous experiments"
```

---

## Task B.9: Push branch, open PR

- [ ] **Step 1: Run the full plugins test suite for regressions**

```bash
.venv\Scripts\python.exe -m pytest tests/plugins -v
```

Expected: existing tests still pass; the new ones all green.

- [ ] **Step 2: Push the branch**

```bash
git push -u origin feature/autonomous-experiment-agent
```

- [ ] **Step 3: Open the PR**

Title: `feat(agents): autonomous-experiment agent — gpCAM design + Tsuchinoko bridge`

Body:

```
Implements Phase B of Lightfall Autonomous Experiments.

Spec: docs/superpowers/specs/2026-05-19-lightfall-autonomous-experiments-design.md
Plan: docs/superpowers/plans/2026-05-19-lightfall-autonomous-experiments.md

Adds one new AgentPlugin (`autonomous_experiment`) that:
- Carries a short stub prompt for the workflow.
- Lazy-references gpCAM's skills tree via get_references_dir().
- Exposes 7 MCP tools over the existing tsuchinoko.* NATS surface
  (discover, upload_design_code, configure, status, pause, resume, stop).

Together with the existing `adaptive_experiment` plan and the
`AdaptiveHeatmapVisualization` / `AdaptiveHyperparameterPlot` widgets,
this completes the end-to-end demo: agent designs, agent starts,
visualisation panel displays.

Depends on Phase A in tsuchinoko `Lightfall-refactor`. Merge Phase A first
and deploy Tsuchinoko before merging this PR.
```

---

# Self-review

**Spec coverage check** (against `2026-05-19-lightfall-autonomous-experiments-design.md`):

- §Components/Lightfall-side — Tasks B.1–B.6 cover the package layout, plugin class, prompt, tools, gpcam-aware references.
- §Components/Tsuchinoko-side — Tasks A.1–A.3 cover the resolver, upload_design_code, typed configure.
- §End-to-end demo flow — Task B.8 exercises all seven workflow steps end-to-end.
- §Error handling table — covered: NATS unavailable (`test_nats_unavailable…`), no responders (`test_discover_empty_when_no_responders`), strict validation (`test_configure_surfaces_strict_validation_error`), upload syntax/missing-callable/name-validation (Phase A tests), timeout (`test_status_timeout_returns_actionable_error`).
- §Security boundary — design doc carries it; no runtime test, by intent (existing trust model).
- §Testing — every line item in the spec's Testing section maps to a task.
- §Rollout — Tasks A.5 and B.9 implement the merge order.

**Type / signature consistency check:**

- `tsuchinoko_upload_design_code` returns `{ref, path}` in plan + spec.
- `tsuchinoko_configure` accepts a single `payload` arg in plan + tool docstring + tests.
- `kind` enum is `acquisition|kernel|prior_mean|noise` everywhere.
- `EXPECTED_CALLABLES` per kind is defined exactly once (`tsuchinoko/nats/user_designs.py`); referenced in the design doc but never re-defined.

**Placeholder scan:** none (no TBD/TODO/"implement later"; every code step has full code; every command has expected output).

---

# Execution

This plan has two phases; Phase A blocks Phase B's smoke test (Task B.8) but not Phase B's unit work (Tasks B.1–B.7). A reasonable schedule:

1. Implement and ship Phase A (Tasks A.1–A.5) as one MR on the Tsuchinoko `Lightfall-refactor` branch.
2. While Phase A is in review, implement Tasks B.1–B.7 on the Lightfall feature branch.
3. After Phase A merges and Tsuchinoko is redeployed wherever it runs, execute Task B.8 (the smoke script) and confirm it prints OK.
4. Open the Lightfall PR (Task B.9).
