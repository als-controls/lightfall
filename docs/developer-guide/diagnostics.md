# Diagnostics and Debugging Tools

Lightfall ships several layers of diagnostics. This page lists them in the
order you usually reach for them: what is recorded automatically, where it
goes, how to read it, and what the embedded agent can do with it.

| You want to know… | Look at |
|---|---|
| Why Lightfall crashed or froze | `logs/diagnostics/fault.log` (see *crash diagnostics* below) |
| What was logged a minute ago | the in-process log buffer, or a log file if one is configured (see *logs* below) |
| Which errors happened this session | the error collector, shown in **Help → Report Bug…** (see *errors and bug reports* below) |
| Why startup is slow | `LIGHTFALL_STARTUP_PROFILE=1` (see *startup profiling* below) |
| What the agents cost and how they are used | `~/lightfall/telemetry/agent-usage.jsonl` (see *agent usage telemetry* below) |

Paths starting `logs/` are relative to the directory Lightfall was launched
from; paths starting `~/lightfall/` are in the user's home directory.

## Crash diagnostics

`lightfall.utils.crash_diagnostics` is installed by `lightfall.main` before
PySide6 is imported. Tests and scripts should not reinstall it unless they are
isolating it.

- **Segfaults and fatal errors**: `faulthandler` is enabled for all threads
  and writes per-thread Python tracebacks to `logs/diagnostics/fault.log` (and
  stderr). Each Lightfall start appends a line
  `--- crash_diagnostics installed at <time> ---`, so a crash that killed the
  previous run sits just above the current session's marker.
- **Unhandled exceptions**: `sys.excepthook` and `threading.excepthook` log
  them at CRITICAL through loguru, then chain to whatever hook was installed
  before (Sentry's, Python's default).
- **Qt's own warnings** (thread-affinity violations, failed queued
  connections) are routed into loguru by `install_qt_bridge()` instead of
  vanishing on stderr.
- **On-demand thread dump** of a hung but living process: `kill -USR1 <pid>`
  on POSIX, Ctrl-Break in the console window on Windows. The dump is appended
  to `fault.log`.

For new slot and callback code, the same module provides tripwires that are
no-ops in correct code: `assert_gui_thread`, `assert_object_thread`,
`@gui_thread_only` (stack it below `@Slot`), `safe_call` and `valid_or_skip`.
Use them on the specific path you are debugging rather than everywhere; see
the project's `CLAUDE.md` for when each applies.

## Logs

Lightfall logs through loguru (`from lightfall.utils.logging import logger`),
configured in `LFApplication.initialize()`.

- **Console**: always on, at the configured level.
- **Log file**: opt-in; pass `log_file=...` when initializing the application.
- **In-process buffer**: `lightfall.utils.log_buffer.LogBuffer` keeps the
  most recent records in memory (installed by `main`). It is what the agent's
  `get_recent_logs` tool reads, filtered by level, time window, logger-name
  prefix and substring.

## Errors and bug reports

- **Error collector**: `lightfall.utils.error_collector.ErrorCollector`
  keeps the last 50 ERROR-and-above records, with tracebacks.
  `ErrorCollector.get_instance().get_recent_errors()` returns them.
- **Help → Report Bug…** opens `BugReportDialog`: pick one of the recent
  errors for context, describe the problem, choose a priority and submit.
  The report goes to Sentry/GlitchTip tagged `bug_report:true`.
- **Sentry / GlitchTip** is opt-in: it starts only when a DSN is configured,
  through the `SENTRY_DSN` environment variable or the `telemetry_dsn`
  preference (the environment wins). With no DSN, all reporting is a no-op.
  The `QApplication` subclass also wraps `notify()`, and `@sentry_slot()`
  (in `lightfall.utils.sentry`) captures exceptions raised in slots.

## What the agent can do with this

The embedded agent has three self-diagnosis tools on its `qt` tool server:

- **`get_recent_logs`** reads the in-process log buffer (default: WARNING and
  above from the last two minutes).
- **`get_diagnostics`** returns the error collector's recent errors with
  tracebacks; this session's and the previous session's `fault.log`
  sections, with `previous_session_crashed` true when the previous one holds
  a fatal-error dump; and Lightfall, Python and platform versions. Only the
  last 256 KB of `fault.log` is read.
- **`draft_bug_report`** opens the Report Bug dialog pre-filled with the
  agent's write-up, priority and the relevant error. **The user reviews and
  submits it; the agent cannot send a report itself.**

So "Lightfall crashed last time, what happened?" is a question the agent can
answer from evidence rather than guesswork.

## Startup profiling

`LIGHTFALL_STARTUP_PROFILE=1 lightfall` records how long each startup phase
takes and writes an HTML waterfall and the raw JSON to `logs/diagnostics/`;
`=cprofile` adds a `cProfile` dump. See [Startup Profiling](startup-profiling.md).

## Agent usage telemetry

`lightfall.agents.telemetry` records what agents cost and how they are used.

**What is recorded**, one JSON line per event in
`~/lightfall/telemetry/agent-usage.jsonl`, each with time, host, user and
beamline:

| Event | Fields |
|---|---|
| `turn` | agent, backend (`claude_code` / `agy`), model, `total_cost_usd`, input / output / cache-read / cache-creation tokens, `duration_ms`, `duration_api_ms`, `num_turns`, `is_error` |
| `tool` | agent, tool name, whether its result was an error |
| `permission` | agent, tool, allowed / denied / timed out (auto-approved tools are not counted) |

Prompts, tool arguments and replies are never recorded. The observer
advisor's turns are recorded as agent `observer`. agy does not report cost, so
its turns record a cost of 0. The local log is always on and never leaves the
machine.

**Reading it**:

```bash
# per user, beamline, agent and model: turns, cost, tokens
python -m lightfall.agents.telemetry
python -m lightfall.agents.telemetry --days 30
python -m lightfall.agents.telemetry --json path/to/agent-usage.jsonl
```

In code, `AgentTelemetry.get_instance().summary()` gives this session's
totals per agent and overall.

**Exporting to the facility LGTM stack** (optional). With the `telemetry`
extra installed (`pip install "lightfall[telemetry]"`) and an endpoint
configured, the same events are exported as OTLP metrics every 60 s:

| Setting | Where |
|---|---|
| Endpoint, e.g. `https://bcgotel.als.lbl.gov:8443` | `LIGHTFALL_OTLP_ENDPOINT`, or the `agent_telemetry_otlp_endpoint` preference |
| Producer credential | `LIGHTFALL_OTLP_USERNAME` and `LIGHTFALL_OTLP_PASSWORD` (environment only, never a preference) |

Without both halves of the credential no `Authorization` header is sent, so a
missing credential shows up as "no credential" rather than "wrong password".
Metrics are `lightfall_agent_turns_total`, `lightfall_agent_cost_usd_total`,
`lightfall_agent_tokens_total{kind}`, `lightfall_agent_turn_duration_seconds`,
`lightfall_agent_tool_calls_total` and
`lightfall_agent_permission_decisions_total`, with resource attributes
`service.name=lightfall` and `service.instance.id=<host>`. User names are not
exported. How a Lightfall workstation obtains a producer credential is still
open (issue #35).
