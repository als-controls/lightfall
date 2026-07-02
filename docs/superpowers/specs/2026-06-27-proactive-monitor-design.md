# Proactive measurement monitor — design

**Date:** 2026-06-27
**Status:** Approved (design) — pending implementation plan
**Branch:** `feature/proactive-monitor`

> **Implementation note — use a separate git worktree.** Do the implementation
> in its own worktree (not the primary checkout) to keep it isolated from other
> in-flight work. Reminder: in a worktree the editable install still resolves to
> the **main** checkout, so run tests with `PYTHONPATH=src
> .venv/Scripts/python -m pytest` or the worktree silently tests the wrong code.

## Goal

Add a **second, autonomous subsystem** to Lightfall that watches the *running*
measurement and gives the scientist **unsolicited, human-scale feedback** —
e.g. during an XPCS scan, warn when there is no coherent speckle, or report when
the dynamics have been sufficiently captured. It runs **fully separate** from the
existing reactive chat agent so it never blocks chat input and never pollutes
that conversation.

The guiding principles, settled in brainstorming:

- **No data analysis runs in the Lightfall process.** Heavy data reduction
  (correlation/g₂, FFT, fitting) lives in external services (e.g. the `xpcs_live`
  CUDA correlator) or in detector IOCs. Monitor feeds **consume already-reduced
  signals** — IOC-computed ROI stats, `xpcs_live` g₂/metrics — and apply cheap,
  deterministic *judgment* (thresholds, decay/trend checks) against experiment
  intent. A feed never correlates raw frames.
- **Deterministic code does the sensing/judging; the LLM is an opt-in layer** that
  only does what code cannot: *fuse* heterogeneous signals against intent, put a
  finding into plain language with a recommendation, and *triage* whether it is
  worth interrupting the user. It ships in v1 but is **off by default**.
- **Evaluation is on a user time scale (15–60 s),** not per frame — concurrency
  stays simple and LLM cost is a non-issue.

### On pystxmcontrol / Osprey (why this is new, not a port)

The "integrated agent" in the pystxm/Osprey stack
(`als-computing/pystxm-agent`) is **reactive** — a LangChain ReAct agent that
selects MCP tools when a user sends a message. It has no live monitoring loop.
The autonomous behaviour we want does not exist there to port. Two *ideas* from
that design are worth borrowing and are reflected below: **structured context
objects** the agent reasons over (our `ExperimentContext` / `Observation`), and
**capability/registry composition** (we already have this in the plugin rails).
We do **not** adopt Osprey itself (LangGraph + OpenWebUI service model is a poor
fit for an embedded Qt app already standardised on the Claude Agent SDK).

## Scope (v1)

In:

1. A pluggable **`MonitorFeed`** abstraction: deterministic
   `evaluate(...) -> Observation | None` over **already-reduced** signals,
   interval, user-toggle, config.
2. **`MonitorPlugin` / `MonitorRegistry`** rails mirroring
   `AgentPlugin` / `AgentRegistry`, so any plugin (including domain plugins like
   `lightfall-endstation-7011`) ships feeds.
3. A **`MonitorScheduler`** armed/disarmed by run lifecycle, evaluating enabled
   feeds on a timer, off the UI and engine threads, with state-change
   rate-limiting.
4. **`ExperimentContext`** carried with the run (front-loaded at launch) plus
   **`Observation`** as the structured result type.
5. **Notification surface:** toasts for warn/critical + a new dockable
   **Monitor panel** holding the per-run, severity-coded observation log, with a
   **"discuss in assistant"** hand-off into the reactive chat agent.
6. **Two reference monitors:** a beamline-agnostic **acquisition-health** feed
   (IOC-provided scalars) and an **XPCS speckle / dynamics** feed
   (`lightfall-endstation-7011`) that reads `xpcs_live`'s pre-computed metrics.
7. An **optional LLM advisor** (second `QtClaudeAgent`, headless, limited tools,
   off by default) that consumes a batch of `Observation`s and emits a single
   fused, plain-language message.

Out (YAGNI for v1):

- Per-frame / high-rate analysis, and **any** in-process data reduction.
- The monitor **acting on hardware** — v1 is advisory only. Aborting/adjusting
  stays with the user or, via hand-off, the reactive chat agent (which already
  has the toolset).
- Cross-run trend memory / a monitor "history database".
- Chat-agent *arming* of monitors (designed-for via a tool seam, but the tool
  itself can land later).

## Why a second, separate subsystem (the constraint)

The reactive agent is `QtClaudeAgent` → one persistent `PersistentClaudeWorker`
(QThread + asyncio loop) → one Claude Agent SDK subprocess → **one** conversation,
triggered **only** by the chat input field
(`claude/agent.py`, `claude/_internal/worker.py`, `claude/widget.py`). Reusing it
for proactive work would serialize against user input and pollute the chat
context. So the monitor is its own subsystem; the optional advisor is its own
`QtClaudeAgent` instance with its own SDK session (feasible — see §"LLM advisor").

## Architecture overview

```
 RunEngine (worker thread)
   │  documents (start/descriptor/event/stop) via engine.subscribe(cb)  [non-blocking enqueue]
   ▼
 MonitorScheduler  ── arms on 'start' (uid from doc), disarms on 'stop'/abort
   │  • maintains a cheap in-process RollingBuffer (inline reduced event scalars)
   │  • QTimer (15–60 s) on GUI thread
   ▼  each tick → for each enabled MonitorFeed:
 QThreadFuture(feed.evaluate, key="monitor:<feed>")   [off UI thread; key dedupes slow ticks]
   │      DataWindow = {inline events (buffer) + derived metrics (xpcs_live Tiled stream)}
   │      ExperimentContext (from start-doc metadata)
   ▼  Observation | None  → callback_slot on GUI thread
 RateLimiter (surface once on state-change, not every tick)
   ├──► ToastManager        (warn / critical)
   ├──► Monitor panel        (all severities; per-run log; "discuss in assistant")
   └──► [optional] LLM advisor (batch of Observations → one fused message)
```

## Data model

### `ExperimentContext`
A small, **JSON-serializable** dataclass describing *what this measurement is
trying to do* — the thing run metadata lacks today (a grep for `ExperimentContext`
finds nothing). Template: forge `XPCSParams`
(`als-computing/forge_XPCS_tiled_finch/backend/xpcs/types.py:52-69`).

Minimum v1 fields:
- `experiment_type: str` (e.g. `"xpcs"`, `"generic"`)
- `intent: str` (free text — what good looks like; seeds the advisor)
- `feed_config: dict[str, dict]` — per-feed thresholds / ROI / expectations, keyed
  by feed name.
- XPCS-specific (under `feed_config["xpcs_speckle"]`): `roi_id` (which `xpcs_live`
  ROI to judge), `expected_tau_s` (optional), `min_frames`, `contrast_warn`.

It is read by feeds and the advisor; it is **not** mutated at runtime.

### `Observation`
The structured result a feed emits:
- `severity: "info" | "warn" | "critical"`
- `feed_name: str`, `run_uid: str`, `ts: float`
- `title: str`, `message: str`
- `metrics: dict[str, float]` (e.g. `{"contrast": 0.21, "tau_c_s": 4.2}`)
- `recommendation: str | None`
- `state_key: str` — identity of the *condition* for rate-limiting (e.g.
  `"xpcs_speckle:no_speckle"`); the scheduler surfaces only on change of
  `(state_key, severity)`.

## Components

### `MonitorFeed` (the pluggable unit)
```python
class MonitorFeed:
    name: str
    default_interval_s: float          # 15–60; floor ~5
    def evaluate(self, ctx: ExperimentContext,
                 window: DataWindow,
                 prior: list[Observation]) -> Observation | None: ...
```
Pure / deterministic by contract (testable as a function), and **cheap** — it
*judges* reduced signals, it does not reduce raw data. `prior` lets a feed express
"low **and not improving**" without ad-hoc state. Evaluation runs off-thread
(§ Threading) so even a feed that reads a small Tiled metric stream never touches
the UI thread.

### `MonitorPlugin` / `MonitorRegistry` (rails — mirror the agent rails)
Mirror `AgentPlugin`/`AgentRegistry` exactly so behaviour and settings are
predictable:

- `class MonitorPlugin(PluginType)` with `type_name="monitor"`,
  `is_singleton=True` (mirror `plugins/agent_plugin.py:31-32`); `name`,
  `description`, `display_name`, `category`, `enabled_by_default`, `priority`
  (mirror `:59-76`); a `create_feeds() -> list[MonitorFeed]` contribution hook
  (replaces `get_system_prompt`/`create_tools`).
- `class MonitorRegistry` — copy `ui/panels/claude/agent_registry.py:37-86`
  (singleton + register/get) and the opt-out helpers; `enabled_plugins()` clone
  (`:147-158`) keyed on **`disabled_monitor_plugins`** /
  **`forced_enabled_monitor_plugins`**. Opt-out semantics mean a feed is on iff
  `enabled_by_default and name not in disabled`, or `name in forced_enabled`.
- Wire a `"monitor"` branch into `plugins/loader.py:_register_with_type_registry`
  (after the `"agent"` branch at `:629-644`) → `MonitorRegistry.get_instance()
  .register(instance)`; register the type and add manifest entries in
  `plugins/builtin_manifest.py` next to the agent entries (`:193-257`).

### `MonitorScheduler`
Greenfield (`src/lightfall/monitor/scheduler.py`). Responsibilities:

- **Arm / disarm by run lifecycle.** `sigStart`/`sigFinish`/`sigAbort` are
  **payload-less** (`acquire/engine/base.py:75-89`) and `sigStart` fires *before*
  the run opens — so the run uid must come from the **`start` document**, not the
  signal. Subscribe via `engine.subscribe(cb) -> token`
  (`acquire/engine/base.py:454-467`); arm on the `start` doc (capture
  `doc["uid"]`), disarm on the matching `stop` (and handle abort/exception /
  `sigProcedureFinished` for plans that never open a run). Use the **atomic
  subscribe + state-snapshot** idiom (`plugins/agents/engine_tools.py:176-201`) so
  a run that starts between subscribe and first read is not missed.
- **Non-blocking ingest.** `engine.subscribe` callbacks run **synchronously on the
  engine worker thread** inside `_emit_output` (`base.py:531-546`); a slow
  subscriber **stalls plan execution**. The scheduler's buffer callback must only
  enqueue/append and return — reuse the `LiveDataBuffer` pattern
  (`acquire/buffer.py:76-152`, already a `subscribe`-compatible rolling buffer
  with per-field `deque(maxlen=…)` and `RunInfo.from_start_doc`) or the
  `ThreadedTiledWriter` enqueue-and-drain pattern
  (`services/threaded_tiled_writer.py:127-143`).
- **Tick.** A `QTimer` on the GUI thread (per-feed interval; global default).
  Each tick, for each enabled feed, launch
  `QThreadFuture(feed.evaluate, key=f"monitor:{feed.name}",
  callback_slot=self._on_observation)` (`utils/threads.py:452-466`). The `key`
  auto-cancels a still-running prior tick of the same feed
  (`ThreadManager`, `:141-146`) so slow evaluations can't stack. Results arrive
  on the GUI thread via the callback signal — no manual marshalling needed.
- **Rate-limit.** Keep last `(state_key → severity)` per run; forward an
  `Observation` to the surface only when that pair changes. A standing condition
  toasts once, not every 30 s.

### `DataWindow`
What a feed sees each tick — **reduced signals only**, never raw pixel data to
analyse:

- **`events`** — the cheap inline scalar table from the rolling buffer:
  IOC-computed `roi_stat1` per-frame stats, count rates, monitors. The
  acquisition-health feed uses only this.
- **`derived(name)`** — already-computed metrics produced by an external service,
  read from the run's recorded Tiled streams (for XPCS: `xpcs_live`'s `xpcs`
  stream — g₂, τ, contrast, fit metrics). Small arrays, not images.
- **`pv_get(pv)`** — an *escape hatch* only: a single direct caproto PV get for a
  live value if a feed ever truly needs one. Expected to be **rare and
  infrequent**, never used for in-process analysis.

There is deliberately **no raw-frame facet**. Per the no-in-process-analysis rule
the XPCS feed judges `xpcs_live`'s g₂/metrics rather than correlating frames, which
also sidesteps the in-flight Tiled image-array read hazard (0-row shape / 500
until stop-flush, `services/threaded_tiled_writer.py:103-111`) entirely.

### Monitor panel (notification surface)
A new `PanelPlugin` (`plugins/panel_plugin.py:19`; copy the ~15-line template
`ui/panels/plugins/claude_plugin.py:13-29`, register in `builtin_manifest.py`
like the claude panel `:295-300`). The `BasePanel` subclass sets
`panel_metadata` (`ui/panels/base.py:159`) with `default_area="right"` (or
`"bottom"`), `proactive_init=False` (stay lazy until opened). It renders the
**per-run, severity-coded, dismissible observation log**; each row offers
**"discuss in assistant"** which injects the `Observation` + `ExperimentContext`
as a prompt turn into the reactive chat agent (`QtClaudeAgent.query_sync`,
`agent.py:500`) so the user can investigate/act with the full toolset.
Warn/critical also raise a toast via `ToastManager.get_instance()`
(`ui/toast.py`).

### LLM advisor (optional, off by default)
A second `QtClaudeAgent` (`claude/agent.py:170`) — feasible because most state is
already per-instance (own `PermissionManager` `:278`, own worker+loop `:436` /
`worker.py:95`, own temp plugin dir `:322`, own SDK client `:418`). It consumes a
**batch** of deterministic `Observation`s per tick and returns **one** fused,
plain-language message (or "nothing to report"); it does **not** sense data
itself. Construction differences from the reactive agent ("must-change" list):

- **Headless:** no `ClaudeAssistantWidget`; drive via `query_sync`, consume
  `message_received`/`result_received`. Chat input lives only in the widget
  (`widget.py`), so omitting it is sufficient.
- **No approval UI:** `require_approval=False` + `permission_mode=
  "bypassPermissions"` (`agent.py:286-290`); do **not** wire permission signals to
  toasts (the reactive panel does this at `claude_panel.py:505-509`).
- **Limited toolset:** do **not** read the shared `AgentRegistry`
  (`agent.py:316`) — pass an explicit, read-only plugin/`allowed_tools` set.
- **No session pollution:** do **not** connect `session_id_changed →
  _store_session_id` (`agent.py:423`); skip auto-restore; use a **separate cwd**
  so it doesn't share the reactive agent's transcript project dir / `list_sessions`
  (`agent.py:24-33`, `:630-637`).
- **Safe-to-share:** the Windows command-line temp-file monkeypatch
  (idempotent, `agent.py:36-103`), env-based auth (process-global
  `ANTHROPIC_API_KEY/BASE_URL`, `:370-384` — same backend), `ToastManager`.

Gate its construction behind a single pref (default `False`) so it is never even
instantiated unless enabled.

## The two v1 reference monitors

### 1. Acquisition-health (beamline-agnostic) — `lightfall.monitor.feeds`
IOC-provided scalars only (no asset reads, no reduction), so it works on any run
and de-risks the framework. Deterministic checks over the rolling-buffer `events`:
- **Count-rate collapse / dead detector:** primary counts / `roi_stat1` total
  drop to ~0 (or below a configured floor) for N consecutive frames.
- **Saturation:** the IOC-computed ROI max stat exceeds a configured fraction of
  detector full scale.
- **Stalled run:** no new events for ≫ expected frame time.
Severity → `warn`/`critical`; messages name the offending field + value.

### 2. XPCS speckle / dynamics — `lightfall-endstation-7011`
Ships as a `MonitorPlugin` in the endstation plugin (XPCS is the Andor
areaDetector there — `lightfall-endstation-7011/devices/andor.py`; runs bind by
uid in `xpcs/binding.py`). Per the no-in-process-analysis rule, this feed does
**not** correlate frames. The g₂/τ/contrast/fit metrics are computed by the
external `xpcs_live` CUDA correlator, which records them into the bound run's
**`xpcs` Tiled stream** (payload: `{tau, g2:{average, <roi>}, intensity, metrics,
frames_count, ...}` — `lightfall-endstation-7011/.../xpcs/plots.py:76-83`; design
doc `2026-06-05-xpcs-lightfall-port-design.md:138-145`). The feed **reads those
metrics** via `DataWindow.derived` and applies deterministic judgment:

- **No coherent speckle** (Detector 1): contrast `β = g₂(τ→0) − 1` taken from the
  provided g₂. **warn** when `β < contrast_warn` (default ≈ 0.02–0.05; real XPCS
  contrast is ~0.1–1.0). Gate on a minimum `frames_count` first (at ~5.5 s/frame a
  60 s tick adds only ~10 frames) → otherwise "insufficient signal".
- **Dynamics captured** (Detector 2): from the provided fit/decay metrics
  (`tau_c`, fit quality) and the g₂ curve — declare **captured** when the fit is
  good and g₂ has decayed within the measured lag range (`g₂(0)−1` fallen below
  ~1/e·β, or `tau_c` comfortably below the longest measured lag). If g₂ is still
  flat at the longest lag → **info/warn** "dynamics not yet captured — extend
  acquisition".

**Reading without subscribing to NATS:** consume the recorded `xpcs` Tiled stream
(or another already-recorded sink), **not** a NATS subscription — `xpcs_live`'s
bind is single-occupant and a second subscriber would collide with the XPCS panel.
If `xpcs_live` is not running, the feed reports "no live g₂ available" and stays
quiet rather than computing anything itself.

## ExperimentContext injection (at launch)

Inject a JSON-serializable `experiment_context` dict into the run's **start doc**
via a **pre-submit hook** — the same mechanism and call site as the existing
sample-metadata dialog: `BaseEngine.register_pre_submit(callable(plan_name,
kwargs) -> dict)` (`acquire/engine/base.py:480-516`), wired next to
`BlueskyPanel._auto_configure` (`ui/panels/bluesky_panel.py:291-299`; cf.
`_sample_metadata_pre_submit` `:56-90`). The returned dict merges into plan
kwargs, which `_execute_plan` folds into the start doc
(`acquire/engine/bluesky.py:317-361`). Constraints: values must be plain JSON
(callables are silently dropped, `:343-349`) and must avoid reserved start-doc
keys. (If context must be sampled fresh at each execution rather than at submit,
`subscribe_kwargs_callable` (`bluesky.py:588-607`) is the worker-thread
alternative; avoid `RE.md`, which stores values verbatim and does not evaluate
callables.)

v1 UX for supplying intent: a lightweight "experiment context" affordance at
launch (defaults per `experiment_type`; XPCS fields pre-filled from the active
Andor device / XPCS binding where possible). The chat-agent-arming path is
deferred.

## Threading & concurrency (summary)

| Work | Thread | Rule |
|---|---|---|
| Document ingest (`engine.subscribe`) | engine worker | enqueue/append only — never block |
| Tick timer | GUI | cheap; just launches futures |
| `feed.evaluate` | `QThreadFuture` pool | reads small reduced metrics; `key=` dedupes |
| Observation handling / rate-limit / UI | GUI (via callback signal) | toast + panel |
| Advisor SDK turn | its own worker thread + asyncio loop | isolated session |

Nothing crosses into the engine's asyncio loop; nothing blocks the reactive
agent.

## Settings / toggles

- **Per-feed enable:** opt-out pref pair `disabled_monitor_plugins` /
  `forced_enabled_monitor_plugins` via `MonitorRegistry.enabled_plugins()`,
  surfaced through a `SettingsPlugin` page copied from
  `ClaudeToolsSettingsPlugin` (`plugins/.../tool_settings.py:241`).
- **Advisor master switch (default `False`):** single pref
  `monitor_advisor_enabled` via `PreferencesManager` (`get`/`set`/`subscribe`,
  `manager.py:277-301`); scheduler hot-reloads by subscribing to it (cf.
  `claude_panel.py:762-775`).
- **Global tick interval & monitor master enable:** prefs with sensible defaults.

## Error handling & safety

- **Advisory only.** No hardware actions in v1. Never gate an abort on a feed or
  the LLM.
- **Never destabilise a run.** All ingest/eval is wrapped; a feed that raises is
  logged, disabled for the run, and surfaces a single `warn` ("monitor X failed")
  — it must not crash the engine or the app. The engine's subscriber fan-out
  already swallows+warns (`base.py:545`); add explicit reporting on top.
- **Tolerate missing/partial data.** Slow frames, `xpcs_live` not running, a
  not-yet-present metric stream → "insufficient signal" / "no live g₂ available",
  not a false alarm.

## Testing strategy

- **Feeds** are pure functions → unit-test with canned `DataWindow` +
  `ExperimentContext`. The XPCS feed is tested against canned `xpcs_live`-style
  g₂/metric payloads (good-contrast, washed-out, not-yet-decayed cases) — there is
  no frame correlation to test.
- **Scheduler** with a fake engine emitting synthetic documents + a fake clock;
  assert arm/disarm on start/stop/abort, non-blocking ingest, and `key=` dedupe.
- **Rate-limiter** — assert surface-once-per-state-change.
- **Advisor** mocked at the SDK boundary; assert it never writes the shared
  session id and uses the limited toolset.
- Use the project venv: `PYTHONPATH=src .venv/Scripts/python -m pytest`.

## Open decisions / risks (for review)

1. **Speckle threshold:** fixed β cutoff vs "contrast collapsed vs start-of-run".
   A fixed cutoff depends on beamline coherence/flux; v1 uses a configurable
   absolute value with a relative-trend fallback.
2. **Where `ExperimentContext` lives in the start doc** (own key vs merged into
   existing metadata) — no convention exists today; proposed dedicated
   `experiment_context` key.
3. **Reading `xpcs_live` metrics mid-run:** confirm the recorded `xpcs` stream is
   readable while the run is in flight (small metric arrays, but the in-flight
   Tiled caveat should be checked); fall back to "no live g₂ available" if not.

Resolved in review: live raw-frame reads are **not** needed (escape hatch is a
rare direct caproto PV get — never Tiled, never for analysis); the XPCS feed
**reads pre-computed g₂/metrics from `xpcs_live`** rather than analysing frames
in-process; **no data analysis runs in the Lightfall process**.

## Build sequence (for the plan)

1. Data model (`ExperimentContext`, `Observation`, `DataWindow`) + tests.
2. `MonitorFeed` + `MonitorPlugin`/`MonitorRegistry` + loader/manifest wiring +
   tests (mirror agent rails).
3. `MonitorScheduler` (arm/disarm, rolling buffer, tick, rate-limit) against a
   fake engine + tests.
4. Monitor panel (`PanelPlugin`) + toast wiring + "discuss in assistant" hand-off.
5. Acquisition-health feed (IOC scalars) — first end-to-end proof.
6. `ExperimentContext` launch injection (pre-submit hook) + minimal launch UX.
7. XPCS speckle/dynamics feed in `lightfall-endstation-7011` — reads `xpcs_live`'s
   recorded g₂/metrics (no in-process correlation) + resolve open item #3.
8. Optional LLM advisor (second `QtClaudeAgent`, headless, gated off) + settings.
9. Settings pages (per-feed table + advisor switch + interval).

Suggested split into two plans: **(A)** steps 1–6 (framework + health feed +
panel + context), then **(B)** steps 7–9 (XPCS feed + advisor + settings polish).

## Key file references

- Plugin rails to mirror: `plugins/agent_plugin.py:17-84`,
  `ui/panels/claude/agent_registry.py:37-158`, `plugins/loader.py:629-662`,
  `plugins/builtin_manifest.py:193-300`, `plugins/types.py:46-121`.
- Panel: `plugins/panel_plugin.py:19-87`, `ui/panels/plugins/claude_plugin.py`,
  `ui/panels/base.py:41-91,159`, dock `ui/panels/.../manager.py:213-222,758-783`.
- Engine: `acquire/engine/base.py:75-89,454-546`,
  `acquire/engine/bluesky.py:317-361,588-607`, `acquire/buffer.py:76-152`,
  `services/threaded_tiled_writer.py:24-143`, `plugins/agents/engine_tools.py:176-201`.
- Launch metadata: `ui/panels/bluesky_panel.py:56-90,291-299,461-483`,
  `services/access_stamper.py:191-240`.
- Live data: `utils/tiled_helpers.py:62-223`, `services/tiled_service.py` (live
  `client[uid]`).
- Second agent: `claude/agent.py:24-103,170-500,630-637`,
  `claude/_internal/worker.py`, `claude/permission_manager.py`,
  `ui/panels/claude_panel.py:505-509,762-775`, `claude/_session_assembly.py:91`.
- Threads/settings: `utils/threads.py:452-466,830-861`,
  `plugins/settings_plugin.py:20-168`, `preferences/manager.py:277-301`.
- XPCS: `lightfall-endstation-7011/devices/andor.py`,
  `lightfall-endstation-7011/src/.../xpcs/binding.py`,
  `lightfall-endstation-7011/src/.../xpcs/plots.py:76-83` (g₂/metrics payload
  recorded by `xpcs_live`); design doc
  `lightfall-endstation-7011/docs/superpowers/specs/2026-06-05-xpcs-lightfall-port-design.md:138-145`.
