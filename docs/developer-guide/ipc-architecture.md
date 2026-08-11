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

Trust is scoped to the current **login session**, not the process lifetime. `_wire_session_trust()`
connects `SessionManager.state_changed`: whenever the state transitions to `UNAUTHENTICATED` (Lightfall
logout), it calls `TrustManager.clear()` and `IPCService.teardown_session_channels()` together — every
app's trust decision is forgotten and every live capability channel is torn down in the same step.
A client that authenticated in a previous login session finds its channel dead after the next logout
and must re-run `auth.request` once a new session starts.

### TrustDialog (`lightfall.ipc.trust`)

A modal Qt dialog shown when an `UNKNOWN` app requests authentication. It has a 60-second
auto-reject timer set by the `_handle_ipc_auth_request` handler in `LFApplication`.
The user sees the app name and version and chooses "Trust for this session" or "Deny".

### RemoteControlService (`lightfall.remote.service`)

`RemoteControlService` owns the remote-control action surface: plan, queue, engine, and device
actions, plus the run-lifecycle broadcast events (`runs.new`, `runs.complete`, `state.engine`). It
is registered in `ServiceRegistry` and wired up in `_start_ipc()` via `_wire_remote_control()`
(replacing the old `_wire_engine_ipc` / `_wire_plan_commands` inline handlers).

Every action it registers passes `trusted=True` — the handlers themselves never gate on
authentication or trust state. Enforcement is centralized in `IPCService` (see the "Capability
Channels" section below); `RemoteControlService` only has to implement the action semantics.

### IPCSettingsPlugin (`lightfall.ui.preferences.ipc_settings`)

A `SettingsPlugin` that exposes IPC configuration in Lightfall's Preferences dialog under the
**General > IPC** category. It reads and writes three `PreferencesManager` keys:

| Key                | Type  | Default                                       | Description                          |
|--------------------|-------|-----------------------------------------------|--------------------------------------|
| `ipc_nats_url`     | `str` | `"nats://bcgnats.als.private.lbl.gov:4222"`   | Full NATS broker URL                 |
| `ipc_topic_prefix` | `str` | `"als.7011"`                                  | Prefix prepended to all subjects     |
| `ipc_display_name` | `str` | `""`                                          | Human-readable instance name shown to discovery clients |

The plugin also displays a live connection status label and a "Trusted Applications" list with a
"Revoke Selected" button (backed by the in-memory `TrustManager`).

## Capability Channels

NATS core messages carry no sender identity — any process that can reach the broker can publish
to any subject. Because of this, the per-session **private subject** minted after a successful
auth handshake *is* the authentication mechanism for command traffic, not an optional add-on.

- **Minting**: when `auth.request` is approved (a previously-`UNKNOWN` app is approved via the
  `TrustDialog`, or a previously-`APPROVED` app re-authenticates), `IPCService.build_auth_response()`
  calls `mint_session_channel(app_name)`. This generates a `session_token` with
  `secrets.token_urlsafe(32)`, subscribes the wildcard subject `{prefix}.session.{token}.>`, and
  returns the token in the approved reply.
- **Routing**: every command travels on `{prefix}.session.{session_token}.<suffix>` (e.g.
  `als.7011.session.<token>.commands.plan.run`). `IPCService._route_session_message` resolves the
  token from the subject, validates the `contract_version`, attaches
  `data["_identity"] = {"app_name", "session_token"}`, and dispatches to the trusted action handler
  registered for `<suffix>`.
- **Bare subjects are rejected**: an action registered with `register_action(..., trusted=True)` is
  *only* reachable through its capability channel. A request on the bare, un-channeled subject
  (e.g. `als.7011.commands.plan.run`) hits `IPCService._reject_untrusted`, which replies with a
  structured `denied` error rather than executing the action.
- **Broadcast events are the documented exception**: `runs.new`, `runs.complete`, and `state.engine`
  are published on public, un-prefixed-by-session subjects (`{prefix}.runs.new`, etc.). They carry
  no secrets and are meant for multiple simultaneous listeners, so they are not gated by a
  capability channel.
- **Teardown**: session channels die on logout (`_wire_session_trust`, see the TrustManager section
  above) or on a single app's trust revocation (`teardown_session_channels(app_name=...)` unsubscribes
  just that app's wildcard and invalidates its token).
- **Production posture**: the session token authenticates the *client* to Lightfall — it proves the
  holder completed a real handshake. It does **not**, by itself, stop a hostile peer already on the
  same broker from *subscribing* to another session's subject and observing traffic (NATS core has
  no subject-level ACL of its own). On bcgnats, that isolation comes from broker-side subject
  permissions configured per connecting user (operational NATS config, out of scope for this
  client-side contract). The bundled `LocalNatsServer` (used for local/offline development) runs
  **plaintext and unenforced** — do not point external, untrusted clients at it.

## Wiring and Lifecycle

`LFApplication` owns both the `TrustManager` and the `IPCService`, registering them in
`ServiceRegistry` during `_register_core_services()`. The IPC service is not started until
`run()` is called (after the main window is visible), at which point `_start_ipc()`:

1. Calls `ipc.start()` to open the background NATS thread.
2. Registers `auth.request` for the trust handshake.
3. Calls `_wire_remote_control()`, which constructs `RemoteControlService` and calls `.start()`.
   This registers `commands.plan.list`, `commands.plan.run`, `commands.plan.abort`,
   `commands.queue.get`, `commands.engine.status`, and `commands.device.search/components/info/get/put`
   as trusted actions, and publishes `runs.new`, `runs.complete`, and `state.engine` off engine
   signals.
4. Registers `commands.logbook.add` (now `trusted=True`).
5. Registers `commands.agent.message` (now `trusted=True`).
6. Calls `_wire_session_trust()` to tie capability-channel teardown to logout.

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

### RemoteControlService's executor model

`RemoteControlService` registers all of its actions with `main_thread=False`, so its handlers run
directly on the `ipc-nats` thread. Rather than doing the work there, each handler (except
`plan.abort`) immediately submits the actual work to a small internal `ThreadPoolExecutor`
(`max_workers=4`) via `_dispatch()`, and replies from that worker thread once done. This keeps both
the NATS event loop and the Qt main thread free while a device read/write or plan submission is in
flight; a handler that raises is caught in `_dispatch()` and turned into a structured `unknown`
error reply instead of leaving the client hanging.

`plan.abort` is the one exception: it marshals to the Qt main thread via `invoke_in_main_thread`,
matching how the UI itself calls `engine.abort()` — abort must run wherever the engine expects to
be driven from.

Engine Qt signals (`sigOutput`, `sigFinish`, `sigAbort`, `sigException`, `sigStateChanged`) are
connected with the default queued behavior and therefore fire on the main thread; executor worker
threads that need to observe engine state changes (e.g. waiting for a run's start document) do so
via `threading.Event` objects set from those main-thread signal handlers, not by touching Qt objects
directly from the worker.

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
     │                                        mint_session_channel(app_name)
     │                                             │
     │◀── {status: "approved",                     │
     │     session_token: "...",                   │
     │     tiled_token: "...",                     │
     │     tiled_url: "...",                       │
     │     session_id: "...",                      │
     │     contract_version: 1}  ──────────────────│
     │                                             │
     │ (or "denied" / "timeout" if rejected)       │
     │                                             │
     │  subsequent commands travel on the          │
     │  capability channel:                        │
     │──── {prefix}.session.{session_token}        │
     │        .commands.plan.run {...} ───────────▶│
     │◀─── {status: "submitted", ...,              │
     │      contract_version: 1}  ─────────────────│
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
    trusted=True,
)

# Later, to remove the action:
# handle.unregister()
```

The full NATS subject will be `{topic_prefix}.commands.myfeature.do`
(e.g. `als.7011.commands.myfeature.do`).

Pass `trusted=True` for any action a remote client is meant to invoke *after* completing the auth
handshake — which in practice means essentially every `commands.*` action. With `trusted=True`,
the action is unreachable on its bare subject (requests there get a structured `denied` reply); it
is only invoked through a minted session capability channel
(`{topic_prefix}.session.{session_token}.commands.myfeature.do`), and the callback receives
`data["_identity"] = {"app_name", "session_token"}` identifying the caller. Omit `trusted=True` (the
default) only for endpoints that must be reachable pre-handshake, such as `auth.request` itself or
the `meta.*` discovery endpoints.

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

## Structured Errors

`lightfall.remote.protocol` defines the reply shapes for the remote-control contract (v1). Every
reply — success or error — carries `contract_version: 1` so clients can detect a protocol mismatch.

An error reply has the shape:

```json
{"status": "error", "code": "busy", "message": "Engine is busy and behavior is 'reject'", "contract_version": 1}
```

`code` is one of the following (`lightfall.remote.protocol.ERROR_CODES`):

| Code               | Meaning                                                        |
|---------------------|-----------------------------------------------------------------|
| `busy`              | Engine/queue state conflicts with the requested behavior       |
| `limits`            | Value out of range or signal is read-only                      |
| `timeout`           | An operation (e.g. `device.put` wait) did not complete in time |
| `unknown`           | Unknown plan/device/signal, or an unhandled handler exception  |
| `denied`            | Missing/invalid capability channel, or bare `commands.*` access|
| `bad_request`       | Malformed or missing request fields                            |
| `version_mismatch`  | Request `contract_version` does not match the server's         |

This structured form **supersedes** the earlier ad hoc `{"error": true, "message": ...}` shape used
before the remote-control contract existed; new handlers should build error replies with
`lightfall.remote.protocol.error_reply(code, message)` rather than hand-rolling `{"error": true}`.

## Configuration

Configuration is stored in `PreferencesManager` and read at service creation time:

| Key                | Default                                     | Notes                                           |
|--------------------|---------------------------------------------|-------------------------------------------------|
| `ipc_nats_url`     | `"nats://bcgnats.als.private.lbl.gov:4222"` | Set to an empty string to disable IPC (`start()` becomes a no-op) |
| `ipc_topic_prefix` | `"als.7011"`                                | Prepended to every published/subscribed subject |
| `ipc_display_name` | `""`                                        | Optional instance name for discovery replies    |

Changes to these preferences take effect on the next Lightfall restart; the service is not
dynamically reconfigured at runtime.
