# IPC Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add NATS-based IPC to Lightfall so external tools can send commands and receive event notifications over the network.

**Architecture:** IPCService singleton manages a NATS connection on a background thread, dispatches inbound messages to registered callbacks on the Qt main thread, and publishes outbound events. TrustManager handles auth token sharing via NATS request/reply with user approval dialogs. An IPCSettingsPlugin provides configuration UI.

**Tech Stack:** nats-py, PySide6, asyncio, loguru

**Spec:** `docs/superpowers/specs/2026-04-09-ipc-design.md`

---

## File Structure

| Action | Path | Responsibility |
|--------|------|----------------|
| Create | `src/lightfall/ipc/__init__.py` | Public API exports |
| Create | `src/lightfall/ipc/service.py` | IPCService — connection, pub/sub, topic builder, catalogs |
| Create | `src/lightfall/ipc/trust.py` | TrustManager (logic) + TrustDialog (UI) |
| Create | `src/lightfall/ui/preferences/ipc_settings.py` | IPCSettingsPlugin |
| Create | `tests/ipc/__init__.py` | Test package |
| Create | `tests/ipc/test_service.py` | IPCService unit tests |
| Create | `tests/ipc/test_trust.py` | TrustManager unit tests |
| Create | `tests/ipc/test_settings.py` | Settings plugin tests |
| Create | `tests/ipc/test_integration.py` | Engine/logbook/agent integration tests |
| Modify | `pyproject.toml` | Add nats-py dependency |
| Modify | `src/lightfall/plugins/builtin_manifest.py` | Register IPC settings plugin |
| Modify | `src/lightfall/core/application.py` | IPCService lifecycle (start/stop) |
| Modify | `src/lightfall/acquire/engine/bluesky.py` | Publish run events, register plan commands |
| Modify | `src/lightfall/logbook/client.py` | Register logbook.add command |
| Modify | `src/lightfall/claude/agent.py` | Register agent.message command |
| Create | `docs/ipc-architecture.md` | Internal architecture documentation |
| Create | `docs/ipc-client-guide.md` | External client integration guide |

---

### Task 1: Package Skeleton + Dependency

**Files:**
- Modify: `pyproject.toml`
- Create: `src/lightfall/ipc/__init__.py`
- Create: `tests/ipc/__init__.py`

- [ ] **Step 1: Add nats-py dependency**

In `pyproject.toml`, add `nats-py` to the dependencies list:

```toml
    "happi>=2.0",
    "nats-py>=2.0",
]
```

- [ ] **Step 2: Install the dependency**

Run: `cd ~/PycharmProjects/ncs/ncs && pip install -e .`

- [ ] **Step 3: Create ipc package**

Create `src/lightfall/ipc/__init__.py`:

```python
"""NATS-based inter-process communication for Lightfall."""

from lightfall.ipc.service import IPCService

__all__ = ["IPCService"]
```

- [ ] **Step 4: Create test package**

Create `tests/ipc/__init__.py` (empty file).

- [ ] **Step 5: Verify import works**

Run: `python -c "import nats; print(nats.__version__)"`
Expected: version string printed, no import error.

- [ ] **Step 6: Commit**

```bash
git add pyproject.toml src/lightfall/ipc/__init__.py tests/ipc/__init__.py
git commit -m "feat(ipc): add nats-py dependency and ipc package skeleton"
```

---

### Task 2: IPCService Core — Topic Builder, Connection, Pub/Sub

**Files:**
- Create: `src/lightfall/ipc/service.py`
- Create: `tests/ipc/test_service.py`

- [ ] **Step 1: Write tests for topic builder**

Create `tests/ipc/test_service.py`:

```python
"""Tests for IPCService."""

from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from lightfall.ipc.service import IPCService


class TestTopicBuilder:
    def test_topic_joins_prefix_and_suffix(self) -> None:
        svc = IPCService.__new__(IPCService)
        svc._prefix = "als.7011"
        assert svc.topic("commands.plan.run") == "als.7011.commands.plan.run"

    def test_topic_with_empty_prefix(self) -> None:
        svc = IPCService.__new__(IPCService)
        svc._prefix = ""
        assert svc.topic("commands.plan.run") == "commands.plan.run"

    def test_topic_strips_leading_dot_when_prefix_empty(self) -> None:
        svc = IPCService.__new__(IPCService)
        svc._prefix = ""
        result = svc.topic("commands.plan.run")
        assert not result.startswith(".")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/ipc/test_service.py::TestTopicBuilder -v`
Expected: FAIL — `IPCService` does not exist yet.

- [ ] **Step 3: Write IPCService with topic builder**

Create `src/lightfall/ipc/service.py`:

```python
"""NATS-based IPC service for Lightfall."""

from __future__ import annotations

import asyncio
import json
import threading
from dataclasses import dataclass, field
from typing import Any, Callable

from loguru import logger
from PySide6.QtCore import QObject, Signal

from lightfall.utils.threads import invoke_in_main_thread


@dataclass
class ActionInfo:
    """Metadata for a registered IPC action."""

    subject: str
    description: str = ""
    schema: dict[str, Any] | None = None


@dataclass
class EventInfo:
    """Metadata for a registered outbound IPC event."""

    subject: str
    description: str = ""
    schema: dict[str, Any] | None = None


@dataclass
class _Subscription:
    """Internal record of a NATS subscription."""

    subject: str
    callback: Callable
    main_thread: bool
    nats_sub: Any = None  # nats.aio.subscription.Subscription


class IPCService(QObject):
    """NATS-based inter-process communication service.

    Manages NATS connection, pub/sub dispatch, action/event catalogs,
    and trust management for auth token sharing.
    """

    sigConnectionChanged = Signal(bool)

    def __init__(
        self,
        nats_url: str = "",
        topic_prefix: str = "als.7011",
    ) -> None:
        super().__init__()
        self._nats_url = nats_url
        self._prefix = topic_prefix
        self._nc: Any | None = None  # nats.NATS client
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._subscriptions: dict[str, _Subscription] = {}
        self._action_catalog: dict[str, ActionInfo] = {}
        self._event_catalog: dict[str, EventInfo] = {}
        self._connected = False
        self._shutdown_event = threading.Event()

    # -- Topic builder --

    def topic(self, suffix: str) -> str:
        """Build a full NATS subject from the configured prefix and a suffix.

        Args:
            suffix: Subject suffix (e.g. "commands.plan.run").

        Returns:
            Full subject string. If prefix is empty, returns suffix as-is.
        """
        if self._prefix:
            return f"{self._prefix}.{suffix}"
        return suffix

    # -- Connection lifecycle --

    @property
    def is_connected(self) -> bool:
        """Whether the NATS connection is active."""
        return self._connected

    def start(self) -> None:
        """Start the IPC service — connect to NATS on a background thread.

        Does nothing if nats_url is empty.
        """
        if not self._nats_url:
            logger.info("IPC disabled — no NATS URL configured")
            return

        self._shutdown_event.clear()
        self._thread = threading.Thread(
            target=self._run_loop,
            name="IPCService",
            daemon=True,
        )
        self._thread.start()

    def stop(self) -> None:
        """Stop the IPC service — drain and disconnect."""
        self._shutdown_event.set()
        if self._loop and not self._loop.is_closed():
            asyncio.run_coroutine_threadsafe(self._disconnect(), self._loop)
        if self._thread:
            self._thread.join(timeout=5.0)
            self._thread = None

    def _run_loop(self) -> None:
        """Background thread entry — run the asyncio event loop."""
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        try:
            self._loop.run_until_complete(self._connect_and_serve())
        except Exception:
            logger.exception("IPC event loop crashed")
        finally:
            self._loop.close()
            self._loop = None

    async def _connect_and_serve(self) -> None:
        """Connect to NATS and block until shutdown."""
        import nats

        try:
            self._nc = await nats.connect(
                self._nats_url,
                tls_required=True,
                error_cb=self._on_error,
                disconnected_cb=self._on_disconnected,
                reconnected_cb=self._on_reconnected,
            )
            self._connected = True
            invoke_in_main_thread(self.sigConnectionChanged.emit, True)
            logger.info("IPC connected to {}", self._nats_url)

            # Re-subscribe existing subscriptions (for reconnect scenarios)
            for sub in self._subscriptions.values():
                sub.nats_sub = await self._nc.subscribe(
                    sub.subject, cb=self._make_handler(sub),
                )

        except Exception:
            logger.exception("IPC failed to connect to {}", self._nats_url)
            self._connected = False
            invoke_in_main_thread(self.sigConnectionChanged.emit, False)
            return

        # Block until shutdown
        while not self._shutdown_event.is_set():
            await asyncio.sleep(0.25)

    async def _disconnect(self) -> None:
        """Drain subscriptions and close NATS connection."""
        if self._nc and self._nc.is_connected:
            try:
                await self._nc.drain()
            except Exception:
                logger.exception("Error draining NATS connection")
            self._nc = None
        self._connected = False
        invoke_in_main_thread(self.sigConnectionChanged.emit, False)

    async def _on_error(self, e: Exception) -> None:
        logger.error("NATS error: {}", e)

    async def _on_disconnected(self) -> None:
        self._connected = False
        invoke_in_main_thread(self.sigConnectionChanged.emit, False)
        logger.warning("NATS disconnected")

    async def _on_reconnected(self) -> None:
        self._connected = True
        invoke_in_main_thread(self.sigConnectionChanged.emit, True)
        logger.info("NATS reconnected")

    # -- Subscribe / Publish / Request --

    def _make_handler(self, sub: _Subscription) -> Callable:
        """Create an async NATS message handler that dispatches to the callback."""

        async def handler(msg: Any) -> None:
            try:
                data = json.loads(msg.data) if msg.data else {}
            except json.JSONDecodeError:
                logger.warning("IPC: malformed JSON on {}: {}", msg.subject, msg.data)
                if msg.reply:
                    await self._nc.publish(
                        msg.reply,
                        json.dumps({"error": True, "message": "Malformed JSON"}).encode(),
                    )
                return

            try:
                if sub.main_thread:
                    invoke_in_main_thread(sub.callback, msg.subject, data, msg.reply)
                else:
                    sub.callback(msg.subject, data, msg.reply)
            except Exception as exc:
                logger.exception("IPC callback error on {}: {}", msg.subject, exc)
                if msg.reply:
                    await self._nc.publish(
                        msg.reply,
                        json.dumps({"error": True, "message": str(exc)}).encode(),
                    )

        return handler

    def subscribe(
        self,
        subject: str,
        callback: Callable[[str, dict, str | None], Any],
        *,
        main_thread: bool = True,
    ) -> None:
        """Subscribe to a NATS subject.

        Args:
            subject: Full NATS subject string.
            callback: Called with (subject, data_dict, reply_subject).
                reply_subject is None for pub/sub, a string for request/reply.
            main_thread: If True, callback is dispatched to the Qt main thread.
        """
        sub = _Subscription(
            subject=subject,
            callback=callback,
            main_thread=main_thread,
        )
        self._subscriptions[subject] = sub

        if self._nc and self._nc.is_connected and self._loop:
            future = asyncio.run_coroutine_threadsafe(
                self._nc.subscribe(subject, cb=self._make_handler(sub)),
                self._loop,
            )
            sub.nats_sub = future.result(timeout=5.0)

    def unsubscribe(self, subject: str) -> None:
        """Unsubscribe from a NATS subject."""
        sub = self._subscriptions.pop(subject, None)
        if sub and sub.nats_sub and self._loop:
            asyncio.run_coroutine_threadsafe(sub.nats_sub.unsubscribe(), self._loop)

    def publish(self, subject: str, data: dict[str, Any]) -> None:
        """Publish a JSON message to a NATS subject.

        Messages are dropped if not connected.

        Args:
            subject: Full NATS subject string.
            data: Dict to serialize as JSON.
        """
        if not self._nc or not self._connected or not self._loop:
            return
        payload = json.dumps(data).encode()
        asyncio.run_coroutine_threadsafe(
            self._nc.publish(subject, payload),
            self._loop,
        )

    def reply(self, reply_subject: str, data: dict[str, Any]) -> None:
        """Send a reply to a request/reply message.

        Args:
            reply_subject: The reply inbox subject from the inbound message.
            data: Dict to serialize as JSON.
        """
        if not reply_subject:
            return
        self.publish(reply_subject, data)
```

- [ ] **Step 4: Run topic builder tests**

Run: `pytest tests/ipc/test_service.py::TestTopicBuilder -v`
Expected: PASS

- [ ] **Step 5: Write tests for connection lifecycle**

Append to `tests/ipc/test_service.py`:

```python
class TestConnectionLifecycle:
    def test_start_does_nothing_when_url_empty(self) -> None:
        svc = IPCService(nats_url="", topic_prefix="test")
        svc.start()
        assert svc._thread is None
        assert not svc.is_connected

    def test_is_connected_initially_false(self) -> None:
        svc = IPCService(nats_url="nats://localhost:4222", topic_prefix="test")
        assert not svc.is_connected

    def test_stop_without_start_is_safe(self) -> None:
        svc = IPCService(nats_url="nats://localhost:4222", topic_prefix="test")
        svc.stop()  # Should not raise
```

- [ ] **Step 6: Run connection tests**

Run: `pytest tests/ipc/test_service.py::TestConnectionLifecycle -v`
Expected: PASS

- [ ] **Step 7: Write tests for subscribe and publish (mocked NATS)**

Append to `tests/ipc/test_service.py`:

```python
class TestSubscribePublish:
    def test_subscribe_stores_subscription(self) -> None:
        svc = IPCService(nats_url="nats://localhost:4222", topic_prefix="test")
        callback = MagicMock()
        svc.subscribe("test.foo", callback)
        assert "test.foo" in svc._subscriptions
        assert svc._subscriptions["test.foo"].callback is callback
        assert svc._subscriptions["test.foo"].main_thread is True

    def test_subscribe_background_dispatch(self) -> None:
        svc = IPCService(nats_url="nats://localhost:4222", topic_prefix="test")
        callback = MagicMock()
        svc.subscribe("test.foo", callback, main_thread=False)
        assert svc._subscriptions["test.foo"].main_thread is False

    def test_unsubscribe_removes_subscription(self) -> None:
        svc = IPCService(nats_url="nats://localhost:4222", topic_prefix="test")
        callback = MagicMock()
        svc.subscribe("test.foo", callback)
        svc.unsubscribe("test.foo")
        assert "test.foo" not in svc._subscriptions

    def test_unsubscribe_nonexistent_is_safe(self) -> None:
        svc = IPCService(nats_url="nats://localhost:4222", topic_prefix="test")
        svc.unsubscribe("nonexistent")  # Should not raise

    def test_publish_drops_when_not_connected(self) -> None:
        svc = IPCService(nats_url="nats://localhost:4222", topic_prefix="test")
        svc.publish("test.foo", {"key": "value"})  # Should not raise

    def test_reply_drops_when_no_reply_subject(self) -> None:
        svc = IPCService(nats_url="nats://localhost:4222", topic_prefix="test")
        svc.reply("", {"key": "value"})  # Should not raise
        svc.reply(None, {"key": "value"})  # Should not raise
```

- [ ] **Step 8: Run subscribe/publish tests**

Run: `pytest tests/ipc/test_service.py::TestSubscribePublish -v`
Expected: PASS

- [ ] **Step 9: Commit**

```bash
git add src/lightfall/ipc/service.py tests/ipc/test_service.py
git commit -m "feat(ipc): IPCService core — topic builder, connection, pub/sub"
```

---

### Task 3: Action & Event Catalogs + Meta Discovery

**Files:**
- Modify: `src/lightfall/ipc/service.py`
- Create: `tests/ipc/test_actions.py`

- [ ] **Step 1: Write tests for action/event registration and discovery**

Create `tests/ipc/test_actions.py`:

```python
"""Tests for IPC action/event registration and meta discovery."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from lightfall.ipc.service import ActionInfo, EventInfo, IPCService


class TestActionRegistration:
    def test_register_action_adds_to_catalog(self) -> None:
        svc = IPCService(nats_url="nats://localhost:4222", topic_prefix="test")
        callback = MagicMock()
        svc.register_action(
            "commands.plan.run",
            callback,
            description="Run a plan",
            schema={"plan_name": "str"},
        )
        assert "commands.plan.run" in svc._action_catalog
        info = svc._action_catalog["commands.plan.run"]
        assert info.description == "Run a plan"
        assert info.schema == {"plan_name": "str"}

    def test_register_action_creates_subscription(self) -> None:
        svc = IPCService(nats_url="nats://localhost:4222", topic_prefix="test")
        callback = MagicMock()
        svc.register_action("commands.plan.run", callback)
        assert "test.commands.plan.run" in svc._subscriptions

    def test_unregister_action_removes_catalog_and_subscription(self) -> None:
        svc = IPCService(nats_url="nats://localhost:4222", topic_prefix="test")
        callback = MagicMock()
        handle = svc.register_action("commands.plan.run", callback)
        handle.unregister()
        assert "commands.plan.run" not in svc._action_catalog
        assert "test.commands.plan.run" not in svc._subscriptions


class TestEventRegistration:
    def test_register_event_adds_to_catalog(self) -> None:
        svc = IPCService(nats_url="nats://localhost:4222", topic_prefix="test")
        svc.register_event(
            "runs.new",
            description="New run started",
            schema={"run_id": "str"},
        )
        assert "runs.new" in svc._event_catalog
        info = svc._event_catalog["runs.new"]
        assert info.description == "New run started"

    def test_register_event_does_not_create_subscription(self) -> None:
        svc = IPCService(nats_url="nats://localhost:4222", topic_prefix="test")
        svc.register_event("runs.new", description="New run started")
        assert "test.runs.new" not in svc._subscriptions


class TestMetaDiscovery:
    def test_list_actions(self) -> None:
        svc = IPCService(nats_url="nats://localhost:4222", topic_prefix="test")
        svc.register_action("commands.foo", MagicMock(), description="Do foo")
        svc.register_action("commands.bar", MagicMock(), description="Do bar")
        result = svc.list_actions()
        assert len(result) == 2
        subjects = {a["subject"] for a in result}
        assert subjects == {"commands.foo", "commands.bar"}

    def test_list_events(self) -> None:
        svc = IPCService(nats_url="nats://localhost:4222", topic_prefix="test")
        svc.register_event("runs.new", description="New run")
        svc.register_event("runs.complete", description="Run done")
        result = svc.list_events()
        assert len(result) == 2
        subjects = {e["subject"] for e in result}
        assert subjects == {"runs.new", "runs.complete"}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/ipc/test_actions.py -v`
Expected: FAIL — `register_action`, `register_event`, `list_actions`, `list_events` not defined.

- [ ] **Step 3: Implement action/event registration**

Add to `src/lightfall/ipc/service.py`, inside the `IPCService` class, after the reply method:

```python
    # -- Action & event catalogs --

    def register_action(
        self,
        suffix: str,
        callback: Callable[[str, dict, str | None], Any],
        *,
        description: str = "",
        schema: dict[str, Any] | None = None,
        main_thread: bool = True,
    ) -> _ActionHandle:
        """Register an IPC action — subscribes and adds to discoverable catalog.

        Uses the topic builder internally: the suffix is joined with the
        configured prefix to form the full NATS subject.

        Args:
            suffix: Subject suffix (e.g. "commands.plan.run").
            callback: Called with (subject, data_dict, reply_subject).
            description: Human-readable description for discovery.
            schema: Optional informational schema dict.
            main_thread: If True, callback dispatches to Qt main thread.

        Returns:
            Handle with an ``unregister()`` method.
        """
        subject = self.topic(suffix)
        self._action_catalog[suffix] = ActionInfo(
            subject=suffix,
            description=description,
            schema=schema,
        )
        self.subscribe(subject, callback, main_thread=main_thread)
        return _ActionHandle(self, suffix, subject)

    def register_event(
        self,
        suffix: str,
        *,
        description: str = "",
        schema: dict[str, Any] | None = None,
    ) -> None:
        """Register an outbound event for discoverability.

        Does NOT create a subscription — this only adds the event to the
        catalog so external clients can discover what Lightfall publishes.

        Args:
            suffix: Subject suffix (e.g. "runs.new").
            description: Human-readable description.
            schema: Optional informational schema dict.
        """
        self._event_catalog[suffix] = EventInfo(
            subject=suffix,
            description=description,
            schema=schema,
        )

    def list_actions(self) -> list[dict[str, Any]]:
        """Return the action catalog as a list of dicts."""
        return [
            {
                "subject": info.subject,
                "description": info.description,
                "schema": info.schema,
            }
            for info in self._action_catalog.values()
        ]

    def list_events(self) -> list[dict[str, Any]]:
        """Return the event catalog as a list of dicts."""
        return [
            {
                "subject": info.subject,
                "description": info.description,
                "schema": info.schema,
            }
            for info in self._event_catalog.values()
        ]

    def _handle_meta_actions(
        self, subject: str, data: dict, reply: str | None,
    ) -> None:
        """Built-in handler for meta.actions discovery."""
        if reply:
            self.reply(reply, {"actions": self.list_actions()})

    def _handle_meta_events(
        self, subject: str, data: dict, reply: str | None,
    ) -> None:
        """Built-in handler for meta.events discovery."""
        if reply:
            self.reply(reply, {"events": self.list_events()})

    def register_meta_endpoints(self) -> None:
        """Register the built-in meta.actions and meta.events discovery handlers.

        Called during service startup after connection is established.
        """
        self.register_action(
            "meta.actions",
            self._handle_meta_actions,
            description="List available IPC actions",
        )
        self.register_action(
            "meta.events",
            self._handle_meta_events,
            description="List outbound event topics Lightfall publishes",
        )
```

Add the `_ActionHandle` class before `IPCService`:

```python
class _ActionHandle:
    """Handle returned by register_action for unregistration."""

    def __init__(self, service: IPCService, suffix: str, subject: str) -> None:
        self._service = service
        self._suffix = suffix
        self._subject = subject

    def unregister(self) -> None:
        """Remove this action from the catalog and unsubscribe."""
        self._service._action_catalog.pop(self._suffix, None)
        self._service.unsubscribe(self._subject)
```

- [ ] **Step 4: Run tests**

Run: `pytest tests/ipc/test_actions.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/lightfall/ipc/service.py tests/ipc/test_actions.py
git commit -m "feat(ipc): action/event catalogs and meta discovery endpoints"
```

---

### Task 4: TrustManager + TrustDialog

**Files:**
- Create: `src/lightfall/ipc/trust.py`
- Create: `tests/ipc/test_trust.py`
- Modify: `src/lightfall/ipc/__init__.py`

- [ ] **Step 1: Write tests for TrustManager logic**

Create `tests/ipc/test_trust.py`:

```python
"""Tests for IPC TrustManager."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from lightfall.ipc.trust import TrustManager, TrustState


class TestTrustState:
    def test_initial_state_empty(self) -> None:
        mgr = TrustManager()
        assert not mgr.is_trusted("tsuchinoko")
        assert not mgr.is_denied("tsuchinoko")

    def test_approve_adds_to_trusted(self) -> None:
        mgr = TrustManager()
        mgr.approve("tsuchinoko")
        assert mgr.is_trusted("tsuchinoko")
        assert not mgr.is_denied("tsuchinoko")

    def test_deny_adds_to_denied(self) -> None:
        mgr = TrustManager()
        mgr.deny("tsuchinoko")
        assert mgr.is_denied("tsuchinoko")
        assert not mgr.is_trusted("tsuchinoko")

    def test_revoke_removes_from_trusted(self) -> None:
        mgr = TrustManager()
        mgr.approve("tsuchinoko")
        mgr.revoke("tsuchinoko")
        assert not mgr.is_trusted("tsuchinoko")

    def test_trusted_apps_returns_set(self) -> None:
        mgr = TrustManager()
        mgr.approve("tsuchinoko")
        mgr.approve("processor")
        assert mgr.trusted_apps == {"tsuchinoko", "processor"}

    def test_clear_resets_all(self) -> None:
        mgr = TrustManager()
        mgr.approve("tsuchinoko")
        mgr.deny("badapp")
        mgr.clear()
        assert not mgr.is_trusted("tsuchinoko")
        assert not mgr.is_denied("badapp")


class TestTrustDecision:
    def test_already_trusted_returns_approved(self) -> None:
        mgr = TrustManager()
        mgr.approve("tsuchinoko")
        state = mgr.check("tsuchinoko")
        assert state == TrustState.APPROVED

    def test_already_denied_returns_denied(self) -> None:
        mgr = TrustManager()
        mgr.deny("badapp")
        state = mgr.check("badapp")
        assert state == TrustState.DENIED

    def test_unknown_app_returns_unknown(self) -> None:
        mgr = TrustManager()
        state = mgr.check("newapp")
        assert state == TrustState.UNKNOWN
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/ipc/test_trust.py -v`
Expected: FAIL — module does not exist.

- [ ] **Step 3: Implement TrustManager**

Create `src/lightfall/ipc/trust.py`:

```python
"""Trust management for IPC auth token sharing."""

from __future__ import annotations

import enum
import threading
from typing import Any

from loguru import logger
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QDialog, QDialogButtonBox, QLabel, QVBoxLayout, QWidget


class TrustState(enum.Enum):
    """Result of checking trust status for an app."""

    UNKNOWN = "unknown"
    APPROVED = "approved"
    DENIED = "denied"


class TrustManager:
    """Manages the set of trusted/denied IPC applications.

    Trust state is session-scoped — cleared on Lightfall restart.
    """

    def __init__(self) -> None:
        self._trusted: set[str] = set()
        self._denied: set[str] = set()
        self._lock = threading.Lock()

    def check(self, app_name: str) -> TrustState:
        """Check the trust status of an application.

        Returns:
            TrustState.APPROVED if previously approved,
            TrustState.DENIED if previously denied,
            TrustState.UNKNOWN if never seen.
        """
        with self._lock:
            if app_name in self._trusted:
                return TrustState.APPROVED
            if app_name in self._denied:
                return TrustState.DENIED
            return TrustState.UNKNOWN

    def approve(self, app_name: str) -> None:
        """Mark an application as trusted for this session."""
        with self._lock:
            self._trusted.add(app_name)
            self._denied.discard(app_name)
        logger.info("IPC: trusted app '{}'", app_name)

    def deny(self, app_name: str) -> None:
        """Mark an application as denied for this session."""
        with self._lock:
            self._denied.add(app_name)
            self._trusted.discard(app_name)
        logger.info("IPC: denied app '{}'", app_name)

    def revoke(self, app_name: str) -> None:
        """Revoke trust for an application."""
        with self._lock:
            self._trusted.discard(app_name)
        logger.info("IPC: revoked trust for '{}'", app_name)

    @property
    def trusted_apps(self) -> set[str]:
        """Return the set of currently trusted app names."""
        with self._lock:
            return set(self._trusted)

    def is_trusted(self, app_name: str) -> bool:
        with self._lock:
            return app_name in self._trusted

    def is_denied(self, app_name: str) -> bool:
        with self._lock:
            return app_name in self._denied

    def clear(self) -> None:
        """Clear all trust state."""
        with self._lock:
            self._trusted.clear()
            self._denied.clear()


class TrustDialog(QDialog):
    """Dialog prompting the user to trust or deny an IPC application."""

    def __init__(
        self,
        app_name: str,
        app_version: str = "",
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("IPC Trust Request")
        self.setModal(True)

        layout = QVBoxLayout(self)

        version_str = f" v{app_version}" if app_version else ""
        label = QLabel(
            f"<b>{app_name}{version_str}</b> wants to connect to Lightfall.<br><br>"
            "Trust this application for this session?"
        )
        label.setWordWrap(True)
        layout.addWidget(label)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Yes | QDialogButtonBox.StandardButton.No,
        )
        buttons.button(QDialogButtonBox.StandardButton.Yes).setText("Trust for this session")
        buttons.button(QDialogButtonBox.StandardButton.No).setText("Deny")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        self.setMinimumWidth(350)
```

- [ ] **Step 4: Run trust tests**

Run: `pytest tests/ipc/test_trust.py -v`
Expected: PASS

- [ ] **Step 5: Update __init__.py exports**

Update `src/lightfall/ipc/__init__.py`:

```python
"""NATS-based inter-process communication for Lightfall."""

from lightfall.ipc.service import IPCService
from lightfall.ipc.trust import TrustDialog, TrustManager, TrustState

__all__ = ["IPCService", "TrustDialog", "TrustManager", "TrustState"]
```

- [ ] **Step 6: Commit**

```bash
git add src/lightfall/ipc/trust.py src/lightfall/ipc/__init__.py tests/ipc/test_trust.py
git commit -m "feat(ipc): TrustManager logic and TrustDialog UI"
```

---

### Task 5: Auth Handshake — Wire Trust into IPCService

**Files:**
- Modify: `src/lightfall/ipc/service.py`
- Modify: `tests/ipc/test_service.py`

- [ ] **Step 1: Write tests for auth handshake**

Append to `tests/ipc/test_service.py`:

```python
from lightfall.ipc.trust import TrustManager, TrustState


class TestAuthHandshake:
    def test_handle_auth_request_unknown_app_returns_unknown_state(self) -> None:
        """When trust state is UNKNOWN, service should not auto-respond."""
        svc = IPCService(nats_url="nats://localhost:4222", topic_prefix="test")
        trust = TrustManager()
        svc.set_trust_manager(trust)

        # UNKNOWN apps need user interaction — handle_auth_request returns
        # the trust state so the caller (with UI access) can show the dialog
        state = svc.evaluate_trust("newapp")
        assert state == TrustState.UNKNOWN

    def test_handle_auth_request_trusted_app_auto_approves(self) -> None:
        svc = IPCService(nats_url="nats://localhost:4222", topic_prefix="test")
        trust = TrustManager()
        trust.approve("tsuchinoko")
        svc.set_trust_manager(trust)

        state = svc.evaluate_trust("tsuchinoko")
        assert state == TrustState.APPROVED

    def test_handle_auth_request_denied_app_auto_denies(self) -> None:
        svc = IPCService(nats_url="nats://localhost:4222", topic_prefix="test")
        trust = TrustManager()
        trust.deny("badapp")
        svc.set_trust_manager(trust)

        state = svc.evaluate_trust("badapp")
        assert state == TrustState.DENIED

    def test_build_auth_response_approved(self) -> None:
        svc = IPCService(nats_url="nats://localhost:4222", topic_prefix="test")
        mock_session = MagicMock()
        mock_session.token = "test-token-123"

        resp = svc.build_auth_response(approved=True, session=mock_session, tiled_url="https://tiled.example.com")
        assert resp["status"] == "approved"
        assert resp["tiled_token"] == "test-token-123"
        assert resp["tiled_url"] == "https://tiled.example.com"

    def test_build_auth_response_denied(self) -> None:
        svc = IPCService(nats_url="nats://localhost:4222", topic_prefix="test")
        resp = svc.build_auth_response(approved=False)
        assert resp["status"] == "denied"
        assert "tiled_token" not in resp
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/ipc/test_service.py::TestAuthHandshake -v`
Expected: FAIL — `set_trust_manager`, `evaluate_trust`, `build_auth_response` not defined.

- [ ] **Step 3: Implement auth handshake methods**

Add to `IPCService.__init__`:

```python
        self._trust: TrustManager | None = None
```

Add import at top of `service.py`:

```python
from lightfall.ipc.trust import TrustManager, TrustState
```

Add methods to `IPCService`, after `register_meta_endpoints`:

```python
    # -- Trust & auth --

    def set_trust_manager(self, trust: TrustManager) -> None:
        """Set the trust manager for auth handshake."""
        self._trust = trust

    def evaluate_trust(self, app_name: str) -> TrustState:
        """Check trust state for an app.

        Returns TrustState.APPROVED/DENIED for known apps, UNKNOWN for new ones.
        Callers should show a TrustDialog for UNKNOWN and then call
        approve/deny on the TrustManager.
        """
        if self._trust is None:
            return TrustState.DENIED
        return self._trust.check(app_name)

    def build_auth_response(
        self,
        *,
        approved: bool,
        session: Any | None = None,
        tiled_url: str = "",
        reason: str = "",
    ) -> dict[str, Any]:
        """Build a JSON-serializable auth response dict.

        Args:
            approved: Whether the app was approved.
            session: Session object with .token attribute (if approved).
            tiled_url: Tiled server URL to include (if approved).
            reason: Denial reason (if denied).
        """
        if approved and session:
            return {
                "status": "approved",
                "tiled_token": session.token,
                "tiled_url": tiled_url,
            }
        resp: dict[str, Any] = {"status": "denied"}
        if reason:
            resp["reason"] = reason
        return resp
```

- [ ] **Step 4: Run tests**

Run: `pytest tests/ipc/test_service.py::TestAuthHandshake -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/lightfall/ipc/service.py tests/ipc/test_service.py
git commit -m "feat(ipc): auth handshake — trust evaluation and response building"
```

---

### Task 6: IPCSettingsPlugin

**Files:**
- Create: `src/lightfall/ui/preferences/ipc_settings.py`
- Create: `tests/ipc/test_settings.py`

- [ ] **Step 1: Write tests for settings load/save**

Create `tests/ipc/test_settings.py`:

```python
"""Tests for IPC settings plugin."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from lightfall.ui.preferences.ipc_settings import IPCSettingsPlugin


class TestIPCSettingsPlugin:
    def test_name(self) -> None:
        plugin = IPCSettingsPlugin()
        assert plugin.name == "ipc"

    def test_display_name(self) -> None:
        plugin = IPCSettingsPlugin()
        assert "IPC" in plugin.display_name or "ipc" in plugin.display_name.lower()

    def test_validate_empty_url_is_valid(self) -> None:
        """Empty URL means IPC disabled — that's valid."""
        plugin = IPCSettingsPlugin()
        widget = plugin.create_widget()
        plugin._url_edit.setText("")
        assert plugin.validate() == []

    def test_validate_valid_url(self) -> None:
        plugin = IPCSettingsPlugin()
        widget = plugin.create_widget()
        plugin._url_edit.setText("nats://localhost:4222")
        assert plugin.validate() == []

    def test_validate_bad_scheme(self) -> None:
        plugin = IPCSettingsPlugin()
        widget = plugin.create_widget()
        plugin._url_edit.setText("http://localhost:4222")
        errors = plugin.validate()
        assert len(errors) > 0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/ipc/test_settings.py -v`
Expected: FAIL — module does not exist.

- [ ] **Step 3: Implement IPCSettingsPlugin**

Create `src/lightfall/ui/preferences/ipc_settings.py`:

```python
"""IPC settings plugin for NATS connection configuration."""

from __future__ import annotations

from PySide6.QtWidgets import (
    QFormLayout,
    QGroupBox,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from lightfall.plugins.settings_plugin import SettingsPlugin


class IPCSettingsPlugin(SettingsPlugin):
    """Settings plugin for IPC / NATS configuration."""

    def __init__(self) -> None:
        self._widget: QWidget | None = None
        self._url_edit: QLineEdit | None = None
        self._prefix_edit: QLineEdit | None = None
        self._status_label: QLabel | None = None
        self._trusted_list: QListWidget | None = None
        self._revoke_btn: QPushButton | None = None

    @property
    def name(self) -> str:
        return "ipc"

    @property
    def display_name(self) -> str:
        return "IPC"

    @property
    def category(self) -> str:
        return "general"

    @property
    def priority(self) -> int:
        return 80

    def create_widget(self, parent: QWidget | None = None) -> QWidget:
        widget = QWidget(parent)
        layout = QVBoxLayout(widget)

        # Connection group
        conn_group = QGroupBox("NATS Connection")
        conn_layout = QFormLayout(conn_group)

        self._url_edit = QLineEdit()
        self._url_edit.setPlaceholderText("nats://broker.als.lbl.gov:4222")
        conn_layout.addRow("Server URL:", self._url_edit)

        self._prefix_edit = QLineEdit()
        self._prefix_edit.setPlaceholderText("als.7011")
        conn_layout.addRow("Topic prefix:", self._prefix_edit)

        self._status_label = QLabel("Disconnected")
        conn_layout.addRow("Status:", self._status_label)

        layout.addWidget(conn_group)

        # Trusted apps group
        trust_group = QGroupBox("Trusted Applications")
        trust_layout = QVBoxLayout(trust_group)

        self._trusted_list = QListWidget()
        trust_layout.addWidget(self._trusted_list)

        self._revoke_btn = QPushButton("Revoke Selected")
        self._revoke_btn.clicked.connect(self._on_revoke)
        trust_layout.addWidget(self._revoke_btn)

        layout.addWidget(trust_group)

        layout.addStretch()
        self._widget = widget
        return widget

    def load_settings(self) -> None:
        from lightfall.ui.preferences.manager import PreferencesManager

        prefs = PreferencesManager.get_instance()
        if self._url_edit:
            self._url_edit.setText(prefs.get("ipc_nats_url", ""))
        if self._prefix_edit:
            self._prefix_edit.setText(prefs.get("ipc_topic_prefix", "als.7011"))

    def save_settings(self) -> None:
        from lightfall.ui.preferences.manager import PreferencesManager

        prefs = PreferencesManager.get_instance()
        if self._url_edit:
            prefs.set("ipc_nats_url", self._url_edit.text())
        if self._prefix_edit:
            prefs.set("ipc_topic_prefix", self._prefix_edit.text())

    def validate(self) -> list[str]:
        errors: list[str] = []
        if self._url_edit:
            url = self._url_edit.text().strip()
            if url and not url.startswith("nats://"):
                errors.append("NATS URL must start with nats://")
        return errors

    def _on_revoke(self) -> None:
        """Revoke trust for the selected app."""
        if not self._trusted_list:
            return
        selected = self._trusted_list.currentItem()
        if selected:
            app_name = selected.text()
            self._trusted_list.takeItem(self._trusted_list.row(selected))
            # Actual revocation happens via IPCService/TrustManager
            # which the preferences dialog will wire up
```

- [ ] **Step 4: Run tests**

Run: `pytest tests/ipc/test_settings.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/lightfall/ui/preferences/ipc_settings.py tests/ipc/test_settings.py
git commit -m "feat(ipc): IPCSettingsPlugin with NATS URL, prefix, and trusted apps UI"
```

---

### Task 7: Application Wiring — Manifest, ServiceRegistry, Lifecycle

**Files:**
- Modify: `src/lightfall/plugins/builtin_manifest.py`
- Modify: `src/lightfall/core/application.py`

- [ ] **Step 1: Read current builtin_manifest.py**

Read `src/lightfall/plugins/builtin_manifest.py` to find the exact location and format
for adding the IPC settings plugin entry.

- [ ] **Step 2: Add IPC settings to builtin manifest**

Add a `PluginEntry` to the `builtin_manifest.plugins` list:

```python
        PluginEntry(
            type_name="settings",
            name="ipc",
            import_path="lightfall.ui.preferences.ipc_settings:IPCSettingsPlugin",
        ),
```

- [ ] **Step 3: Read current application.py**

Read `src/lightfall/core/application.py` to find where services are registered
and where shutdown hooks run.

- [ ] **Step 4: Register IPCService in application startup**

In `NCSApplication`, after other services are registered during initialization,
add IPCService registration:

```python
from lightfall.ipc.service import IPCService
from lightfall.ipc.trust import TrustManager

# In the initialization method, after service registration:
trust_manager = TrustManager()
self._services.register(TrustManager, lambda: trust_manager)
self._services.register(IPCService, lambda: self._create_ipc_service(trust_manager))
```

Add the factory method:

```python
def _create_ipc_service(self, trust_manager: TrustManager) -> IPCService:
    """Create and configure the IPC service from preferences."""
    from lightfall.ui.preferences.manager import PreferencesManager

    prefs = PreferencesManager.get_instance()
    nats_url = prefs.get("ipc_nats_url", "")
    topic_prefix = prefs.get("ipc_topic_prefix", "als.7011")

    svc = IPCService(nats_url=nats_url, topic_prefix=topic_prefix)
    svc.set_trust_manager(trust_manager)
    svc.register_meta_endpoints()
    return svc
```

- [ ] **Step 5: Start IPC service after app enters RUNNING state**

In the `run()` method or equivalent post-initialization hook:

```python
# After main window is shown and app is RUNNING:
ipc = self._services.get(IPCService)
ipc.start()
```

- [ ] **Step 6: Stop IPC service on shutdown**

In the shutdown method:

```python
# During shutdown, before services are cleared:
try:
    ipc = self._services.get(IPCService, None)
    if ipc:
        ipc.stop()
except Exception:
    logger.exception("Error stopping IPC service")
```

- [ ] **Step 7: Wire auth request handler**

The auth request handler needs UI access (trust dialog), so it should be
registered in the application layer. Add to the IPC setup:

```python
def _handle_ipc_auth_request(self, subject: str, data: dict, reply: str | None) -> None:
    """Handle incoming auth trust requests from external apps."""
    if not reply:
        return

    ipc = self._services.get(IPCService)
    app_name = data.get("app_name", "unknown")
    app_version = data.get("app_version", "")
    trust = self._services.get(TrustManager)

    state = ipc.evaluate_trust(app_name)

    if state == TrustState.APPROVED:
        session = self._get_current_session()
        tiled_url = self._get_tiled_url()
        ipc.reply(reply, ipc.build_auth_response(
            approved=True, session=session, tiled_url=tiled_url,
        ))
        return

    if state == TrustState.DENIED:
        ipc.reply(reply, ipc.build_auth_response(approved=False))
        return

    # Unknown app — show trust dialog
    from lightfall.ipc.trust import TrustDialog
    dialog = TrustDialog(app_name, app_version, parent=self._main_window)
    # Use a 60-second auto-close timer
    from PySide6.QtCore import QTimer
    timer = QTimer()
    timer.setSingleShot(True)
    timer.timeout.connect(dialog.reject)
    timer.start(60_000)

    if dialog.exec() == TrustDialog.DialogCode.Accepted:
        trust.approve(app_name)
        session = self._get_current_session()
        tiled_url = self._get_tiled_url()
        ipc.reply(reply, ipc.build_auth_response(
            approved=True, session=session, tiled_url=tiled_url,
        ))
    else:
        trust.deny(app_name)
        ipc.reply(reply, ipc.build_auth_response(
            approved=False, reason="denied" if timer.isActive() else "timeout",
        ))
```

Register this handler during IPC setup:

```python
ipc.register_action("auth.request", self._handle_ipc_auth_request, description="Trust handshake + token sharing")
```

- [ ] **Step 8: Commit**

```bash
git add src/lightfall/plugins/builtin_manifest.py src/lightfall/core/application.py
git commit -m "feat(ipc): wire IPCService into application lifecycle and manifest"
```

---

### Task 8: BlueskyEngine IPC Integration

**Files:**
- Modify: `src/lightfall/acquire/engine/bluesky.py`
- Create: `tests/ipc/test_integration.py`

- [ ] **Step 1: Write tests for engine IPC integration**

Create `tests/ipc/test_integration.py`:

```python
"""Tests for IPC integration with Lightfall components."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from lightfall.ipc.service import IPCService


class TestEngineIPCIntegration:
    def test_run_event_published_on_start(self) -> None:
        """Verify IPC publishes run.new when engine starts a run."""
        svc = IPCService(nats_url="nats://localhost:4222", topic_prefix="test")
        svc.publish = MagicMock()

        # Simulate what the engine integration code does
        from lightfall.acquire.engine.bluesky import _publish_run_started
        _publish_run_started(svc, "test-run-id", "count")
        svc.publish.assert_called_once_with(
            "test.runs.new",
            {"run_id": "test-run-id", "plan_name": "count"},
        )

    def test_complete_event_published_on_finish(self) -> None:
        svc = IPCService(nats_url="nats://localhost:4222", topic_prefix="test")
        svc.publish = MagicMock()

        from lightfall.acquire.engine.bluesky import _publish_run_completed
        _publish_run_completed(svc, "test-run-id", "success")
        svc.publish.assert_called_once_with(
            "test.runs.complete",
            {"run_id": "test-run-id", "exit_status": "success"},
        )
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/ipc/test_integration.py::TestEngineIPCIntegration -v`
Expected: FAIL — helper functions not defined.

- [ ] **Step 3: Read current BlueskyEngine implementation**

Read `src/lightfall/acquire/engine/bluesky.py` to find the exact signal emission
points and `_execute_plan` method.

- [ ] **Step 4: Add IPC helper functions to bluesky.py**

Add module-level helper functions at the bottom of `bluesky.py`:

```python
def _publish_run_started(ipc: Any, run_id: str, plan_name: str) -> None:
    """Publish a runs.new event via IPC."""
    ipc.publish(ipc.topic("runs.new"), {
        "run_id": run_id,
        "plan_name": plan_name,
    })


def _publish_run_completed(ipc: Any, run_id: str, exit_status: str) -> None:
    """Publish a runs.complete event via IPC."""
    ipc.publish(ipc.topic("runs.complete"), {
        "run_id": run_id,
        "exit_status": exit_status,
    })
```

- [ ] **Step 5: Wire IPC into engine signals**

Add IPC event publishing to the engine's initialization or a setup method
that is called after IPCService is available. This should be done in the
application wiring layer (e.g. `application.py`) to avoid a hard dependency
from engine to IPC:

```python
# In application setup, after both engine and IPC are available:
def _wire_engine_ipc(self) -> None:
    from lightfall.acquire.engine import get_engine
    from lightfall.acquire.engine.bluesky import _publish_run_started, _publish_run_completed

    engine = get_engine()
    ipc = self._services.get(IPCService)

    # Track current run ID from start documents
    current_run = {}

    def on_output(name: str, doc: dict) -> None:
        if name == "start":
            run_id = doc.get("uid", "")
            plan_name = doc.get("plan_name", "unknown")
            current_run["uid"] = run_id
            current_run["plan_name"] = plan_name
            _publish_run_started(ipc, run_id, plan_name)

    def on_finish() -> None:
        run_id = current_run.get("uid", "")
        _publish_run_completed(ipc, run_id, "success")

    def on_abort() -> None:
        run_id = current_run.get("uid", "")
        _publish_run_completed(ipc, run_id, "abort")

    def on_exception(exc: Exception) -> None:
        run_id = current_run.get("uid", "")
        _publish_run_completed(ipc, run_id, "error")

    engine.sigOutput.connect(on_output)
    engine.sigFinish.connect(on_finish)
    engine.sigAbort.connect(on_abort)
    engine.sigException.connect(on_exception)

    # Register state change event
    def on_state_changed(state: str) -> None:
        ipc.publish(ipc.topic("state.engine"), {"state": state})

    engine.sigStateChanged.connect(on_state_changed)

    # Register outbound events in catalog
    ipc.register_event("runs.new", description="Fired when a new run starts",
                       schema={"run_id": "str", "plan_name": "str"})
    ipc.register_event("runs.complete", description="Fired when a run finishes",
                       schema={"run_id": "str", "exit_status": "str"})
    ipc.register_event("state.engine", description="Engine state change",
                       schema={"state": "str"})
```

- [ ] **Step 6: Register plan command handlers**

Add command handlers for `commands.plan.run` and `commands.plan.abort`:

```python
def _wire_plan_commands(self) -> None:
    from lightfall.acquire.engine import get_engine

    ipc = self._services.get(IPCService)
    engine = get_engine()

    def handle_plan_run(subject: str, data: dict, reply: str | None) -> None:
        plan_name = data.get("plan_name")
        params = data.get("params", {})
        if not plan_name:
            if reply:
                ipc.reply(reply, {"error": True, "message": "plan_name is required"})
            return

        # Look up plan by name from plugin registry
        from lightfall.plugins.loader import PluginLoader
        loader = PluginLoader.get_instance()
        plan_info = loader.get_plugin_by_name("plan", plan_name)
        if not plan_info or not plan_info.instance:
            if reply:
                ipc.reply(reply, {"error": True, "message": f"Plan '{plan_name}' not found"})
            return

        try:
            plan_gen = plan_info.instance.create_plan(**params)
            engine.submit(plan_gen, name=plan_name)
            if reply:
                ipc.reply(reply, {"status": "submitted", "plan_name": plan_name})
        except Exception as exc:
            if reply:
                ipc.reply(reply, {"error": True, "message": str(exc)})

    def handle_plan_abort(subject: str, data: dict, reply: str | None) -> None:
        try:
            engine.abort()
            if reply:
                ipc.reply(reply, {"status": "abort_requested"})
        except Exception as exc:
            if reply:
                ipc.reply(reply, {"error": True, "message": str(exc)})

    ipc.register_action("commands.plan.run", handle_plan_run,
                        description="Submit a plan to the BlueskyEngine",
                        schema={"plan_name": "str", "params": "dict"})
    ipc.register_action("commands.plan.abort", handle_plan_abort,
                        description="Abort the active run")
```

- [ ] **Step 7: Run tests**

Run: `pytest tests/ipc/test_integration.py::TestEngineIPCIntegration -v`
Expected: PASS

- [ ] **Step 8: Commit**

```bash
git add src/lightfall/acquire/engine/bluesky.py src/lightfall/core/application.py tests/ipc/test_integration.py
git commit -m "feat(ipc): BlueskyEngine integration — plan commands and run events"
```

---

### Task 9: Logbook + Claude Agent IPC Integration

**Files:**
- Modify: `src/lightfall/core/application.py`
- Modify: `tests/ipc/test_integration.py`

- [ ] **Step 1: Write tests for logbook IPC**

Append to `tests/ipc/test_integration.py`:

```python
class TestLogbookIPCIntegration:
    def test_logbook_add_calls_create_entry_and_add_fragment(self) -> None:
        svc = IPCService(nats_url="nats://localhost:4222", topic_prefix="test")
        svc.reply = MagicMock()

        mock_client = MagicMock()
        mock_client.active_logbook_id = "lb-123"
        mock_client.create_entry.return_value = "entry-456"

        data = {"title": "Test Entry", "content": "Hello from IPC"}

        # Simulate the handler
        from lightfall.core.application import _handle_logbook_add
        _handle_logbook_add(mock_client, svc, "test.commands.logbook.add", data, "_INBOX.reply")

        mock_client.create_entry.assert_called_once_with("lb-123", title="Test Entry")
        mock_client.add_fragment.assert_called_once_with("entry-456", content="Hello from IPC")
        svc.reply.assert_called_once()
        reply_data = svc.reply.call_args[0][1]
        assert reply_data["status"] == "created"
        assert reply_data["entry_id"] == "entry-456"


class TestAgentIPCIntegration:
    def test_agent_message_calls_query_sync(self) -> None:
        svc = IPCService(nats_url="nats://localhost:4222", topic_prefix="test")
        svc.reply = MagicMock()

        mock_agent = MagicMock()
        data = {"message": "What devices are available?"}

        from lightfall.core.application import _handle_agent_message
        _handle_agent_message(mock_agent, svc, "test.commands.agent.message", data, "_INBOX.reply")

        mock_agent.query_sync.assert_called_once_with("What devices are available?")
        svc.reply.assert_called_once()
        reply_data = svc.reply.call_args[0][1]
        assert reply_data["status"] == "sent"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/ipc/test_integration.py -k "Logbook or Agent" -v`
Expected: FAIL — handler functions not defined.

- [ ] **Step 3: Implement handler functions**

Add module-level handler functions to `src/lightfall/core/application.py`:

```python
def _handle_logbook_add(
    client: Any,
    ipc: Any,
    subject: str,
    data: dict,
    reply: str | None,
) -> None:
    """IPC handler for commands.logbook.add."""
    title = data.get("title")
    content = data.get("content", "")
    tags = data.get("tags")

    logbook_id = client.active_logbook_id
    if not logbook_id:
        if reply:
            ipc.reply(reply, {"error": True, "message": "No active logbook"})
        return

    entry_id = client.create_entry(logbook_id, title=title, tags=tags)
    if content:
        client.add_fragment(entry_id, content=content)

    if reply:
        ipc.reply(reply, {"status": "created", "entry_id": entry_id})


def _handle_agent_message(
    agent: Any,
    ipc: Any,
    subject: str,
    data: dict,
    reply: str | None,
) -> None:
    """IPC handler for commands.agent.message."""
    message = data.get("message", "")
    if not message:
        if reply:
            ipc.reply(reply, {"error": True, "message": "message is required"})
        return

    agent.query_sync(message)
    if reply:
        ipc.reply(reply, {"status": "sent"})
```

- [ ] **Step 4: Wire handlers in application setup**

In the application's IPC wiring method:

```python
def _wire_logbook_ipc(self) -> None:
    from lightfall.logbook.client import LogbookClient

    ipc = self._services.get(IPCService)
    client = LogbookClient.get_instance()

    ipc.register_action(
        "commands.logbook.add",
        lambda subj, data, reply: _handle_logbook_add(client, ipc, subj, data, reply),
        description="Add entry to active logbook",
        schema={"title": "str", "content": "str", "tags": "list[str]"},
    )


def _wire_agent_ipc(self) -> None:
    from lightfall.claude.agent import QtClaudeAgent

    ipc = self._services.get(IPCService)

    # Agent may not be available immediately — wire when it exists
    def register_when_ready(agent: QtClaudeAgent) -> None:
        ipc.register_action(
            "commands.agent.message",
            lambda subj, data, reply: _handle_agent_message(agent, ipc, subj, data, reply),
            description="Send message to Claude agent",
            schema={"message": "str"},
        )

    # Check if agent already exists, or defer
    # This depends on agent lifecycle — adapt to actual initialization pattern
    register_when_ready(self._agent)
```

- [ ] **Step 5: Run tests**

Run: `pytest tests/ipc/test_integration.py -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add src/lightfall/core/application.py tests/ipc/test_integration.py
git commit -m "feat(ipc): logbook and Claude agent IPC command handlers"
```

---

### Task 10: Documentation

**Files:**
- Create: `docs/ipc-architecture.md`
- Create: `docs/ipc-client-guide.md`

- [ ] **Step 1: Write internal architecture doc**

Create `docs/ipc-architecture.md`:

```markdown
# IPC Architecture — Internal Guide

## Overview

Lightfall uses NATS as a message broker for inter-process communication.
The `IPCService` singleton manages the NATS connection, dispatches
inbound messages to registered callbacks, and publishes outbound events.

## Components

### IPCService (`lightfall.ipc.service`)

Singleton registered in `ServiceRegistry`. Manages:

- **NATS connection** on a background thread with its own asyncio event loop
- **Subscriptions** with automatic main-thread dispatch
- **Action catalog** — registered inbound commands, discoverable via `meta.actions`
- **Event catalog** — registered outbound events, discoverable via `meta.events`
- **Topic builder** — `ipc.topic(suffix)` joins the configured prefix

### TrustManager (`lightfall.ipc.trust`)

Session-scoped trust state for external applications. Apps are:
- **UNKNOWN** on first contact → user sees a TrustDialog
- **APPROVED** for the session → auto-approved on subsequent requests
- **DENIED** for the session → auto-denied, no repeat prompts

### IPCSettingsPlugin (`lightfall.ui.preferences.ipc_settings`)

User-facing config: NATS URL, topic prefix, trusted app list with revoke.

## Registering a New Action

```python
from lightfall.core.services import ServiceRegistry
from lightfall.ipc.service import IPCService

ipc = ServiceRegistry.get_instance().get(IPCService)

def my_handler(subject: str, data: dict, reply: str | None) -> None:
    result = do_something(data)
    if reply:
        ipc.reply(reply, {"status": "ok", "result": result})

ipc.register_action(
    "commands.myfeature.do_thing",
    my_handler,
    description="Does a thing",
    schema={"param": "str"},
)
```

## Registering a New Outbound Event

```python
ipc.register_event(
    "myfeature.status",
    description="Status update from my feature",
    schema={"status": "str"},
)

# Later, when the event occurs:
ipc.publish(ipc.topic("myfeature.status"), {"status": "done"})
```

## Threading

- IPCService runs nats-py on a background thread with a dedicated asyncio loop
- Callbacks registered with `main_thread=True` (default) are dispatched to the
  Qt main thread via `invoke_in_main_thread()`
- Use `main_thread=False` for callbacks that don't touch the UI
- `publish()` and `reply()` are thread-safe — they schedule onto the async loop

## Auth Token Sharing

1. External app sends NATS request to `{prefix}.auth.request`
2. `NCSApplication` handles it — checks TrustManager state
3. Unknown apps trigger a TrustDialog (60s timeout)
4. Approved: reply includes Tiled token + URL on ephemeral inbox
5. Clients re-request when their token expires — auto-approved if still trusted
```

- [ ] **Step 2: Write external client integration guide**

Create `docs/ipc-client-guide.md`:

```markdown
# IPC Client Integration Guide

Connect your application to Lightfall over the beamline NATS bus.

## Prerequisites

- NATS server running on the beamline network
- TLS CA certificate (for verifying the NATS server)
- Python: `pip install nats-py` (or equivalent client for your language)

## Connecting

```python
import nats

nc = await nats.connect("nats://broker.als.lbl.gov:4222", tls_required=True)
```

## Authentication — Getting a Tiled Token

Before you can access experimental data from Tiled, request trust from Lightfall:

```python
import json

response = await nc.request(
    "als.7011.auth.request",
    json.dumps({"app_name": "my-app", "app_version": "1.0.0"}).encode(),
    timeout=90,  # User has 60s to respond + network margin
)
result = json.loads(response.data)

if result["status"] == "approved":
    tiled_token = result["tiled_token"]
    tiled_url = result["tiled_url"]
    print(f"Approved! Tiled at {tiled_url}")
else:
    print(f"Denied: {result.get('reason', 'user declined')}")
```

The Lightfall user will see a dialog asking if they trust your app. Once approved,
you stay trusted for the Lightfall session.

### Token Refresh

Tiled tokens expire. When you get a 401 from Tiled, re-request:

```python
response = await nc.request(
    "als.7011.auth.request",
    json.dumps({"app_name": "my-app", "app_version": "1.0.0"}).encode(),
    timeout=10,
)
result = json.loads(response.data)
# Already trusted — no dialog, instant response
tiled_token = result["tiled_token"]
```

## Discovering Available Actions and Events

```python
# What commands can I send?
resp = await nc.request("als.7011.meta.actions", b"", timeout=5)
actions = json.loads(resp.data)["actions"]
for a in actions:
    print(f"  {a['subject']}: {a['description']}")

# What events does Lightfall publish?
resp = await nc.request("als.7011.meta.events", b"", timeout=5)
events = json.loads(resp.data)["events"]
for e in events:
    print(f"  {e['subject']}: {e['description']}")
```

## Sending Commands

### Run a Plan

```python
resp = await nc.request(
    "als.7011.commands.plan.run",
    json.dumps({"plan_name": "count", "params": {"num": 10}}).encode(),
    timeout=10,
)
result = json.loads(resp.data)
if result.get("error"):
    print(f"Error: {result['message']}")
else:
    print(f"Plan submitted: {result['plan_name']}")
```

### Abort Current Run

```python
resp = await nc.request(
    "als.7011.commands.plan.abort",
    b"{}",
    timeout=10,
)
```

### Add a Logbook Entry

```python
resp = await nc.request(
    "als.7011.commands.logbook.add",
    json.dumps({
        "title": "Automated measurement note",
        "content": "Sample alignment completed at 14:30",
        "tags": ["automated", "alignment"],
    }).encode(),
    timeout=10,
)
```

### Send a Message to the Claude Agent

```python
resp = await nc.request(
    "als.7011.commands.agent.message",
    json.dumps({"message": "What is the current beam energy?"}).encode(),
    timeout=30,
)
```

## Subscribing to Events

```python
async def on_new_run(msg):
    data = json.loads(msg.data)
    print(f"New run: {data['run_id']} (plan: {data['plan_name']})")
    # Pull data from Tiled using the run_id and your token

async def on_run_complete(msg):
    data = json.loads(msg.data)
    print(f"Run {data['run_id']} finished: {data['exit_status']}")

await nc.subscribe("als.7011.runs.new", cb=on_new_run)
await nc.subscribe("als.7011.runs.complete", cb=on_run_complete)
await nc.subscribe("als.7011.state.engine", cb=lambda msg: print(json.loads(msg.data)))
```

## Complete Example — Tsuchinoko-Style Client

```python
"""Example: subscribe to run events and submit measurement targets."""

import asyncio
import json
import nats


async def main():
    nc = await nats.connect("nats://broker.als.lbl.gov:4222", tls_required=True)

    # 1. Request trust + Tiled token
    resp = await nc.request(
        "als.7011.auth.request",
        json.dumps({"app_name": "tsuchinoko", "app_version": "2.0.0"}).encode(),
        timeout=90,
    )
    auth = json.loads(resp.data)
    if auth["status"] != "approved":
        print("Trust denied — exiting")
        return

    tiled_token = auth["tiled_token"]
    tiled_url = auth["tiled_url"]
    print(f"Authenticated with Tiled at {tiled_url}")

    # 2. Subscribe to run completions
    async def on_run_complete(msg):
        data = json.loads(msg.data)
        run_id = data["run_id"]
        print(f"Run {run_id} completed — fetching data from Tiled...")
        # Use tiled_token to pull data:
        #   from tiled.client import from_uri
        #   client = from_uri(tiled_url, api_key=tiled_token)
        #   run = client[run_id]

    await nc.subscribe("als.7011.runs.complete", cb=on_run_complete)

    # 3. Submit a measurement plan
    resp = await nc.request(
        "als.7011.commands.plan.run",
        json.dumps({
            "plan_name": "adaptive_scan",
            "params": {"target_x": 42.0, "target_y": 7.5},
        }).encode(),
        timeout=10,
    )
    result = json.loads(resp.data)
    print(f"Plan submitted: {result}")

    # Keep running to receive events
    try:
        while True:
            await asyncio.sleep(1)
    except KeyboardInterrupt:
        await nc.drain()


asyncio.run(main())
```

## Message Format

All messages are JSON-encoded UTF-8 bytes. Standard fields:

| Field | Type | Description |
|-------|------|-------------|
| `error` | bool | Present and `true` on error responses |
| `message` | str | Error description (when `error` is true) |
| `status` | str | Operation status (e.g. "approved", "submitted", "created") |

Heavy data (images, spectra, etc.) should go through Tiled, not IPC.
IPC messages carry identifiers and metadata only.

## Topic Hierarchy

```
{prefix}.auth.request           — trust handshake
{prefix}.meta.actions           — list available commands
{prefix}.meta.events            — list published events
{prefix}.commands.plan.run      — run a bluesky plan
{prefix}.commands.plan.abort    — abort active run
{prefix}.commands.logbook.add   — add logbook entry
{prefix}.commands.agent.message — message Claude agent
{prefix}.runs.new               — (event) new run started
{prefix}.runs.complete          — (event) run finished
{prefix}.state.engine           — (event) engine state change
```

Default prefix: `als.7011` (configurable per deployment).
```

- [ ] **Step 3: Commit documentation**

```bash
git add docs/ipc-architecture.md docs/ipc-client-guide.md
git commit -m "docs(ipc): internal architecture and external client integration guide"
```

---

## Summary

| Task | Description | Key Files |
|------|-------------|-----------|
| 1 | Package skeleton + dependency | `pyproject.toml`, `src/lightfall/ipc/__init__.py` |
| 2 | IPCService core — topic builder, connection, pub/sub | `src/lightfall/ipc/service.py` |
| 3 | Action/event catalogs + meta discovery | `src/lightfall/ipc/service.py` |
| 4 | TrustManager + TrustDialog | `src/lightfall/ipc/trust.py` |
| 5 | Auth handshake wired into IPCService | `src/lightfall/ipc/service.py` |
| 6 | IPCSettingsPlugin | `src/lightfall/ui/preferences/ipc_settings.py` |
| 7 | Application wiring — manifest, lifecycle, auth handler | `application.py`, `builtin_manifest.py` |
| 8 | BlueskyEngine integration — events + commands | `bluesky.py`, `application.py` |
| 9 | Logbook + Claude agent commands | `application.py` |
| 10 | Documentation | `docs/ipc-*.md` |
