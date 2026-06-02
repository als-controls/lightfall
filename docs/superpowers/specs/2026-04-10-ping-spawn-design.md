# Ping/Spawn Design for Export

## Overview

Add request/reply capability to Lightfall's IPCService and use it to ping the local exporter before sending jobs. If no exporter is running, spawn one on demand.

## Goals

- Make the export flow seamless — user clicks Export, it just works
- Add `request()` to IPCService as a reusable primitive
- Spawn a local exporter subprocess if none is detected
- Log spawn errors for debuggability, keep toast messages clean

## Non-Goals

- Remote exporter management (spawn on other hosts)
- Exporter health monitoring (continuous ping loop)
- Restart logic for crashed exporters

## IPCService.request()

New method on `IPCService`:

```python
def request(self, subject: str, data: dict, timeout_ms: int = 1000) -> dict | None:
```

- Thread-safe: callable from the Qt main thread
- Submits a NATS `nc.request()` via `asyncio.run_coroutine_threadsafe` on the IPC background thread
- Waits up to `timeout_ms` for a reply
- Returns the decoded JSON reply dict, or `None` on timeout or error
- Logs warnings on timeout, errors on exceptions

Internally delegates to an async helper:

```python
async def _do_request(self, subject: str, payload: bytes, timeout: float) -> bytes | None:
    try:
        msg = await self._nc.request(subject, payload, timeout=timeout)
        return msg.data
    except nats.errors.TimeoutError:
        return None
```

## Export Dialog: Ping-Then-Send Flow

Replace the current fire-and-forget `ipc.publish()` in `_send_to_exporter` with a background thread that:

1. Ping the local exporter: `ipc.request(ping_subject, {}, timeout_ms=1000)`
2. **If reply received** → exporter is running, proceed to step 6
3. **If None** → spawn exporter subprocess:
   ```python
   proc = subprocess.Popen(
       ["lightfall-exporter", "--nats", nats_url],
       stderr=subprocess.PIPE,
       stdout=subprocess.DEVNULL,
   )
   ```
4. Retry ping up to 4 more times (1s timeout each, ~5s total wait)
5. **If still no reply** → read `proc.stderr`, log it via `logger.error("Exporter failed to start: %s", stderr_text)`. Show toast: "Could not start exporter". Return.
6. **Send the job** via `ipc.publish(job_subject, message)`
7. Show "Export queued" toast, subscribe to progress

The entire flow runs in a `QThreadFuture` so the dialog (already closed via `self.accept()`) doesn't block. The toast/subscribe callbacks are marshalled back to the main thread via `callback_slot`.

## NATS URL Discovery

The export dialog needs to know the NATS URL to pass to the spawned exporter. It reads this from the existing IPCService instance: `ipc._nats_url`. This is a private attribute but acceptable since the dialog and IPC service are both internal Lightfall code.

## No Exporter Changes

The exporter already handles ping requests on `lightfall.export.<hostname>.ping` (implemented in Task 3). No changes needed.

## Testing

- **IPCService.request():** Unit test with mocked NATS client — verify it calls `nc.request`, handles timeout (returns None), handles success (returns decoded dict)
- **Ping/spawn flow:** Unit test with mocked IPCService — verify ping success sends job directly, ping failure triggers Popen, repeated ping failure shows error toast
