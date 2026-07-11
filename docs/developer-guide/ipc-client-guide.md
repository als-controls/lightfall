# IPC Client Integration Guide

This guide explains how to connect an external process to a running Lightfall instance over NATS.
No knowledge of Lightfall internals is required.

## Prerequisites

- A running [NATS](https://nats.io/) server reachable from your client (ask your beamline controls
  group for the URL and port).
- The server's TLS CA certificate, or a certificate signed by a trusted CA.
- Python 3.10+ with `nats-py` installed:
  ```
  pip install nats-py
  ```
- A topic prefix matching the one configured in Lightfall (default: `als.7011`).

## Connecting

Lightfall's NATS server requires TLS. Pass an `ssl.SSLContext` to `nats.connect`:

```python
import asyncio
import ssl
import nats

NATS_URL = "nats://broker.als.lbl.gov:4222"
TOPIC_PREFIX = "als.7011"

async def main():
    tls_ctx = ssl.create_default_context()
    # If using a private CA:
    # tls_ctx.load_verify_locations("/path/to/ca.crt")

    nc = await nats.connect(NATS_URL, tls=tls_ctx)
    print("Connected")
    # ... use nc ...
    await nc.drain()

asyncio.run(main())
```

## Authentication

Before sending commands, you must authenticate with Lightfall. This is a request/reply handshake on
the `auth.request` subject. Lightfall will show a trust dialog the first time; subsequent requests from
the same `app_name` are approved or denied automatically.

```python
import json

async def authenticate(nc, app_name: str, app_version: str = "") -> dict:
    subject = f"{TOPIC_PREFIX}.auth.request"
    payload = json.dumps({"app_name": app_name, "app_version": app_version}).encode()

    # Timeout >60 s to allow the user to respond to the dialog
    msg = await nc.request(subject, payload, timeout=70)
    response = json.loads(msg.data)

    if response.get("status") == "approved":
        print("Authenticated. Tiled token:", response.get("tiled_token"))
        print("Tiled URL:", response.get("tiled_url"))
        return response
    else:
        reason = response.get("reason", "denied")
        raise PermissionError(f"Lightfall denied the connection request: {reason}")
```

A successful response has this shape:

```json
{
  "status": "approved",
  "session_token": "<url-safe-random-token>",
  "tiled_token": "<api_key_secret>",
  "tiled_url": "https://tiled.als.lbl.gov",
  "session_id": "<keycloak-sub-or-null>",
  "contract_version": 1
}
```

`session_token` is new (remote-control contract v1): it identifies your **capability channel** and
is required for every `commands.*` call — see
the "The capability channel" section below. `contract_version` is `1`; send it back on
every request (see "Structured Errors" below).

> **Trust is per login session, not per process.** The trust decision and the capability channel
> both live only as long as the *current Lightfall login session*. When the logged-in user logs out
> of Lightfall, every capability channel is torn down and every app's trust decision is forgotten,
> even if the Lightfall process keeps running. Your client will not receive a notification — the
> channel simply stops answering. Detect this by timing out on a request and re-running
> `auth.request` from scratch (a fresh handshake mints a new `session_token`; the old one is dead).

> **Auth v2 (since 2026-05):** The `tiled_token` field name is preserved for
> wire-format compatibility, but the value is now a Tiled API key (not a
> Keycloak JWT bearer). Consume it via the Tiled client's `api_key=` parameter:
>
> ```python
> from tiled.client import from_uri
> client = from_uri(tiled_url, api_key=tiled_token)
> ```
>
> The key has a TTL (~1 week by default) configured server-side; clients should
> handle 401 responses by re-requesting via the IPC `auth.request` flow.

A denial looks like:

```json
{"status": "denied", "reason": "timeout"}
```

### Token Refresh

If a Tiled request comes back `401`, re-run the authentication handshake. Under auth-v2,
`tiled_token` is a server-issued Tiled API key with a TTL (typically 1 week) — it may outlive the
IPC requester's local session, and conversely a new Lightfall session (restart, or a logout/login
cycle) will invalidate old keys. On a 401 from Tiled, re-run `auth.request` to obtain a fresh key
(this also mints a fresh `session_token`).

## Discovering Available Actions and Events

Before hard-coding subject names, you can ask Lightfall what it supports:

```python
async def discover(nc):
    # List all request/reply actions
    msg = await nc.request(f"{TOPIC_PREFIX}.meta.actions", b"{}", timeout=5)
    actions = json.loads(msg.data)["actions"]
    for a in actions:
        print(f"  action: {a['subject']} — {a['description']}")

    # List all outbound events
    msg = await nc.request(f"{TOPIC_PREFIX}.meta.events", b"{}", timeout=5)
    events = json.loads(msg.data)["events"]
    for e in events:
        print(f"  event:  {e['subject']} — {e['description']}")
```

## The capability channel

NATS core messages carry no sender identity, so Lightfall cannot tell "who" sent a message just from
the subject or payload. Instead, every `commands.*` action lives behind a **capability channel**: a
private subject built from the `session_token` you got back from `auth.request`.

- **Subject shape**: `{prefix}.session.{session_token}.<command-suffix>` — e.g.
  `als.7011.session.<token>.commands.plan.list`. There is no bare `commands.plan.list` you can call
  directly; sending a request to the un-channeled subject gets you back a structured `denied` error
  instead of a real reply.
- **Every request should include `contract_version: 1`** in its JSON body. If it's missing, the
  server currently assumes `1` for backward tolerance, but you should send it explicitly — a
  mismatched version (once the contract bumps past 1) gets a `version_mismatch` error instead of
  being silently misinterpreted.
- **The broadcast events are the exception**: `runs.new`, `runs.complete`, and `state.engine` are
  *not* behind the capability channel — they're plain `{prefix}.runs.new` etc., meant to be
  subscribed by multiple listeners at once and carry no secrets.
- **The channel dies on logout** (see the Authentication section above) — treat request timeouts as
  a signal to re-authenticate rather than retrying forever.

```python
async def call(nc, session_token: str, suffix: str, payload: dict | None = None, timeout: float = 5.0) -> dict:
    subject = f"{TOPIC_PREFIX}.session.{session_token}.{suffix}"
    body = dict(payload or {})
    body.setdefault("contract_version", 1)
    msg = await nc.request(subject, json.dumps(body).encode(), timeout=timeout)
    return json.loads(msg.data)
```

## Sending Commands

All request payloads are JSON objects sent on the capability channel (see above). Replies are also
JSON and always carry `contract_version: 1`. Send `{}` (plus `contract_version`) when no other
payload is required. This is the full v1 verb table.

| Verb                        | Request fields                                                        | Success reply fields                                        | Notes |
|------------------------------|------------------------------------------------------------------------|----------------------------------------------------------------|-------|
| `commands.plan.list`         | *(none)*                                                                | `plans: [{name, params: [{name, type, unit, default}]}]`       | Enumerates registered plans with parameter metadata |
| `commands.plan.run`          | `plan_name` (str, required), `params` (dict, default `{}`), `behavior` (`"reject"` \| `"queue"`, default `"reject"`) | `status: "submitted"`, `plan_name`, `item_id`, `run_uid` (str or `null`) | `behavior="reject"` errors `busy` if the engine isn't idle; `behavior="queue"` queues it. `run_uid` is `null` when the run hasn't produced a start document within ~2s of submission (e.g. it was queued behind another run) — fall back to the `runs.new` event to learn the `run_uid` once it starts |
| `commands.plan.abort`        | `reason` (str, optional)                                                | `status: "abort_requested"` or `status: "not_aborted", message` | `not_aborted` when there is nothing running to abort |
| `commands.queue.get`         | *(none)*                                                                | `items: [{item_id, plan_name, state}]`                         | `state` is `"running"` for the current item (if any) and `"queued"` for the rest |
| `commands.engine.status`     | *(none)*                                                                | `state: "idle"` or `state: "running", item_id, run_uid, plan_name` | |
| `commands.device.search`     | any device-metadata filters as top-level fields (e.g. `category`, `device_class`) | `devices: [name, ...]` (sorted)                                 | happi-style filter matching against `DeviceInfo` fields/metadata |
| `commands.device.components` | `device` (str, required)                                               | `components: [{name, type, writable}]`                          | `unknown` error if the device isn't instantiated |
| `commands.device.info`       | `device` (str, required)                                               | `name`, `category`, `device_class`                              | |
| `commands.device.get`        | `device` (str, required), `signal` (str, optional)                      | `value`, `timestamp` (float, epoch seconds)                     | Omitting `signal` reads the device's primary readback (`user_readback`/`readback` for positioners) |
| `commands.device.put`        | `device` (str, required), `signal` (str, optional), `value` (required), `wait` (bool, default `true`), `timeout_s` (float, default `30.0`) | `status: "accepted"` (if `wait=false`) or `status: "ok", value` | `behavior` other than `"reject"` is rejected (`bad_request`) — v1 supports only reject semantics; read-only signals get `limits`; engine not idle gets `busy`; wait timeout gets `timeout` |
| `commands.logbook.add`       | `title` (str, required), `content` (str, optional), `tags` (list[str], optional) | `status: "created"`, `entry_id`                                 | |
| `commands.agent.message`     | `message` (str, required)                                               | `status: "sent"`                                                | |

```python
async def run_plan(nc, session_token: str, plan_name: str, params: dict) -> dict:
    reply = await call(nc, session_token, "commands.plan.run", {
        "plan_name": plan_name,
        "params": params,
    }, timeout=30)
    return reply

# Example
result = await run_plan(nc, session_token, "count", {"detectors": ["det1"], "num": 5})
# {"status": "submitted", "plan_name": "count", "item_id": "...", "run_uid": "...", "contract_version": 1}
# run_uid may be null if the start document hadn't arrived yet — listen for runs.new.
```

```python
async def abort_run(nc, session_token: str, reason: str = "") -> dict:
    return await call(nc, session_token, "commands.plan.abort", {"reason": reason}, timeout=10)

# {"status": "abort_requested", "contract_version": 1}
```

```python
async def add_logbook_entry(nc, session_token: str, title: str, content: str = "", tags: list[str] | None = None) -> dict:
    return await call(nc, session_token, "commands.logbook.add", {
        "title": title,
        "content": content,
        "tags": tags or [],
    }, timeout=10)

# {"status": "created", "entry_id": "...", "contract_version": 1}
```

```python
async def send_agent_message(nc, session_token: str, message: str) -> dict:
    return await call(nc, session_token, "commands.agent.message", {"message": message}, timeout=10)

# {"status": "sent", "contract_version": 1}
```

## Subscribing to Events

Lightfall publishes run lifecycle and engine state changes as NATS core messages on plain,
un-channeled subjects (they carry no secrets and are meant for multiple simultaneous listeners).
Subscribe before starting a plan so you don't miss early events.

> **Breaking rename:** the `runs.new`/`runs.complete` field previously named `run_id` is now
> `run_uid`. `runs.new` also gained `item_id` (was implicitly `procedure_id` in the old
> `commands.plan.run` reply; the two concepts are now the same field name across the whole
> contract).

### Run Start and Completion

```python
async def watch_runs(nc):
    async def on_run_new(msg):
        data = json.loads(msg.data)
        print(f"Run started: {data['run_uid']} (item {data['item_id']}, plan {data['plan_name']})")

    async def on_run_complete(msg):
        data = json.loads(msg.data)
        print(f"Run finished: {data['run_uid']} — {data['exit_status']}")

    await nc.subscribe(f"{TOPIC_PREFIX}.runs.new", cb=on_run_new)
    await nc.subscribe(f"{TOPIC_PREFIX}.runs.complete", cb=on_run_complete)
```

`runs.new` payload: `{"item_id": str, "run_uid": str, "plan_name": str}`.
`runs.complete` payload: `{"run_uid": str, "exit_status": "success" | "abort" | "error"}`.

### Engine State Changes

```python
async def watch_engine_state(nc):
    async def on_state(msg):
        data = json.loads(msg.data)
        print(f"Engine state: {data['state']}")

    await nc.subscribe(f"{TOPIC_PREFIX}.state.engine", cb=on_state)
```

## Structured Errors

Any reply with `"status": "error"` carries a `code` and a human-readable `message`, alongside the
usual `contract_version`:

```json
{"status": "error", "code": "busy", "message": "Engine is busy and behavior is 'reject'", "contract_version": 1}
```

`code` is one of:

| Code               | Meaning                                                          |
|---------------------|---------------------------------------------------------------------|
| `busy`              | Engine/queue state conflicts with the requested behavior            |
| `limits`            | Value out of range, or the target signal is read-only               |
| `timeout`           | An operation (e.g. `device.put` with `wait=true`) did not complete in time |
| `unknown`           | Unknown plan/device/signal name, or an unhandled server-side error   |
| `denied`            | Missing/invalid/expired capability channel, or a bare `commands.*` request |
| `bad_request`       | Malformed or missing request fields                                  |
| `version_mismatch`  | Your `contract_version` doesn't match the server's                   |

This supersedes the older, unstructured `{"error": true, "message": ...}` shape — check
`reply.get("status") == "error"` rather than `reply.get("error")`.

## Closed-loop

External services participate in closed experimental loops by combining the NATS bus (for notifications and suggestions) with Tiled (for the measured data). The canonical loop, as implemented by Lightfall's built-in `adaptive_experiment` plan with [Tsuchinoko](https://github.com/lbl-camera/tsuchinoko):

1. At run start, the plan publishes a bind message carrying the run UID, the Tiled URL, and a Tiled API key, so the external engine can read the run's data directly from the catalog.
2. After each measurement (or batch), the plan publishes a notification on `{prefix}.adaptive.measured`.
3. The autonomous engine reads the new points from Tiled, updates its surrogate model, and publishes the next measurement targets on its own subject (`tsuchinoko.targets`).
4. The plan, polling that subject between plan messages via `NATSPlanBridge` (`lightfall.acquire.nats_bridge`), moves the motors to each target and measures — and the loop closes.

No participant in this loop requires modifications to Lightfall's core: notifications and suggestions travel over the same bus described in this guide, and the data travels through the same Tiled catalog every other client uses. The script below is a minimal implementation of a simpler participant that uses only the generic action and event subjects.

## Complete Example: Reference Client

`tests/integration/remote_client.py` in the Lightfall repo is the canonical reference client for
this contract — deliberately dependency-free (raw `nats-py`, no Lightfall imports), and the starting
point for building a language-specific client of your own. The trimmed flow below (handshake →
capability call → event subscribe) mirrors it; the Tsuchinoko-style single-file example that used to
live here has been replaced by this and by the reference client itself.

```python
#!/usr/bin/env python3
"""Example: connect to Lightfall, submit a plan, wait for it to finish."""

import asyncio
import json
import ssl
import nats

NATS_URL = "nats://broker.als.lbl.gov:4222"
TOPIC_PREFIX = "als.7011"
APP_NAME = "my-client"
APP_VERSION = "1.0.0"
CONTRACT_VERSION = 1


async def main():
    tls_ctx = ssl.create_default_context()
    nc = await nats.connect(NATS_URL, tls=tls_ctx)
    print("Connected to NATS")

    # 1. Handshake — auth.request
    auth_payload = json.dumps({"app_name": APP_NAME, "app_version": APP_VERSION}).encode()
    auth_msg = await nc.request(f"{TOPIC_PREFIX}.auth.request", auth_payload, timeout=90)
    auth = json.loads(auth_msg.data)
    if auth.get("status") != "approved":
        raise PermissionError(f"Authentication denied: {auth.get('reason', 'unknown')}")
    session_token = auth["session_token"]
    print(f"Authenticated. Tiled URL: {auth.get('tiled_url')}")

    async def call(suffix: str, payload: dict | None = None, timeout: float = 5.0) -> dict:
        subject = f"{TOPIC_PREFIX}.session.{session_token}.{suffix}"
        body = dict(payload or {})
        body.setdefault("contract_version", CONTRACT_VERSION)
        msg = await nc.request(subject, json.dumps(body).encode(), timeout=timeout)
        reply = json.loads(msg.data)
        if reply.get("status") == "error":
            raise RuntimeError(f"{reply['code']}: {reply['message']}")
        return reply

    # 2. Subscribe to run events (public subjects, not behind the capability channel)
    run_done = asyncio.Event()
    last_run_uid = None

    async def on_run_new(msg):
        nonlocal last_run_uid
        data = json.loads(msg.data)
        last_run_uid = data["run_uid"]
        print(f"Run started: {last_run_uid} ({data['plan_name']})")

    async def on_run_complete(msg):
        data = json.loads(msg.data)
        print(f"Run complete: {data['run_uid']} — {data['exit_status']}")
        if data["run_uid"] == last_run_uid:
            run_done.set()

    await nc.subscribe(f"{TOPIC_PREFIX}.runs.new", cb=on_run_new)
    await nc.subscribe(f"{TOPIC_PREFIX}.runs.complete", cb=on_run_complete)

    # 3. Capability call — submit a plan
    plan_reply = await call("commands.plan.run", {
        "plan_name": "count",
        "params": {"detectors": ["det1"], "num": 3},
    }, timeout=30)
    print(f"Plan submitted (item_id: {plan_reply['item_id']}, run_uid: {plan_reply.get('run_uid')})")

    # Wait for the run to complete (with a generous timeout)
    await asyncio.wait_for(run_done.wait(), timeout=300)
    print("Done")

    await nc.drain()


asyncio.run(main())
```

## Message Format Reference

All messages use JSON encoding (UTF-8). The following fields appear in replies:

| Field              | Type              | When present                                                        |
|---------------------|-------------------|----------------------------------------------------------------------|
| `status`            | `str`             | All replies — e.g. `"approved"`, `"submitted"`, `"ok"`, `"error"`     |
| `contract_version`  | `int` (`1`)       | Every reply                                                           |
| `code`              | `str`             | Error replies only — see "Structured Errors" below      |
| `message`           | `str`             | Error replies — human-readable description                           |
| `session_token`     | `str`             | `auth.request` approved response — capability-channel token          |
| `tiled_token`       | `str`             | `auth.request` approved response — Tiled API key (auth-v2; pass to `from_uri(..., api_key=…)`) |
| `tiled_url`         | `str`             | `auth.request` approved response                                     |
| `item_id`           | `str`             | `commands.plan.run` reply, `commands.queue.get` items, `runs.new` event, `commands.engine.status` (running) |
| `run_uid`           | `str` or `null`   | `commands.plan.run` reply, `commands.engine.status` (running), `runs.new` and `runs.complete` events |
| `entry_id`          | `str`             | `commands.logbook.add` success reply                                  |
| `exit_status`       | `str`             | `runs.complete` event (`success`/`abort`/`error`)                     |
| `state`             | `str`             | `state.engine` event, `commands.engine.status` reply                  |
| `plan_name`         | `str`             | `commands.plan.run` reply, `runs.new` event, `commands.queue.get`/`commands.engine.status` items |
| `plans`             | `list[dict]`      | `commands.plan.list` reply                                            |
| `items`             | `list[dict]`      | `commands.queue.get` reply                                             |
| `devices`           | `list[str]`       | `commands.device.search` reply                                        |
| `components`        | `list[dict]`      | `commands.device.components` reply                                    |
| `value`             | any JSON value    | `commands.device.get`/`commands.device.put` reply                     |
| `timestamp`         | `float`           | `commands.device.get` reply (epoch seconds)                           |

`procedure_id` and `run_id` from the pre-v1 contract are gone — see the breaking-rename callout
under "Subscribing to Events"; `procedure_id` is now `item_id` throughout.

## Topic Hierarchy Reference

All subjects below are prefixed with the configured `topic_prefix` (default: `als.7011`).
The full NATS subject is `{prefix}.{suffix}`, except for `commands.*` actions, which additionally
require the per-session capability segment: `{prefix}.session.{session_token}.{suffix}` (see
"The capability channel").

| Suffix                        | Direction          | Pattern            | Description                                          |
|--------------------------------|--------------------|--------------------|-------------------------------------------------------|
| `auth.request`                 | client → Lightfall | request/reply      | Trust handshake; receive `session_token` + Tiled token |
| `meta.actions`                 | client → Lightfall | request/reply      | Enumerate registered actions                           |
| `meta.events`                  | client → Lightfall | request/reply      | Enumerate registered events                            |
| `session.{token}.commands.plan.list`       | client → Lightfall | request/reply (capability channel) | List available plans with parameter metadata |
| `session.{token}.commands.plan.run`        | client → Lightfall | request/reply (capability channel) | Submit a plan to the Bluesky engine |
| `session.{token}.commands.plan.abort`      | client → Lightfall | request/reply (capability channel) | Abort the currently active run |
| `session.{token}.commands.queue.get`       | client → Lightfall | request/reply (capability channel) | List queued/running plan items |
| `session.{token}.commands.engine.status`   | client → Lightfall | request/reply (capability channel) | Engine state + current run |
| `session.{token}.commands.device.search`   | client → Lightfall | request/reply (capability channel) | Search devices by metadata filters |
| `session.{token}.commands.device.components` | client → Lightfall | request/reply (capability channel) | List a device's sub-devices/signals |
| `session.{token}.commands.device.info`     | client → Lightfall | request/reply (capability channel) | Thin device metadata |
| `session.{token}.commands.device.get`      | client → Lightfall | request/reply (capability channel) | Read a device signal value |
| `session.{token}.commands.device.put`      | client → Lightfall | request/reply (capability channel) | Write a device signal |
| `session.{token}.commands.logbook.add`     | client → Lightfall | request/reply (capability channel) | Create a logbook entry |
| `session.{token}.commands.agent.message`   | client → Lightfall | request/reply (capability channel) | Send a message to the Claude agent |
| `runs.new`                     | Lightfall → client | publish/subscribe  | Fired when a new run starts                            |
| `runs.complete`                | Lightfall → client | publish/subscribe  | Fired when a run finishes (any exit status)            |
| `state.engine`                 | Lightfall → client | publish/subscribe  | Fired when the Bluesky engine state changes            |

Bare (non-channeled) `commands.*` subjects still exist as NATS subscriptions internally, but every
request sent to one gets a structured `denied` reply rather than executing the action — treat them
as unreachable from a client's perspective.
