# Notebook Pipelines Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship Lightfall notebook pipelines: a headless executor consumes NATS jobs, runs pip-installed `PipelinePlugin` packages via Papermill in per-package venvs, writes derived runs to Tiled with auto-stamped provenance; Lightfall-side glue mints job-scoped Tiled API keys, manages user-configured triggers, and surfaces the feature in three UI panels.

**Architecture:** Two repositories. **`lightfall-pipelines`** (new, at `~/PycharmProjects/lightfall-pipelines/`) — the SDK + executor that pipeline authors and ops install; no PySide6 dependency. **`lightfall` (ncs/ncs)** — adds `lightfall.auth.mint_job_key`, the trigger framework, `lightfall.pipelines.PipelineClient`, and three UI surfaces. The wire format between them is NATS request/reply on `lightfall.pipeline.<host>` topics. End-to-end work uses Tiled's per-user API-key endpoint (delivered in Plan A `~/PycharmProjects/als-tiled/docs/superpowers/plans/2026-05-16-user-scoped-api-keys.md`) as a precondition.

**Tech Stack:** Python 3.11+, PySide6 6.6+ (Lightfall side only), nats-py, papermill, scrapbook, jupyter_client, tiled[client], bluesky[tiled], pytest + pytest-asyncio + pytest-qt.

**Spec reference:** `~/PycharmProjects/ncs/ncs/docs/superpowers/specs/2026-05-15-notebook-pipelines-design.md`.

**Prerequisite:** Plan A merged + deployed. `bcgtiled` accepts `POST /api/v1/auth/apikey` with a Keycloak bearer.

---

## File structure

### New repository: `~/PycharmProjects/lightfall-pipelines/`

```
lightfall-pipelines/
├── pyproject.toml                    # hatch + hatch-vcs; [project.optional-dependencies] for executor extra
├── README.md
├── src/lightfall_pipelines/
│   ├── __init__.py                   # __version__ re-export
│   ├── plugin.py                     # PipelinePlugin ABC, discover()
│   ├── notebook.py                   # TiledWriter wrapper, get_input_run, get_provenance, bootstrap-cell helpers
│   ├── messages.py                   # JobMessage / ProgressEvent dataclasses + serde
│   ├── executor/
│   │   ├── __init__.py
│   │   ├── env_cache.py              # EnvCache (venv + pip)
│   │   ├── runner.py                 # PapermillRunner (in-memory exec + scrapbook harvest)
│   │   ├── notebook_store.py         # NotebookStore (disk-write + Tiled-pointer)
│   │   └── service.py                # PipelineService (NATS subscribe + queue + dispatch + idempotency)
│   └── cli.py                        # `lightfall-pipelines` console-script
└── tests/
    ├── conftest.py
    ├── test_plugin.py
    ├── test_notebook.py
    ├── test_messages.py
    ├── test_env_cache.py
    ├── test_runner.py
    ├── test_notebook_store.py
    ├── test_service.py
    ├── test_cli.py
    └── fixtures/
        ├── echo_pipeline/             # tiny pip-installable fixture plugin
        │   ├── pyproject.toml
        │   └── src/echo_pipeline/{__init__.py,echo.ipynb}
        └── notebooks/
            └── echo.ipynb
```

### Existing repository: `~/PycharmProjects/ncs/ncs/`

```
ncs/ncs/
├── src/lightfall/
│   ├── auth/
│   │   └── job_key.py                # NEW: mint_job_key() helper
│   ├── acquire/
│   │   └── triggers/                 # NEW directory
│   │       ├── __init__.py
│   │       ├── base.py               # Trigger ABC
│   │       ├── manager.py            # TriggerManager (hooks BaseEngine.subscribe)
│   │       ├── filter.py             # FilterPredicate + predicate evaluation
│   │       ├── run_start.py          # RunStartTrigger
│   │       ├── run_end.py            # RunEndTrigger
│   │       └── manual.py             # ManualTrigger
│   ├── pipelines/                    # NEW directory (Lightfall-side)
│   │   ├── __init__.py
│   │   └── client.py                 # PipelineClient (mint, submit, track, revoke)
│   └── ui/
│       ├── dialogs/
│       │   └── run_pipeline_dialog.py    # NEW: parameter form + submit
│       └── panels/
│           ├── pipeline_jobs_panel.py    # NEW: queue + recent jobs
│           └── pipeline_triggers_panel.py # NEW: settings UI for triggers
└── tests/
    ├── auth/test_job_key.py
    ├── acquire/triggers/test_{base,manager,filter,run_start,run_end,manual}.py
    ├── pipelines/test_client.py
    └── ui/
        ├── dialogs/test_run_pipeline_dialog.py
        ├── panels/test_pipeline_jobs_panel.py
        └── panels/test_pipeline_triggers_panel.py
```

### New repository: `~/PycharmProjects/als-saxs/` (Stage 4 reference)

```
als-saxs/
├── README.md
└── packages/
    └── als-saxs-pipelines/
        ├── pyproject.toml
        ├── src/als_saxs_pipelines/
        │   ├── __init__.py
        │   ├── reduce.py             # ReduceSaxsPipeline class
        │   └── reduce.ipynb
        └── tests/test_reduce.py
```

---

## STAGE 1 — Lightfall-side shared primitives

### Task 1: `lightfall.auth.mint_job_key()` helper

**Files:**
- Create: `src/lightfall/auth/job_key.py`
- Create: `tests/auth/test_job_key.py`
- Modify: `src/lightfall/auth/__init__.py` (export `mint_job_key`)

- [ ] **Step 1: Confirm `lightfall.auth` exists and find the session-token accessor**

Run: `ls src/lightfall/auth/ && grep -n "session\|token\|bearer" src/lightfall/auth/*.py | head -20`

Identify how to obtain the current Keycloak access token. Common path: `lightfall.auth.session_manager.SessionManager.get_current_token()`. Note the exact accessor for use in `job_key.py`.

- [ ] **Step 2: Write the failing tests**

Create `tests/auth/test_job_key.py`:

```python
"""Tests for lightfall.auth.job_key.mint_job_key()."""
from __future__ import annotations

from unittest.mock import MagicMock, patch
import pytest

from lightfall.auth.job_key import MintedJobKey, mint_job_key


@pytest.fixture
def mock_httpx_post():
    """Patch httpx.post to return a canned Tiled apikey response."""
    with patch("lightfall.auth.job_key.httpx.post") as mock:
        response = MagicMock()
        response.status_code = 200
        response.json.return_value = {
            "secret": "ab12cd34ef56" + "0" * 52,        # 64-hex chars
            "first_eight": "ab12cd34",
            "expiration_time": "2026-05-17T20:14:00Z",
            "scopes": ["read:metadata", "read:data", "write:metadata", "write:data"],
            "note": "lightfall pipeline reduce_saxs",
        }
        response.raise_for_status.return_value = None
        mock.return_value = response
        yield mock


def test_mint_job_key_returns_secret_and_expiry(mock_httpx_post):
    result = mint_job_key(
        tiled_url="https://tiled.test/api/v1",
        bearer_token="fake-keycloak-token",
        lifetime=86400,
        scopes=["read:metadata", "read:data", "write:metadata", "write:data"],
        note="lightfall pipeline reduce_saxs",
    )
    assert isinstance(result, MintedJobKey)
    assert result.secret.startswith("ab12cd34")
    assert result.first_eight == "ab12cd34"
    assert result.expires_at == "2026-05-17T20:14:00Z"


def test_mint_job_key_posts_to_correct_url(mock_httpx_post):
    mint_job_key(
        tiled_url="https://tiled.test/api/v1",
        bearer_token="fake-keycloak-token",
        lifetime=3600,
        scopes=["read:metadata"],
        note="t",
    )
    args, kwargs = mock_httpx_post.call_args
    assert args[0] == "https://tiled.test/api/v1/auth/apikey"
    assert kwargs["headers"]["Authorization"] == "Bearer fake-keycloak-token"
    assert kwargs["json"]["lifetime"] == 3600
    assert kwargs["json"]["scopes"] == ["read:metadata"]


def test_revoke_calls_delete():
    with patch("lightfall.auth.job_key.httpx.delete") as mock_del:
        resp = MagicMock(status_code=200)
        resp.raise_for_status.return_value = None
        mock_del.return_value = resp
        from lightfall.auth.job_key import revoke_job_key
        revoke_job_key("https://tiled.test/api/v1", "bearer-tok", first_eight="ab12cd34")
        args, kwargs = mock_del.call_args
        assert args[0] == "https://tiled.test/api/v1/auth/apikey"
        assert kwargs["params"] == {"first_eight": "ab12cd34"}
        assert kwargs["headers"]["Authorization"] == "Bearer bearer-tok"
```

- [ ] **Step 3: Run tests; verify they fail**

Run: `.venv/Scripts/python -m pytest tests/auth/test_job_key.py -v`

Expected: ImportError on `from lightfall.auth.job_key import ...` because the module doesn't exist yet.

- [ ] **Step 4: Implement `lightfall.auth.job_key`**

Create `src/lightfall/auth/job_key.py`:

```python
"""Job-scoped Tiled API key minting.

Provides `mint_job_key()` and `revoke_job_key()` — thin wrappers over Tiled's
standard /api/v1/auth/apikey endpoint. Used by lightfall-pipelines (and tsuchinoko
and any future headless workload) to obtain a short-lived API key that
outlives the user's Keycloak access token.

als-tiled grants `create:apikeys` / `revoke:apikeys` to authenticated users
(see Plan A 2026-05-16-user-scoped-api-keys.md in als-tiled).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional

import httpx
from loguru import logger


@dataclass(frozen=True)
class MintedJobKey:
    secret: str
    first_eight: str
    expires_at: Optional[str]
    scopes: List[str]
    note: Optional[str]


def mint_job_key(
    tiled_url: str,
    bearer_token: str,
    lifetime: int,
    scopes: List[str],
    note: str,
    *,
    timeout: float = 10.0,
) -> MintedJobKey:
    """Mint a user-scoped Tiled API key.

    Args:
        tiled_url: Base URL of the Tiled API (e.g. "https://bcgtiled.../api/v1").
        bearer_token: Caller's Keycloak access token.
        lifetime: TTL in seconds.
        scopes: Scopes to grant. Must be a subset of the caller's scopes.
        note: Free-form audit string (shows up in Tiled's apikey table).

    Returns:
        MintedJobKey with the secret and metadata.

    Raises:
        httpx.HTTPStatusError on a 4xx/5xx from Tiled.
    """
    url = tiled_url.rstrip("/") + "/auth/apikey"
    response = httpx.post(
        url,
        headers={"Authorization": f"Bearer {bearer_token}"},
        json={"lifetime": lifetime, "scopes": scopes, "note": note},
        timeout=timeout,
    )
    response.raise_for_status()
    body = response.json()
    minted = MintedJobKey(
        secret=body["secret"],
        first_eight=body["first_eight"],
        expires_at=body.get("expiration_time"),
        scopes=body.get("scopes", scopes),
        note=body.get("note"),
    )
    logger.info("minted job key first_eight={} note='{}'", minted.first_eight, minted.note)
    return minted


def revoke_job_key(
    tiled_url: str,
    bearer_token: str,
    *,
    first_eight: str,
    timeout: float = 10.0,
) -> None:
    """Revoke a previously-minted job key. Best-effort; expiry is the backstop."""
    url = tiled_url.rstrip("/") + "/auth/apikey"
    response = httpx.delete(
        url,
        headers={"Authorization": f"Bearer {bearer_token}"},
        params={"first_eight": first_eight},
        timeout=timeout,
    )
    response.raise_for_status()
    logger.info("revoked job key first_eight={}", first_eight)
```

- [ ] **Step 5: Export from `lightfall.auth.__init__`**

Open `src/lightfall/auth/__init__.py` and add:

```python
from lightfall.auth.job_key import MintedJobKey, mint_job_key, revoke_job_key

__all__ = [..., "MintedJobKey", "mint_job_key", "revoke_job_key"]
```

- [ ] **Step 6: Run tests; verify they pass**

Run: `.venv/Scripts/python -m pytest tests/auth/test_job_key.py -v`

Expected: 3 PASS.

- [ ] **Step 7: Commit**

```bash
git add src/lightfall/auth/job_key.py src/lightfall/auth/__init__.py tests/auth/test_job_key.py
git commit -m "feat(auth): mint_job_key() for short-lived Tiled API keys

Thin wrapper over Tiled's POST/DELETE /api/v1/auth/apikey. Used by
lightfall-pipelines, tsuchinoko, and any future headless workload to escape
the Keycloak access-token refresh treadmill. Requires Plan A's
create:apikeys grant in als-tiled."
```

---

### Task 2: Trigger framework — base ABC + filter predicate + manager

**Files:**
- Create: `src/lightfall/acquire/triggers/__init__.py`
- Create: `src/lightfall/acquire/triggers/base.py`
- Create: `src/lightfall/acquire/triggers/filter.py`
- Create: `src/lightfall/acquire/triggers/manager.py`
- Create: `tests/acquire/triggers/test_base.py`
- Create: `tests/acquire/triggers/test_filter.py`
- Create: `tests/acquire/triggers/test_manager.py`

- [ ] **Step 1: Inspect `BaseEngine.subscribe()` shape**

Run: `grep -n "def subscribe\|def emit\|_subscribers" src/lightfall/acquire/engine/base.py | head -20`

Confirm callbacks have the signature `(name: str, doc: dict) -> Any` and `subscribe()` returns a token (int).

- [ ] **Step 2: Write failing tests for `FilterPredicate`**

Create `tests/acquire/triggers/test_filter.py`:

```python
"""Tests for trigger filter predicates."""
from __future__ import annotations

import pytest

from lightfall.acquire.triggers.filter import FilterPredicate


def test_filter_plan_name_exact_match():
    f = FilterPredicate(plan_name="count")
    assert f.matches({"plan_name": "count", "tags": []})
    assert not f.matches({"plan_name": "scan", "tags": []})


def test_filter_plan_name_any_of():
    f = FilterPredicate(plan_name=["count", "scan"])
    assert f.matches({"plan_name": "scan", "tags": []})
    assert not f.matches({"plan_name": "list_scan", "tags": []})


def test_filter_tags_includes_any():
    f = FilterPredicate(tags_includes=["saxs"])
    assert f.matches({"plan_name": "count", "tags": ["saxs", "raw"]})
    assert not f.matches({"plan_name": "count", "tags": ["waxs"]})


def test_filter_start_doc_match_exact():
    f = FilterPredicate(start_doc_match={"sample_name": "Si-001"})
    assert f.matches({"plan_name": "count", "tags": [], "sample_name": "Si-001"})
    assert not f.matches({"plan_name": "count", "tags": [], "sample_name": "Si-002"})


def test_filter_combination_is_and():
    f = FilterPredicate(plan_name="count", tags_includes=["saxs"])
    assert f.matches({"plan_name": "count", "tags": ["saxs"]})
    assert not f.matches({"plan_name": "count", "tags": ["waxs"]})
    assert not f.matches({"plan_name": "scan", "tags": ["saxs"]})


def test_filter_empty_matches_all():
    f = FilterPredicate()
    assert f.matches({"plan_name": "count", "tags": []})
    assert f.matches({})
```

- [ ] **Step 3: Run; verify failure**

Run: `.venv/Scripts/python -m pytest tests/acquire/triggers/test_filter.py -v`

Expected: ImportError.

- [ ] **Step 4: Implement `FilterPredicate`**

Create `src/lightfall/acquire/triggers/filter.py`:

```python
"""Filter predicates for trigger matching.

Phase 1 fixed-set: plan_name, tags_includes, start_doc_match. Combinations
are conjunctive (AND). Free-form expressions (jq-style) are deferred.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Union


@dataclass(frozen=True)
class FilterPredicate:
    """Predicate evaluated against a bluesky start doc."""

    plan_name: Optional[Union[str, List[str]]] = None
    tags_includes: Optional[Union[str, List[str]]] = None
    start_doc_match: Optional[Dict[str, Any]] = None

    def matches(self, start_doc: Dict[str, Any]) -> bool:
        if self.plan_name is not None:
            allowed = [self.plan_name] if isinstance(self.plan_name, str) else list(self.plan_name)
            if start_doc.get("plan_name") not in allowed:
                return False

        if self.tags_includes is not None:
            wanted = {self.tags_includes} if isinstance(self.tags_includes, str) else set(self.tags_includes)
            doc_tags = set(start_doc.get("tags") or [])
            if not wanted & doc_tags:
                return False

        if self.start_doc_match:
            for k, v in self.start_doc_match.items():
                if start_doc.get(k) != v:
                    return False

        return True
```

- [ ] **Step 5: Run; verify pass**

Run: `.venv/Scripts/python -m pytest tests/acquire/triggers/test_filter.py -v`

Expected: 6 PASS.

- [ ] **Step 6: Write failing tests for `Trigger` ABC + `TriggerManager`**

Create `tests/acquire/triggers/test_base.py`:

```python
"""Tests for the Trigger ABC."""
from __future__ import annotations

import pytest

from lightfall.acquire.triggers.base import Trigger


def test_trigger_is_abstract():
    with pytest.raises(TypeError):
        Trigger()                       # noqa - abstract instantiation


def test_concrete_trigger_must_implement_attach_and_detach():
    class Half(Trigger):
        def attach(self, manager):
            pass
        # missing detach
    with pytest.raises(TypeError):
        Half()
```

Create `tests/acquire/triggers/test_manager.py`:

```python
"""Tests for TriggerManager — engine subscription, fire routing."""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from lightfall.acquire.triggers.base import Trigger
from lightfall.acquire.triggers.manager import TriggerManager


class _FakeEngine:
    def __init__(self):
        self._cbs = {}
        self._next = 1

    def subscribe(self, cb):
        tok = self._next
        self._next += 1
        self._cbs[tok] = cb
        return tok

    def unsubscribe(self, tok):
        self._cbs.pop(tok, None)

    def emit(self, name, doc):
        for cb in list(self._cbs.values()):
            cb(name, doc)


class _RecordingTrigger(Trigger):
    def __init__(self):
        self.attached_to = None
        self.detached = False
        self.fires = []

    def attach(self, manager):
        self.attached_to = manager

    def detach(self):
        self.detached = True

    def fire(self, run_uid, parameters):
        self.fires.append((run_uid, parameters))


def test_manager_attaches_triggers():
    engine = _FakeEngine()
    mgr = TriggerManager(engine=engine, submit_callable=MagicMock())
    t = _RecordingTrigger()
    mgr.add(t)
    assert t.attached_to is mgr


def test_manager_detaches_on_remove():
    engine = _FakeEngine()
    mgr = TriggerManager(engine=engine, submit_callable=MagicMock())
    t = _RecordingTrigger()
    mgr.add(t)
    mgr.remove(t)
    assert t.detached


def test_manager_routes_fire_to_submit_callable():
    engine = _FakeEngine()
    submit = MagicMock()
    mgr = TriggerManager(engine=engine, submit_callable=submit)
    mgr.fire(pipeline="reduce_saxs", run_uid="abc", parameters={"k": 1})
    submit.assert_called_once_with(pipeline="reduce_saxs", run_uid="abc", parameters={"k": 1})


def test_manager_exposes_engine_subscribe_to_subclasses():
    engine = _FakeEngine()
    mgr = TriggerManager(engine=engine, submit_callable=MagicMock())
    called = []
    tok = mgr.subscribe_engine(lambda name, doc: called.append((name, doc)))
    engine.emit("start", {"uid": "u1"})
    assert called == [("start", {"uid": "u1"})]
    mgr.unsubscribe_engine(tok)
    engine.emit("start", {"uid": "u2"})
    assert called == [("start", {"uid": "u1"})]
```

- [ ] **Step 7: Run; verify failure**

Run: `.venv/Scripts/python -m pytest tests/acquire/triggers/ -v`

Expected: ImportError on missing modules.

- [ ] **Step 8: Implement `Trigger` ABC**

Create `src/lightfall/acquire/triggers/base.py`:

```python
"""Trigger abstract base class.

A Trigger is something that, on some criterion, asks the TriggerManager to
fire a pipeline submission. Concrete subclasses determine the criterion
(run-start doc match, run-stop doc match, manual invocation).
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from lightfall.acquire.triggers.manager import TriggerManager


class Trigger(ABC):
    """Base class for pipeline triggers."""

    @abstractmethod
    def attach(self, manager: "TriggerManager") -> None:
        """Called when added to a manager. Subscribe to engine docs here."""

    @abstractmethod
    def detach(self) -> None:
        """Called when removed. Unsubscribe from the engine."""
```

- [ ] **Step 9: Implement `TriggerManager`**

Create `src/lightfall/acquire/triggers/manager.py`:

```python
"""TriggerManager — owns a set of configured Triggers, hooks BaseEngine.

The manager is engine-agnostic: it only uses BaseEngine.subscribe() /
unsubscribe() (`src/lightfall/acquire/engine/base.py:396`). Triggers subscribe
through the manager so their tokens are tracked centrally.
"""
from __future__ import annotations

from typing import Any, Callable, Dict, List, Set

from loguru import logger

from lightfall.acquire.triggers.base import Trigger


class TriggerManager:
    """Owns Triggers, routes their `fire()` calls to a submit callable."""

    def __init__(
        self,
        engine: Any,
        submit_callable: Callable[..., None],
    ) -> None:
        self._engine = engine
        self._submit = submit_callable
        self._triggers: List[Trigger] = []
        self._engine_tokens: Set[int] = set()

    def add(self, trigger: Trigger) -> None:
        self._triggers.append(trigger)
        trigger.attach(self)
        logger.debug("TriggerManager: added {}", type(trigger).__name__)

    def remove(self, trigger: Trigger) -> None:
        try:
            self._triggers.remove(trigger)
        except ValueError:
            return
        trigger.detach()
        logger.debug("TriggerManager: removed {}", type(trigger).__name__)

    def clear(self) -> None:
        for t in list(self._triggers):
            self.remove(t)

    def triggers(self) -> List[Trigger]:
        return list(self._triggers)

    def subscribe_engine(self, callback: Callable[[str, Dict[str, Any]], Any]) -> int:
        tok = self._engine.subscribe(callback)
        self._engine_tokens.add(tok)
        return tok

    def unsubscribe_engine(self, token: int) -> None:
        self._engine.unsubscribe(token)
        self._engine_tokens.discard(token)

    def fire(self, *, pipeline: str, run_uid: str, parameters: Dict[str, Any]) -> None:
        logger.info("TriggerManager: fire pipeline={} run_uid={}", pipeline, run_uid)
        self._submit(pipeline=pipeline, run_uid=run_uid, parameters=parameters)
```

- [ ] **Step 10: Add `__init__.py`**

Create `src/lightfall/acquire/triggers/__init__.py`:

```python
"""Trigger framework — engine-agnostic dispatch for pipeline submissions."""
from lightfall.acquire.triggers.base import Trigger
from lightfall.acquire.triggers.filter import FilterPredicate
from lightfall.acquire.triggers.manager import TriggerManager

__all__ = ["Trigger", "FilterPredicate", "TriggerManager"]
```

- [ ] **Step 11: Run all trigger tests; verify pass**

Run: `.venv/Scripts/python -m pytest tests/acquire/triggers/ -v`

Expected: all PASS.

- [ ] **Step 12: Commit**

```bash
git add src/lightfall/acquire/triggers/ tests/acquire/triggers/
git commit -m "feat(acquire/triggers): base ABC + FilterPredicate + TriggerManager

Engine-agnostic trigger framework hooked into BaseEngine.subscribe().
FilterPredicate supports the Phase 1 fixed set (plan_name, tags_includes,
start_doc_match). Concrete subclasses land in Task 3."
```

---

### Task 3: Concrete triggers — RunStartTrigger / RunEndTrigger / ManualTrigger

**Files:**
- Create: `src/lightfall/acquire/triggers/run_start.py`
- Create: `src/lightfall/acquire/triggers/run_end.py`
- Create: `src/lightfall/acquire/triggers/manual.py`
- Create: `tests/acquire/triggers/test_run_start.py`
- Create: `tests/acquire/triggers/test_run_end.py`
- Create: `tests/acquire/triggers/test_manual.py`
- Modify: `src/lightfall/acquire/triggers/__init__.py` (re-export the three concrete classes)

- [ ] **Step 1: Write failing tests for `RunStartTrigger`**

Create `tests/acquire/triggers/test_run_start.py`:

```python
"""Tests for RunStartTrigger."""
from unittest.mock import MagicMock

import pytest

from lightfall.acquire.triggers.filter import FilterPredicate
from lightfall.acquire.triggers.manager import TriggerManager
from lightfall.acquire.triggers.run_start import RunStartTrigger


class _FakeEngine:
    def __init__(self):
        self._cbs = {}
        self._next = 1

    def subscribe(self, cb):
        tok = self._next
        self._next += 1
        self._cbs[tok] = cb
        return tok

    def unsubscribe(self, tok):
        self._cbs.pop(tok, None)

    def emit(self, name, doc):
        for cb in list(self._cbs.values()):
            cb(name, doc)


def test_run_start_fires_on_matching_start_doc():
    engine = _FakeEngine()
    submit = MagicMock()
    mgr = TriggerManager(engine=engine, submit_callable=submit)
    trigger = RunStartTrigger(
        filter=FilterPredicate(plan_name="count"),
        pipeline="reduce_saxs",
        parameter_overrides={"roi_x": [0, 1024]},
    )
    mgr.add(trigger)

    engine.emit("start", {"uid": "abc", "plan_name": "count", "tags": ["saxs"]})

    submit.assert_called_once_with(
        pipeline="reduce_saxs",
        run_uid="abc",
        parameters={"roi_x": [0, 1024]},
    )


def test_run_start_ignores_non_matching():
    engine = _FakeEngine()
    submit = MagicMock()
    mgr = TriggerManager(engine=engine, submit_callable=submit)
    mgr.add(RunStartTrigger(
        filter=FilterPredicate(plan_name="count"),
        pipeline="reduce_saxs",
        parameter_overrides={},
    ))

    engine.emit("start", {"uid": "abc", "plan_name": "scan", "tags": []})

    submit.assert_not_called()


def test_run_start_ignores_non_start_docs():
    engine = _FakeEngine()
    submit = MagicMock()
    mgr = TriggerManager(engine=engine, submit_callable=submit)
    mgr.add(RunStartTrigger(
        filter=FilterPredicate(),  # match-all
        pipeline="p",
        parameter_overrides={},
    ))

    engine.emit("stop", {"uid": "abc", "run_start": "xyz"})
    engine.emit("descriptor", {"uid": "abc"})

    submit.assert_not_called()


def test_run_start_detach_unsubscribes():
    engine = _FakeEngine()
    submit = MagicMock()
    mgr = TriggerManager(engine=engine, submit_callable=submit)
    trigger = RunStartTrigger(
        filter=FilterPredicate(),
        pipeline="p",
        parameter_overrides={},
    )
    mgr.add(trigger)
    mgr.remove(trigger)

    engine.emit("start", {"uid": "x", "plan_name": "count"})

    submit.assert_not_called()
```

- [ ] **Step 2: Write failing tests for `RunEndTrigger`**

Create `tests/acquire/triggers/test_run_end.py`:

```python
"""Tests for RunEndTrigger."""
from unittest.mock import MagicMock

import pytest

from lightfall.acquire.triggers.filter import FilterPredicate
from lightfall.acquire.triggers.manager import TriggerManager
from lightfall.acquire.triggers.run_end import RunEndTrigger


class _FakeEngine:
    def __init__(self):
        self._cbs = {}
        self._next = 1

    def subscribe(self, cb):
        tok = self._next
        self._next += 1
        self._cbs[tok] = cb
        return tok

    def unsubscribe(self, tok):
        self._cbs.pop(tok, None)

    def emit(self, name, doc):
        for cb in list(self._cbs.values()):
            cb(name, doc)


def test_run_end_fires_on_stop_when_paired_start_matches():
    engine = _FakeEngine()
    submit = MagicMock()
    mgr = TriggerManager(engine=engine, submit_callable=submit)
    mgr.add(RunEndTrigger(
        filter=FilterPredicate(plan_name="count"),
        pipeline="reduce_saxs",
        parameter_overrides={},
    ))

    # 1) start doc — cached internally by the trigger
    engine.emit("start", {"uid": "abc", "plan_name": "count", "tags": ["saxs"]})
    # 2) stop doc — refers back to abc; the trigger pulls the cached start to filter
    engine.emit("stop", {"uid": "stop1", "run_start": "abc"})

    submit.assert_called_once_with(pipeline="reduce_saxs", run_uid="abc", parameters={})


def test_run_end_ignores_stop_without_matching_start():
    engine = _FakeEngine()
    submit = MagicMock()
    mgr = TriggerManager(engine=engine, submit_callable=submit)
    mgr.add(RunEndTrigger(
        filter=FilterPredicate(plan_name="count"),
        pipeline="p",
        parameter_overrides={},
    ))

    engine.emit("start", {"uid": "abc", "plan_name": "scan", "tags": []})
    engine.emit("stop", {"uid": "s", "run_start": "abc"})

    submit.assert_not_called()


def test_run_end_handles_stop_with_unknown_start():
    """Stop arriving before/without its start doc is silently ignored."""
    engine = _FakeEngine()
    submit = MagicMock()
    mgr = TriggerManager(engine=engine, submit_callable=submit)
    mgr.add(RunEndTrigger(
        filter=FilterPredicate(),
        pipeline="p",
        parameter_overrides={},
    ))

    engine.emit("stop", {"uid": "s", "run_start": "never-seen"})

    submit.assert_not_called()
```

- [ ] **Step 3: Write failing tests for `ManualTrigger`**

Create `tests/acquire/triggers/test_manual.py`:

```python
"""Tests for ManualTrigger."""
from unittest.mock import MagicMock

from lightfall.acquire.triggers.manager import TriggerManager
from lightfall.acquire.triggers.manual import ManualTrigger


def test_manual_trigger_does_not_subscribe_engine():
    engine = MagicMock()
    submit = MagicMock()
    mgr = TriggerManager(engine=engine, submit_callable=submit)
    trigger = ManualTrigger()
    mgr.add(trigger)
    engine.subscribe.assert_not_called()


def test_manual_trigger_invoke_fires_through_manager():
    engine = MagicMock(subscribe=MagicMock(return_value=1))
    submit = MagicMock()
    mgr = TriggerManager(engine=engine, submit_callable=submit)
    trigger = ManualTrigger()
    mgr.add(trigger)

    trigger.invoke(pipeline="reduce_saxs", run_uid="abc", parameters={"k": 1})

    submit.assert_called_once_with(
        pipeline="reduce_saxs", run_uid="abc", parameters={"k": 1}
    )
```

- [ ] **Step 4: Run all three test files; verify failures**

Run: `.venv/Scripts/python -m pytest tests/acquire/triggers/ -v`

Expected: ImportErrors on missing modules.

- [ ] **Step 5: Implement `RunStartTrigger`**

Create `src/lightfall/acquire/triggers/run_start.py`:

```python
"""RunStartTrigger — fires on engine `start` docs matching a filter."""
from __future__ import annotations

from typing import Any, Dict, Optional

from loguru import logger

from lightfall.acquire.triggers.base import Trigger
from lightfall.acquire.triggers.filter import FilterPredicate


class RunStartTrigger(Trigger):
    """Fires `manager.fire()` for each engine 'start' doc matching `filter`."""

    def __init__(
        self,
        *,
        filter: FilterPredicate,
        pipeline: str,
        parameter_overrides: Dict[str, Any],
    ) -> None:
        self._filter = filter
        self._pipeline = pipeline
        self._params = dict(parameter_overrides)
        self._manager = None
        self._token: Optional[int] = None

    def attach(self, manager) -> None:
        self._manager = manager
        self._token = manager.subscribe_engine(self._on_doc)

    def detach(self) -> None:
        if self._manager is not None and self._token is not None:
            self._manager.unsubscribe_engine(self._token)
        self._manager = None
        self._token = None

    def _on_doc(self, name: str, doc: Dict[str, Any]) -> None:
        if name != "start":
            return
        if not self._filter.matches(doc):
            return
        uid = doc.get("uid")
        if not uid:
            logger.warning("RunStartTrigger: matching start doc has no uid; skipping")
            return
        self._manager.fire(pipeline=self._pipeline, run_uid=uid, parameters=dict(self._params))
```

- [ ] **Step 6: Implement `RunEndTrigger`**

Create `src/lightfall/acquire/triggers/run_end.py`:

```python
"""RunEndTrigger — fires on engine `stop` docs whose paired `start` matches a filter."""
from __future__ import annotations

from collections import OrderedDict
from typing import Any, Dict, Optional

from loguru import logger

from lightfall.acquire.triggers.base import Trigger
from lightfall.acquire.triggers.filter import FilterPredicate


class RunEndTrigger(Trigger):
    """Fires on `stop` docs whose `run_start` was a 'start' matching `filter`.

    Maintains a small bounded LRU of recent start docs so a stop doc can be
    matched against its origin without round-tripping to Tiled.
    """

    _START_LRU_SIZE = 512

    def __init__(
        self,
        *,
        filter: FilterPredicate,
        pipeline: str,
        parameter_overrides: Dict[str, Any],
    ) -> None:
        self._filter = filter
        self._pipeline = pipeline
        self._params = dict(parameter_overrides)
        self._manager = None
        self._token: Optional[int] = None
        self._starts: "OrderedDict[str, Dict[str, Any]]" = OrderedDict()

    def attach(self, manager) -> None:
        self._manager = manager
        self._token = manager.subscribe_engine(self._on_doc)

    def detach(self) -> None:
        if self._manager is not None and self._token is not None:
            self._manager.unsubscribe_engine(self._token)
        self._manager = None
        self._token = None
        self._starts.clear()

    def _remember_start(self, uid: str, doc: Dict[str, Any]) -> None:
        self._starts[uid] = doc
        if len(self._starts) > self._START_LRU_SIZE:
            self._starts.popitem(last=False)

    def _on_doc(self, name: str, doc: Dict[str, Any]) -> None:
        if name == "start":
            uid = doc.get("uid")
            if uid:
                self._remember_start(uid, doc)
            return
        if name != "stop":
            return
        start_uid = doc.get("run_start")
        if not start_uid:
            return
        start = self._starts.get(start_uid)
        if start is None:
            logger.debug("RunEndTrigger: stop for unknown start {}; ignoring", start_uid)
            return
        if not self._filter.matches(start):
            return
        self._manager.fire(pipeline=self._pipeline, run_uid=start_uid, parameters=dict(self._params))
```

- [ ] **Step 7: Implement `ManualTrigger`**

Create `src/lightfall/acquire/triggers/manual.py`:

```python
"""ManualTrigger — invoked from the Data Browser, no engine subscription."""
from __future__ import annotations

from typing import Any, Dict

from lightfall.acquire.triggers.base import Trigger


class ManualTrigger(Trigger):
    """A handle for direct, user-initiated pipeline submissions."""

    def __init__(self) -> None:
        self._manager = None

    def attach(self, manager) -> None:
        self._manager = manager

    def detach(self) -> None:
        self._manager = None

    def invoke(self, *, pipeline: str, run_uid: str, parameters: Dict[str, Any]) -> None:
        if self._manager is None:
            raise RuntimeError("ManualTrigger not attached to a TriggerManager")
        self._manager.fire(pipeline=pipeline, run_uid=run_uid, parameters=parameters)
```

- [ ] **Step 8: Re-export from `__init__.py`**

Update `src/lightfall/acquire/triggers/__init__.py`:

```python
"""Trigger framework — engine-agnostic dispatch for pipeline submissions."""
from lightfall.acquire.triggers.base import Trigger
from lightfall.acquire.triggers.filter import FilterPredicate
from lightfall.acquire.triggers.manager import TriggerManager
from lightfall.acquire.triggers.manual import ManualTrigger
from lightfall.acquire.triggers.run_end import RunEndTrigger
from lightfall.acquire.triggers.run_start import RunStartTrigger

__all__ = [
    "Trigger",
    "FilterPredicate",
    "TriggerManager",
    "RunStartTrigger",
    "RunEndTrigger",
    "ManualTrigger",
]
```

- [ ] **Step 9: Run all trigger tests**

Run: `.venv/Scripts/python -m pytest tests/acquire/triggers/ -v`

Expected: all 18+ PASS.

- [ ] **Step 10: Commit**

```bash
git add src/lightfall/acquire/triggers/ tests/acquire/triggers/
git commit -m "feat(acquire/triggers): RunStart, RunEnd, Manual concrete triggers

RunStartTrigger and RunEndTrigger subscribe through TriggerManager to
BaseEngine and fire on filter-matching start/stop docs. RunEndTrigger
maintains a 512-entry LRU of recent start docs to resolve stop->start.
ManualTrigger has no engine hook; .invoke() is called from the Data
Browser context menu."
```

---

## STAGE 2 — `lightfall-pipelines` SDK + executor (new repository)

### Task 4: Initialize the `lightfall-pipelines` repository

**Files:**
- Create directory: `~/PycharmProjects/lightfall-pipelines/`
- Create: `pyproject.toml`, `README.md`, `.gitignore`, `LICENSE`
- Create: `src/lightfall_pipelines/__init__.py`
- Create: `tests/__init__.py` (empty)

This task uses the `python-package-setup` skill where helpful, but the core artifacts are listed concretely below.

- [ ] **Step 1: Create the directory structure**

Run from `~/PycharmProjects/`:

```bash
mkdir -p lightfall-pipelines/{src/lightfall_pipelines/executor,tests/fixtures}
cd lightfall-pipelines
git init
```

- [ ] **Step 2: Write `pyproject.toml`**

Create `pyproject.toml`:

```toml
[build-system]
requires = ["hatchling", "hatch-vcs"]
build-backend = "hatchling.build"

[project]
name = "lightfall-pipelines"
dynamic = ["version"]
description = "SDK + headless executor for Lightfall notebook data-processing pipelines"
readme = "README.md"
license = "BSD-3-Clause"
requires-python = ">=3.11"
authors = [{ name = "ALS Controls Team" }]
dependencies = [
    "tiled[client]>=0.2.3",
    "bluesky>=1.13",
    "scrapbook>=0.5",
    "loguru>=0.7",
    "pydantic>=2.0",
]

[project.optional-dependencies]
executor = [
    "nats-py>=2.6",
    "papermill>=2.5",
    "jupyter_client>=8.0",
]
dev = [
    "pytest>=7",
    "pytest-asyncio",
    "pytest-cov",
    "ruff",
]

[project.scripts]
lightfall-pipelines = "lightfall_pipelines.cli:main"

[tool.hatch.version]
source = "vcs"

[tool.hatch.build.hooks.vcs]
version-file = "src/lightfall_pipelines/_version.py"

[tool.pytest.ini_options]
asyncio_mode = "auto"
testpaths = ["tests"]
```

- [ ] **Step 3: Write `README.md`**

```markdown
# lightfall-pipelines

SDK + headless executor for Lightfall notebook data-processing pipelines.

- **SDK** (default install): `PipelinePlugin` base class, notebook-side
  `TiledWriter` wrapper, provenance/parent-uid auto-stamping.
- **Executor** (`pip install lightfall-pipelines[executor]`): headless service
  that subscribes to NATS, discovers installed pipeline plugins, runs
  notebooks via Papermill, writes derived data to Tiled.

See the design at
`~/PycharmProjects/ncs/ncs/docs/superpowers/specs/2026-05-15-notebook-pipelines-design.md`.
```

- [ ] **Step 4: Write `.gitignore` and `LICENSE`**

`.gitignore`:

```
__pycache__/
*.egg-info/
.venv/
.pytest_cache/
.coverage
dist/
build/
src/lightfall_pipelines/_version.py
```

`LICENSE`: BSD-3-Clause text (copy from `~/PycharmProjects/ncs/ncs/LICENSE` if compatible).

- [ ] **Step 5: Create the package skeleton**

`src/lightfall_pipelines/__init__.py`:

```python
"""Lightfall notebook pipelines — SDK + executor."""
try:
    from lightfall_pipelines._version import version as __version__
except ImportError:
    __version__ = "unknown"

__all__ = ["__version__"]
```

`src/lightfall_pipelines/executor/__init__.py`: empty file.

`tests/__init__.py`: empty file.

- [ ] **Step 6: Bootstrap a dev venv and install**

```bash
cd ~/PycharmProjects/lightfall-pipelines
python -m venv .venv
.venv/Scripts/python -m pip install -e .[dev,executor]
.venv/Scripts/python -m pytest tests/ -v
```

Expected: pytest runs but reports "no tests collected" — that's fine; the next tasks add tests.

- [ ] **Step 7: Initial commit**

```bash
git add .
git commit -m "chore: initialize lightfall-pipelines package

SDK + executor for Lightfall notebook pipelines. Hatch + hatch-vcs;
executor extras gate nats-py/papermill/jupyter_client behind
`pip install lightfall-pipelines[executor]` so notebook-author installs
stay light."
```

---

### Task 5: `PipelinePlugin` base class + entry-point discovery

**Files:**
- Create: `src/lightfall_pipelines/plugin.py`
- Create: `tests/test_plugin.py`
- Create: `tests/fixtures/echo_pipeline/pyproject.toml`
- Create: `tests/fixtures/echo_pipeline/src/echo_pipeline/__init__.py`
- Create: `tests/fixtures/echo_pipeline/src/echo_pipeline/echo.ipynb`

Work in `~/PycharmProjects/lightfall-pipelines/`.

- [ ] **Step 1: Write failing tests**

Create `tests/test_plugin.py`:

```python
"""Tests for PipelinePlugin + entry-point discovery."""
from __future__ import annotations

import pytest

from lightfall_pipelines.plugin import PipelinePlugin, discover


def test_plugin_class_required_fields_enforced():
    """Subclasses missing `name` should error at instantiation."""
    class NoName(PipelinePlugin):
        description = "x"
        parameters_schema: dict = {}
        notebook = None
    with pytest.raises(TypeError):
        NoName()


def test_plugin_concrete_instance_introspects():
    class Echo(PipelinePlugin):
        name = "echo"
        description = "Echo pipeline"
        parameters_schema = {"msg": {"type": "string", "default": "hi"}}
        output_tags = ["echo"]
        notebook = "echo.ipynb"
        package_name = "echo_pipeline"

    p = Echo()
    info = p.get_introspection_data()
    assert info["name"] == "echo"
    assert info["description"] == "Echo pipeline"
    assert info["parameters_schema"]["msg"]["default"] == "hi"


def test_discover_finds_installed_fixture(install_echo_fixture):
    pipelines = discover()
    names = [p.name for p in pipelines]
    assert "echo" in names


def test_discover_returns_empty_when_none_installed(monkeypatch):
    monkeypatch.setattr(
        "lightfall_pipelines.plugin.entry_points",
        lambda group: [],
    )
    assert discover() == []
```

- [ ] **Step 2: Set up the fixture plugin package**

Create `tests/fixtures/echo_pipeline/pyproject.toml`:

```toml
[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[project]
name = "echo-pipeline"
version = "0.0.1"
dependencies = []

[project.entry-points."lightfall_pipelines.pipeline"]
echo = "echo_pipeline:EchoPipeline"

[tool.hatch.build.targets.wheel]
packages = ["src/echo_pipeline"]
```

Create `tests/fixtures/echo_pipeline/src/echo_pipeline/__init__.py`:

```python
from lightfall_pipelines.plugin import PipelinePlugin


class EchoPipeline(PipelinePlugin):
    name = "echo"
    description = "Echo pipeline for testing"
    parameters_schema = {"msg": {"type": "string", "default": "hi"}}
    output_tags: list = []
    notebook = "echo.ipynb"          # resolved via importlib.resources
    package_name = "echo_pipeline"
```

Create `tests/fixtures/echo_pipeline/src/echo_pipeline/echo.ipynb` — a minimal nbformat-v4 JSON file:

```json
{
  "cells": [
    { "cell_type": "code", "metadata": {}, "execution_count": null, "outputs": [], "source": ["print('echo')"] }
  ],
  "metadata": {
    "kernelspec": { "display_name": "Python 3", "language": "python", "name": "python3" },
    "language_info": { "name": "python" }
  },
  "nbformat": 4,
  "nbformat_minor": 5
}
```

- [ ] **Step 3: Add the `install_echo_fixture` fixture**

Create or modify `tests/conftest.py`:

```python
"""Shared test fixtures."""
import subprocess
import sys
from pathlib import Path

import pytest


@pytest.fixture(scope="session")
def install_echo_fixture():
    """Install the echo-pipeline fixture into the active venv (editable)."""
    fixture_dir = Path(__file__).parent / "fixtures" / "echo_pipeline"
    subprocess.check_call([sys.executable, "-m", "pip", "install", "-e", str(fixture_dir)])
    yield
    subprocess.check_call([sys.executable, "-m", "pip", "uninstall", "-y", "echo-pipeline"])
```

- [ ] **Step 4: Run tests; verify failure**

Run: `.venv/Scripts/python -m pytest tests/test_plugin.py -v`

Expected: ImportError on `lightfall_pipelines.plugin`.

- [ ] **Step 5: Implement `PipelinePlugin` and `discover()`**

Create `src/lightfall_pipelines/plugin.py`:

```python
"""PipelinePlugin ABC + entry-point discovery."""
from __future__ import annotations

import abc
from importlib.metadata import entry_points
from importlib.resources import files
from typing import Any, ClassVar, Dict, List, Optional


class PipelinePlugin(abc.ABC):
    """Base class for pipeline plugins. Subclasses set class attributes.

    Required: `name`, `description`, `parameters_schema`, `notebook`,
    `package_name`. Optional: `output_tags`, `inherit_input_access_blob`,
    `store_executed_notebook`, `timeout_seconds`, `python_executable`,
    `expects`.
    """

    name: ClassVar[str]
    description: ClassVar[str] = ""
    display_name: ClassVar[Optional[str]] = None
    parameters_schema: ClassVar[Dict[str, Any]] = {}
    output_tags: ClassVar[List[str]] = []
    notebook: ClassVar[str]                          # importlib.resources path within package_name
    package_name: ClassVar[str]                      # the installed dist's import name
    inherit_input_access_blob: ClassVar[bool] = True
    store_executed_notebook: ClassVar[bool] = True
    timeout_seconds: ClassVar[Optional[int]] = None
    python_executable: ClassVar[Optional[str]] = None
    expects: ClassVar[Dict[str, Any]] = {}

    def __init_subclass__(cls, **kwargs):
        super().__init_subclass__(**kwargs)
        for required in ("name", "notebook", "package_name"):
            if not getattr(cls, required, None):
                raise TypeError(
                    f"PipelinePlugin subclass {cls.__name__} missing required attribute '{required}'"
                )

    def __init__(self) -> None:
        # No abstract methods, but enforce required attributes via __init_subclass__.
        pass

    def notebook_path(self):
        """Resolve the bundled notebook resource."""
        return files(self.package_name) / self.notebook

    def get_introspection_data(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "display_name": self.display_name or self.name.replace("_", " ").title(),
            "parameters_schema": dict(self.parameters_schema),
            "output_tags": list(self.output_tags),
            "inherit_input_access_blob": self.inherit_input_access_blob,
            "store_executed_notebook": self.store_executed_notebook,
            "timeout_seconds": self.timeout_seconds,
            "python_executable": self.python_executable,
            "expects": dict(self.expects),
            "module": self.__class__.__module__,
            "class": self.__class__.__name__,
        }


_ENTRY_POINT_GROUP = "lightfall_pipelines.pipeline"


def discover() -> List[PipelinePlugin]:
    """Return one instantiated PipelinePlugin per installed entry point."""
    plugins: List[PipelinePlugin] = []
    for ep in entry_points(group=_ENTRY_POINT_GROUP):
        cls = ep.load()
        if not issubclass(cls, PipelinePlugin):
            raise TypeError(f"Entry point {ep.name} points at {cls!r}, not a PipelinePlugin")
        plugins.append(cls())
    return plugins
```

- [ ] **Step 6: Run tests; verify pass**

Run: `.venv/Scripts/python -m pytest tests/test_plugin.py -v`

Expected: 4 PASS. (The `install_echo_fixture` test takes a few seconds for the pip install.)

- [ ] **Step 7: Commit**

```bash
git add src/lightfall_pipelines/plugin.py tests/test_plugin.py tests/fixtures/ tests/conftest.py
git commit -m "feat(plugin): PipelinePlugin ABC + entry-point discovery"
```

---

### Task 6: `lightfall_pipelines.notebook.TiledWriter` wrapper

**Files:**
- Create: `src/lightfall_pipelines/notebook.py`
- Create: `tests/test_notebook.py`

- [ ] **Step 1: Inspect bluesky's `TiledWriter` constructor**

Run: `.venv/Scripts/python -c "from bluesky.callbacks.tiled_writer import TiledWriter; import inspect; print(inspect.signature(TiledWriter.__init__))"`

Note the constructor signature — typically `(client, batch_size=10000, backup_directory=None, ...)`. Our wrapper will pass through `client`/`batch_size` and inject metadata.

- [ ] **Step 2: Write failing tests**

Create `tests/test_notebook.py`:

```python
"""Tests for notebook-author helpers."""
from __future__ import annotations

import json
import os
from unittest.mock import MagicMock, patch

import pytest

from lightfall_pipelines.notebook import (
    TiledWriter,
    get_provenance,
    get_input_access_blob,
)


@pytest.fixture
def patch_env(monkeypatch):
    monkeypatch.setenv("TILED_URL", "https://t.test/api/v1")
    monkeypatch.setenv("TILED_API_KEY", "secret")
    monkeypatch.setenv("Lightfall_INPUT_RUN_UID", "raw-uid")
    monkeypatch.setenv("Lightfall_INPUT_ACCESS_BLOB", json.dumps({
        "tags": ["esaf:12345", "beamline:bl1"],
        "owner": "rpandolfi",
    }))
    monkeypatch.setenv("Lightfall_PIPELINE_PROVENANCE", json.dumps({
        "pipeline_name": "reduce_saxs",
        "pipeline_package": "als-saxs-pipelines",
        "pipeline_package_version": "0.4.1",
        "python_executable": "/foo/python",
        "env_hash": "deadbeef",
    }))


def test_get_provenance_reads_env(patch_env):
    p = get_provenance()
    assert p["pipeline_name"] == "reduce_saxs"
    assert p["pipeline_package_version"] == "0.4.1"


def test_get_input_access_blob_reads_env(patch_env):
    blob = get_input_access_blob()
    assert blob["tags"] == ["esaf:12345", "beamline:bl1"]


def test_tiled_writer_stamps_parent_and_provenance(patch_env):
    sub_md = []

    class FakeBlueskyTW:
        def __init__(self, client, batch_size=10000, **kwargs):
            self.client = client
            self.batch_size = batch_size
            self.kwargs = kwargs

        def __call__(self, name, doc):
            if name == "start":
                sub_md.append(doc)

    with patch("lightfall_pipelines.notebook._BlueskyTiledWriter", FakeBlueskyTW):
        client = MagicMock()
        tw = TiledWriter(client)
        tw("start", {"plan_name": "count", "uid": "out-uid"})

    assert len(sub_md) == 1
    doc = sub_md[0]
    assert doc["parent_run_uid"] == "raw-uid"
    assert doc["pipeline_provenance"]["pipeline_name"] == "reduce_saxs"
    # tags merged from input access_blob + output_tags (none here)
    assert "esaf:12345" in doc.get("tiled_access_tags", [])


def test_tiled_writer_merges_output_tags(patch_env):
    sub_md = []

    class FakeBlueskyTW:
        def __init__(self, client, **kwargs):
            pass

        def __call__(self, name, doc):
            if name == "start":
                sub_md.append(doc)

    with patch("lightfall_pipelines.notebook._BlueskyTiledWriter", FakeBlueskyTW):
        client = MagicMock()
        tw = TiledWriter(client, output_tags=["saxs", "reduced"])
        tw("start", {"plan_name": "x", "uid": "u"})

    tags = set(sub_md[0].get("tiled_access_tags", []))
    assert "esaf:12345" in tags
    assert "saxs" in tags
    assert "reduced" in tags
```

- [ ] **Step 3: Run; verify failure**

Run: `.venv/Scripts/python -m pytest tests/test_notebook.py -v`

Expected: ImportError on `lightfall_pipelines.notebook`.

- [ ] **Step 4: Implement `notebook.py`**

Create `src/lightfall_pipelines/notebook.py`:

```python
"""Notebook-author helpers.

`TiledWriter` wraps `bluesky.callbacks.tiled_writer.TiledWriter`, auto-
stamping `parent_run_uid`, `pipeline_provenance`, and merging the input
access_blob tags + plugin output_tags into the start doc's
`tiled_access_tags` field. This is the one-line replacement notebook
authors use:

    from lightfall_pipelines.notebook import TiledWriter
    tw = TiledWriter(client)
    # subscribe to RunEngine, or call tw(name, doc) directly
"""
from __future__ import annotations

import json
import os
from typing import Any, Dict, List, Optional

from bluesky.callbacks.tiled_writer import TiledWriter as _BlueskyTiledWriter
from loguru import logger
from tiled.client import from_uri


def _env(name: str, default: Optional[str] = None) -> Optional[str]:
    v = os.environ.get(name)
    return v if v is not None else default


def get_input_run():
    """Return a tiled client positioned at the input run."""
    url = os.environ["TILED_URL"]
    key = os.environ["TILED_API_KEY"]
    uid = os.environ["Lightfall_INPUT_RUN_UID"]
    client = from_uri(url, api_key=key)
    return client[uid]


def get_input_access_blob() -> Dict[str, Any]:
    raw = _env("Lightfall_INPUT_ACCESS_BLOB")
    return json.loads(raw) if raw else {}


def get_provenance() -> Dict[str, Any]:
    raw = _env("Lightfall_PIPELINE_PROVENANCE")
    return json.loads(raw) if raw else {}


class TiledWriter:
    """Wrap bluesky's TiledWriter, auto-stamping provenance + parent uid + tags.

    Notebook authors construct exactly as they would the bluesky version:

        tw = TiledWriter(client)                   # all auto-stamping enabled
        tw = TiledWriter(client, output_tags=[...]) # extra tags on derived runs

    Pass-through kwargs go to bluesky's underlying TiledWriter.
    """

    def __init__(
        self,
        client: Any,
        *,
        output_tags: Optional[List[str]] = None,
        **kwargs: Any,
    ) -> None:
        self._inner = _BlueskyTiledWriter(client, **kwargs)
        self._output_tags = list(output_tags or [])
        self._parent_uid = _env("Lightfall_INPUT_RUN_UID")
        self._provenance = get_provenance()
        self._input_blob = get_input_access_blob()

    def __call__(self, name: str, doc: Dict[str, Any]) -> Any:
        if name == "start":
            doc = self._stamp_start(dict(doc))
        return self._inner(name, doc)

    def _stamp_start(self, doc: Dict[str, Any]) -> Dict[str, Any]:
        if self._parent_uid and "parent_run_uid" not in doc:
            doc["parent_run_uid"] = self._parent_uid
        if self._provenance and "pipeline_provenance" not in doc:
            doc["pipeline_provenance"] = self._provenance

        # Merge tags: input access_blob tags + plugin output_tags + any
        # explicit tags already on the doc.
        existing = list(doc.get("tiled_access_tags") or [])
        from_input = list(self._input_blob.get("tags") or [])
        merged: List[str] = []
        for t in existing + from_input + self._output_tags:
            if t not in merged:
                merged.append(t)
        if merged:
            doc["tiled_access_tags"] = merged

        logger.debug(
            "TiledWriter: stamped parent={} prov={} tags={}",
            doc.get("parent_run_uid"),
            doc.get("pipeline_provenance", {}).get("pipeline_name"),
            doc.get("tiled_access_tags"),
        )
        return doc
```

- [ ] **Step 5: Run; verify pass**

Run: `.venv/Scripts/python -m pytest tests/test_notebook.py -v`

Expected: 4 PASS.

- [ ] **Step 6: Commit**

```bash
git add src/lightfall_pipelines/notebook.py tests/test_notebook.py
git commit -m "feat(notebook): TiledWriter wrapper auto-stamps provenance + parent_uid"
```

---

### Task 7: `lightfall_pipelines.messages` — JobMessage / ProgressEvent dataclasses + serde

**Files:**
- Create: `src/lightfall_pipelines/messages.py`
- Create: `tests/test_messages.py`

- [ ] **Step 1: Write failing tests**

Create `tests/test_messages.py`:

```python
"""Tests for the NATS wire-format dataclasses."""
from __future__ import annotations

import json

from lightfall_pipelines.messages import JobMessage, ProgressEvent, JobReply


def test_job_message_roundtrip():
    m = JobMessage(
        job_id="00000000-0000-0000-0000-000000000001",
        tiled_url="https://t/api/v1",
        api_key="key",
        api_key_expires_at="2026-05-17T00:00:00Z",
        input_run_uid="abc",
        input_access_blob={"tags": ["x"]},
        pipeline="reduce_saxs",
        parameters={"k": 1},
        user_id="rpandolfi",
        requested_by="lightfall@h",
        submitted_at="2026-05-15T20:00:00Z",
    )
    blob = m.to_json()
    parsed = JobMessage.from_json(blob)
    assert parsed == m


def test_progress_event_roundtrip():
    e = ProgressEvent(
        job_id="j",
        status="running",
        detail="OK",
        input_run_uid="i",
        output_run_uids=["o1", "o2"],
        executed_notebook_path="/d/r/j.ipynb",
        error=None,
        ts="2026-05-15T20:14:42Z",
    )
    assert ProgressEvent.from_json(e.to_json()) == e


def test_job_reply_serializes_minimal_fields():
    r = JobReply(job_id="j", status="queued", position=0)
    obj = json.loads(r.to_json())
    assert obj == {"job_id": "j", "status": "queued", "position": 0}


def test_job_reply_error_form():
    r = JobReply(error="unknown_pipeline", code="unknown_pipeline")
    obj = json.loads(r.to_json())
    assert obj == {"error": "unknown_pipeline", "code": "unknown_pipeline"}
```

- [ ] **Step 2: Run; verify failure**

Expected: ImportError.

- [ ] **Step 3: Implement `messages.py`**

Create `src/lightfall_pipelines/messages.py`:

```python
"""NATS wire-format dataclasses for lightfall-pipelines.

JobMessage, JobReply, ProgressEvent — JSON serialized; pydantic-free
to keep the executor's startup time low.
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class JobMessage:
    job_id: str
    tiled_url: str
    api_key: str
    api_key_expires_at: str
    input_run_uid: str
    input_access_blob: Dict[str, Any]
    pipeline: str
    parameters: Dict[str, Any]
    user_id: str
    requested_by: str
    submitted_at: str

    def to_json(self) -> bytes:
        return json.dumps(asdict(self)).encode("utf-8")

    @classmethod
    def from_json(cls, payload: bytes | str) -> "JobMessage":
        if isinstance(payload, (bytes, bytearray)):
            payload = payload.decode("utf-8")
        return cls(**json.loads(payload))


@dataclass
class JobReply:
    job_id: Optional[str] = None
    status: Optional[str] = None
    position: Optional[int] = None
    current_status: Optional[str] = None
    error: Optional[str] = None
    code: Optional[str] = None

    def to_json(self) -> bytes:
        # Strip Nones to keep the wire compact and tests predictable.
        body = {k: v for k, v in asdict(self).items() if v is not None}
        return json.dumps(body).encode("utf-8")


@dataclass
class ProgressEvent:
    job_id: str
    status: str                                          # queued|env_building|running|completed|failed
    detail: str = ""
    input_run_uid: str = ""
    output_run_uids: List[str] = field(default_factory=list)
    executed_notebook_path: str = ""
    error: Optional[str] = None
    ts: str = ""

    def to_json(self) -> bytes:
        return json.dumps(asdict(self)).encode("utf-8")

    @classmethod
    def from_json(cls, payload: bytes | str) -> "ProgressEvent":
        if isinstance(payload, (bytes, bytearray)):
            payload = payload.decode("utf-8")
        return cls(**json.loads(payload))
```

- [ ] **Step 4: Run; verify pass**

Run: `.venv/Scripts/python -m pytest tests/test_messages.py -v`

Expected: 4 PASS.

- [ ] **Step 5: Commit**

```bash
git add src/lightfall_pipelines/messages.py tests/test_messages.py
git commit -m "feat(messages): JobMessage/JobReply/ProgressEvent wire-format dataclasses"
```

---

### Task 8: `EnvCache` — per-package venv builder

**Files:**
- Create: `src/lightfall_pipelines/executor/env_cache.py`
- Create: `tests/test_env_cache.py`

- [ ] **Step 1: Write failing tests**

Create `tests/test_env_cache.py`:

```python
"""Tests for the per-package venv cache."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from lightfall_pipelines.executor.env_cache import EnvCache, EnvSpec


@pytest.fixture
def cache_dir(tmp_path):
    return tmp_path / "envs"


def test_envspec_path(cache_dir):
    spec = EnvSpec(package_name="als-saxs-pipelines", version="0.4.1")
    cache = EnvCache(cache_dir)
    assert cache.path_for(spec) == cache_dir / "als-saxs-pipelines@0.4.1"


def test_has_env_returns_false_when_missing(cache_dir):
    cache = EnvCache(cache_dir)
    assert not cache.has_env(EnvSpec("pkg", "1.0"))


def test_build_env_invokes_venv_and_pip(cache_dir):
    spec = EnvSpec("pkg", "1.0")
    cache = EnvCache(cache_dir)

    with patch("lightfall_pipelines.executor.env_cache.subprocess.check_call") as run, \
         patch("lightfall_pipelines.executor.env_cache.shutil.which", return_value=None):
        cache.build(spec)

    calls = [c.args[0] for c in run.call_args_list]
    # First a venv creation, then a pip install
    assert any("venv" in " ".join(c) for c in calls)
    assert any("pip" in " ".join(c) and "install" in " ".join(c) for c in calls)


def test_build_prefers_uv_when_available(cache_dir):
    spec = EnvSpec("pkg", "1.0")
    cache = EnvCache(cache_dir)
    with patch("lightfall_pipelines.executor.env_cache.subprocess.check_call") as run, \
         patch("lightfall_pipelines.executor.env_cache.shutil.which", return_value="/usr/bin/uv"):
        cache.build(spec)
    calls = [" ".join(c.args[0]) for c in run.call_args_list]
    assert any("uv venv" in c for c in calls)
    assert any("uv pip install" in c for c in calls)


def test_python_executable_returns_venv_python(cache_dir):
    spec = EnvSpec("pkg", "1.0")
    cache = EnvCache(cache_dir)
    env_path = cache.path_for(spec)
    env_path.mkdir(parents=True)
    (env_path / "bin").mkdir()
    (env_path / "bin" / "python").touch()
    assert cache.python_executable(spec) == env_path / "bin" / "python"
```

- [ ] **Step 2: Run; verify failure**

Expected: ImportError.

- [ ] **Step 3: Implement `env_cache.py`**

Create `src/lightfall_pipelines/executor/env_cache.py`:

```python
"""Per-package venv cache for the executor.

One venv per (package_name, version). Built lazily on first use; persistent
across executor restarts. Uses stdlib `venv` + `pip` by default; if `uv` is
available on PATH, prefers it for ~10x faster builds. Functional behavior
is identical either way.
"""
from __future__ import annotations

import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from loguru import logger


@dataclass(frozen=True)
class EnvSpec:
    package_name: str
    version: str


class EnvCache:
    def __init__(self, root: Path) -> None:
        self._root = Path(root)

    def path_for(self, spec: EnvSpec) -> Path:
        return self._root / f"{spec.package_name}@{spec.version}"

    def has_env(self, spec: EnvSpec) -> bool:
        return self.path_for(spec).exists() and (self.path_for(spec) / "bin").exists()

    def python_executable(self, spec: EnvSpec) -> Path:
        # Linux/macOS: bin/python; Windows: Scripts/python.exe
        env = self.path_for(spec)
        unix = env / "bin" / "python"
        win = env / "Scripts" / "python.exe"
        return unix if unix.exists() else win

    def build(self, spec: EnvSpec, *, log_handler=None) -> Path:
        """Build the venv for `spec`. Returns the env path. Idempotent."""
        env_path = self.path_for(spec)
        env_path.parent.mkdir(parents=True, exist_ok=True)

        uv = shutil.which("uv")
        if uv:
            logger.info("EnvCache: building {} via uv", env_path)
            subprocess.check_call(["uv", "venv", str(env_path)])
            subprocess.check_call([
                "uv", "pip", "install",
                "--python", str(self.python_executable(spec)),
                f"{spec.package_name}=={spec.version}",
            ])
        else:
            logger.info("EnvCache: building {} via stdlib venv + pip", env_path)
            subprocess.check_call([sys.executable, "-m", "venv", str(env_path)])
            subprocess.check_call([
                str(self.python_executable(spec)), "-m", "pip", "install",
                f"{spec.package_name}=={spec.version}",
            ])
        return env_path
```

- [ ] **Step 4: Run; verify pass**

Run: `.venv/Scripts/python -m pytest tests/test_env_cache.py -v`

Expected: 5 PASS.

- [ ] **Step 5: Commit**

```bash
git add src/lightfall_pipelines/executor/env_cache.py tests/test_env_cache.py
git commit -m "feat(executor): EnvCache — per-package venv with uv-accelerated builds"
```

---

### Task 9: `PapermillRunner` — in-memory notebook execution + scrapbook harvest

**Files:**
- Create: `src/lightfall_pipelines/executor/runner.py`
- Create: `tests/test_runner.py`

- [ ] **Step 1: Write failing tests**

Create `tests/test_runner.py`:

```python
"""Tests for the Papermill-driven notebook runner."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from lightfall_pipelines.executor.runner import PapermillRunner, RunResult


@pytest.fixture
def fake_notebook(tmp_path):
    nb = tmp_path / "echo.ipynb"
    nb.write_text("""{"cells":[],"metadata":{"kernelspec":{"name":"python3","display_name":"Python 3","language":"python"}},"nbformat":4,"nbformat_minor":5}""")
    return nb


def test_runner_invokes_papermill_with_parameters(fake_notebook, tmp_path):
    with patch("lightfall_pipelines.executor.runner.papermill") as pm:
        pm.execute_notebook.return_value = None
        runner = PapermillRunner()
        runner.run(
            notebook_path=fake_notebook,
            kernel_name="python3",
            parameters={"msg": "hi"},
            env={"FOO": "BAR"},
            output_path=tmp_path / "out.ipynb",
        )
    assert pm.execute_notebook.called
    args, kwargs = pm.execute_notebook.call_args
    assert kwargs["parameters"] == {"msg": "hi"}
    assert kwargs["kernel_name"] == "python3"


def test_runner_harvests_scrapbook_output_uids(fake_notebook, tmp_path):
    fake_scraps = {"output_run_uids": MagicMock(data=["u1", "u2"])}
    fake_nb = MagicMock(scraps=fake_scraps)
    with patch("lightfall_pipelines.executor.runner.papermill") as pm, \
         patch("lightfall_pipelines.executor.runner.sb") as sb:
        pm.execute_notebook.return_value = None
        sb.read_notebook.return_value = fake_nb
        runner = PapermillRunner()
        result = runner.run(
            notebook_path=fake_notebook,
            kernel_name="python3",
            parameters={},
            env={},
            output_path=tmp_path / "out.ipynb",
        )
    assert isinstance(result, RunResult)
    assert result.output_run_uids == ["u1", "u2"]


def test_runner_returns_failed_on_exception(fake_notebook, tmp_path):
    with patch("lightfall_pipelines.executor.runner.papermill") as pm:
        pm.execute_notebook.side_effect = RuntimeError("boom")
        runner = PapermillRunner()
        result = runner.run(
            notebook_path=fake_notebook,
            kernel_name="python3",
            parameters={},
            env={},
            output_path=tmp_path / "out.ipynb",
        )
    assert result.status == "failed"
    assert "boom" in result.error
```

- [ ] **Step 2: Run; verify failure**

Expected: ImportError.

- [ ] **Step 3: Implement `runner.py`**

Create `src/lightfall_pipelines/executor/runner.py`:

```python
"""PapermillRunner — executes a single notebook job."""
from __future__ import annotations

import os
import traceback
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

import papermill
import scrapbook as sb
from loguru import logger


@dataclass
class RunResult:
    status: str                                          # "completed" | "failed"
    output_run_uids: List[str] = field(default_factory=list)
    output_files: List[str] = field(default_factory=list)
    metrics: Dict[str, Any] = field(default_factory=dict)
    error: Optional[str] = None
    output_path: Optional[Path] = None


class PapermillRunner:
    def run(
        self,
        *,
        notebook_path: Path,
        kernel_name: str,
        parameters: Dict[str, Any],
        env: Dict[str, str],
        output_path: Path,
        timeout_seconds: Optional[int] = None,
    ) -> RunResult:
        """Execute `notebook_path` via Papermill; return RunResult with scraps."""
        logger.info("Runner: executing {} kernel={}", notebook_path, kernel_name)
        # Papermill inherits the current-process env; we layer on extras here.
        orig_env = {}
        for k, v in env.items():
            orig_env[k] = os.environ.get(k)
            os.environ[k] = v
        try:
            papermill.execute_notebook(
                input_path=str(notebook_path),
                output_path=str(output_path),
                parameters=parameters,
                kernel_name=kernel_name,
                progress_bar=False,
                stdout_file=None,
                stderr_file=None,
                request_save_on_cell_execute=False,
                execution_timeout=timeout_seconds,
            )
        except Exception as e:
            tb = traceback.format_exc()
            logger.error("Runner: papermill failed: {}", e)
            return RunResult(status="failed", error=tb[-2000:], output_path=output_path)
        finally:
            for k, v in orig_env.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v

        scraps = sb.read_notebook(str(output_path)).scraps
        uids = list(scraps["output_run_uids"].data) if "output_run_uids" in scraps else []
        files = list(scraps["output_files"].data) if "output_files" in scraps else []
        metrics = dict(scraps["metrics"].data) if "metrics" in scraps else {}
        return RunResult(
            status="completed",
            output_run_uids=uids,
            output_files=files,
            metrics=metrics,
            output_path=output_path,
        )
```

- [ ] **Step 4: Run; verify pass**

Run: `.venv/Scripts/python -m pytest tests/test_runner.py -v`

Expected: 3 PASS.

- [ ] **Step 5: Commit**

```bash
git add src/lightfall_pipelines/executor/runner.py tests/test_runner.py
git commit -m "feat(executor): PapermillRunner — execute + scrapbook harvest"
```

---

### Task 10: `NotebookStore` — write executed notebook + register Tiled pointer

**Files:**
- Create: `src/lightfall_pipelines/executor/notebook_store.py`
- Create: `tests/test_notebook_store.py`

- [ ] **Step 1: Write failing tests**

Create `tests/test_notebook_store.py`:

```python
"""Tests for executed-notebook persistence + Tiled-pointer registration."""
from __future__ import annotations

import hashlib
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from lightfall_pipelines.executor.notebook_store import NotebookStore


def test_path_for_namespaces_by_input_uid(tmp_path):
    store = NotebookStore(root=tmp_path)
    p = store.path_for(input_run_uid="raw-abc", job_id="job-1")
    assert p == tmp_path / "raw-abc" / "job-1.ipynb"


def test_write_and_metadata(tmp_path):
    store = NotebookStore(root=tmp_path)
    src = tmp_path / "executed.ipynb"
    src.write_bytes(b"hello-notebook")
    meta = store.write(
        executed_path=src,
        input_run_uid="raw-abc",
        job_id="job-1",
    )
    dest = tmp_path / "raw-abc" / "job-1.ipynb"
    assert dest.exists()
    assert meta["path"] == str(dest)
    assert meta["size_bytes"] == len(b"hello-notebook")
    assert meta["sha256"] == hashlib.sha256(b"hello-notebook").hexdigest()


def test_register_pointer_calls_tiled_client(tmp_path):
    store = NotebookStore(root=tmp_path)
    tiled = MagicMock()
    src = tmp_path / "executed.ipynb"
    src.write_bytes(b"x")
    meta = store.write(executed_path=src, input_run_uid="r", job_id="j")
    store.register_pointer(tiled_run=tiled, meta=meta)
    tiled.metadata.update.assert_called_once()
```

- [ ] **Step 2: Run; verify failure**

Expected: ImportError.

- [ ] **Step 3: Implement `notebook_store.py`**

Create `src/lightfall_pipelines/executor/notebook_store.py`:

```python
"""Filesystem-backed storage for executed notebooks.

Follows the AreaDetector pattern: file lives on disk; Tiled holds a
metadata pointer to its path. Cheap, predictable, easy to back up.
"""
from __future__ import annotations

import hashlib
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict

from loguru import logger


@dataclass
class NotebookStore:
    root: Path

    def path_for(self, *, input_run_uid: str, job_id: str) -> Path:
        return Path(self.root) / input_run_uid / f"{job_id}.ipynb"

    def write(
        self,
        *,
        executed_path: Path,
        input_run_uid: str,
        job_id: str,
    ) -> Dict[str, Any]:
        dest = self.path_for(input_run_uid=input_run_uid, job_id=job_id)
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(executed_path, dest)

        data = dest.read_bytes()
        meta = {
            "path": str(dest),
            "size_bytes": len(data),
            "sha256": hashlib.sha256(data).hexdigest(),
        }
        logger.info("NotebookStore: stored {} ({} bytes)", dest, meta["size_bytes"])
        return meta

    def register_pointer(self, *, tiled_run: Any, meta: Dict[str, Any]) -> None:
        """Update the output run's metadata with the executed_notebook pointer.

        `tiled_run` is the tiled client for the output run (e.g. obtained via
        `tiled_client[output_uid]`). The exact metadata-update API depends on
        the tiled version; we use the standard `node.metadata.update({...})`.
        """
        tiled_run.metadata.update({
            "executed_notebook": meta,
        })
```

- [ ] **Step 4: Run; verify pass**

Run: `.venv/Scripts/python -m pytest tests/test_notebook_store.py -v`

Expected: 3 PASS.

- [ ] **Step 5: Commit**

```bash
git add src/lightfall_pipelines/executor/notebook_store.py tests/test_notebook_store.py
git commit -m "feat(executor): NotebookStore — disk write + Tiled pointer registration"
```

---

### Task 11: `PipelineService` — NATS subscribe + queue + dispatch + idempotency

**Files:**
- Create: `src/lightfall_pipelines/executor/service.py`
- Create: `tests/test_service.py`

This is the biggest single module. ~250 lines.

- [ ] **Step 1: Write failing tests**

Create `tests/test_service.py`:

```python
"""Tests for the headless PipelineService — NATS subscription, queue, idempotency."""
from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from lightfall_pipelines.executor.service import PipelineService
from lightfall_pipelines.messages import JobMessage


def _job(job_id="j1", pipeline="echo") -> dict:
    return {
        "job_id": job_id,
        "tiled_url": "https://t/api/v1",
        "api_key": "k",
        "api_key_expires_at": "2026-05-17T00:00:00Z",
        "input_run_uid": "raw",
        "input_access_blob": {},
        "pipeline": pipeline,
        "parameters": {},
        "user_id": "u",
        "requested_by": "lightfall@h",
        "submitted_at": "2026-05-15T20:00:00Z",
    }


@pytest.mark.asyncio
async def test_service_replies_queued_for_valid_job():
    svc = PipelineService(
        nats_url="nats://x",
        hostname="testhost",
        notebook_store_root="/tmp/ns",
        env_cache_root="/tmp/envs",
    )
    svc._discover = MagicMock(return_value={"echo": MagicMock(name="EchoPlugin")})
    msg = MagicMock(reply="reply-subject", data=json.dumps(_job()).encode())
    svc._nc = AsyncMock()
    await svc._handle_job_request(msg)
    svc._nc.publish.assert_awaited_once()
    args, kwargs = svc._nc.publish.call_args
    assert args[0] == "reply-subject"
    body = json.loads(args[1])
    assert body["status"] == "queued"
    assert body["job_id"] == "j1"


@pytest.mark.asyncio
async def test_service_replies_unknown_pipeline_for_missing_plugin():
    svc = PipelineService(
        nats_url="nats://x",
        hostname="h",
        notebook_store_root="/tmp/ns",
        env_cache_root="/tmp/envs",
    )
    svc._discover = MagicMock(return_value={})
    msg = MagicMock(reply="r", data=json.dumps(_job(pipeline="missing")).encode())
    svc._nc = AsyncMock()
    await svc._handle_job_request(msg)
    body = json.loads(svc._nc.publish.call_args.args[1])
    assert body.get("code") == "unknown_pipeline"


@pytest.mark.asyncio
async def test_service_idempotent_on_duplicate_job_id():
    svc = PipelineService(
        nats_url="nats://x",
        hostname="h",
        notebook_store_root="/tmp/ns",
        env_cache_root="/tmp/envs",
    )
    svc._discover = MagicMock(return_value={"echo": MagicMock()})
    svc._nc = AsyncMock()

    first_msg = MagicMock(reply="r1", data=json.dumps(_job(job_id="J")).encode())
    second_msg = MagicMock(reply="r2", data=json.dumps(_job(job_id="J")).encode())
    await svc._handle_job_request(first_msg)
    await svc._handle_job_request(second_msg)

    first_reply = json.loads(svc._nc.publish.call_args_list[0].args[1])
    second_reply = json.loads(svc._nc.publish.call_args_list[1].args[1])
    assert first_reply["status"] == "queued"
    assert second_reply["status"] == "already_processed"


@pytest.mark.asyncio
async def test_list_endpoint_returns_discovered_plugins():
    plugin = MagicMock()
    plugin.get_introspection_data.return_value = {"name": "echo", "description": "x"}
    svc = PipelineService(
        nats_url="nats://x", hostname="h",
        notebook_store_root="/tmp/ns", env_cache_root="/tmp/envs",
    )
    svc._discover = MagicMock(return_value={"echo": plugin})
    svc._nc = AsyncMock()
    msg = MagicMock(reply="r", data=b"{}")
    await svc._handle_list(msg)
    body = json.loads(svc._nc.publish.call_args.args[1])
    assert body["pipelines"][0]["name"] == "echo"
```

- [ ] **Step 2: Run; verify failure**

Expected: ImportError.

- [ ] **Step 3: Implement `service.py`**

Create `src/lightfall_pipelines/executor/service.py`:

```python
"""PipelineService — headless NATS-subscribing job dispatcher.

Mirrors `lightfall.exporter.service.ExporterService` (`~/PycharmProjects/ncs/ncs/
src/lightfall/exporter/service.py`). One service per host. Subscribes to:

  lightfall.pipeline.<host>           request/reply, job submission
  lightfall.pipeline.<host>.ping      request/reply, health
  lightfall.pipeline.<host>.list      request/reply, discovered pipelines + schemas
  lightfall.pipeline.<host>.refresh   request/reply, re-scan entry points

Publishes to:

  lightfall.pipeline.<host>.progress  pub/sub, ProgressEvent per state change
"""
from __future__ import annotations

import asyncio
import json
import os
import time
from collections import OrderedDict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

import nats
from loguru import logger

from lightfall_pipelines.executor.env_cache import EnvCache, EnvSpec
from lightfall_pipelines.executor.notebook_store import NotebookStore
from lightfall_pipelines.executor.runner import PapermillRunner
from lightfall_pipelines.messages import JobMessage, JobReply, ProgressEvent
from lightfall_pipelines.plugin import PipelinePlugin, discover


_IDEMPOTENCY_LRU_SIZE = 1024


class PipelineService:
    def __init__(
        self,
        *,
        nats_url: str,
        hostname: str,
        notebook_store_root: str,
        env_cache_root: str,
    ) -> None:
        self._nats_url = nats_url
        self._hostname = hostname
        self._notebook_store = NotebookStore(root=Path(notebook_store_root))
        self._env_cache = EnvCache(root=Path(env_cache_root))
        self._runner = PapermillRunner()

        self._nc: Optional[nats.NATS] = None
        self._queue: asyncio.Queue[JobMessage] = asyncio.Queue()
        self._running = False
        self._discover = discover         # patched in tests
        self._plugin_cache: Optional[Dict[str, PipelinePlugin]] = None
        self._recent_jobs: "OrderedDict[str, str]" = OrderedDict()    # job_id -> current_status

    # -- topics ------------------------------------------------------------

    @property
    def job_subject(self) -> str:
        return f"lightfall.pipeline.{self._hostname}"

    @property
    def ping_subject(self) -> str:
        return f"lightfall.pipeline.{self._hostname}.ping"

    @property
    def list_subject(self) -> str:
        return f"lightfall.pipeline.{self._hostname}.list"

    @property
    def refresh_subject(self) -> str:
        return f"lightfall.pipeline.{self._hostname}.refresh"

    @property
    def progress_subject(self) -> str:
        return f"lightfall.pipeline.{self._hostname}.progress"

    # -- plugins -----------------------------------------------------------

    def _plugins(self, *, refresh: bool = False) -> Dict[str, PipelinePlugin]:
        if refresh or self._plugin_cache is None:
            self._plugin_cache = {p.name: p for p in self._discover()}
        return self._plugin_cache

    # -- idempotency -------------------------------------------------------

    def _remember(self, job_id: str, status: str) -> None:
        self._recent_jobs[job_id] = status
        if len(self._recent_jobs) > _IDEMPOTENCY_LRU_SIZE:
            self._recent_jobs.popitem(last=False)

    def _seen(self, job_id: str) -> Optional[str]:
        return self._recent_jobs.get(job_id)

    # -- handlers ----------------------------------------------------------

    async def _handle_job_request(self, msg: Any) -> None:
        try:
            job = JobMessage.from_json(msg.data)
        except Exception as e:
            await self._reply(msg, JobReply(error=str(e), code="bad_message"))
            return

        prior = self._seen(job.job_id)
        if prior is not None:
            await self._reply(msg, JobReply(
                job_id=job.job_id,
                status="already_processed",
                current_status=prior,
            ))
            return

        plugins = self._plugins()
        if job.pipeline not in plugins:
            await self._reply(msg, JobReply(
                error=f"unknown pipeline {job.pipeline!r}",
                code="unknown_pipeline",
            ))
            return

        await self._queue.put(job)
        self._remember(job.job_id, "queued")
        await self._reply(msg, JobReply(job_id=job.job_id, status="queued", position=self._queue.qsize() - 1))

    async def _handle_ping(self, msg: Any) -> None:
        await self._reply_raw(msg, {
            "hostname": self._hostname,
            "status": "ready",
            "queue_depth": self._queue.qsize(),
        })

    async def _handle_list(self, msg: Any) -> None:
        plugins = list(self._plugins().values())
        await self._reply_raw(msg, {
            "pipelines": [p.get_introspection_data() for p in plugins],
        })

    async def _handle_refresh(self, msg: Any) -> None:
        self._plugins(refresh=True)
        await self._handle_list(msg)

    async def _reply(self, msg: Any, reply: JobReply) -> None:
        if not msg.reply:
            return
        await self._nc.publish(msg.reply, reply.to_json())

    async def _reply_raw(self, msg: Any, payload: Dict[str, Any]) -> None:
        if not msg.reply:
            return
        await self._nc.publish(msg.reply, json.dumps(payload).encode("utf-8"))

    async def _publish_progress(self, ev: ProgressEvent) -> None:
        if self._nc is None:
            return
        await self._nc.publish(self.progress_subject, ev.to_json())

    # -- worker loop -------------------------------------------------------

    async def _process_jobs(self) -> None:
        while self._running:
            try:
                job = await asyncio.wait_for(self._queue.get(), timeout=1.0)
            except asyncio.TimeoutError:
                continue
            await self._run_job(job)

    async def _run_job(self, job: JobMessage) -> None:
        plugin = self._plugins()[job.pipeline]
        spec = EnvSpec(plugin.package_name, _get_installed_version(plugin.package_name))

        await self._publish_progress(ProgressEvent(
            job_id=job.job_id, status="env_building" if not self._env_cache.has_env(spec) else "running",
            detail=f"env={spec.package_name}@{spec.version}",
            input_run_uid=job.input_run_uid, ts=_now_iso(),
        ))
        self._remember(job.job_id, "running")

        if not self._env_cache.has_env(spec):
            await asyncio.to_thread(self._env_cache.build, spec)

        kernel_name = f"lightfall-pipelines:{spec.package_name}@{spec.version}"
        env = _build_kernel_env(job, plugin)
        notebook_src = plugin.notebook_path()
        out_tmp = Path(f"/tmp/lightfall-pipelines-{job.job_id}.ipynb")

        result = await asyncio.to_thread(
            self._runner.run,
            notebook_path=notebook_src,
            kernel_name=kernel_name,
            parameters=job.parameters,
            env=env,
            output_path=out_tmp,
            timeout_seconds=plugin.timeout_seconds,
        )

        if result.status == "completed":
            if plugin.store_executed_notebook:
                meta = self._notebook_store.write(
                    executed_path=result.output_path,
                    input_run_uid=job.input_run_uid,
                    job_id=job.job_id,
                )
                executed_path = meta["path"]
            else:
                executed_path = ""
            await self._publish_progress(ProgressEvent(
                job_id=job.job_id, status="completed",
                detail=f"{len(result.output_run_uids)} output runs",
                input_run_uid=job.input_run_uid,
                output_run_uids=result.output_run_uids,
                executed_notebook_path=executed_path,
                ts=_now_iso(),
            ))
            self._remember(job.job_id, "completed")
        else:
            await self._publish_progress(ProgressEvent(
                job_id=job.job_id, status="failed",
                detail="see error",
                input_run_uid=job.input_run_uid,
                error=result.error,
                ts=_now_iso(),
            ))
            self._remember(job.job_id, "failed")

    # -- lifecycle ---------------------------------------------------------

    async def run(self) -> None:
        self._nc = await nats.connect(self._nats_url)
        logger.info("PipelineService: connected to NATS at {}", self._nats_url)
        await self._nc.subscribe(self.job_subject, cb=self._handle_job_request)
        await self._nc.subscribe(self.ping_subject, cb=self._handle_ping)
        await self._nc.subscribe(self.list_subject, cb=self._handle_list)
        await self._nc.subscribe(self.refresh_subject, cb=self._handle_refresh)
        self._running = True
        await self._process_jobs()

    async def stop(self) -> None:
        self._running = False
        if self._nc:
            await self._nc.drain()


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _get_installed_version(package_name: str) -> str:
    """Look up the installed dist version. Returns 'unknown' on failure."""
    try:
        from importlib.metadata import version as _v
        return _v(package_name)
    except Exception:
        return "unknown"


def _build_kernel_env(job: JobMessage, plugin: PipelinePlugin) -> Dict[str, str]:
    return {
        "TILED_URL": job.tiled_url,
        "TILED_API_KEY": job.api_key,
        "Lightfall_INPUT_RUN_UID": job.input_run_uid,
        "Lightfall_INPUT_ACCESS_BLOB": json.dumps(job.input_access_blob),
        "Lightfall_PIPELINE_PROVENANCE": json.dumps({
            "pipeline_name": plugin.name,
            "pipeline_package": plugin.package_name,
            "pipeline_package_version": _get_installed_version(plugin.package_name),
            "python_executable": plugin.python_executable or "",
        }),
        "Lightfall_OUTPUT_TAGS": json.dumps(plugin.output_tags),
    }
```

- [ ] **Step 4: Run; verify pass**

Run: `.venv/Scripts/python -m pytest tests/test_service.py -v`

Expected: 4 PASS.

- [ ] **Step 5: Commit**

```bash
git add src/lightfall_pipelines/executor/service.py tests/test_service.py
git commit -m "feat(executor): PipelineService — NATS dispatch with idempotency LRU"
```

---

### Task 12: `lightfall-pipelines` CLI + console-script entrypoint

**Files:**
- Create: `src/lightfall_pipelines/cli.py`
- Create: `tests/test_cli.py`

- [ ] **Step 1: Write failing test**

Create `tests/test_cli.py`:

```python
"""Tests for the lightfall-pipelines CLI."""
from unittest.mock import patch, AsyncMock

from lightfall_pipelines.cli import build_parser, main


def test_parser_accepts_required_args():
    p = build_parser()
    args = p.parse_args([
        "--nats", "nats://localhost:4222",
        "--hostname", "h",
        "--notebook-store", "/data/runs",
        "--env-cache", "/var/cache/envs",
    ])
    assert args.nats == "nats://localhost:4222"
    assert args.hostname == "h"


def test_main_constructs_and_runs_service():
    with patch("lightfall_pipelines.cli.PipelineService") as Svc, \
         patch("lightfall_pipelines.cli.asyncio.run") as run:
        svc = Svc.return_value
        svc.run = AsyncMock()
        main([
            "--nats", "nats://x",
            "--hostname", "h",
            "--notebook-store", "/d",
            "--env-cache", "/e",
        ])
    Svc.assert_called_once()
    run.assert_called_once()
```

- [ ] **Step 2: Run; verify failure**

Expected: ImportError.

- [ ] **Step 3: Implement `cli.py`**

Create `src/lightfall_pipelines/cli.py`:

```python
"""`lightfall-pipelines` console-script entrypoint."""
from __future__ import annotations

import argparse
import asyncio
import logging
import platform
import sys
from typing import List, Optional

from lightfall_pipelines.executor.service import PipelineService


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="lightfall-pipelines",
                                description="Headless notebook-pipeline executor for Lightfall")
    p.add_argument("--nats", required=True, help="NATS URL (nats://host:port)")
    p.add_argument("--hostname", default=platform.node(),
                   help="hostname used to build NATS topic prefix")
    p.add_argument("--notebook-store", required=True,
                   help="directory for executed-notebook .ipynb artifacts")
    p.add_argument("--env-cache", required=True,
                   help="directory for per-package venv cache")
    p.add_argument("--log-level", default="INFO")
    return p


def main(argv: Optional[List[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(level=getattr(logging, args.log_level.upper()))

    svc = PipelineService(
        nats_url=args.nats,
        hostname=args.hostname,
        notebook_store_root=args.notebook_store,
        env_cache_root=args.env_cache,
    )
    asyncio.run(svc.run())
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: Run; verify pass**

Run: `.venv/Scripts/python -m pytest tests/test_cli.py -v`

Expected: 2 PASS.

- [ ] **Step 5: Verify console-script is wired up**

Run: `.venv/Scripts/lightfall-pipelines --help`

Expected: prints the argparse help — confirms the `[project.scripts]` entry point in `pyproject.toml` resolves to `cli.main`.

- [ ] **Step 6: Commit**

```bash
git add src/lightfall_pipelines/cli.py tests/test_cli.py
git commit -m "feat(cli): lightfall-pipelines console-script entrypoint"
```

---

### Task 13: Publish `lightfall-pipelines` 0.1.0 + tag

**Files:**
- None (release task)

- [ ] **Step 1: Run the full test suite**

Run: `.venv/Scripts/python -m pytest -v`

Expected: all tests PASS.

- [ ] **Step 2: Tag the release**

```bash
cd ~/PycharmProjects/lightfall-pipelines
git tag -a v0.1.0 -m "Initial release: SDK + executor"
```

- [ ] **Step 3: Build the wheel**

```bash
.venv/Scripts/python -m pip install build
.venv/Scripts/python -m build
ls dist/
```

Expected: `dist/lightfall_pipelines-0.1.0-py3-none-any.whl` and source dist.

- [ ] **Step 4: Publish to the deployment-targeted package index**

The exact index depends on deployment policy. If using GitLab Package Registry:

```bash
.venv/Scripts/python -m pip install twine
TWINE_USERNAME=__token__ TWINE_PASSWORD=$GITLAB_DEPLOY_TOKEN \
  .venv/Scripts/python -m twine upload --repository-url https://git.als.lbl.gov/api/v4/projects/<id>/packages/pypi dist/*
```

For local-only development testing, skip publishing — use `pip install -e .` paths in Plan B's downstream tasks.

---

## STAGE 3 — Lightfall-side integration (in `ncs/ncs`)

For these tasks, **switch back to the Lightfall worktree** at `~/PycharmProjects/ncs/ncs/`, branch `feature/notebook-pipelines-spec` (or create `feature/notebook-pipelines-impl` if you'd rather keep spec and impl separate).

```bash
cd ~/PycharmProjects/ncs/ncs
git checkout -b feature/notebook-pipelines-impl feature/notebook-pipelines-spec
```

Add `lightfall-pipelines` as a dev dep so the SDK base classes import:

```bash
.venv/Scripts/python -m pip install -e ~/PycharmProjects/lightfall-pipelines
```

### Task 14: `lightfall.pipelines.PipelineClient`

**Files:**
- Create: `src/lightfall/pipelines/__init__.py`
- Create: `src/lightfall/pipelines/client.py`
- Create: `tests/pipelines/test_client.py`

- [ ] **Step 1: Inspect `lightfall.ipc.service.IPCService`**

Run: `grep -n "def request\|def publish\|def subscribe" src/lightfall/ipc/service.py | head -15`

Confirm the request/reply API; the client uses it to send job messages.

- [ ] **Step 2: Write failing tests**

Create `tests/pipelines/test_client.py`:

```python
"""Tests for the Lightfall-side PipelineClient."""
from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest
from PySide6.QtCore import QObject

from lightfall.pipelines.client import PipelineClient


@pytest.fixture
def mock_ipc():
    ipc = MagicMock()
    ipc.topic = lambda suffix: f"lightfall.pipeline.testhost.{suffix}" if suffix else "lightfall.pipeline.testhost"
    return ipc


def test_client_mints_key_and_submits(mock_ipc, qtbot):
    with patch("lightfall.pipelines.client.mint_job_key") as mint:
        mint.return_value = MagicMock(
            secret="hex"*16,
            first_eight="hexhexhe",
            expires_at="2026-05-17T00:00:00Z",
            scopes=["read:metadata"],
        )
        client = PipelineClient(
            ipc=mock_ipc,
            tiled_url="https://t/api/v1",
            bearer_provider=lambda: "fake-bearer",
        )
        client.submit(
            pipeline="reduce_saxs",
            input_run_uid="raw",
            parameters={"k": 1},
            input_access_blob={"tags": ["x"]},
            user_id="u",
        )

    mint.assert_called_once()
    mock_ipc.request.assert_called_once()
    args, kwargs = mock_ipc.request.call_args
    subject = args[0]
    payload = json.loads(args[1])
    assert subject == "lightfall.pipeline.testhost"
    assert payload["pipeline"] == "reduce_saxs"
    assert payload["api_key"].startswith("hex")


def test_client_emits_signal_on_progress_event(mock_ipc, qtbot):
    client = PipelineClient(
        ipc=mock_ipc,
        tiled_url="https://t/api/v1",
        bearer_provider=lambda: "tok",
    )
    received = []
    client.sigJobProgress.connect(lambda ev: received.append(ev))

    # Simulate IPCService delivering a progress event
    client._on_progress(
        subject="lightfall.pipeline.testhost.progress",
        data={
            "job_id": "j1", "status": "running", "detail": "x",
            "input_run_uid": "raw", "output_run_uids": [],
            "executed_notebook_path": "", "error": None, "ts": "2026-05-15T20:14:42Z",
        },
        reply=None,
    )

    assert len(received) == 1
    assert received[0]["status"] == "running"
```

- [ ] **Step 3: Run; verify failure**

Expected: ImportError.

- [ ] **Step 4: Implement `client.py`**

Create `src/lightfall/pipelines/__init__.py`:

```python
"""Lightfall-side notebook-pipeline integration."""
from lightfall.pipelines.client import PipelineClient

__all__ = ["PipelineClient"]
```

Create `src/lightfall/pipelines/client.py`:

```python
"""PipelineClient — Lightfall-side client for the lightfall-pipelines NATS service.

Responsibilities:
- Mint a job-scoped Tiled API key via `lightfall.auth.mint_job_key()`.
- Build a JobMessage and dispatch over IPCService request/reply.
- Subscribe to progress events; re-emit as Qt signals.
- Revoke the API key after the job terminates (best-effort).
"""
from __future__ import annotations

import json
import socket
import uuid
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional

from loguru import logger
from PySide6.QtCore import QObject, Signal

from lightfall.auth.job_key import mint_job_key, revoke_job_key


class PipelineClient(QObject):
    """In-process Lightfall client; pairs 1:1 with a running `lightfall-pipelines` executor."""

    sigJobQueued = Signal(dict)
    sigJobProgress = Signal(dict)
    sigJobCompleted = Signal(dict)
    sigJobFailed = Signal(dict)

    def __init__(
        self,
        *,
        ipc: Any,
        tiled_url: str,
        bearer_provider: Callable[[], str],
        default_lifetime: int = 86400,
        default_scopes: Optional[List[str]] = None,
        parent: Optional[QObject] = None,
    ) -> None:
        super().__init__(parent)
        self._ipc = ipc
        self._tiled_url = tiled_url
        self._get_bearer = bearer_provider
        self._default_lifetime = default_lifetime
        self._default_scopes = list(default_scopes or [
            "read:metadata", "read:data", "write:metadata", "write:data",
        ])
        self._active_keys: Dict[str, str] = {}                # job_id -> first_eight

        # Subscribe to progress events from the local executor host.
        progress_subject = self._ipc.topic("progress")
        self._ipc.subscribe(progress_subject, callback=self._on_progress, main_thread=True)

    def list_available(self) -> List[Dict[str, Any]]:
        """Synchronous request to the executor — returns its discovered plugins."""
        subject = self._ipc.topic("list")
        reply = self._ipc.request(subject, b"{}", timeout=5.0)
        body = json.loads(reply)
        return body.get("pipelines", [])

    def submit(
        self,
        *,
        pipeline: str,
        input_run_uid: str,
        parameters: Dict[str, Any],
        input_access_blob: Dict[str, Any],
        user_id: str,
    ) -> str:
        """Mint a key, send the job. Returns job_id."""
        job_id = str(uuid.uuid4())
        bearer = self._get_bearer()
        minted = mint_job_key(
            tiled_url=self._tiled_url,
            bearer_token=bearer,
            lifetime=self._default_lifetime,
            scopes=self._default_scopes,
            note=f"lightfall pipeline {pipeline} job {job_id[:8]}",
        )
        self._active_keys[job_id] = minted.first_eight

        payload = {
            "job_id": job_id,
            "tiled_url": self._tiled_url,
            "api_key": minted.secret,
            "api_key_expires_at": minted.expires_at,
            "input_run_uid": input_run_uid,
            "input_access_blob": input_access_blob,
            "pipeline": pipeline,
            "parameters": parameters,
            "user_id": user_id,
            "requested_by": f"lightfall@{socket.gethostname()}",
            "submitted_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        }

        subject = self._ipc.topic(None) if hasattr(self._ipc, "topic") else "lightfall.pipeline"
        if subject is None or subject == "":
            subject = self._ipc.topic("")
        self._ipc.request(subject, json.dumps(payload).encode("utf-8"))
        self.sigJobQueued.emit({"job_id": job_id, "pipeline": pipeline})
        return job_id

    # -- progress handling -------------------------------------------------

    def _on_progress(self, subject: str, data: Dict[str, Any], reply: Optional[str]) -> None:
        self.sigJobProgress.emit(data)
        if data.get("status") == "completed":
            self.sigJobCompleted.emit(data)
            self._maybe_revoke(data.get("job_id"))
        elif data.get("status") == "failed":
            self.sigJobFailed.emit(data)
            self._maybe_revoke(data.get("job_id"))

    def _maybe_revoke(self, job_id: Optional[str]) -> None:
        if not job_id:
            return
        first_eight = self._active_keys.pop(job_id, None)
        if not first_eight:
            return
        try:
            revoke_job_key(self._tiled_url, self._get_bearer(), first_eight=first_eight)
        except Exception as e:
            logger.warning("PipelineClient: revoke failed for {}: {}", first_eight, e)
```

- [ ] **Step 5: Run; verify pass**

Run: `.venv/Scripts/python -m pytest tests/pipelines/test_client.py -v`

Expected: 2 PASS.

- [ ] **Step 6: Commit**

```bash
git add src/lightfall/pipelines/ tests/pipelines/test_client.py
git commit -m "feat(pipelines): PipelineClient — mint, submit, revoke, Qt signals"
```

---

### Task 15: Data Browser context-menu integration

**Files:**
- Modify: `src/lightfall/ui/panels/tiled_browser_panel.py` (add context-menu entry)
- Create: `src/lightfall/ui/dialogs/run_pipeline_dialog.py`
- Create: `tests/ui/dialogs/test_run_pipeline_dialog.py`

This task wires up the user-facing "Run pipeline…" action on a Data Browser run row.

- [ ] **Step 1: Locate the Tiled Browser's existing context-menu code**

Run: `grep -n "contextMenu\|customContextMenu\|QMenu\|actionAt" src/lightfall/ui/panels/tiled_browser_panel.py | head -20`

Identify the existing context-menu builder (likely `_build_context_menu` or similar).

- [ ] **Step 2: Write a failing widget test for the dialog**

Create `tests/ui/dialogs/test_run_pipeline_dialog.py`:

```python
"""Tests for the Run Pipeline dialog."""
from unittest.mock import MagicMock

import pytest

from lightfall.ui.dialogs.run_pipeline_dialog import RunPipelineDialog


def test_dialog_lists_pipelines(qtbot):
    client = MagicMock()
    client.list_available.return_value = [
        {"name": "reduce_saxs", "description": "x",
         "parameters_schema": {"roi_x": {"type": "array<int>", "default": [0, 1024]}}},
        {"name": "qc", "description": "y", "parameters_schema": {}},
    ]
    dialog = RunPipelineDialog(client=client, run_uid="abc", input_access_blob={})
    qtbot.addWidget(dialog)
    items = [dialog.pipeline_combo.itemText(i) for i in range(dialog.pipeline_combo.count())]
    assert "reduce_saxs" in items
    assert "qc" in items


def test_dialog_submits_via_client(qtbot):
    client = MagicMock()
    client.list_available.return_value = [
        {"name": "reduce_saxs", "description": "x", "parameters_schema": {}},
    ]
    dialog = RunPipelineDialog(client=client, run_uid="abc", input_access_blob={"tags": ["t"]})
    qtbot.addWidget(dialog)
    dialog.user_id = "rpandolfi"
    dialog._submit()
    client.submit.assert_called_once()
    args, kwargs = client.submit.call_args
    assert kwargs["pipeline"] == "reduce_saxs"
    assert kwargs["input_run_uid"] == "abc"
```

- [ ] **Step 3: Implement the dialog**

Create `src/lightfall/ui/dialogs/run_pipeline_dialog.py`:

```python
"""Run Pipeline dialog — picker + parameter form + submit."""
from __future__ import annotations

from typing import Any, Dict, Optional

from PySide6.QtWidgets import (
    QComboBox, QDialog, QDialogButtonBox, QFormLayout, QLabel, QLineEdit,
    QVBoxLayout, QWidget,
)


class RunPipelineDialog(QDialog):
    def __init__(
        self,
        *,
        client: Any,
        run_uid: str,
        input_access_blob: Dict[str, Any],
        user_id: str = "",
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self._client = client
        self._run_uid = run_uid
        self._blob = input_access_blob
        self.user_id = user_id

        self.setWindowTitle("Run pipeline…")
        self._pipelines = client.list_available()

        outer = QVBoxLayout(self)
        outer.addWidget(QLabel(f"Input run: <code>{run_uid[:8]}…</code>"))

        self.pipeline_combo = QComboBox()
        for p in self._pipelines:
            self.pipeline_combo.addItem(p["name"], userData=p)
        outer.addWidget(self.pipeline_combo)
        self.pipeline_combo.currentIndexChanged.connect(self._rebuild_param_form)

        self._param_form_container = QWidget()
        outer.addWidget(self._param_form_container)
        self._param_widgets: Dict[str, QLineEdit] = {}
        self._rebuild_param_form(0)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self._submit)
        buttons.rejected.connect(self.reject)
        outer.addWidget(buttons)

    def _rebuild_param_form(self, _index: int) -> None:
        pipeline = self.pipeline_combo.currentData()
        # Tear down old form
        if self._param_form_container.layout() is not None:
            old = self._param_form_container.layout()
            while old.count():
                item = old.takeAt(0)
                if item.widget():
                    item.widget().deleteLater()
            QWidget().setLayout(old)
        layout = QFormLayout(self._param_form_container)
        self._param_widgets = {}
        if not pipeline:
            return
        schema = pipeline.get("parameters_schema", {}) or {}
        for name, meta in schema.items():
            edit = QLineEdit(str(meta.get("default", "")))
            layout.addRow(name, edit)
            self._param_widgets[name] = edit

    def _collect_parameters(self) -> Dict[str, Any]:
        out: Dict[str, Any] = {}
        for k, w in self._param_widgets.items():
            text = w.text()
            # Try to interpret JSON-ish values; fall back to string.
            import json
            try:
                out[k] = json.loads(text)
            except Exception:
                out[k] = text
        return out

    def _submit(self) -> None:
        pipeline = self.pipeline_combo.currentText()
        if not pipeline:
            self.reject()
            return
        self._client.submit(
            pipeline=pipeline,
            input_run_uid=self._run_uid,
            parameters=self._collect_parameters(),
            input_access_blob=self._blob,
            user_id=self.user_id,
        )
        self.accept()
```

- [ ] **Step 4: Run dialog tests**

Run: `.venv/Scripts/python -m pytest tests/ui/dialogs/test_run_pipeline_dialog.py -v`

Expected: 2 PASS.

- [ ] **Step 5: Add the context-menu entry to the Tiled Browser**

Open `src/lightfall/ui/panels/tiled_browser_panel.py`, find the existing context-menu builder (from Step 1's grep), and add:

```python
def _add_run_pipeline_action(self, menu, record):
    action = menu.addAction("Run pipeline…")
    action.triggered.connect(lambda: self._open_run_pipeline_dialog(record))

def _open_run_pipeline_dialog(self, record):
    from lightfall.ui.dialogs.run_pipeline_dialog import RunPipelineDialog
    client = self._pipeline_client_provider()       # injected at panel init
    dialog = RunPipelineDialog(
        client=client,
        run_uid=record.uid,
        input_access_blob=record.access_blob,
        user_id=self._current_user_id(),
        parent=self,
    )
    dialog.exec()
```

The exact integration depends on the existing panel's pattern (where does it get the pipeline client, the current user_id, etc.) — wire through the available injection points. Add a `pipeline_client_provider` constructor argument if needed; defaults to a callable returning `None` (in which case the menu entry is grayed out).

In the panel's `__init__`, accept the provider:

```python
def __init__(self, ..., pipeline_client_provider=lambda: None):
    ...
    self._pipeline_client_provider = pipeline_client_provider
```

In the context-menu builder, call `_add_run_pipeline_action(menu, record)` after the existing actions, and disable the action if `self._pipeline_client_provider() is None`.

- [ ] **Step 6: Wire the provider at panel construction in the main window**

Find where `TiledBrowserPanel` is constructed (`grep -n "TiledBrowserPanel(" src/lightfall/`). Add the `pipeline_client_provider=lambda: self._pipeline_client` argument; instantiate `self._pipeline_client` somewhere in the main window's setup that has access to `ipc`, the Tiled URL, and a bearer-token accessor.

- [ ] **Step 7: Smoke-test in the running app**

Run the Lightfall app. Right-click a run in the Tiled browser. Confirm "Run pipeline…" appears and opens the dialog. (Picker may be empty if no executor is running with installed plugins.)

- [ ] **Step 8: Commit**

```bash
git add src/lightfall/ui/dialogs/run_pipeline_dialog.py tests/ui/dialogs/ \
        src/lightfall/ui/panels/tiled_browser_panel.py
git commit -m "feat(ui): Run pipeline… context-menu action + RunPipelineDialog"
```

---

### Task 16: Pipeline Jobs dock panel

**Files:**
- Create: `src/lightfall/ui/panels/pipeline_jobs_panel.py`
- Create: `tests/ui/panels/test_pipeline_jobs_panel.py`

- [ ] **Step 1: Write a failing widget test**

Create `tests/ui/panels/test_pipeline_jobs_panel.py`:

```python
"""Tests for the Pipeline Jobs dock panel."""
from unittest.mock import MagicMock

import pytest

from lightfall.ui.panels.pipeline_jobs_panel import PipelineJobsPanel


def test_panel_adds_row_on_queued(qtbot):
    client = MagicMock()
    panel = PipelineJobsPanel(client=client)
    qtbot.addWidget(panel)
    # Simulate the client emitting a queued signal
    client.sigJobQueued.emit({"job_id": "j1", "pipeline": "reduce_saxs"})
    assert panel.row_count() == 1
    assert panel.row(0)["job_id"] == "j1"


def test_panel_updates_row_on_progress(qtbot):
    client = MagicMock()
    panel = PipelineJobsPanel(client=client)
    qtbot.addWidget(panel)
    client.sigJobQueued.emit({"job_id": "j1", "pipeline": "p"})
    client.sigJobProgress.emit({"job_id": "j1", "status": "running", "detail": "x"})
    assert panel.row(0)["status"] == "running"


def test_panel_shows_completed_outputs(qtbot):
    client = MagicMock()
    panel = PipelineJobsPanel(client=client)
    qtbot.addWidget(panel)
    client.sigJobQueued.emit({"job_id": "j1", "pipeline": "p"})
    client.sigJobCompleted.emit({"job_id": "j1", "status": "completed",
                                 "output_run_uids": ["o1", "o2"],
                                 "executed_notebook_path": "/d/r/j1.ipynb"})
    assert panel.row(0)["status"] == "completed"
    assert panel.row(0)["output_count"] == 2
```

Note: The MagicMock client's signals don't actually trigger Qt slots — the test needs the panel to expose connect-through methods OR the test needs to invoke the panel's slot directly. Adjust the test to call `panel._on_queued(...)` etc., or use a real `QObject` subclass with real signals. Cleanest: replace the MagicMock with a small fixture class:

```python
from PySide6.QtCore import QObject, Signal

class FakeClient(QObject):
    sigJobQueued = Signal(dict)
    sigJobProgress = Signal(dict)
    sigJobCompleted = Signal(dict)
    sigJobFailed = Signal(dict)
```

Use `FakeClient()` instead of MagicMock and emit via `client.sigJobQueued.emit({...})`.

- [ ] **Step 2: Implement the panel**

Create `src/lightfall/ui/panels/pipeline_jobs_panel.py`:

```python
"""Pipeline Jobs dock panel — queue + recent jobs table."""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QHBoxLayout, QHeaderView, QLabel, QPushButton, QTableWidget,
    QTableWidgetItem, QVBoxLayout, QWidget,
)


_COLUMNS = ["job_id", "pipeline", "input_uid", "status", "started", "outputs"]


class PipelineJobsPanel(QWidget):
    def __init__(self, *, client: Any, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._client = client
        self._rows: List[Dict[str, Any]] = []

        outer = QVBoxLayout(self)
        header = QHBoxLayout()
        self._queue_label = QLabel("Queue: 0")
        header.addWidget(self._queue_label)
        header.addStretch()
        outer.addLayout(header)

        self._table = QTableWidget(0, len(_COLUMNS))
        self._table.setHorizontalHeaderLabels(_COLUMNS)
        self._table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self._table.setSelectionBehavior(QTableWidget.SelectRows)
        outer.addWidget(self._table)

        client.sigJobQueued.connect(self._on_queued)
        client.sigJobProgress.connect(self._on_progress)
        client.sigJobCompleted.connect(self._on_completed)
        client.sigJobFailed.connect(self._on_failed)

    def row_count(self) -> int:
        return len(self._rows)

    def row(self, index: int) -> Dict[str, Any]:
        return self._rows[index]

    def _find_row(self, job_id: str) -> Optional[int]:
        for i, r in enumerate(self._rows):
            if r["job_id"] == job_id:
                return i
        return None

    def _add_row(self, data: Dict[str, Any]) -> None:
        self._rows.append(data)
        row = self._table.rowCount()
        self._table.insertRow(row)
        for col, key in enumerate(_COLUMNS):
            self._table.setItem(row, col, QTableWidgetItem(str(data.get(key, ""))))

    def _update_row(self, index: int) -> None:
        data = self._rows[index]
        for col, key in enumerate(_COLUMNS):
            self._table.item(index, col).setText(str(data.get(key, "")))

    def _on_queued(self, evt: Dict[str, Any]) -> None:
        self._add_row({
            "job_id": evt.get("job_id", ""),
            "pipeline": evt.get("pipeline", ""),
            "input_uid": evt.get("input_run_uid", ""),
            "status": "queued",
            "started": "",
            "outputs": "",
            "output_count": 0,
        })

    def _on_progress(self, evt: Dict[str, Any]) -> None:
        idx = self._find_row(evt.get("job_id", ""))
        if idx is None:
            self._add_row({
                "job_id": evt["job_id"], "pipeline": "",
                "input_uid": evt.get("input_run_uid", ""),
                "status": evt.get("status", ""), "started": "", "outputs": "",
                "output_count": 0,
            })
            idx = self._find_row(evt["job_id"])
        self._rows[idx]["status"] = evt.get("status", self._rows[idx]["status"])
        self._update_row(idx)

    def _on_completed(self, evt: Dict[str, Any]) -> None:
        idx = self._find_row(evt.get("job_id", ""))
        if idx is None:
            return
        uids = evt.get("output_run_uids", []) or []
        self._rows[idx].update({
            "status": "completed",
            "outputs": ", ".join(u[:8] for u in uids),
            "output_count": len(uids),
        })
        self._update_row(idx)

    def _on_failed(self, evt: Dict[str, Any]) -> None:
        idx = self._find_row(evt.get("job_id", ""))
        if idx is None:
            return
        self._rows[idx]["status"] = "failed"
        self._update_row(idx)
```

- [ ] **Step 3: Run tests**

Run: `.venv/Scripts/python -m pytest tests/ui/panels/test_pipeline_jobs_panel.py -v`

Expected: 3 PASS.

- [ ] **Step 4: Register the panel with Lightfall's panel registry**

Find Lightfall's panel plugin registry (probably `lightfall.plugins.panel_plugin` or `builtin_manifest.py`) and add the `PipelineJobsPanel` to the discoverable set. Pattern follows existing dock panels.

- [ ] **Step 5: Commit**

```bash
git add src/lightfall/ui/panels/pipeline_jobs_panel.py tests/ui/panels/test_pipeline_jobs_panel.py
git commit -m "feat(ui): Pipeline Jobs dock panel — queue + recent jobs table"
```

---

### Task 17: Pipeline Triggers settings panel

**Files:**
- Create: `src/lightfall/ui/panels/pipeline_triggers_panel.py`
- Create: `tests/ui/panels/test_pipeline_triggers_panel.py`

This panel exposes the `TriggerManager`'s configured triggers, lets the user add/edit/delete them, and persists via Lightfall's settings backend.

- [ ] **Step 1: Locate Lightfall's settings backend**

Run: `grep -rn "settings\.\|SettingsBackend\|QSettings" src/lightfall/settings/ | head -15`

Identify the read/write API for user-facing preferences.

- [ ] **Step 2: Write a failing test**

Create `tests/ui/panels/test_pipeline_triggers_panel.py`:

```python
"""Tests for the Pipeline Triggers settings panel."""
from unittest.mock import MagicMock

import pytest

from lightfall.ui.panels.pipeline_triggers_panel import PipelineTriggersPanel


def test_panel_loads_existing_triggers(qtbot):
    backend = MagicMock()
    backend.load.return_value = [
        {"type": "run_end", "filter": {"plan_name": "count"},
         "pipeline": "reduce_saxs", "parameter_overrides": {}},
    ]
    manager = MagicMock()
    panel = PipelineTriggersPanel(manager=manager, settings_backend=backend)
    qtbot.addWidget(panel)
    assert panel.row_count() == 1


def test_panel_adds_new_trigger(qtbot):
    backend = MagicMock()
    backend.load.return_value = []
    manager = MagicMock()
    panel = PipelineTriggersPanel(manager=manager, settings_backend=backend)
    qtbot.addWidget(panel)
    panel.add_trigger({"type": "run_end", "filter": {"plan_name": "scan"},
                       "pipeline": "p", "parameter_overrides": {}})
    assert panel.row_count() == 1
    backend.save.assert_called()
    manager.add.assert_called()
```

- [ ] **Step 3: Implement the panel**

Create `src/lightfall/ui/panels/pipeline_triggers_panel.py`:

```python
"""Pipeline Triggers settings panel."""
from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QHBoxLayout, QPushButton, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from lightfall.acquire.triggers.filter import FilterPredicate
from lightfall.acquire.triggers.run_end import RunEndTrigger
from lightfall.acquire.triggers.run_start import RunStartTrigger


_COLUMNS = ["type", "filter", "pipeline", "parameter_overrides"]


def _construct_trigger(spec: Dict[str, Any]):
    f = FilterPredicate(**spec.get("filter", {}))
    if spec["type"] == "run_start":
        return RunStartTrigger(filter=f, pipeline=spec["pipeline"],
                               parameter_overrides=spec.get("parameter_overrides", {}))
    return RunEndTrigger(filter=f, pipeline=spec["pipeline"],
                         parameter_overrides=spec.get("parameter_overrides", {}))


class PipelineTriggersPanel(QWidget):
    SETTINGS_KEY = "pipeline_triggers"

    def __init__(
        self,
        *,
        manager: Any,
        settings_backend: Any,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self._manager = manager
        self._backend = settings_backend
        self._specs: List[Dict[str, Any]] = []

        outer = QVBoxLayout(self)
        controls = QHBoxLayout()
        add_btn = QPushButton("Add…")
        add_btn.clicked.connect(self._open_add_dialog)
        controls.addWidget(add_btn)
        controls.addStretch()
        outer.addLayout(controls)

        self._table = QTableWidget(0, len(_COLUMNS))
        self._table.setHorizontalHeaderLabels(_COLUMNS)
        outer.addWidget(self._table)

        for spec in self._backend.load() or []:
            self._add_row(spec, register_with_manager=True)

    def row_count(self) -> int:
        return len(self._specs)

    def add_trigger(self, spec: Dict[str, Any]) -> None:
        self._add_row(spec, register_with_manager=True)
        self._backend.save(self.SETTINGS_KEY, self._specs)

    def _add_row(self, spec: Dict[str, Any], *, register_with_manager: bool) -> None:
        self._specs.append(spec)
        row = self._table.rowCount()
        self._table.insertRow(row)
        self._table.setItem(row, 0, QTableWidgetItem(spec.get("type", "")))
        self._table.setItem(row, 1, QTableWidgetItem(json.dumps(spec.get("filter", {}))))
        self._table.setItem(row, 2, QTableWidgetItem(spec.get("pipeline", "")))
        self._table.setItem(row, 3, QTableWidgetItem(json.dumps(spec.get("parameter_overrides", {}))))
        if register_with_manager:
            self._manager.add(_construct_trigger(spec))

    def _open_add_dialog(self) -> None:
        # Minimal stub — full implementation should be a proper QDialog with
        # type/pipeline/filter/parameter fields. Out of MVP scope for Phase 1.
        pass
```

- [ ] **Step 4: Run tests**

Run: `.venv/Scripts/python -m pytest tests/ui/panels/test_pipeline_triggers_panel.py -v`

Expected: 2 PASS.

- [ ] **Step 5: Wire into the Lightfall settings UI**

Add an entry in the settings/preferences dialog that opens this panel. Pattern follows existing settings panels.

- [ ] **Step 6: Commit**

```bash
git add src/lightfall/ui/panels/pipeline_triggers_panel.py tests/ui/panels/test_pipeline_triggers_panel.py
git commit -m "feat(ui): Pipeline Triggers settings panel"
```

---

## STAGE 4 — End-to-end + reference pipeline

### Task 18: End-to-end integration test against `bcgtiled` + local NATS

**Files:**
- Create: `tests/integration/test_pipeline_e2e.py`

- [ ] **Step 1: Inspect the existing tsuchinoko e2e test for layout**

Run: `cat tests/integration/test_tsuchinoko_e2e.py | head -60`

Note: that test spawns nats-server, in-memory Tiled, and uses sync bridges. Reuse the pattern.

- [ ] **Step 2: Write the e2e test**

Create `tests/integration/test_pipeline_e2e.py`:

```python
"""End-to-end test for notebook pipelines.

Boots: local nats-server (Docker or system binary), bcgtiled-style Tiled
instance with ALSAccessPolicy + an api-key-creating user, the lightfall-pipelines
executor against a fixture plugin, and a PipelineClient. Submits one job;
asserts the output run lands in Tiled with correct provenance + parent_uid.
"""
from __future__ import annotations

import asyncio
import json
import os
import subprocess
import time
from pathlib import Path

import pytest

# Skip the whole module if either dependency is unavailable.
pytest.importorskip("lightfall_pipelines")
pytest.importorskip("nats")


@pytest.fixture(scope="session")
def nats_server():
    """Start nats-server on a free port. Yield its URL."""
    proc = subprocess.Popen(["nats-server", "-p", "4226"])
    time.sleep(0.5)
    try:
        yield "nats://localhost:4226"
    finally:
        proc.terminate()
        proc.wait(timeout=5)


@pytest.fixture(scope="session")
def fixture_plugin_installed(tmp_path_factory):
    """Install the echo-pipeline fixture from lightfall-pipelines into the test env."""
    fixture_root = Path(os.environ["Lightfall_PIPELINES_ROOT"]) / "tests" / "fixtures" / "echo_pipeline"
    subprocess.check_call(["pip", "install", "-e", str(fixture_root)])
    yield
    subprocess.check_call(["pip", "uninstall", "-y", "echo-pipeline"])


@pytest.fixture
def executor_proc(nats_server, fixture_plugin_installed, tmp_path):
    proc = subprocess.Popen([
        "lightfall-pipelines",
        "--nats", nats_server,
        "--hostname", "testhost",
        "--notebook-store", str(tmp_path / "runs"),
        "--env-cache", str(tmp_path / "envs"),
        "--log-level", "DEBUG",
    ])
    time.sleep(2.0)                                    # let it subscribe
    try:
        yield proc
    finally:
        proc.terminate()
        proc.wait(timeout=10)


def test_submit_and_complete_against_real_tiled(executor_proc, nats_server):
    """The complete loop — requires bcgtiled reachable. Skip if not."""
    pytest.importorskip("tiled")
    # ... full body left to the executor — uses PipelineClient against
    # bcgtiled (real instance), submits an echo job, asserts progress
    # events arrive in order, output appears, executed notebook is registered.
    # See spec §Testing strategy → Integration for the full assertion list.
```

The full body of this test is sizable; given the existing tsuchinoko e2e test in `tests/integration/`, follow that pattern. Key assertions per the spec's testing-strategy section:

1. Submit a job via PipelineClient
2. Progress events arrive in order: queued → env_building → running → completed
3. The output run exists in Tiled with `metadata.start.parent_run_uid` matching the input
4. The output run inherits the input's `tiled_access_tags`
5. The executed `.ipynb` exists on disk at the expected `notebook-store` path
6. The output run's Tiled metadata includes the `executed_notebook` pointer with correct sha256

- [ ] **Step 3: Run the e2e test (skip if dependencies absent)**

Run: `.venv/Scripts/python -m pytest tests/integration/test_pipeline_e2e.py -v`

Expected: PASS, or SKIPPED if nats-server / lightfall-pipelines / tiled not available in the env.

- [ ] **Step 4: Commit**

```bash
git add tests/integration/test_pipeline_e2e.py
git commit -m "test(e2e): notebook-pipelines end-to-end against bcgtiled + NATS"
```

---

### Task 19: Reference beamline plugins package (`als-saxs-pipelines`)

**Files:** (in new repo `~/PycharmProjects/als-saxs/`)
- Create entire `als-saxs/packages/als-saxs-pipelines/` package per the multi-package monorepo layout in the spec.

- [ ] **Step 1: Initialize the repo**

```bash
cd ~/PycharmProjects/
mkdir -p als-saxs/packages/als-saxs-pipelines/src/als_saxs_pipelines
mkdir -p als-saxs/packages/als-saxs-pipelines/tests
cd als-saxs
git init
```

- [ ] **Step 2: Write the monorepo README**

```markdown
# als-saxs

ALS SAXS beamline plugins. Multi-package monorepo:

- `packages/als-saxs-pipelines/` — notebook pipelines (lightfall_pipelines.pipeline entry points)

(Future additions: panels, agents.)
```

- [ ] **Step 3: Write `packages/als-saxs-pipelines/pyproject.toml`**

```toml
[build-system]
requires = ["hatchling", "hatch-vcs"]
build-backend = "hatchling.build"

[project]
name = "als-saxs-pipelines"
dynamic = ["version"]
description = "SAXS beamline notebook pipelines for Lightfall"
readme = "README.md"
license = "BSD-3-Clause"
requires-python = ">=3.11"
dependencies = [
    "lightfall-pipelines>=0.1",
    "numpy>=1.26",
    "scipy>=1.11",
    "pyFAI>=2024.1",
    "scrapbook>=0.5",
]

[project.entry-points."lightfall_pipelines.pipeline"]
reduce_saxs = "als_saxs_pipelines.reduce:ReduceSaxsPipeline"

[tool.hatch.version]
source = "vcs"
```

- [ ] **Step 4: Write the pipeline class**

`packages/als-saxs-pipelines/src/als_saxs_pipelines/__init__.py`: empty.

`packages/als-saxs-pipelines/src/als_saxs_pipelines/reduce.py`:

```python
"""Reduce SAXS pipeline — azimuthal integration via pyFAI."""
from lightfall_pipelines.plugin import PipelinePlugin


class ReduceSaxsPipeline(PipelinePlugin):
    name = "reduce_saxs"
    description = "Azimuthal integration + flat-field correction for SAXS runs"
    parameters_schema = {
        "roi_x": {"type": "array<int>", "default": [0, 1024]},
        "subtract_dark": {"type": "bool", "default": True},
    }
    output_tags = ["saxs", "reduced"]
    notebook = "reduce.ipynb"
    package_name = "als_saxs_pipelines"
    timeout_seconds = 900
```

- [ ] **Step 5: Write the notebook**

`packages/als-saxs-pipelines/src/als_saxs_pipelines/reduce.ipynb` — a real notebook that:

1. Imports the Lightfall bootstrap (`from lightfall_pipelines.notebook import TiledWriter, get_input_run`)
2. Reads the input run's primary detector stream
3. Runs pyFAI azimuthal integration on each frame
4. Writes a derived run via `TiledWriter`
5. `sb.glue('output_run_uids', [...])` with the new UID

Use jupyter or vscode to author the .ipynb interactively; commit the resulting JSON file.

- [ ] **Step 6: Write a test**

`packages/als-saxs-pipelines/tests/test_reduce.py`:

```python
"""Contract test: the plugin discovers + introspects."""
from als_saxs_pipelines.reduce import ReduceSaxsPipeline


def test_plugin_class_introspects():
    p = ReduceSaxsPipeline()
    info = p.get_introspection_data()
    assert info["name"] == "reduce_saxs"
    assert "saxs" in info["output_tags"]
    assert info["parameters_schema"]["roi_x"]["default"] == [0, 1024]
```

- [ ] **Step 7: Install + run**

```bash
cd ~/PycharmProjects/als-saxs/packages/als-saxs-pipelines
python -m venv .venv
.venv/Scripts/python -m pip install -e ".[dev]"
.venv/Scripts/python -m pytest -v
```

Expected: PASS.

- [ ] **Step 8: Verify discovery from a fresh venv**

```bash
python -c "from lightfall_pipelines.plugin import discover; print([p.name for p in discover()])"
```

Expected output: `['reduce_saxs']` (plus anything else installed in the env).

- [ ] **Step 9: Commit and tag**

```bash
cd ~/PycharmProjects/als-saxs
git add .
git commit -m "feat: als-saxs-pipelines 0.1.0 — reduce_saxs reference pipeline"
git tag -a packages/als-saxs-pipelines/v0.1.0 -m "Initial release"
```

---

## Self-review checklist

Verify before declaring Plan B complete:

- [ ] **Spec coverage:** Each of the spec's 4 stages and 13 deliverables maps to a numbered task here. (Stage 1 → Tasks 1–3; Stage 2 → Tasks 4–13; Stage 3 → Tasks 14–17; Stage 4 → Tasks 18–19.)
- [ ] **No placeholders:** Search for "TBD", "TODO", "implement later" in this doc. Replace any with concrete content or remove.
- [ ] **Type consistency:** `JobMessage` fields used in Task 11 match those defined in Task 7. `MintedJobKey` fields used in Task 14 match Task 1's dataclass. `PipelinePlugin` class attributes used in Task 5's fixture match the ABC's declared attrs.
- [ ] **Test commands:** All `.venv/Scripts/python -m pytest` invocations are correct for the target repo (lightfall-pipelines tests live in `~/PycharmProjects/lightfall-pipelines/`, Lightfall tests in `~/PycharmProjects/ncs/ncs/`).
- [ ] **Cross-task references:** Task 11's PipelineService uses Task 7's JobMessage, Task 8's EnvCache, Task 9's PapermillRunner, Task 10's NotebookStore, Task 5's PipelinePlugin/discover — all defined.

## Completion criteria

- [ ] All unit tests pass in both `lightfall-pipelines` and `ncs/ncs` repos.
- [ ] The end-to-end test (Task 18) passes against `bcgtiled` + local NATS.
- [ ] The reference `als-saxs-pipelines` package installs and is discovered by the executor.
- [ ] Manual smoke test: launch Lightfall, right-click a real run in the Tiled browser, select "Run pipeline… → reduce_saxs", verify the Pipeline Jobs panel shows queued → running → completed and the output run appears in Tiled with correct provenance.
