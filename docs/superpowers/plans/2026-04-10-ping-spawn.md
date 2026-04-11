# Ping/Spawn Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add request/reply to IPCService and use it to auto-detect/spawn the local exporter before sending export jobs.

**Architecture:** IPCService gets a new `request()` method wrapping NATS request/reply via `asyncio.run_coroutine_threadsafe`. The export dialog's `_send_to_exporter` is replaced with a ping-then-send flow that runs in a `QThreadFuture` — ping, spawn if needed, retry, then send or fail.

**Tech Stack:** nats-py, asyncio, subprocess, PySide6 (QThreadFuture)

**Spec:** `docs/superpowers/specs/2026-04-10-ping-spawn-design.md`

---

## File Structure

### Modified Files

| File | Changes |
|------|---------|
| `src/lucid/ipc/service.py` | Add `request()` method and async `_do_request()` helper |
| `src/lucid/ui/dialogs/export_dialog.py` | Replace `_send_to_exporter` with ping/spawn flow |
| `tests/ipc/test_service.py` | Add tests for `request()` |
| `tests/test_export_dialog.py` | Add tests for ping/spawn logic |

---

## Task 1: IPCService.request()

**Files:**
- Modify: `src/lucid/ipc/service.py:381-401` (add after `publish`, before `reply`)
- Modify: `tests/ipc/test_service.py` (add TestRequest class)

- [ ] **Step 1: Write failing tests for request()**

Append to `tests/ipc/test_service.py`:

```python
class TestRequest:
    def test_request_returns_none_when_not_connected(self, qapp):
        svc = IPCService(nats_url="nats://localhost:4222", topic_prefix="test")
        # Never started, so not connected
        result = svc.request("test.ping", {})
        assert result is None

    def test_request_returns_none_when_no_loop(self, qapp):
        svc = IPCService(nats_url="nats://localhost:4222", topic_prefix="test")
        # Manually set connected but no loop
        with svc._connected_lock:
            svc._connected = True
        svc._loop = None
        result = svc.request("test.ping", {})
        assert result is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd ~/PycharmProjects/ncs/ncs && .venv/Scripts/python -m pytest tests/ipc/test_service.py::TestRequest -v`
Expected: FAIL — `AttributeError: 'IPCService' object has no attribute 'request'`

- [ ] **Step 3: Implement request() and _do_request()**

In `src/lucid/ipc/service.py`, add these two methods after `publish()` (around line 393) and before `reply()`:

```python
    def request(
        self, subject: str, data: dict, timeout_ms: int = 1000
    ) -> dict | None:
        """Send a request and wait for a reply.

        Thread-safe — can be called from any thread including the Qt main
        thread.  Blocks the *calling* thread for up to *timeout_ms*.

        Args:
            subject: NATS subject to send the request to.
            data: JSON-serialisable request payload.
            timeout_ms: Maximum time to wait for a reply in milliseconds.

        Returns:
            Decoded JSON reply dict, or ``None`` on timeout / error /
            not connected.
        """
        if not self.is_connected or self._loop is None or self._nc is None:
            return None

        payload = json.dumps(data).encode()
        timeout = timeout_ms / 1000.0

        future = asyncio.run_coroutine_threadsafe(
            self._do_request(payload, subject, timeout), self._loop
        )
        try:
            result = future.result(timeout=timeout + 1.0)
            return result
        except Exception as exc:
            logger.warning("IPCService: request to '{}' failed: {}", subject, exc)
            return None

    async def _do_request(
        self, payload: bytes, subject: str, timeout: float
    ) -> dict | None:
        """Async helper — execute NATS request/reply."""
        if self._nc is None:
            return None
        try:
            msg = await self._nc.request(subject, payload, timeout=timeout)
            return json.loads(msg.data.decode())
        except asyncio.TimeoutError:
            logger.debug("IPCService: request to '{}' timed out", subject)
            return None
        except Exception as exc:
            logger.warning("IPCService: request error on '{}': {}", subject, exc)
            return None
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd ~/PycharmProjects/ncs/ncs && .venv/Scripts/python -m pytest tests/ipc/test_service.py::TestRequest -v`
Expected: PASS (2 tests)

- [ ] **Step 5: Run all IPC tests for regressions**

Run: `cd ~/PycharmProjects/ncs/ncs && .venv/Scripts/python -m pytest tests/ipc/test_service.py -v`
Expected: All existing tests still pass

- [ ] **Step 6: Commit**

```bash
cd ~/PycharmProjects/ncs/ncs
git add src/lucid/ipc/service.py tests/ipc/test_service.py
git commit -m "feat(ipc): add request/reply method to IPCService"
```

---

## Task 2: Export Dialog Ping/Spawn Flow

**Files:**
- Modify: `src/lucid/ui/dialogs/export_dialog.py` (replace `_send_to_exporter`)
- Modify: `tests/test_export_dialog.py` (add ping/spawn tests)

- [ ] **Step 1: Write failing tests for ping/spawn logic**

The ping/spawn logic will be extracted as a pure function `ping_or_spawn_exporter` for testability. Append to `tests/test_export_dialog.py`:

```python
from unittest.mock import patch, MagicMock

from lucid.ui.dialogs.export_dialog import ping_or_spawn_exporter


class TestPingOrSpawnExporter:
    def test_ping_success_returns_true(self):
        """If exporter responds to ping, return True without spawning."""
        ipc = MagicMock()
        ipc.request.return_value = {"hostname": "test", "status": "ready"}

        result = ping_or_spawn_exporter(
            ipc=ipc,
            ping_subject="lucid.export.test.ping",
            nats_url="nats://localhost:4222",
        )
        assert result is True
        ipc.request.assert_called_once()

    @patch("lucid.ui.dialogs.export_dialog.subprocess.Popen")
    def test_ping_fail_spawns_then_retries(self, mock_popen):
        """If first ping fails, spawn exporter, retry pings."""
        ipc = MagicMock()
        # First ping fails, second succeeds (after spawn)
        ipc.request.side_effect = [None, {"hostname": "test", "status": "ready"}]

        mock_proc = MagicMock()
        mock_proc.poll.return_value = None  # still running
        mock_popen.return_value = mock_proc

        result = ping_or_spawn_exporter(
            ipc=ipc,
            ping_subject="lucid.export.test.ping",
            nats_url="nats://localhost:4222",
        )
        assert result is True
        mock_popen.assert_called_once()
        assert ipc.request.call_count == 2

    @patch("lucid.ui.dialogs.export_dialog.subprocess.Popen")
    def test_all_pings_fail_returns_false(self, mock_popen):
        """If all pings fail after spawn, return False."""
        ipc = MagicMock()
        ipc.request.return_value = None  # all pings fail

        mock_proc = MagicMock()
        mock_proc.poll.return_value = 1  # process exited
        mock_proc.stderr = MagicMock()
        mock_proc.stderr.read.return_value = b"Connection refused"
        mock_popen.return_value = mock_proc

        result = ping_or_spawn_exporter(
            ipc=ipc,
            ping_subject="lucid.export.test.ping",
            nats_url="nats://localhost:4222",
        )
        assert result is False
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd ~/PycharmProjects/ncs/ncs && .venv/Scripts/python -m pytest tests/test_export_dialog.py::TestPingOrSpawnExporter -v`
Expected: FAIL — `ImportError: cannot import name 'ping_or_spawn_exporter'`

- [ ] **Step 3: Implement ping_or_spawn_exporter and update _send_to_exporter**

In `src/lucid/ui/dialogs/export_dialog.py`, add `import subprocess` to the imports at the top (after `import platform`), then add this function before the `ExportDialog` class:

```python
import subprocess


MAX_PING_RETRIES = 4
PING_TIMEOUT_MS = 1000


def ping_or_spawn_exporter(
    ipc: Any,
    ping_subject: str,
    nats_url: str,
) -> bool:
    """Ping the local exporter; spawn one if not running.

    Callable from any thread (uses IPCService.request which is thread-safe).

    Args:
        ipc: IPCService instance.
        ping_subject: NATS subject for the exporter's ping endpoint.
        nats_url: NATS URL to pass to the spawned exporter.

    Returns:
        True if the exporter is reachable, False if all retries failed.
    """
    # Try initial ping
    reply = ipc.request(ping_subject, {}, timeout_ms=PING_TIMEOUT_MS)
    if reply is not None:
        return True

    # No response — spawn a local exporter
    logger.info("No exporter running, spawning lucid-exporter")
    try:
        proc = subprocess.Popen(
            ["lucid-exporter", "--nats", nats_url],
            stderr=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
        )
    except FileNotFoundError:
        logger.error("lucid-exporter not found on PATH")
        return False

    # Retry pings
    for i in range(MAX_PING_RETRIES):
        reply = ipc.request(ping_subject, {}, timeout_ms=PING_TIMEOUT_MS)
        if reply is not None:
            logger.info("Exporter responded after spawn (attempt %d)", i + 1)
            return True

        # Check if process died
        if proc.poll() is not None:
            stderr_text = proc.stderr.read().decode(errors="replace") if proc.stderr else ""
            logger.error("Exporter process exited (code %d): %s", proc.returncode, stderr_text)
            return False

    logger.error("Exporter did not respond after %d retries", MAX_PING_RETRIES)
    return False
```

Now replace the `_send_to_exporter` method in the `ExportDialog` class:

```python
    def _send_to_exporter(self, message: dict[str, Any]) -> None:
        """Send the export job to the local exporter via NATS IPC.

        Pings the exporter first. If no response, spawns a local instance.
        The ping/spawn/send flow runs in a background thread.
        """
        from lucid.core.services import NCSApplication
        from lucid.ui.toast import ToastManager
        from lucid.utils.threads import QThreadFuture

        toast = ToastManager.get_instance()
        app = NCSApplication.get_instance()
        ipc = getattr(app, "_ipc_service", None)

        if ipc is None:
            toast.error("Export Error", "IPC service not available")
            return

        hostname = platform.node()
        job_subject = f"lucid.export.{hostname}"
        progress_subject = f"lucid.export.{hostname}.progress"
        ping_subject = f"lucid.export.{hostname}.ping"
        nats_url = ipc._nats_url

        job_id = message["job_id"]

        def _on_progress(subject: str, data: dict, reply: str | None) -> None:
            if data.get("job_id") != job_id:
                return
            status = data.get("status", "")
            detail = data.get("detail", "")
            current = data.get("current_run", 0)
            total = data.get("total_runs", 0)

            if status == "processing":
                toast.info("Exporting", f"Run {current}/{total}: {detail}")
            elif status == "completed":
                toast.success("Export Complete", detail)
                ipc.unsubscribe(progress_subject)
            elif status == "failed":
                toast.error("Export Failed", detail)
                ipc.unsubscribe(progress_subject)

        def _ping_and_send() -> bool:
            """Background thread: ping/spawn exporter, then send job."""
            if not ping_or_spawn_exporter(ipc, ping_subject, nats_url):
                return False
            ipc.subscribe(progress_subject, _on_progress)
            ipc.publish(job_subject, message)
            return True

        def _on_send_result(success: bool) -> None:
            if success:
                toast.info("Export Queued", f"{len(message['run_uids'])} run(s) queued for export")
                logger.info("Export job {} sent to {}", job_id, job_subject)
            else:
                toast.error("Export Error", "Could not start exporter")

        def _on_send_error(exc: Exception) -> None:
            toast.error("Export Error", str(exc))
            logger.error("Export send failed: {}", exc)

        thread = QThreadFuture(
            _ping_and_send,
            callback_slot=_on_send_result,
            except_slot=_on_send_error,
            name="export_ping_send",
        )
        thread.start()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd ~/PycharmProjects/ncs/ncs && .venv/Scripts/python -m pytest tests/test_export_dialog.py -v`
Expected: PASS (5 tests — 2 existing build_job_message + 3 new ping/spawn)

- [ ] **Step 5: Run all export-related tests**

Run: `cd ~/PycharmProjects/ncs/ncs && .venv/Scripts/python -m pytest tests/exporter/ tests/test_export_dialog.py -v`
Expected: All pass

- [ ] **Step 6: Commit**

```bash
cd ~/PycharmProjects/ncs/ncs
git add src/lucid/ui/dialogs/export_dialog.py tests/test_export_dialog.py
git commit -m "feat(export): add ping/spawn flow to auto-start local exporter"
```

---

## Summary

| Task | Description | Files Modified | Tests |
|------|-------------|---------------|-------|
| 1 | IPCService.request() | `ipc/service.py`, `tests/ipc/test_service.py` | 2 new |
| 2 | Export dialog ping/spawn | `ui/dialogs/export_dialog.py`, `tests/test_export_dialog.py` | 3 new |

**Total:** 4 files modified, 5 new tests
