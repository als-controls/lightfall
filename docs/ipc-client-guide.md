# IPC Client Integration Guide

This guide explains how to connect an external process to a running LUCID instance over NATS.
No knowledge of LUCID internals is required.

## Prerequisites

- A running [NATS](https://nats.io/) server reachable from your client (ask your beamline controls
  group for the URL and port).
- The server's TLS CA certificate, or a certificate signed by a trusted CA.
- Python 3.10+ with `nats-py` installed:
  ```
  pip install nats-py
  ```
- A topic prefix matching the one configured in LUCID (default: `als.7011`).

## Connecting

LUCID's NATS server requires TLS. Pass an `ssl.SSLContext` to `nats.connect`:

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

Before sending commands, you must authenticate with LUCID. This is a request/reply handshake on
the `auth.request` subject. LUCID will show a trust dialog the first time; subsequent requests from
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
        raise PermissionError(f"LUCID denied the connection request: {reason}")
```

A successful response has this shape:

```json
{
  "status": "approved",
  "tiled_token": "<jwt>",
  "tiled_url": "https://tiled.als.lbl.gov"
}
```

A denial looks like:

```json
{"status": "denied", "reason": "timeout"}
```

### Token Refresh

If you receive a reply with `{"error": true}` and the message indicates an auth error, re-run the
authentication handshake. Tokens are session-scoped and do not expire independently, but a new
LUCID session (restart) will invalidate old tokens.

## Discovering Available Actions and Events

Before hard-coding subject names, you can ask LUCID what it supports:

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

## Sending Commands

All request payloads are JSON objects. Replies are also JSON. Send an empty object (`{}`) when no
payload is required.

### Run a Plan

```python
async def run_plan(nc, plan_name: str, params: dict) -> dict:
    subject = f"{TOPIC_PREFIX}.commands.plan.run"
    payload = json.dumps({"plan_name": plan_name, "params": params}).encode()
    msg = await nc.request(subject, payload, timeout=30)
    return json.loads(msg.data)

# Example
result = await run_plan(nc, "count", {"detectors": ["det1"], "num": 5})
# {"status": "submitted", "plan_name": "count", "procedure_id": "..."}
```

### Abort the Active Run

```python
async def abort_run(nc, reason: str = "") -> dict:
    subject = f"{TOPIC_PREFIX}.commands.plan.abort"
    payload = json.dumps({"reason": reason}).encode()
    msg = await nc.request(subject, payload, timeout=10)
    return json.loads(msg.data)

# {"status": "abort_requested"}
```

### Add a Logbook Entry

```python
async def add_logbook_entry(nc, title: str, content: str = "", tags: list[str] | None = None) -> dict:
    subject = f"{TOPIC_PREFIX}.commands.logbook.add"
    payload = json.dumps({
        "title": title,
        "content": content,
        "tags": tags or [],
    }).encode()
    msg = await nc.request(subject, payload, timeout=10)
    return json.loads(msg.data)

# {"status": "created", "entry_id": "..."}
```

### Send a Message to the Claude Agent

```python
async def send_agent_message(nc, message: str) -> dict:
    subject = f"{TOPIC_PREFIX}.commands.agent.message"
    payload = json.dumps({"message": message}).encode()
    msg = await nc.request(subject, payload, timeout=10)
    return json.loads(msg.data)

# {"status": "sent"}
```

## Subscribing to Events

LUCID publishes run lifecycle and engine state changes as NATS core messages. Subscribe before
starting a plan so you don't miss early events.

### Run Start and Completion

```python
async def watch_runs(nc):
    async def on_run_new(msg):
        data = json.loads(msg.data)
        print(f"Run started: {data['run_id']} ({data['plan_name']})")

    async def on_run_complete(msg):
        data = json.loads(msg.data)
        print(f"Run finished: {data['run_id']} — {data['exit_status']}")

    await nc.subscribe(f"{TOPIC_PREFIX}.runs.new", cb=on_run_new)
    await nc.subscribe(f"{TOPIC_PREFIX}.runs.complete", cb=on_run_complete)
```

### Engine State Changes

```python
async def watch_engine_state(nc):
    async def on_state(msg):
        data = json.loads(msg.data)
        print(f"Engine state: {data['state']}")

    await nc.subscribe(f"{TOPIC_PREFIX}.state.engine", cb=on_state)
```

## Complete Example: Tsuchinoko-Style Client

The following is a self-contained script that connects, authenticates, subscribes to run events,
submits a plan, and waits for completion.

```python
#!/usr/bin/env python3
"""Example: connect to LUCID, submit a plan, wait for it to finish."""

import asyncio
import json
import ssl
import nats

NATS_URL = "nats://broker.als.lbl.gov:4222"
TOPIC_PREFIX = "als.7011"
APP_NAME = "my-client"
APP_VERSION = "1.0.0"


async def main():
    tls_ctx = ssl.create_default_context()
    nc = await nats.connect(NATS_URL, tls=tls_ctx)
    print("Connected to NATS")

    # Authenticate
    auth_payload = json.dumps({"app_name": APP_NAME, "app_version": APP_VERSION}).encode()
    auth_msg = await nc.request(f"{TOPIC_PREFIX}.auth.request", auth_payload, timeout=70)
    auth = json.loads(auth_msg.data)
    if auth.get("status") != "approved":
        raise PermissionError(f"Authentication denied: {auth.get('reason', 'unknown')}")
    print(f"Authenticated. Tiled URL: {auth.get('tiled_url')}")

    # Track run completion
    run_done = asyncio.Event()
    last_run_id = None

    async def on_run_new(msg):
        nonlocal last_run_id
        data = json.loads(msg.data)
        last_run_id = data["run_id"]
        print(f"Run started: {last_run_id} ({data['plan_name']})")

    async def on_run_complete(msg):
        data = json.loads(msg.data)
        print(f"Run complete: {data['run_id']} — {data['exit_status']}")
        if data["run_id"] == last_run_id:
            run_done.set()

    await nc.subscribe(f"{TOPIC_PREFIX}.runs.new", cb=on_run_new)
    await nc.subscribe(f"{TOPIC_PREFIX}.runs.complete", cb=on_run_complete)

    # Submit a plan
    plan_payload = json.dumps({
        "plan_name": "count",
        "params": {"detectors": ["det1"], "num": 3},
    }).encode()
    plan_msg = await nc.request(f"{TOPIC_PREFIX}.commands.plan.run", plan_payload, timeout=30)
    plan_reply = json.loads(plan_msg.data)

    if plan_reply.get("error"):
        raise RuntimeError(f"Plan submission failed: {plan_reply.get('message')}")

    print(f"Plan submitted (procedure_id: {plan_reply.get('procedure_id')})")

    # Wait for the run to complete (with a generous timeout)
    await asyncio.wait_for(run_done.wait(), timeout=300)
    print("Done")

    await nc.drain()


asyncio.run(main())
```

## Message Format Reference

All messages use JSON encoding (UTF-8). The following fields appear in replies:

| Field          | Type              | When present                                  |
|----------------|-------------------|-----------------------------------------------|
| `status`       | `str`             | Success replies — describes the outcome       |
| `error`        | `bool` (`true`)   | Error replies only                            |
| `message`      | `str`             | Error replies — human-readable description    |
| `tiled_token`  | `str`             | `auth.request` approved response             |
| `tiled_url`    | `str`             | `auth.request` approved response             |
| `procedure_id` | `str`             | `commands.plan.run` success reply             |
| `entry_id`     | `str`             | `commands.logbook.add` success reply          |
| `run_id`       | `str`             | `runs.new` and `runs.complete` events         |
| `exit_status`  | `str`             | `runs.complete` event (`success`/`abort`/`error`) |
| `state`        | `str`             | `state.engine` event                          |

## Topic Hierarchy Reference

All subjects below are prefixed with the configured `topic_prefix` (default: `als.7011`).
The full NATS subject is `{prefix}.{suffix}`.

| Suffix                      | Direction          | Pattern         | Description                                      |
|-----------------------------|--------------------|-----------------|--------------------------------------------------|
| `auth.request`              | client → LUCID     | request/reply   | Trust handshake; receive Tiled token             |
| `meta.actions`              | client → LUCID     | request/reply   | Enumerate registered actions                     |
| `meta.events`               | client → LUCID     | request/reply   | Enumerate registered events                      |
| `commands.plan.run`         | client → LUCID     | request/reply   | Submit a plan to the Bluesky engine              |
| `commands.plan.abort`       | client → LUCID     | request/reply   | Abort the currently active run                   |
| `commands.logbook.add`      | client → LUCID     | request/reply   | Create a logbook entry                           |
| `commands.agent.message`    | client → LUCID     | request/reply   | Send a message to the Claude agent               |
| `runs.new`                  | LUCID → client     | publish/subscribe | Fired when a new run starts                    |
| `runs.complete`             | LUCID → client     | publish/subscribe | Fired when a run finishes (any exit status)    |
| `state.engine`              | LUCID → client     | publish/subscribe | Fired when the Bluesky engine state changes    |
