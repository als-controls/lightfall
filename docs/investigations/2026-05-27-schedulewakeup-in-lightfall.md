# `ScheduleWakeup` in Lightfall's embedded Claude session — investigation

**Date:** 2026-05-27
**Author:** Claude (Opus 4.7)
**Status:** Investigation only; no behaviour change shipped with this note.
**Companion:** `src/lightfall/plugins/agents/engine_tools.py::ncs_wait_for_idle` (Task 1) — the immediate workaround.

## Summary

`ScheduleWakeup` does not currently fire in Lightfall's embedded Claude session.
The model calls it, the tool reports `Next wakeup scheduled for HH:MM:SS (in
Ns)`, the turn ends with a `ResultMessage`, and the model is never re-invoked.
The user has to type something to bring the model back.

This is **not** a SDK or CLI bug from the SDK's perspective — it is a missing
host responsibility. In interactive REPL mode the CLI is the harness and
self-injects the wakeup back onto its own stdin. In SDK subprocess mode the
*host application* is the harness, and Lightfall's host (`PersistentClaudeWorker`,
`src/lightfall/claude/_internal/worker.py:182`) does not re-invoke the model when
the scheduled wakeup time arrives.

## What `ScheduleWakeup` actually does inside the CLI

The bundled CLI (`claude_agent_sdk/_bundled/claude`, a Bun-compiled binary,
≈233 MB, not stripped) was inspected with `strings(1)`. The relevant pieces:

- The tool is named `ScheduleWakeup` and is registered with
  `shouldDefer:!0`, i.e. deferred (only fetched via tool search). Its
  description ties it to `/loop` dynamic mode.
- The schema is `{delaySeconds: number, reason: string, prompt: string}` with
  `delaySeconds` clamped to `[60, 3600]`.
- The implementation calls an internal function (`IlK`) when the feature gate
  `tengu_kairos_loop_dynamic` (`U3H()`) is on. If the gate is off the tool
  returns `{scheduledFor: 0, ...}` and the user-facing string is
  `"Wakeup not scheduled. Either the /loop dynamic runtime gate is off or the
  loop reached its maximum duration — the loop has ended; do not re-issue."`
- When the gate is on, `IlK` writes a cron entry to
  `<cwd>/.claude/scheduled_tasks.json` via `bXH({id, cron, prompt, createdAt,
  kind:"loop"})`. The cron expression is built from the wake-up wallclock
  (`${minute} ${hour} * * *`), then the result message becomes
  `"Next wakeup scheduled for HH:MM:SS (in Ns)…the harness re-invokes you
  when the wakeup fires or a task-notification arrives."`
- The internal scheduler subsystem is called **kairos** (`tengu_kairos_*`).
  It uses Bun's built-in `Bun.cron()` (`strings` shows
  `Bun.cron(schedule, handler)`, `bun-cron-tmp`, etc.).

**Crucial observation:** the firing mechanism is the CLI's *own* main loop.
When kairos detects a due entry it re-enters the model turn pipeline by
injecting a synthesized user message containing the stored `prompt`. There is
no message on the wire that says "wakeup due" — the CLI just behaves as if
the user had typed `prompt` again.

In SDK subprocess mode this self-injection cannot happen, because:

1. The CLI is launched with stream-json input/output by the SDK and its
   "stdin" is the SDK's outbound queue, not a TTY it controls.
2. `client.receive_response()` (`claude_agent_sdk/client.py:567`) terminates
   immediately after yielding the `ResultMessage`. Lightfall's worker mirrors
   that — see `_run_query` in `src/lightfall/claude/_internal/worker.py:302` and
   the `break` on `ResultMessage` at line 363.
3. After `_run_query` returns, the worker blocks on `_query_queue.get_nowait()`
   in `_process_queries` (`src/lightfall/claude/_internal/worker.py:287`). No
   one is reading the CLI's stdout, and nothing in Lightfall is watching the
   wallclock against `scheduled_tasks.json`.

So the wakeup is *scheduled* (the file gets written) but never *delivered*.

## Does the SDK expose a wakeup event?

Short answer: **no**, not for `ScheduleWakeup`.

What it does expose, in `claude_agent_sdk/types.py`:

- `TaskStartedMessage` (subtype `task_started`)
- `TaskProgressMessage` (subtype `task_progress`)
- `TaskNotificationMessage` (subtype `task_notification`, statuses
  `completed | failed | stopped`)

These look like wakeup candidates at first glance, and the CLI's own success
string literally mentions "task-notification" as a wake source. But these are
the **`/task` (subagent) lifecycle** events — the API around them is
`client.stop_task(task_id)` and the example in `client.py:450` makes it
explicit ("the task ID from `task_notification` events"). They are independent
from the kairos wakeup pipeline. A scheduled-wakeup firing does not produce a
`TaskNotificationMessage`.

Searched the SDK Python sources for `wakeup`, `Wakeup`, `kairos`,
`scheduled_task`, `cron`, `task-notification`. The only hits are the
`/task` lifecycle and the docstrings calling out that lifecycle. There is no
SDK API surface for "the CLI scheduled a wakeup" or "a wakeup just fired".

Verified by examining:
- `claude_agent_sdk/types.py` (no Wakeup* message type)
- `claude_agent_sdk/client.py` (no wakeup-related callbacks)
- `claude_agent_sdk/_internal/message_parser.py:215` (parses `task_notification`,
  but as part of the /task family)
- `claude_agent_sdk/_internal/query.py:800` (uses `task_id` for `stop_task`)

## Candidate approaches

### (a) Intercept `ScheduleWakeup` with a PreToolUse hook + QTimer (recommended)

Lightfall already wires `PreToolUse` hooks for permission gating
(`src/lightfall/claude/permission_manager.py`). Extend this with a wakeup
interceptor:

1. The hook inspects each tool call. If `tool_name == "ScheduleWakeup"`, it:
   - Extracts `delaySeconds`, `prompt`, and (optionally) `reason` from the
     input.
   - Schedules a `QTimer.singleShot(delaySeconds * 1000, lambda: …)` on the
     GUI thread that calls `worker.send_query(prompt)`.
   - Returns a deny/allow decision that makes the model believe the wakeup
     was scheduled.
2. Optionally records the pending wakeup so it can be cancelled if the user
   cancels the current query (mirror `cancel_all_pending` in
   `permission_manager.py`).

**Open question — the duplicate-fire problem.** If we `allow` the tool, the
CLI also writes its entry to `.claude/scheduled_tasks.json` and may try to
fire its own wakeup. In SDK mode it currently cannot self-deliver (see above)
so duplication seems unlikely *today*, but kairos may still mark
`lastFiredAt` and skew its cache-lead behaviour. If we `deny`, we have to
synthesize a tool-result string that matches what the CLI would have produced
so the model doesn't see an unexpected denial and panic.

The cleanest variant is **deny with a synthetic permissionDecisionReason** of
the form `Next wakeup scheduled for HH:MM:SS (in Ns)` — that mimics the CLI's
own happy-path output. The Claude Agent SDK supports
`{behavior: "deny", message: ...}` from PreToolUse hooks; the model gets a
tool result with that message and treats the turn as terminating, which is
exactly what we want.

**Verification step before shipping:**

- Spike a one-line `print(f"[hook] saw {tool_name}", file=sys.stderr)` in the
  PreToolUse path and confirm `ScheduleWakeup` actually transits the hook in
  SDK mode. (It is registered with `shouldDefer:!0`, which means it may not
  appear in the default tool list — but the SDK still routes invocations
  through the PreToolUse hook, so this should be safe. **Confirm empirically.**)
- After implementation, manually verify there are no duplicate fires by
  scheduling a 60 s wakeup, watching the log for two `send_query` calls, and
  inspecting `.claude/scheduled_tasks.json` between turns.
- If the CLI's kairos *does* try to fire and produces an extra synthesized
  user turn, fall back to deleting the matching entry from
  `scheduled_tasks.json` from the PreToolUse hook (best-effort; the file is
  in `<cwd>/.claude/` so Lightfall can write it).

**Pros**

- Bypasses the CLI's scheduler entirely. We own the timer.
- Cancellable via `QTimer.stop()` from the GUI thread, integrated with the
  existing cancel/permission flow.
- No need to keep listening to the SDK stream after `ResultMessage`.
- Works regardless of `tengu_kairos_loop_dynamic` gate state.

**Cons**

- Duplicate-fire risk to confirm with a spike (see above).
- A wakeup scheduled before a hard shutdown of Lightfall is lost. (Same behaviour
  the user has today — acceptable.)
- The `prompt` string the model passes to `ScheduleWakeup` is now a *user
  prompt* on the next turn, with no `<task-notification>` framing. The
  CLI's own kairos fire would have framed it identically, so this matches.

### (b) Watch the SDK message stream for a wakeup event

Not viable in the current SDK. There is no message type emitted by the CLI
when a wakeup is scheduled or fires (only the in-tool-result string in the
`ToolResultBlock` for the `ScheduleWakeup` call itself). Even if we kept the
worker's `receive_response()` loop running past `ResultMessage`, nothing
useful would arrive.

Could become viable if a future SDK adds a `WakeupScheduledMessage` /
`WakeupDueMessage`, in which case the implementation collapses to "forward
the event onto `worker.send_query(prompt)`". Worth a watch on SDK changelogs.

### (c) Hybrid: parse the tool-result string for `Next wakeup scheduled` and start a QTimer post-hoc

Mentioned only to be dismissed. The tool result is opaque English; parsing
`Next wakeup scheduled for HH:MM:SS (in Ns)` is fragile (locale, future
wording changes) and we have the structured `delaySeconds` available at the
PreToolUse boundary anyway. (a) supersedes this completely.

## Recommendation

**Implement (a) — PreToolUse interceptor with QTimer**, using
`{behavior: "deny", message: "Next wakeup scheduled for …"}` as the synthetic
tool result, with a verification spike to confirm the duplicate-fire question.
Until that lands, `ncs_wait_for_idle` (Task 1) covers the only concrete use
case the user has surfaced (waiting for a scan to finish), so the urgency to
ship (a) is moderate.

When implementing, factor the wakeup-tracking state out of
`PermissionManager` into a separate `WakeupScheduler` QObject so the
permission-manager file doesn't grow another mission. Wire it up alongside
the permission manager in `QtClaudeAgent.__init__`
(`src/lightfall/claude/agent.py:144`).

## Open follow-ups

- Confirm whether `tengu_kairos_loop_dynamic` is on or off in SDK subprocess
  mode (decides which CLI message the model is seeing today). The string
  `Next wakeup scheduled for …` is reported, which implies the gate is **on**
  — but that contradicts the assumption that SDK mode wouldn't enable the
  loop runtime. Worth confirming via a quick stderr trace.
- Decide policy for wakeups outliving a query cancellation. Today
  `permission_manager.cancel_all_pending()` runs on cancel; a similar
  `wakeup_scheduler.cancel_all_pending()` would be the natural symmetry.
- Decide UI surfacing: should pending wakeups be visible somewhere
  (e.g. in the Claude panel's status row)? Without UI a long delay looks
  like a hang.
