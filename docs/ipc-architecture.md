# IPC Architecture

Lightfall uses [NATS](https://nats.io/) as a message broker for inter-process communication (IPC).
External tools — scientific clients, automation scripts, agent processes — connect to the same NATS
server and exchange JSON messages with Lightfall over well-defined subjects.

## Components

### IPCService (`lightfall.ipc.service`)

`IPCService` is the central IPC object. It owns the NATS connection, manages subscriptions, and
exposes a catalog of registered actions and events.

Key responsibilities:

- **Connection lifecycle** — `start()` opens a TLS connection on a background daemon thread;
  `stop()` drains and closes it.
- **Topic builder** — `topic(suffix)` prepends the configured `topic_prefix`
  (e.g. `"als.7011"`) to form the full NATS subject.
- **Action catalog** — `register_action(suffix, callback)` subscribes to a request/reply subject
  and records the action in a catalog that can be discovered via `meta.actions`.
- **Event catalog** — `register_event(suffix)` records an outbound event without creating a
  subscription. Discoverable via `meta.events`.
- **Meta-discovery** — `register_meta_endpoints()` automatically registers the `meta.actions` and
  `meta.events` request/reply endpoints so clients can enumerate all available actions and events.
- **Auth helpers** — `set_trust_manager()`, `evaluate_trust()`, and `build_auth_response()` support
  the trust handshake flow.
- **Qt signal** — `sigConnectionChanged(bool)` is emitted on the Qt main thread whenever the
  connection state changes.

### TrustManager (`lightfall.ipc.trust`)

`TrustManager` is a thread-safe, session-scoped store of application trust decisions. It tracks
three states per app name:

| State     | Meaning                                       |
|-----------|-----------------------------------------------|
| `UNKNOWN` | Never seen before; prompt the user            |
| `APPROVED`| Trusted for the current Lightfall session         |
| `DENIED`  | Explicitly blocked for the current session    |

Decisions persist only in memory — they reset when Lightfall restarts.

Relevant methods: `approve(app_name)`, `deny(app_name)`, `revoke(app_name)`, `check(app_name)`.

### TrustDialog (`lightfall.ipc.trust`)

A modal Qt dialog shown when an `UNKNOWN` app requests authentication. It has a 60-second
auto-reject timer set by the `_handle_ipc_auth_request` handler in `NCSApplication`.
The user sees the app name and version and chooses "Trust for this session" or "Deny".

### IPCSettingsPlugin (`lightfall.ui.preferences.ipc_settings`)

A `SettingsPlugin` that exposes IPC configuration in Lightfall's Preferences dialog under the
**General > IPC** category. It reads and writes two `PreferencesManager` keys:

| Key                | Type  | Default              | Description                         |
|--------------------|-------|----------------------|-------------------------------------|
| `ipc_nats_url`     | `str` | `""`                 | Full NATS broker URL                |
| `ipc_topic_prefix` | `str` | `"als.7011"`         | Prefix prepended to all subjects    |

The plugin also displays a live connection status label and a "Trusted Applications" list with a
"Revoke Selected" button (backed by the in-memory `TrustManager`).

## Wiring and Lifecycle

`NCSApplication` owns both the `TrustManager` and the `IPCService`, registering them in
`ServiceRegistry` during `_register_core_services()`. The IPC service is not started until
`run()` is called (after the main window is visible), at which point `_start_ipc()`:

1. Calls `ipc.start()` to open the background NATS thread.
2. Registers `auth.request` for the trust handshake.
3. Wires engine signals to `runs.new`, `runs.complete`, and `state.engine` events.
4. Registers `commands.plan.run` and `commands.plan.abort` action handlers.
5. Registers `commands.logbook.add`.
6. Registers `commands.agent.message`.

On `_shutdown()`, `ipc.stop()` is called before the service registry is cleared, ensuring pending
NATS messages are drained before exit.

## Threading Model

`IPCService` runs a dedicated asyncio event loop on a background daemon thread named `ipc-nats`.
All NATS I/O happens on that thread. When a message arrives:

- If the subscription was registered with `main_thread=True` (the default), the callback is
  dispatched to the Qt main thread via `invoke_in_main_thread`.
- If registered with `main_thread=False`, the callback runs directly on the NATS thread.
  Use this only for callbacks that are thread-safe and do not touch Qt objects.

Publishing (`ipc.publish`) and replying (`ipc.reply`) are safe to call from any thread; they use
`asyncio.run_coroutine_threadsafe` to submit the I/O to the background loop.

## Auth Token Sharing Flow

```
External client                              Lightfall (main thread)
     │                                             │
     │──── auth.request {app_name, app_version} ──▶│
     │                                             │ evaluate_trust(app_name)
     │                                             │
     │           if UNKNOWN:                       │
     │                                        show TrustDialog (60 s timeout)
     │                                             │
     │           user clicks "Trust"               │
     │                                        trust.approve(app_name)
     │                                             │
     │◀── {status: "approved",                     │
     │     tiled_token: "...",                     │
     │     tiled_url: "..."}  ─────────────────────│
     │
     │ (or "denied" / "timeout" if rejected)
```

A previously approved app receives the token immediately without a dialog. A previously denied app
receives `{status: "denied"}` immediately.

## Registering a New Action

Actions are request/reply subjects. Use `ServiceRegistry` to obtain the `IPCService` and call
`register_action`. Provide a suffix (relative to the configured topic prefix), a callback, and
optional metadata for discovery.

```python
from lightfall.core.services import ServiceRegistry
from lightfall.ipc.service import IPCService

def handle_my_action(subject: str, data: dict, reply: str | None) -> None:
    # data is the decoded JSON payload
    result = do_something(data.get("param"))
    if reply:
        ipc.reply(reply, {"status": "ok", "result": result})

ipc = ServiceRegistry.get_instance().get(IPCService)
handle = ipc.register_action(
    "commands.myfeature.do",
    handle_my_action,
    description="Do something useful",
    schema={"param": "str"},
    # main_thread=True is the default — callback runs on Qt main thread
)

# Later, to remove the action:
# handle.unregister()
```

The full NATS subject will be `{topic_prefix}.commands.myfeature.do`
(e.g. `als.7011.commands.myfeature.do`).

## Registering a New Outbound Event

Events are fire-and-forget publishes. Register the event in the catalog first (so clients can
discover it via `meta.events`), then publish whenever the relevant state changes.

```python
from lightfall.core.services import ServiceRegistry
from lightfall.ipc.service import IPCService

ipc = ServiceRegistry.get_instance().get(IPCService)

# Register once at startup — creates the catalog entry only, no subscription
ipc.register_event(
    "myfeature.changed",
    description="Fired when myfeature state changes",
    schema={"state": "str"},
)

# Publish whenever the state changes (call from any thread)
ipc.publish(ipc.topic("myfeature.changed"), {"state": "active"})
```

## Configuration

Configuration is stored in `PreferencesManager` and read at service creation time:

| Key                | Default      | Notes                                         |
|--------------------|--------------|-----------------------------------------------|
| `ipc_nats_url`     | `""`         | Empty string disables IPC (`start()` is no-op)|
| `ipc_topic_prefix` | `"als.7011"` | Prepended to every published/subscribed subject |

Changes to these preferences take effect on the next Lightfall restart; the service is not
dynamically reconfigured at runtime.
