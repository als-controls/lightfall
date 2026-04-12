# LUCID MCP Bridge — Design Spec

**Date:** 2026-04-11
**Status:** Draft

## Overview

A standalone MCP server that bridges Claude Code to running LUCID instances via NATS. Claude Code gains the ability to discover, introspect, and control LUCID through three dynamic tools — without hardcoding any LUCID-specific actions into the bridge itself.

```
Claude Code  <--stdio-->  MCP Bridge (FastMCP)  <--NATS-->  LUCID IPCService
```

The bridge is a thin async relay. It does not import LUCID, PySide6, or any beamline-specific code. It speaks the same NATS JSON protocol that any IPC participant uses.

## Package & Plugin Structure

The bridge lives in a standalone repository structured as a Claude Code marketplace plugin:

```
lucid-mcp-bridge/
+-- .claude-plugin/
|   +-- plugin.json              # Plugin metadata (name, version, author)
+-- .mcp.json                    # MCP server declaration
+-- pyproject.toml               # hatch + hatch-vcs, deps: nats-py, fastmcp
+-- src/
|   +-- lucid_bridge/
|       +-- __init__.py
|       +-- __main__.py          # Entry point: python -m lucid_bridge
|       +-- server.py            # FastMCP app, tools, NATS client
+-- skills/
|   +-- setup/
|       +-- SKILL.md             # First-time connection setup guide
+-- tests/
|   +-- conftest.py              # Mock LUCID NATS responder fixture
|   +-- test_tools.py            # Unit tests (no NATS)
|   +-- test_integration.py      # Integration tests (local NATS)
+-- README.md
+-- LICENSE
```

### MCP Server Declaration

`.mcp.json`:

```json
{
  "lucid": {
    "command": "python",
    "args": ["-m", "lucid_bridge", "--nats-url", "nats://localhost:4222"]
  }
}
```

`--nats-url` is the only required argument. Optional `--default-prefix` sets a default LUCID instance prefix for convenience.

### Dependencies

- `nats-py` — async NATS client
- `fastmcp` — MCP server framework (stdio transport)

No dependency on LUCID, PySide6, or any beamline packages.

## Tools

### `list_instances`

Discover LUCID instances on the NATS bus.

**Parameters:** None.

**Behavior:** Scatter-gather over NATS:
1. Generate a unique inbox subject.
2. Subscribe to that inbox.
3. Publish to `_lucid.discover` with the inbox as reply-to.
4. Collect responses for 2 seconds.
5. Unsubscribe and return all responses.

Each LUCID instance subscribes to the well-known subject `_lucid.discover` regardless of its topic prefix. This avoids wildcard matching issues with multi-token prefixes (e.g. `als.7011` has two tokens, so `*.meta.actions` wouldn't match it).

**Returns:**

```json
[
  {
    "instance_id": "beamline-ws3-18432",
    "display_name": "CMS Hutch",
    "prefix": "als.7011",
    "actions_count": 4
  },
  {
    "instance_id": "dev-laptop-67890",
    "display_name": null,
    "prefix": "als.dev",
    "actions_count": 4
  }
]
```

Returns an empty list if no instances respond. This is not an error.

### `list_actions`

Get available actions from a specific LUCID instance.

**Parameters:**
- `prefix` (optional) — topic prefix. Falls back to `--default-prefix`.

**Behavior:** NATS request to `{prefix}.meta.actions`, timeout 5 seconds.

**Returns:**

```json
{
  "instance_id": "beamline-ws3-18432",
  "display_name": "CMS Hutch",
  "prefix": "als.7011",
  "actions": [
    {
      "subject": "commands.plan.run",
      "description": "Run a bluesky plan",
      "schema": {"plan_name": "str", "params": "dict"}
    },
    {
      "subject": "commands.plan.abort",
      "description": "Abort active run",
      "schema": {}
    }
  ]
}
```

Side effect: the bridge caches the action metadata (descriptions, schemas) for use by `execute_action`. Cache is replaced on the next `list_actions` call to the same prefix.

### `execute_action`

Invoke an action on a LUCID instance.

**Parameters:**
- `action` (required) — action subject suffix, e.g. `"commands.plan.run"`.
- `params` (optional, default `{}`) — JSON payload sent as the request body.
- `prefix` (optional) — topic prefix. Falls back to `--default-prefix`.

**Behavior:** NATS request to `{prefix}.{action}` with `params` as JSON body, timeout 5 seconds.

**Returns (success):**

```json
{
  "action": "commands.plan.run",
  "description": "Run a bluesky plan",
  "schema": {"plan_name": "str", "params": "dict"},
  "response": {"status": "submitted", "plan_name": "count"}
}
```

The `description` and `schema` fields come from the cached action metadata (populated by a prior `list_actions` call). If no cached metadata exists for the action, these fields are `null`.

**Returns (LUCID error):**

```json
{
  "action": "commands.plan.run",
  "description": "Run a bluesky plan",
  "schema": {"plan_name": "str", "params": "dict"},
  "response": {"error": true, "message": "Plan 'nonexistent' not found"}
}
```

**Returns (timeout):**

```
No response from 'als.7011' -- LUCID instance may be offline. Timeout: 5s
```

The bridge does not gate unknown actions. If `execute_action` is called with an action not in the cached list, it sends the request anyway. LUCID will reply with its own error if the action doesn't exist.

## Instance Identity (LUCID-side change)

Each LUCID IPCService instance needs a unique identity for discovery:

- **`instance_id`**: Auto-generated at startup as `{hostname}-{pid}`. Always unique, no configuration needed.
- **`display_name`**: Optional, user-configured via `IPCSettingsPlugin` (new text field). For human readability.

Both fields are included in `meta.actions` and `meta.events` responses:

```json
{
  "instance_id": "beamline-ws3-18432",
  "display_name": "CMS Hutch",
  "prefix": "als.7011",
  "actions": [...]
}
```

This requires:
1. New `instance_id` property on `IPCService` (generated in `__init__`).
2. New `display_name` property on `IPCService` (set from preferences).
3. New "Display Name" text field in `IPCSettingsPlugin`.
4. Updated `meta.actions` and `meta.events` response handlers to include identity fields.
5. New subscription to the well-known `_lucid.discover` subject. Handler replies with the same payload as `meta.actions` (instance identity + action list).

## Trust & Authentication

The bridge participates in LUCID's existing trust handshake:

1. On first `list_actions` or `execute_action` targeting a new prefix, the bridge sends a request to `{prefix}.auth.request`:
   ```json
   {"app_name": "claude-code-bridge", "app_version": "0.1.0"}
   ```
2. LUCID pops a TrustDialog. The operator approves or denies.
3. Bridge receives `{"status": "approved", ...}` or `{"status": "denied", ...}`.
4. Bridge caches the auth state per prefix for the session.

If denied, subsequent tool calls to that prefix return:
`"LUCID instance '{display_name}' denied access. Approve the trust prompt in LUCID to continue."`

Auth state is session-scoped. No tokens are persisted across bridge restarts, matching LUCID's trust model.

## Error Handling

| Scenario | Behavior |
|----------|----------|
| NATS unreachable at startup | All tools return `"Cannot connect to NATS at {url}"`. Bridge retries in background (nats-py native reconnect). |
| No instances respond to `list_instances` | Empty list returned. Not an error. |
| LUCID goes away mid-session | `execute_action` gets NATS timeout. Returns timeout message. Cached action metadata remains valid. |
| Malformed JSON from LUCID | Return raw bytes as text with a warning. Don't crash. |
| Unknown action sent via `execute_action` | Bridge sends it anyway. LUCID replies with its own error. |
| No prefix given and no default set | Tools that need a prefix return: `"No prefix specified. Use list_instances to discover available LUCID instances, or set --default-prefix."` Exception: `list_instances` never needs a prefix. |

## Setup Skill

`skills/setup/SKILL.md` guides first-time configuration:

1. Verify NATS is reachable at the configured URL.
2. Run `list_instances` to discover active LUCID instances.
3. Confirm the bridge can communicate (trigger auth handshake).
4. Optionally set `--default-prefix` in the MCP config.

Triggered by plugin installation or user saying "connect to LUCID" / "set up LUCID bridge".

## Testing Strategy

### Unit tests (no NATS needed)

- Tool parameter validation (missing prefix, empty action).
- Response formatting (raw JSON + metadata wrapping).
- Action metadata caching logic.
- Auth state tracking (approved/denied/unknown per prefix).

### Integration tests (local NATS server)

- Full round-trip: bridge tool call -> NATS -> mock LUCID responder -> bridge response.
- Scatter-gather for `list_instances` with 0, 1, and multiple mock responders.
- Auth handshake flow (approve and deny paths).
- Timeout behavior when no responder is listening.
- Reconnection after NATS restart.

The mock LUCID responder is a small async nats-py fixture that subscribes to a prefix and replies to requests. ~30-40 lines.

### Manual testing

Against the local NATS server and a live LUCID instance. The setup skill facilitates this.
