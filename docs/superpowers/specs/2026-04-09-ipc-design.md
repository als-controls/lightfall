# IPC Design — NATS-Based Inter-Process Communication

## Motivation

LUCID needs to communicate with external tools in a distributed beamline
environment. Use cases include:

- **Tsuchinoko** sending measurement targets and receiving notifications when
  new data is acquired
- **Data processing apps** subscribing to run IDs and pulling data from Tiled
- **Configuration tools** controlling LUCID actions remotely
- **Claude agent** receiving messages from external services

The vision is a beamline-wide message bus where LUCID is one participant among
many — not the center. Components communicate openly through a shared broker
using topic-based pub/sub and request/reply patterns.

## Transport: NATS

NATS was chosen over MQTT, ZMQ, Kafka, RabbitMQ, and Redis after evaluating:

- **Native request/reply**: NATS has built-in request/reply with ephemeral inbox
  subjects. MQTT would require building correlated response topics on top of
  pure pub/sub.
- **Deployment simplicity**: Single Go binary, minimal config. Easier than
  Mosquitto (ACL files, password tooling), RabbitMQ (Erlang), or Kafka (JVM).
- **Topic hierarchy**: Subject-based routing with wildcards (`als.7011.>`),
  similar to MQTT topics. Maps naturally to beamline/endstation namespaces.
- **Security**: TLS native, per-subject permissions available if needed later.
- **Python client**: `nats-py` is pure Python (`py3-none-any` wheel), async-native,
  zero external dependencies. Bundles cleanly with Briefcase.
- **Multi-language clients**: Official clients for Go, Rust, JS/TS, Java, C#, C,
  Ruby, Python.

## Architecture Overview

### IPCService

A singleton registered in `ServiceRegistry`. Owns the NATS connection and
provides subscription, publishing, action registration, and trust management.

```
┌─────────────────────────────────────────────────────┐
│ LUCID                                               │
│                                                     │
│  ┌─────────────┐    ┌──────────────────────────┐    │
│  │ Settings     │───▶│ IPCService               │    │
│  │ Plugin       │    │                          │    │
│  └─────────────┘    │  NATS connection          │    │
│                     │  Action catalog           │    │
│  ┌─────────────┐    │  Event catalog            │    │
│  │ BlueskyEngine│──▶│  Trust manager            │    │
│  │             │◀──│  Topic builder             │    │
│  └─────────────┘    └──────────┬───────────────┘    │
│                                │                     │
│  ┌─────────────┐               │  TLS                │
│  │ Logbook     │──▶           │                     │
│  │ Client      │◀──           │                     │
│  └─────────────┘               │                     │
│                                │                     │
│  ┌─────────────┐               │                     │
│  │ Claude Agent│──▶           │                     │
│  └─────────────┘               │                     │
└────────────────────────────────┼─────────────────────┘
                                 │
                          ┌──────┴──────┐
                          │ NATS Broker │
                          └──────┬──────┘
                                 │
              ┌──────────────────┼──────────────────┐
              │                  │                   │
        ┌─────┴─────┐   ┌──────┴──────┐   ┌───────┴───────┐
        │ Tsuchinoko │   │ Data        │   │ Config        │
        │            │   │ Processor   │   │ Tool          │
        └────────────┘   └─────────────┘   └───────────────┘
```

### Connection Management

- Connects on startup when `nats_url` is configured (non-empty)
- TLS always enabled — auth tokens transit the wire
- Auto-reconnect with backoff (nats-py native behavior)
- Connection status exposed via `sigConnectionChanged` Qt signal
- LUCID operates normally when NATS is unreachable — IPC is optional
- No NATS credentials required — TLS for transport security, trust prompts
  for application-level auth

### Threading Model

- IPCService runs a dedicated asyncio event loop on a background thread
- nats-py async operations (connect, subscribe, publish, request) run on
  this loop
- Inbound message callbacks dispatch to the **Qt main thread** via
  `invoke_in_main_thread()` by default
- Components can opt into background dispatch with `main_thread=False`

Implementation should evaluate `lucid.utils.threads` (`QThreadFuture`,
`QThreadFutureIterator`, `invoke_in_main_thread`) as threading primitives
rather than rolling new ones.

### Graceful Shutdown

- On app exit, IPCService drains subscriptions and closes the NATS connection
- Registered callbacks are not invoked after shutdown begins

## Configuration

### IPCSettingsPlugin

```python
class IPCSettingsPlugin(SettingsPlugin):
    nats_url: str = ""         # empty = IPC disabled
    topic_prefix: str = "als.7011"
```

- `nats_url`: NATS server address (e.g. `nats://broker.als.lbl.gov:4222`).
  Empty string disables IPC entirely.
- `topic_prefix`: Namespace for this beamline/endstation. Used by `ipc.topic()`
  helper. Convention: `{facility}.{beamline}`.

### UI Surface

- Settings panel with fields for NATS URL and topic prefix
- Connection status indicator (connected / disconnected / reconnecting)
- "Trusted Apps" section showing session-approved apps with revoke option

## Subscription & Publishing API

### Subscribing

```python
# Direct subject string — always supported
ipc.subscribe("als.7011.commands.plan.run", callback)

# Topic helper — joins configured prefix with suffix
ipc.subscribe(ipc.topic("commands.plan.run"), callback)

# Registered action — subscribes AND adds to discoverable catalog
ipc.register_action(
    "commands.plan.run",
    callback,
    description="Run a bluesky plan",
    schema={"plan_name": "str", "params": "dict"},
)

# Background dispatch (callback won't be marshaled to main thread)
ipc.register_action("commands.plan.run", callback, main_thread=False)

# Unsubscribe
ipc.unsubscribe("als.7011.commands.plan.run")
```

### Publishing

```python
# Fire and forget
ipc.publish(ipc.topic("runs.new"), {"run_id": "abc-123", "plan_name": "count"})

# Request/reply
response = await ipc.request(ipc.topic("auth.request"), {"app": "tsuchinoko"})
```

### Topic Builder

`ipc.topic(suffix)` joins the configured `topic_prefix` with the suffix using
a `.` separator. It is a convenience — direct subject strings are always
accepted by all API methods.

## Action Registration & Discovery

### Registration

Components register IPC actions during initialization. `register_action`
takes a subject suffix (joined with the configured prefix via `ipc.topic()`),
while `subscribe` accepts either a suffix or a full subject string.

`register_action` does three things:

1. Subscribes to the full NATS subject (`{prefix}.{suffix}`)
2. Stores action metadata (name, description, optional schema) in the catalog
3. Returns a handle for unregistration

```python
ipc.register_action(
    "commands.logbook.add",
    self.add_entry,
    description="Add entry to active logbook",
    schema={"title": "str", "content": "str"},
)
```

Schema is optional and informational — not enforced by IPCService. Validation
is the callback's responsibility.

### Outbound Event Registration

Components register the events they publish for discoverability:

```python
ipc.register_event(
    "runs.new",
    description="Fired when a new run starts",
    schema={"run_id": "str", "plan_name": "str", "timestamp": "str"},
)
```

This does not create a subscription — it only adds the event to the catalog
so external clients can discover what LUCID publishes.

### Discovery Endpoints

Two built-in meta actions, always registered by IPCService itself:

**`{prefix}.meta.actions`** — list available inbound commands:

```json
{
  "actions": [
    {
      "subject": "commands.plan.run",
      "description": "Run a bluesky plan",
      "schema": {"plan_name": "str", "params": "dict"}
    },
    {
      "subject": "commands.logbook.add",
      "description": "Add entry to active logbook",
      "schema": {"title": "str", "content": "str"}
    }
  ]
}
```

**`{prefix}.meta.events`** — list outbound event topics:

```json
{
  "events": [
    {
      "subject": "runs.new",
      "description": "Fired when a new run starts",
      "schema": {"run_id": "str", "plan_name": "str"}
    }
  ]
}
```

## Trust & Auth Token Sharing

### Handshake Flow

1. External app sends a NATS request to `{prefix}.auth.request`:
   ```json
   {"app_name": "tsuchinoko", "app_version": "1.2.0"}
   ```

2. IPCService receives it and triggers a trust dialog in the UI:
   > **tsuchinoko v1.2.0** wants to connect to LUCID. Trust this application?
   >
   > [Trust for this session] [Deny]

3. If approved — reply on the NATS ephemeral inbox:
   ```json
   {
     "status": "approved",
     "tiled_token": "eyJ...",
     "tiled_url": "https://bcgtiled.als.lbl.gov"
   }
   ```

4. If denied — reply:
   ```json
   {"status": "denied"}
   ```
   Subsequent requests from the same `app_name` are auto-denied for the
   session to prevent prompt spam.

### Why Ephemeral Inboxes

NATS request/reply uses unique `_INBOX.<random>` subjects for responses.
Only the original requester is subscribed to its inbox. Other clients on the
bus cannot observe the token, even without NATS-level ACLs.

### Token Refresh

Trusted apps re-request tokens when they expire by sending another request to
`{prefix}.auth.request`. LUCID auto-approves already-trusted apps without
re-prompting the user. This puts clients in control of when they need a fresh
token.

### Trust State

- Session-scoped: trusted app set is in-memory, cleared on LUCID restart
- Revocable: user can revoke trust from the settings UI
- Auto-deny list: denied apps are auto-denied for the session

### Trust Dialog Timeout

If the user does not respond within 60 seconds, auto-deny and reply:
```json
{"status": "denied", "reason": "timeout"}
```

## Initial IPC Actions (v1)

### Inbound Commands

| Action             | Subject suffix          | Description                        |
|--------------------|-------------------------|------------------------------------|
| Run a plan         | `commands.plan.run`     | Submit a plan to the BlueskyEngine |
| Abort current run  | `commands.plan.abort`   | Abort the active run               |
| Add logbook entry  | `commands.logbook.add`  | Create entry in active logbook     |
| Send agent message | `commands.agent.message`| Write message into Claude agent    |

### Outbound Events

| Event          | Subject suffix  | Description                        |
|----------------|------------------|------------------------------------|
| Run started    | `runs.new`       | New run ID + plan name             |
| Run completed  | `runs.complete`  | Run ID + exit status               |
| Engine state   | `state.engine`   | Idle/running/paused transitions    |

### Built-in Meta

| Action       | Subject suffix  | Description                          |
|--------------|-----------------|--------------------------------------|
| List actions | `meta.actions`  | Discoverable inbound action catalog  |
| List events  | `meta.events`   | Discoverable outbound event catalog  |

### Auth

| Endpoint      | Subject suffix  | Description                          |
|---------------|-----------------|--------------------------------------|
| Trust request | `auth.request`  | Handshake + token sharing            |

## Error Handling

- **Callback exception**: IPCService catches, logs via loguru. For
  request/reply messages, sends an error response:
  ```json
  {"error": true, "message": "Plan 'nonexistent' not found"}
  ```
- **NATS disconnection**: nats-py auto-reconnects. `sigConnectionChanged(False)`
  emitted. Messages published while disconnected are dropped — this is a
  notification system, not a durable queue.
- **Malformed JSON**: Log warning, discard. For request/reply, send error
  response.
- **Graceful shutdown**: Drain subscriptions, close NATS connection, stop
  dispatching callbacks.

## Documentation Deliverables

Two documents must be delivered alongside the implementation:

### 1. Internal Architecture Doc

- IPCService structure, lifecycle, and ServiceRegistry integration
- How to register new actions and events from within LUCID components
- Threading model and callback dispatch
- Trust handshake internals
- How SettingsPlugin config flows into the connection

### 2. External Client Integration Guide

Standalone — readers should not need to understand LUCID internals.

- Connecting to the NATS bus (URL, TLS)
- The trust handshake protocol (request, approval/denial, token re-request)
- Discovering available actions and events via meta endpoints
- Message format conventions (JSON payloads, standard fields)
- Example Python client code:
  - Connecting and requesting trust
  - Sending a command (run a plan)
  - Subscribing to run notifications
  - Requesting a Tiled token and using it

## Dependencies

- `nats-py` — pure Python, `py3-none-any` wheel, Briefcase-compatible
- NATS server — single Go binary, deployed on beamline network
- TLS certificates — LBNL internal CA or self-signed
