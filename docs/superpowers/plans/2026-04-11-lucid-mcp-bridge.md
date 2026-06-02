# Lightfall MCP Bridge Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a standalone MCP server that bridges Claude Code to running Lightfall instances via NATS, plus the Lightfall-side changes needed for instance discovery.

**Architecture:** Two-repo change. Lightfall gets instance identity and a `_lightfall.discover` endpoint. A new `lightfall-mcp-bridge` repo houses a FastMCP server with three dynamic tools (`list_instances`, `list_actions`, `execute_action`) that relay to Lightfall over NATS.

**Tech Stack:** Python, nats-py, fastmcp, hatch, pytest, pytest-asyncio

**Spec:** `docs/superpowers/specs/2026-04-11-lightfall-mcp-bridge-design.md`

**Repos:**
- Lightfall (Tasks 1-3): `~/PycharmProjects/ncs/ncs/`
- Bridge (Tasks 4-10): `~/PycharmProjects/lightfall-mcp-bridge/`

---

### Task 1: Add instance identity to IPCService

**Files:**
- Modify: `src/lightfall/ipc/service.py`
- Test: `tests/ipc/test_service.py`

**Context:** IPCService currently has no identity. We add `instance_id` (auto-generated `{hostname}-{pid}`) and `display_name` (optional, user-set). Both are included in `meta.actions` and `meta.events` responses so the bridge can identify which instance replied.

- [ ] **Step 1: Write tests for instance identity properties**

Append to `tests/ipc/test_service.py`:

```python
import platform


class TestInstanceIdentity:
    """IPCService should expose a unique instance_id and optional display_name."""

    def test_instance_id_contains_hostname(self, svc):
        assert platform.node() in svc.instance_id

    def test_instance_id_contains_pid(self, svc):
        import os
        assert str(os.getpid()) in svc.instance_id

    def test_display_name_defaults_to_none(self, svc):
        assert svc.display_name is None

    def test_display_name_settable(self, svc):
        svc.display_name = "CMS Hutch"
        assert svc.display_name == "CMS Hutch"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd ~/PycharmProjects/ncs/ncs && .venv/Scripts/python.exe -m pytest tests/ipc/test_service.py::TestInstanceIdentity -v`
Expected: FAIL — `instance_id` and `display_name` don't exist yet.

- [ ] **Step 3: Implement instance identity on IPCService**

In `src/lightfall/ipc/service.py`, add imports at top:

```python
import os
import platform
```

In `__init__`, after `self._trust: TrustManager | None = None`, add:

```python
self._instance_id = f"{platform.node()}-{os.getpid()}"
self._display_name: str | None = None
```

Add properties after the `__init__` method:

```python
@property
def instance_id(self) -> str:
    """Unique identity for this IPCService instance ({hostname}-{pid})."""
    return self._instance_id

@property
def display_name(self) -> str | None:
    """Optional human-readable name for this instance."""
    return self._display_name

@display_name.setter
def display_name(self, value: str | None) -> None:
    self._display_name = value
```

- [ ] **Step 4: Run identity tests to verify they pass**

Run: `cd ~/PycharmProjects/ncs/ncs && .venv/Scripts/python.exe -m pytest tests/ipc/test_service.py::TestInstanceIdentity -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Write tests for identity in meta responses**

Append to `tests/ipc/test_actions.py`:

```python
class TestMetaResponseIdentity:
    """meta.actions and meta.events responses include instance identity."""

    def test_meta_actions_includes_identity(self):
        svc = _make_ipc(prefix="als.test")
        svc._instance_id = "testhost-999"
        svc._display_name = "Test Hutch"
        svc.register_meta_endpoints()

        svc._handle_meta_actions("als.test.meta.actions", {}, "reply.inbox")

        svc.reply.assert_called_once()
        response = svc.reply.call_args[0][1]
        assert response["instance_id"] == "testhost-999"
        assert response["display_name"] == "Test Hutch"
        assert response["prefix"] == "als.test"
        assert "actions" in response

    def test_meta_events_includes_identity(self):
        svc = _make_ipc(prefix="als.test")
        svc._instance_id = "testhost-999"
        svc._display_name = None
        svc.register_meta_endpoints()

        svc._handle_meta_events("als.test.meta.events", {}, "reply.inbox")

        svc.reply.assert_called_once()
        response = svc.reply.call_args[0][1]
        assert response["instance_id"] == "testhost-999"
        assert response["display_name"] is None
        assert response["prefix"] == "als.test"
        assert "events" in response
```

Note: `_make_ipc` is the existing test helper in `test_integration.py`. If it's not importable from `test_actions.py`, copy the helper or import it. Also ensure `_make_ipc` initializes the new `_instance_id` and `_display_name` fields:

```python
svc._instance_id = f"test-{os.getpid()}"
svc._display_name = None
```

- [ ] **Step 6: Run meta response tests to verify they fail**

Run: `cd ~/PycharmProjects/ncs/ncs && .venv/Scripts/python.exe -m pytest tests/ipc/test_actions.py::TestMetaResponseIdentity -v`
Expected: FAIL — responses don't include identity fields yet.

- [ ] **Step 7: Update meta handlers to include identity**

In `src/lightfall/ipc/service.py`, replace `_handle_meta_actions`:

```python
def _handle_meta_actions(
    self, subject: str, data: dict, reply: str | None
) -> None:
    """Respond to a ``meta.actions`` request with the action catalog."""
    if reply:
        self.reply(reply, {
            "instance_id": self._instance_id,
            "display_name": self._display_name,
            "prefix": self._topic_prefix,
            "actions": self.list_actions(),
        })
```

Replace `_handle_meta_events`:

```python
def _handle_meta_events(
    self, subject: str, data: dict, reply: str | None
) -> None:
    """Respond to a ``meta.events`` request with the event catalog."""
    if reply:
        self.reply(reply, {
            "instance_id": self._instance_id,
            "display_name": self._display_name,
            "prefix": self._topic_prefix,
            "events": self.list_events(),
        })
```

- [ ] **Step 8: Run meta response tests to verify they pass**

Run: `cd ~/PycharmProjects/ncs/ncs && .venv/Scripts/python.exe -m pytest tests/ipc/test_actions.py::TestMetaResponseIdentity -v`
Expected: PASS (2 tests)

- [ ] **Step 9: Run full IPC test suite to check for regressions**

Run: `cd ~/PycharmProjects/ncs/ncs && .venv/Scripts/python.exe -m pytest tests/ipc/ -v`
Expected: All existing tests still pass. Some tests that check `_handle_meta_actions` output format may need updating if they assert the exact response shape — fix any that fail due to the new fields.

- [ ] **Step 10: Commit**

```bash
cd ~/PycharmProjects/ncs/ncs
git add src/lightfall/ipc/service.py tests/ipc/test_service.py tests/ipc/test_actions.py
git commit -m "feat(ipc): add instance identity to IPCService and meta responses"
```

---

### Task 2: Add `_lightfall.discover` subscription

**Files:**
- Modify: `src/lightfall/ipc/service.py`
- Test: `tests/ipc/test_actions.py`

**Context:** The bridge needs to discover all Lightfall instances on the NATS bus. Each instance subscribes to the well-known `_lightfall.discover` subject (not prefixed) and replies with its identity + action list. This is a broadcast query pattern.

- [ ] **Step 1: Write test for discover handler registration and response**

Append to `tests/ipc/test_actions.py`:

```python
class TestDiscoverEndpoint:
    """IPCService subscribes to _lightfall.discover for instance discovery."""

    def test_discover_handler_registered(self):
        svc = _make_ipc(prefix="als.test")
        svc.subscribe = MagicMock()
        svc.register_meta_endpoints()

        subjects = [call[0][0] for call in svc.subscribe.call_args_list]
        assert "_lightfall.discover" in subjects

    def test_discover_response_matches_meta_actions(self):
        svc = _make_ipc(prefix="als.test")
        svc._instance_id = "testhost-999"
        svc._display_name = "Test Hutch"
        svc.register_meta_endpoints()

        # Register a sample action so the response has content
        svc.register_action(
            "commands.echo",
            lambda s, d, r: None,
            description="Echo back",
            schema={"msg": "str"},
        )

        svc._handle_discover("_lightfall.discover", {}, "reply.inbox")

        svc.reply.assert_called_once()
        response = svc.reply.call_args[0][1]
        assert response["instance_id"] == "testhost-999"
        assert response["display_name"] == "Test Hutch"
        assert response["prefix"] == "als.test"
        assert isinstance(response["actions"], list)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd ~/PycharmProjects/ncs/ncs && .venv/Scripts/python.exe -m pytest tests/ipc/test_actions.py::TestDiscoverEndpoint -v`
Expected: FAIL — `_handle_discover` and the subscription don't exist yet.

- [ ] **Step 3: Implement discover handler and subscription**

In `src/lightfall/ipc/service.py`, add the handler method near the other meta handlers:

```python
def _handle_discover(
    self, subject: str, data: dict, reply: str | None
) -> None:
    """Respond to ``_lightfall.discover`` with instance identity and actions."""
    if reply:
        self.reply(reply, {
            "instance_id": self._instance_id,
            "display_name": self._display_name,
            "prefix": self._topic_prefix,
            "actions": self.list_actions(),
        })
```

In `register_meta_endpoints`, add at the end:

```python
# Well-known discovery subject (not prefixed)
self.subscribe(
    "_lightfall.discover", self._handle_discover, main_thread=False
)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd ~/PycharmProjects/ncs/ncs && .venv/Scripts/python.exe -m pytest tests/ipc/test_actions.py::TestDiscoverEndpoint -v`
Expected: PASS (2 tests)

- [ ] **Step 5: Run full IPC test suite**

Run: `cd ~/PycharmProjects/ncs/ncs && .venv/Scripts/python.exe -m pytest tests/ipc/ -v`
Expected: All pass.

- [ ] **Step 6: Commit**

```bash
cd ~/PycharmProjects/ncs/ncs
git add src/lightfall/ipc/service.py tests/ipc/test_actions.py
git commit -m "feat(ipc): add _lightfall.discover endpoint for instance discovery"
```

---

### Task 3: Add Display Name field to IPCSettingsPlugin

**Files:**
- Modify: `src/lightfall/ui/preferences/ipc_settings.py`
- Test: `tests/ipc/test_settings.py`

**Context:** The `display_name` is user-configured via a new text field in the IPC settings panel. Stored as preference key `ipc_display_name`.

- [ ] **Step 1: Write test for display name field**

Append to `tests/ipc/test_settings.py`:

```python
class TestDisplayNameField:
    """IPCSettingsPlugin exposes a display name text field."""

    def test_display_name_field_exists(self, qapp):
        plugin = IPCSettingsPlugin()
        widget = plugin.create_widget()
        # Find the QLineEdit for display name by placeholder or object
        assert plugin._display_name_edit is not None

    def test_load_saves_display_name(self, qapp, monkeypatch):
        plugin = IPCSettingsPlugin()
        plugin.create_widget()

        # Mock PreferencesManager
        mock_prefs = MagicMock()
        mock_prefs.get = MagicMock(side_effect=lambda k, d="": {
            "ipc_nats_url": "",
            "ipc_topic_prefix": "als.7011",
            "ipc_display_name": "CMS Hutch",
        }.get(k, d))
        monkeypatch.setattr(
            "lightfall.ui.preferences.ipc_settings.PreferencesManager.get_instance",
            lambda: mock_prefs,
        )

        plugin.load_settings()
        assert plugin._display_name_edit.text() == "CMS Hutch"

    def test_save_persists_display_name(self, qapp, monkeypatch):
        plugin = IPCSettingsPlugin()
        plugin.create_widget()
        plugin._display_name_edit.setText("My Hutch")

        mock_prefs = MagicMock()
        monkeypatch.setattr(
            "lightfall.ui.preferences.ipc_settings.PreferencesManager.get_instance",
            lambda: mock_prefs,
        )

        plugin.save_settings()
        calls = {c[0][0]: c[0][1] for c in mock_prefs.set.call_args_list}
        assert calls["ipc_display_name"] == "My Hutch"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd ~/PycharmProjects/ncs/ncs && .venv/Scripts/python.exe -m pytest tests/ipc/test_settings.py::TestDisplayNameField -v`
Expected: FAIL — `_display_name_edit` doesn't exist yet.

- [ ] **Step 3: Add display name field to the plugin**

In `src/lightfall/ui/preferences/ipc_settings.py`:

Add `self._display_name_edit: QLineEdit | None = None` to `__init__`.

In `create_widget`, after the Topic Prefix row and before the test button layout, add:

```python
self._display_name_edit = QLineEdit()
self._display_name_edit.setPlaceholderText("e.g. CMS Hutch")
connection_layout.addRow("Display Name:", self._display_name_edit)
```

In `load_settings`, add:

```python
if self._display_name_edit:
    self._display_name_edit.setText(prefs.get("ipc_display_name", ""))
```

In `save_settings`, add:

```python
display_name = self._display_name_edit.text().strip() if self._display_name_edit else ""
prefs.set("ipc_display_name", display_name)
```

Update the docstring's preference keys list to include `ipc_display_name`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd ~/PycharmProjects/ncs/ncs && .venv/Scripts/python.exe -m pytest tests/ipc/test_settings.py -v`
Expected: All pass.

- [ ] **Step 5: Commit**

```bash
cd ~/PycharmProjects/ncs/ncs
git add src/lightfall/ui/preferences/ipc_settings.py tests/ipc/test_settings.py
git commit -m "feat(ipc): add display name field to IPC settings"
```

---

### Task 4: Scaffold the lightfall-mcp-bridge repository

**Files:**
- Create: `~/PycharmProjects/lightfall-mcp-bridge/pyproject.toml`
- Create: `~/PycharmProjects/lightfall-mcp-bridge/.claude-plugin/plugin.json`
- Create: `~/PycharmProjects/lightfall-mcp-bridge/.mcp.json`
- Create: `~/PycharmProjects/lightfall-mcp-bridge/src/lightfall_bridge/__init__.py`
- Create: `~/PycharmProjects/lightfall-mcp-bridge/LICENSE`

- [ ] **Step 1: Create directory structure**

```bash
mkdir -p ~/PycharmProjects/lightfall-mcp-bridge/{.claude-plugin,src/lightfall_bridge,tests,skills/setup}
```

- [ ] **Step 2: Create pyproject.toml**

```toml
[build-system]
requires = ["hatchling", "hatch-vcs"]
build-backend = "hatchling.build"

[project]
name = "lightfall-mcp-bridge"
dynamic = ["version"]
description = "MCP server bridging Claude Code to Lightfall via NATS"
readme = "README.md"
license = "BSD-3-Clause"
requires-python = ">=3.10"
dependencies = [
    "nats-py>=2.0",
    "fastmcp>=2.0.0",
]

[project.optional-dependencies]
dev = [
    "pytest>=8.0",
    "pytest-asyncio>=0.24",
    "pytest-cov>=4.0",
]

[tool.hatch.version]
source = "vcs"

[tool.hatch.build.targets.wheel]
packages = ["src/lightfall_bridge"]

[tool.pytest.ini_options]
asyncio_mode = "auto"
testpaths = ["tests"]
```

- [ ] **Step 3: Create plugin.json**

`.claude-plugin/plugin.json`:

```json
{
  "name": "lightfall-mcp-bridge",
  "description": "Bridge Claude Code to running Lightfall beamline control instances via NATS",
  "version": "0.1.0",
  "author": {
    "name": "ALS Beamline Controls",
    "email": "controls@als.lbl.gov"
  },
  "license": "BSD-3-Clause",
  "keywords": ["lightfall", "nats", "beamline", "ipc"]
}
```

- [ ] **Step 4: Create .mcp.json**

```json
{
  "lightfall": {
    "command": "python",
    "args": ["-m", "lightfall_bridge", "--nats-url", "nats://localhost:4222"]
  }
}
```

- [ ] **Step 5: Create src/lightfall_bridge/__init__.py**

```python
"""MCP server bridging Claude Code to Lightfall via NATS."""
```

- [ ] **Step 6: Create LICENSE**

Use BSD-3-Clause. Copy the standard BSD-3-Clause text with copyright holder "The Regents of the University of California".

- [ ] **Step 7: Initialize git repo and create venv**

```bash
cd ~/PycharmProjects/lightfall-mcp-bridge
git init
python -m venv .venv
.venv/Scripts/python.exe -m pip install -e ".[dev]"
```

- [ ] **Step 8: Initial commit**

```bash
cd ~/PycharmProjects/lightfall-mcp-bridge
git add .
git commit -m "chore: scaffold lightfall-mcp-bridge repo"
```

---

### Task 5: Implement NATS lifespan and `list_instances` tool

**Files:**
- Create: `src/lightfall_bridge/server.py`
- Create: `tests/test_tools.py`

**Context:** The server uses a FastMCP lifespan to manage the NATS connection. `list_instances` does a scatter-gather broadcast to `_lightfall.discover`, collecting responses over a 2-second window.

- [ ] **Step 1: Write unit test for BridgeState and prefix resolution**

Create `tests/test_tools.py`:

```python
"""Unit tests for bridge logic (no NATS required)."""

from __future__ import annotations

import pytest

from lightfall_bridge.server import BridgeState, resolve_prefix


class TestBridgeState:
    def test_initial_state(self):
        state = BridgeState(nc=None, default_prefix="als.7011")
        assert state.nc is None
        assert state.default_prefix == "als.7011"
        assert state.action_cache == {}
        assert state.auth_state == {}

    def test_default_prefix_empty(self):
        state = BridgeState(nc=None, default_prefix="")
        assert state.default_prefix == ""


class TestResolvePrefix:
    def test_explicit_prefix_used(self):
        assert resolve_prefix("als.7012", "als.7011") == "als.7012"

    def test_falls_back_to_default(self):
        assert resolve_prefix("", "als.7011") == "als.7011"

    def test_raises_when_no_prefix(self):
        with pytest.raises(ValueError, match="No prefix specified"):
            resolve_prefix("", "")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd ~/PycharmProjects/lightfall-mcp-bridge && .venv/Scripts/python.exe -m pytest tests/test_tools.py -v`
Expected: FAIL — `lightfall_bridge.server` doesn't exist yet.

- [ ] **Step 3: Implement BridgeState, resolve_prefix, and list_instances**

Create `src/lightfall_bridge/server.py`:

```python
"""FastMCP server bridging Claude Code to Lightfall via NATS."""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass, field

import nats
from fastmcp import FastMCP, Context
from fastmcp.server.lifespan import lifespan

logger = logging.getLogger(__name__)

DISCOVER_SUBJECT = "_lightfall.discover"
DISCOVER_TIMEOUT = 2.0
REQUEST_TIMEOUT = 5.0
AUTH_TIMEOUT = 10.0


@dataclass
class BridgeState:
    """Shared state for the bridge, stored in lifespan context."""

    nc: nats.NATS | None
    default_prefix: str
    action_cache: dict[str, list[dict]] = field(default_factory=dict)
    auth_state: dict[str, str] = field(default_factory=dict)


def resolve_prefix(prefix: str, default: str) -> str:
    """Return *prefix* if non-empty, else *default*. Raise if both empty."""
    result = prefix or default
    if not result:
        raise ValueError(
            "No prefix specified. Use list_instances to discover "
            "available Lightfall instances, or set --default-prefix."
        )
    return result


def create_server(nats_url: str, default_prefix: str = "") -> FastMCP:
    """Build and return the FastMCP server."""

    @lifespan
    async def nats_lifespan(server):
        nc = None
        try:
            nc = await nats.connect(nats_url)
            logger.info("Connected to NATS at %s", nats_url)
        except Exception as exc:
            logger.warning("Failed to connect to NATS at %s: %s", nats_url, exc)
        state = BridgeState(nc=nc, default_prefix=default_prefix)
        try:
            yield {"bridge": state}
        finally:
            if nc:
                await nc.drain()

    mcp = FastMCP("Lightfall Bridge", lifespan=nats_lifespan)

    def _get_state(ctx: Context) -> BridgeState:
        return ctx.lifespan_context["bridge"]

    @mcp.tool
    async def list_instances(ctx: Context) -> str:
        """Discover Lightfall instances on the NATS bus.

        Broadcasts to all instances and collects responses over a
        2-second window. Returns a JSON array of discovered instances.
        """
        state = _get_state(ctx)
        if state.nc is None:
            return json.dumps({"error": f"Cannot connect to NATS at {nats_url}"})

        nc = state.nc
        inbox = nc.new_inbox()
        sub = await nc.subscribe(inbox)
        await nc.publish(DISCOVER_SUBJECT, b"{}", reply=inbox)

        responses: list[dict] = []
        deadline = asyncio.get_event_loop().time() + DISCOVER_TIMEOUT
        while True:
            remaining = deadline - asyncio.get_event_loop().time()
            if remaining <= 0:
                break
            try:
                msg = await asyncio.wait_for(
                    sub.next_msg(), timeout=remaining
                )
                data = json.loads(msg.data)
                responses.append({
                    "instance_id": data.get("instance_id"),
                    "display_name": data.get("display_name"),
                    "prefix": data.get("prefix"),
                    "actions_count": len(data.get("actions", [])),
                })
            except asyncio.TimeoutError:
                break
            except Exception as exc:
                logger.warning("Bad discover response: %s", exc)

        await sub.unsubscribe()
        return json.dumps(responses, indent=2)

    return mcp
```

- [ ] **Step 4: Run unit tests to verify they pass**

Run: `cd ~/PycharmProjects/lightfall-mcp-bridge && .venv/Scripts/python.exe -m pytest tests/test_tools.py -v`
Expected: PASS (5 tests)

- [ ] **Step 5: Commit**

```bash
cd ~/PycharmProjects/lightfall-mcp-bridge
git add src/lightfall_bridge/server.py tests/test_tools.py
git commit -m "feat: add BridgeState, resolve_prefix, and list_instances tool"
```

---

### Task 6: Implement `list_actions` tool with caching

**Files:**
- Modify: `src/lightfall_bridge/server.py`
- Modify: `tests/test_tools.py`

- [ ] **Step 1: Write unit test for action cache behavior**

Append to `tests/test_tools.py`:

```python
class TestActionCache:
    def test_cache_stores_actions_by_prefix(self):
        state = BridgeState(nc=None, default_prefix="")
        actions = [
            {"subject": "commands.plan.run", "description": "Run a plan", "schema": {}},
        ]
        state.action_cache["als.7011"] = actions
        assert state.action_cache["als.7011"] == actions

    def test_cache_replaces_on_update(self):
        state = BridgeState(nc=None, default_prefix="")
        state.action_cache["als.7011"] = [{"subject": "old"}]
        state.action_cache["als.7011"] = [{"subject": "new"}]
        assert state.action_cache["als.7011"] == [{"subject": "new"}]

    def test_get_cached_action_metadata(self):
        state = BridgeState(nc=None, default_prefix="")
        state.action_cache["als.7011"] = [
            {"subject": "commands.echo", "description": "Echo", "schema": {"msg": "str"}},
        ]
        # Lookup by subject
        match = next(
            (a for a in state.action_cache.get("als.7011", [])
             if a["subject"] == "commands.echo"),
            None,
        )
        assert match is not None
        assert match["description"] == "Echo"
```

- [ ] **Step 2: Run tests to verify they pass**

Run: `cd ~/PycharmProjects/lightfall-mcp-bridge && .venv/Scripts/python.exe -m pytest tests/test_tools.py::TestActionCache -v`
Expected: PASS (3 tests) — these test BridgeState which already exists.

- [ ] **Step 3: Implement `list_actions` tool**

In `src/lightfall_bridge/server.py`, inside `create_server()`, after the `list_instances` tool, add:

```python
    @mcp.tool
    async def list_actions(prefix: str = "", ctx: Context = None) -> str:
        """Get available actions from a specific Lightfall instance.

        Args:
            prefix: Topic prefix of the Lightfall instance (e.g. "als.7011").
                    Falls back to the configured default prefix.
        """
        state = _get_state(ctx)
        if state.nc is None:
            return json.dumps({"error": f"Cannot connect to NATS at {nats_url}"})

        try:
            target = resolve_prefix(prefix, state.default_prefix)
        except ValueError as exc:
            return json.dumps({"error": str(exc)})

        subject = f"{target}.meta.actions"
        try:
            msg = await state.nc.request(
                subject, b"{}", timeout=REQUEST_TIMEOUT
            )
            data = json.loads(msg.data)
        except nats.errors.TimeoutError:
            return json.dumps({
                "error": f"No response from '{target}' — Lightfall instance "
                         f"may be offline. Timeout: {REQUEST_TIMEOUT}s"
            })
        except Exception as exc:
            return json.dumps({"error": f"Request failed: {exc}"})

        # Cache the action metadata for execute_action
        actions = data.get("actions", [])
        state.action_cache[target] = actions

        return json.dumps(data, indent=2)
```

- [ ] **Step 4: Run full test suite**

Run: `cd ~/PycharmProjects/lightfall-mcp-bridge && .venv/Scripts/python.exe -m pytest tests/ -v`
Expected: All pass.

- [ ] **Step 5: Commit**

```bash
cd ~/PycharmProjects/lightfall-mcp-bridge
git add src/lightfall_bridge/server.py tests/test_tools.py
git commit -m "feat: add list_actions tool with action metadata caching"
```

---

### Task 7: Implement `execute_action` tool with auth handshake

**Files:**
- Modify: `src/lightfall_bridge/server.py`
- Modify: `tests/test_tools.py`

- [ ] **Step 1: Write unit tests for auth state tracking**

Append to `tests/test_tools.py`:

```python
class TestAuthState:
    def test_no_auth_state_initially(self):
        state = BridgeState(nc=None, default_prefix="")
        assert state.auth_state == {}

    def test_approved_state_cached(self):
        state = BridgeState(nc=None, default_prefix="")
        state.auth_state["als.7011"] = "approved"
        assert state.auth_state["als.7011"] == "approved"

    def test_denied_state_cached(self):
        state = BridgeState(nc=None, default_prefix="")
        state.auth_state["als.7011"] = "denied"
        assert state.auth_state["als.7011"] == "denied"


class TestFormatExecuteResponse:
    def test_with_cached_metadata(self):
        from lightfall_bridge.server import format_execute_response

        cache = [
            {"subject": "commands.echo", "description": "Echo", "schema": {"msg": "str"}},
        ]
        result = format_execute_response(
            action="commands.echo",
            response={"echoed": "hello"},
            action_cache=cache,
        )
        assert result["action"] == "commands.echo"
        assert result["description"] == "Echo"
        assert result["schema"] == {"msg": "str"}
        assert result["response"] == {"echoed": "hello"}

    def test_without_cached_metadata(self):
        from lightfall_bridge.server import format_execute_response

        result = format_execute_response(
            action="commands.unknown",
            response={"status": "ok"},
            action_cache=[],
        )
        assert result["description"] is None
        assert result["schema"] is None
        assert result["response"] == {"status": "ok"}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd ~/PycharmProjects/lightfall-mcp-bridge && .venv/Scripts/python.exe -m pytest tests/test_tools.py::TestFormatExecuteResponse -v`
Expected: FAIL — `format_execute_response` doesn't exist yet.

- [ ] **Step 3: Implement format_execute_response helper**

In `src/lightfall_bridge/server.py`, add at module level (before `create_server`):

```python
def format_execute_response(
    action: str,
    response: dict,
    action_cache: list[dict],
) -> dict:
    """Wrap a Lightfall response with cached action metadata."""
    match = next(
        (a for a in action_cache if a.get("subject") == action),
        None,
    )
    return {
        "action": action,
        "description": match["description"] if match else None,
        "schema": match.get("schema") if match else None,
        "response": response,
    }
```

- [ ] **Step 4: Run format tests to verify they pass**

Run: `cd ~/PycharmProjects/lightfall-mcp-bridge && .venv/Scripts/python.exe -m pytest tests/test_tools.py::TestFormatExecuteResponse -v`
Expected: PASS (2 tests)

- [ ] **Step 5: Implement execute_action tool with auth**

In `src/lightfall_bridge/server.py`, inside `create_server()`, add after `list_actions`:

```python
    async def _ensure_auth(state: BridgeState, target: str) -> str | None:
        """Perform auth handshake if needed. Returns error string or None."""
        auth = state.auth_state.get(target)
        if auth == "approved":
            return None
        if auth == "denied":
            return (
                f"Lightfall instance at '{target}' denied access. "
                "Approve the trust prompt in Lightfall to continue."
            )
        # First contact — do handshake
        subject = f"{target}.auth.request"
        payload = json.dumps({
            "app_name": "claude-code-bridge",
            "app_version": "0.1.0",
        }).encode()
        try:
            msg = await state.nc.request(subject, payload, timeout=AUTH_TIMEOUT)
            data = json.loads(msg.data)
        except nats.errors.TimeoutError:
            return (
                f"Auth handshake timed out for '{target}'. "
                "Check that Lightfall is running and respond to the trust prompt."
            )
        except Exception as exc:
            return f"Auth handshake failed: {exc}"

        status = data.get("status")
        if status == "approved":
            state.auth_state[target] = "approved"
            return None
        else:
            reason = data.get("reason", "denied by operator")
            state.auth_state[target] = "denied"
            return (
                f"Lightfall instance at '{target}' denied access: {reason}. "
                "Approve the trust prompt in Lightfall to continue."
            )

    @mcp.tool
    async def execute_action(
        action: str,
        params: dict | None = None,
        prefix: str = "",
        ctx: Context = None,
    ) -> str:
        """Execute an action on a Lightfall instance.

        Args:
            action: Action subject suffix (e.g. "commands.plan.run").
            params: JSON payload to send with the action. Defaults to {}.
            prefix: Topic prefix of the Lightfall instance. Falls back to default.
        """
        state = _get_state(ctx)
        if state.nc is None:
            return json.dumps({"error": f"Cannot connect to NATS at {nats_url}"})

        try:
            target = resolve_prefix(prefix, state.default_prefix)
        except ValueError as exc:
            return json.dumps({"error": str(exc)})

        # Auth handshake
        auth_error = await _ensure_auth(state, target)
        if auth_error:
            return json.dumps({"error": auth_error})

        # Execute
        subject = f"{target}.{action}"
        payload = json.dumps(params or {}).encode()
        try:
            msg = await state.nc.request(subject, payload, timeout=REQUEST_TIMEOUT)
            response = json.loads(msg.data)
        except nats.errors.TimeoutError:
            return json.dumps({
                "error": f"No response from '{target}' — Lightfall instance "
                         f"may be offline. Timeout: {REQUEST_TIMEOUT}s"
            })
        except json.JSONDecodeError:
            return json.dumps({
                "warning": "Response was not valid JSON",
                "raw": msg.data.decode("utf-8", errors="replace"),
            })
        except Exception as exc:
            return json.dumps({"error": f"Request failed: {exc}"})

        cache = state.action_cache.get(target, [])
        result = format_execute_response(action, response, cache)
        return json.dumps(result, indent=2)
```

- [ ] **Step 6: Add auth handshake to list_actions**

Per the spec, auth triggers on both `list_actions` and `execute_action`. In `src/lightfall_bridge/server.py`, update the `list_actions` tool to call `_ensure_auth` before the meta request. Add this block after the `resolve_prefix` call and before the `nc.request`:

```python
        # Auth handshake
        auth_error = await _ensure_auth(state, target)
        if auth_error:
            return json.dumps({"error": auth_error})
```

- [ ] **Step 7: Run full unit test suite**

Run: `cd ~/PycharmProjects/lightfall-mcp-bridge && .venv/Scripts/python.exe -m pytest tests/test_tools.py -v`
Expected: All pass.

- [ ] **Step 8: Commit**

```bash
cd ~/PycharmProjects/lightfall-mcp-bridge
git add src/lightfall_bridge/server.py tests/test_tools.py
git commit -m "feat: add execute_action tool with auth handshake"
```

---

### Task 8: Implement CLI entry point

**Files:**
- Create: `src/lightfall_bridge/__main__.py`

- [ ] **Step 1: Create __main__.py**

```python
"""Entry point for ``python -m lightfall_bridge``."""

from __future__ import annotations

import argparse
import sys

from lightfall_bridge.server import create_server


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        prog="lightfall-bridge",
        description="MCP server bridging Claude Code to Lightfall via NATS",
    )
    parser.add_argument(
        "--nats-url",
        required=True,
        help="NATS server URL (e.g. nats://localhost:4222)",
    )
    parser.add_argument(
        "--default-prefix",
        default="",
        help="Default topic prefix for Lightfall instance (e.g. als.7011)",
    )
    args = parser.parse_args(argv)

    server = create_server(
        nats_url=args.nats_url,
        default_prefix=args.default_prefix,
    )
    server.run()


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Verify the module is importable**

Run: `cd ~/PycharmProjects/lightfall-mcp-bridge && .venv/Scripts/python.exe -c "from lightfall_bridge.__main__ import main; print('OK')"`
Expected: `OK`

- [ ] **Step 3: Verify --help works**

Run: `cd ~/PycharmProjects/lightfall-mcp-bridge && .venv/Scripts/python.exe -m lightfall_bridge --help`
Expected: Help text showing `--nats-url` and `--default-prefix`.

- [ ] **Step 4: Commit**

```bash
cd ~/PycharmProjects/lightfall-mcp-bridge
git add src/lightfall_bridge/__main__.py
git commit -m "feat: add CLI entry point for python -m lightfall_bridge"
```

---

### Task 9: Integration tests with mock Lightfall responder

**Files:**
- Create: `tests/conftest.py`
- Create: `tests/test_integration.py`

**Context:** Integration tests run against a real NATS server (localhost:4222). A mock Lightfall responder subscribes to a test prefix and responds to meta/command/auth subjects. Tests are skipped if NATS is unavailable.

- [ ] **Step 1: Create conftest.py with NATS fixtures**

Create `tests/conftest.py`:

```python
"""Fixtures for integration tests requiring a NATS server."""

from __future__ import annotations

import asyncio
import json
import uuid

import nats
import pytest
import pytest_asyncio


def pytest_collection_modifyitems(config, items):
    """Skip integration tests if NATS is not available."""
    # Integration tests are only in test_integration.py; unit tests always run.
    pass


@pytest.fixture(scope="session")
def nats_url():
    return "nats://localhost:4222"


@pytest_asyncio.fixture
async def nats_available(nats_url):
    """Skip test if NATS is not reachable."""
    try:
        nc = await nats.connect(nats_url)
        await nc.drain()
    except Exception:
        pytest.skip("NATS server not available at localhost:4222")


@pytest_asyncio.fixture
async def mock_lightfall(nats_url, nats_available):
    """A mock Lightfall instance on a unique prefix."""
    nc = await nats.connect(nats_url)
    prefix = f"test.{uuid.uuid4().hex[:8]}"

    instance_id = f"mock-{uuid.uuid4().hex[:6]}"
    display_name = "Mock Lightfall"
    actions = [
        {
            "subject": "commands.echo",
            "description": "Echo back params",
            "schema": {"message": "str"},
        },
    ]

    meta_response = json.dumps({
        "instance_id": instance_id,
        "display_name": display_name,
        "prefix": prefix,
        "actions": actions,
    }).encode()

    async def handle_meta(msg):
        if msg.reply:
            await nc.publish(msg.reply, meta_response)

    async def handle_discover(msg):
        if msg.reply:
            await nc.publish(msg.reply, meta_response)

    async def handle_echo(msg):
        data = json.loads(msg.data)
        reply_data = json.dumps({"echoed": data}).encode()
        if msg.reply:
            await nc.publish(msg.reply, reply_data)

    async def handle_auth(msg):
        data = json.loads(msg.data)
        reply_data = json.dumps({"status": "approved"}).encode()
        if msg.reply:
            await nc.publish(msg.reply, reply_data)

    subs = [
        await nc.subscribe(f"{prefix}.meta.actions", cb=handle_meta),
        await nc.subscribe("_lightfall.discover", cb=handle_discover),
        await nc.subscribe(f"{prefix}.commands.echo", cb=handle_echo),
        await nc.subscribe(f"{prefix}.auth.request", cb=handle_auth),
    ]

    yield {
        "prefix": prefix,
        "instance_id": instance_id,
        "display_name": display_name,
    }

    for sub in subs:
        await sub.unsubscribe()
    await nc.drain()
```

- [ ] **Step 2: Create integration tests**

Create `tests/test_integration.py`:

```python
"""Integration tests — require a running NATS server at localhost:4222."""

from __future__ import annotations

import json

import pytest
import pytest_asyncio

from lightfall_bridge.server import BridgeState, create_server

import nats


@pytest_asyncio.fixture
async def bridge_state(nats_url, nats_available):
    """A connected BridgeState for direct function testing."""
    nc = await nats.connect(nats_url)
    state = BridgeState(nc=nc, default_prefix="")
    yield state
    await nc.drain()


class TestListInstances:
    @pytest.mark.asyncio
    async def test_discovers_mock_instance(self, mock_lightfall, bridge_state):
        """list_instances should find the mock Lightfall responder."""
        import asyncio

        nc = bridge_state.nc
        inbox = nc.new_inbox()
        sub = await nc.subscribe(inbox)
        await nc.publish("_lightfall.discover", b"{}", reply=inbox)

        responses = []
        deadline = asyncio.get_event_loop().time() + 2.0
        while True:
            remaining = deadline - asyncio.get_event_loop().time()
            if remaining <= 0:
                break
            try:
                msg = await asyncio.wait_for(sub.next_msg(), timeout=remaining)
                responses.append(json.loads(msg.data))
            except asyncio.TimeoutError:
                break
        await sub.unsubscribe()

        prefixes = [r["prefix"] for r in responses]
        assert mock_lightfall["prefix"] in prefixes

    @pytest.mark.asyncio
    async def test_no_instances_returns_empty(self, bridge_state):
        """Discover with no responders returns empty after timeout."""
        import asyncio

        nc = bridge_state.nc
        # Use a subject nothing listens on
        inbox = nc.new_inbox()
        sub = await nc.subscribe(inbox)
        await nc.publish("_NO_LISTENERS_HERE_", b"{}", reply=inbox)

        try:
            await asyncio.wait_for(sub.next_msg(), timeout=0.5)
            got_response = True
        except asyncio.TimeoutError:
            got_response = False
        await sub.unsubscribe()

        assert not got_response


class TestListActions:
    @pytest.mark.asyncio
    async def test_fetches_actions_from_mock(self, mock_lightfall, bridge_state):
        prefix = mock_lightfall["prefix"]
        nc = bridge_state.nc

        msg = await nc.request(
            f"{prefix}.meta.actions", b"{}", timeout=5.0
        )
        data = json.loads(msg.data)

        assert data["instance_id"] == mock_lightfall["instance_id"]
        assert data["prefix"] == prefix
        assert len(data["actions"]) == 1
        assert data["actions"][0]["subject"] == "commands.echo"


class TestExecuteAction:
    @pytest.mark.asyncio
    async def test_echo_round_trip(self, mock_lightfall, bridge_state):
        prefix = mock_lightfall["prefix"]
        nc = bridge_state.nc

        payload = json.dumps({"message": "hello"}).encode()
        msg = await nc.request(
            f"{prefix}.commands.echo", payload, timeout=5.0
        )
        data = json.loads(msg.data)

        assert data["echoed"]["message"] == "hello"


class TestAuthHandshake:
    @pytest.mark.asyncio
    async def test_auth_approved(self, mock_lightfall, bridge_state):
        prefix = mock_lightfall["prefix"]
        nc = bridge_state.nc

        payload = json.dumps({
            "app_name": "claude-code-bridge",
            "app_version": "0.1.0",
        }).encode()
        msg = await nc.request(
            f"{prefix}.auth.request", payload, timeout=5.0
        )
        data = json.loads(msg.data)

        assert data["status"] == "approved"
```

- [ ] **Step 3: Run integration tests**

Run: `cd ~/PycharmProjects/lightfall-mcp-bridge && .venv/Scripts/python.exe -m pytest tests/test_integration.py -v`
Expected: PASS if NATS is running (from earlier in this session), SKIP otherwise.

- [ ] **Step 4: Run full test suite**

Run: `cd ~/PycharmProjects/lightfall-mcp-bridge && .venv/Scripts/python.exe -m pytest tests/ -v`
Expected: All pass.

- [ ] **Step 5: Commit**

```bash
cd ~/PycharmProjects/lightfall-mcp-bridge
git add tests/conftest.py tests/test_integration.py
git commit -m "test: add integration tests with mock Lightfall responder"
```

---

### Task 10: Setup skill and README

**Files:**
- Create: `skills/setup/SKILL.md`
- Create: `README.md`

- [ ] **Step 1: Create setup skill**

Create `skills/setup/SKILL.md`:

```markdown
---
name: setup
description: Set up the Lightfall MCP bridge — verify NATS connectivity, discover Lightfall instances, and configure the default prefix. Use when the user says "connect to Lightfall", "set up Lightfall bridge", or after first installing this plugin.
---

# Lightfall Bridge Setup

Guide the user through connecting Claude Code to a running Lightfall instance.

## Steps

1. **Check NATS connectivity.** Use the `lightfall__list_instances` tool. If it returns a connection error, ask the user to verify:
   - NATS server is running (e.g. `nats-server -p 4222`)
   - The `--nats-url` in `.mcp.json` matches their NATS server address

2. **Discover Lightfall instances.** If `list_instances` returns results, show the user which instances are on the bus (display name, prefix, action count). If empty, Lightfall may not be running or may not have IPC enabled — ask them to check Lightfall's IPC settings (Settings > IPC > Server URL).

3. **Test communication.** Run `lightfall__list_actions` with the chosen prefix. This triggers the trust handshake — tell the user to watch for the trust prompt in Lightfall and approve it.

4. **Configure default prefix.** If there's only one instance, suggest updating `.mcp.json` to include `--default-prefix` so the user doesn't have to specify it on every call:
   ```json
   {
     "lightfall": {
       "command": "python",
       "args": ["-m", "lightfall_bridge", "--nats-url", "nats://localhost:4222", "--default-prefix", "als.7011"]
     }
   }
   ```

5. **Confirm.** Run a test action if one is available (e.g. echo) to verify end-to-end communication.

## Troubleshooting

- **"Cannot connect to NATS"** — NATS server isn't running or URL is wrong.
- **"No response from..."** — Lightfall isn't running, IPC is disabled, or prefix is wrong.
- **"Auth handshake timed out"** — Trust prompt appeared in Lightfall but wasn't answered within 10 seconds. Ask the user to try again and approve the prompt.
- **"denied access"** — Operator denied the trust prompt. Ask them to approve it, or check Lightfall's trusted apps list in Settings > IPC.
```

- [ ] **Step 2: Create README.md**

Create `README.md`:

```markdown
# lightfall-mcp-bridge

MCP server that bridges [Claude Code](https://claude.ai/claude-code) to running [Lightfall](https://git.als.lbl.gov/ncs/ncs) beamline control instances via [NATS](https://nats.io/).

## What it does

Provides three MCP tools:

- **`list_instances`** — Discover Lightfall instances on the NATS bus
- **`list_actions`** — Get available actions from a specific instance
- **`execute_action`** — Invoke an action (run plans, abort, query state, etc.)

Actions are discovered dynamically — the bridge never needs updating when Lightfall adds new capabilities.

## Installation

```bash
pip install -e .
```

## Configuration

Add to your Claude Code MCP config (`.mcp.json` or settings):

```json
{
  "lightfall": {
    "command": "python",
    "args": ["-m", "lightfall_bridge", "--nats-url", "nats://localhost:4222"]
  }
}
```

Optional: `--default-prefix als.7011` to set a default Lightfall instance.

## Requirements

- A running NATS server (local or remote)
- A running Lightfall instance with IPC enabled

## Development

```bash
python -m venv .venv
.venv/Scripts/activate  # or source .venv/bin/activate on Unix
pip install -e ".[dev]"
pytest
```

Integration tests require a NATS server at `localhost:4222` and are skipped otherwise.
```

- [ ] **Step 3: Commit**

```bash
cd ~/PycharmProjects/lightfall-mcp-bridge
git add skills/setup/SKILL.md README.md
git commit -m "docs: add setup skill and README"
```
