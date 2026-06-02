# Notebook Pipelines Design

> Consolidated revision. Earlier design-discussion exchanges (`>` Ron / `!` me / `$` Ron / `%` Ron) are preserved in git history on this branch (`c1fc484..c9681d5`).

## Overview

Add post-acquisition (and eventually online) data-processing pipelines to Lightfall, implemented as parameterized Jupyter notebooks executed by a separate headless service. Pipelines are distributed as **pip-installable Python packages** that expose a `lightfall_pipelines.pipeline` entry point — mirroring Lightfall's own plugin model (`AgentPlugin`, `EnginePlugin`, etc.). Lightfall submits jobs over NATS carrying a Tiled run UID and a job-scoped Tiled API key; the notebook reads the input run from Tiled, writes derived data back, and emits output run UIDs via Papermill's scrapbook.

The headless executor (`lightfall-pipelines`) mirrors the existing `lightfall.exporter` service (`src/lightfall/exporter/`): per-host NATS topic, request/reply for job submission and health, pub/sub for progress events, on-demand process spawn.

A scientist's mental model is "I authored a notebook, packaged it, pip-installed it on the workstation, and it runs against each new scan." Papermill makes that the literal implementation. Netflix's notebook-as-job pattern is prior art; NSLS-II's Raydata is the closest sibling system (see Related Work); Prefect and Marimo target adjacent problems we don't have.

## Goals

- Execute user-authored notebooks against a Tiled run, parameterized through a plugin manifest
- Per-pipeline Python environments — pipelines do not share a global env; each pipeline package installs into its own venv
- Notebook writes derived data back to Tiled via a thin Lightfall-supplied `TiledWriter` wrapper that stamps `parent_run_uid` + `pipeline_provenance` automatically
- Output runs inherit the input run's `access_blob`/`tiled_access_tags` so the original user retains access
- Token strategy that survives access-token expiry without an executor-side refresh loop (job-scoped Tiled API keys)
- Pipelines distributed as standard Python packages, discoverable via entry points — uniform with Lightfall's other plugin types
- Reuse Lightfall's existing IPC, ping/spawn, and progress-event idioms

## Non-Goals (Phase 1)

- Online / during-acquisition triggers (designed-for, not built — see Phase 2+)
- Bulk-reprocess UI (deferred)
- Persistent-kernel pipelines for cross-batch state (Phase 2+)
- Container-per-pipeline isolation (Phase 2+ if needed)
- Remote-host targeting from the UI (executor is local per workstation; cross-host routing possible but unexposed)
- Per-pipeline resource limits (cgroup-style) — trust model gives full freedom to notebooks; beamline scientists own quality
- Managing system-side libraries (HDF5 ABI, CUDA toolkit, GPU drivers) — registry-author's responsibility
- Network policy from kernels — deployment / firewall concern, not the design's

## Stage 0 dependency: als-tiled user-scoped API key endpoint

The token strategy below (job-scoped Tiled API keys) requires `als-tiled` (in-team at `https://git.als.lbl.gov/ncs/als-tiled`, deployed `bcgtiled:/opt/als-tiled`, local clone `~/PycharmProjects/als-tiled`) to expose an endpoint that:

- Accepts a Keycloak bearer token
- Mints a TTL-bounded user-scoped Tiled API key (TTL passed by client up to a deployment-enforced max, e.g. 24h)
- Returns the key + its expiry timestamp

Once shipped, this primitive also lets Lightfall itself move off the access-token refresh treadmill for its own data-browser calls — independent benefit. Implementation lands first, in als-tiled, before any Lightfall work depends on it.

## Architecture

### Components

1. **`lightfall.pipelines.PipelineClient`** (new, in-process in Lightfall) — mints a job-scoped Tiled API key, builds the job message, submits over NATS, tracks progress, revokes the key on completion.
2. **`lightfall.pipelines.TriggerManager`** + `Trigger` subclasses (new, in-process in Lightfall) — owns the user's configured triggers, hooks `BaseEngine.subscribe()` for engine-driven triggers, routes each fire to `PipelineClient.submit()`.
3. **`lightfall-pipelines` service** (new, headless console script) — subscribes to NATS, discovers pipeline plugins via entry points, manages per-pipeline-package venv cache, dispatches Papermill runs in subprocesses, harvests scrapbook outputs, publishes progress.
4. **`PipelinePlugin` base class + `lightfall_pipeline` SDK** (new, separately pip-installable) — base class for pipeline authors; `lightfall_pipelines.notebook` helpers including the `TiledWriter` wrapper that auto-stamps provenance and `parent_run_uid` on derived runs.
5. **UI surface** in Lightfall — Data Browser context-menu action ("Run pipeline…"), pipeline picker / parameter dialog, "Pipeline Jobs" dock panel, "Pipeline Triggers" settings panel.

### Topology

```
Lightfall                                       lightfall-pipelines (per host)
┌───────────────────────────────┐           ┌──────────────────────────────────┐
│ TriggerManager                │           │ PipelineService                  │
│  ├ RunStartTrigger ───────┐   │  NATS req │   subscribe                      │
│  ├ RunEndTrigger ─────────┤   │ ─────────►│     lightfall.pipeline.<host>        │
│  └ ManualTrigger ─────────┤   │           │                                  │
│  hooks BaseEngine.subscribe() │  NATS rep │ Job Queue (sequential, Phase 1)  │
│                           ▼   │ ◄─────────│                                  │
│ PipelineClient                │           │ Plugin Registry                  │
│  - mint API key (Tiled)       │           │   entry_points(group=            │
│  - submit job ────────────────┘           │     "lightfall_pipelines.pipeline")  │
│  - track progress             │  pub/sub  │                                  │
│  - revoke on completion       │ ◄─────────│ EnvCache  (per-package venvs     │
│                               │           │            (pkg_name, version))  │
│ Pipeline Jobs panel           │           │                                  │
│ Pipeline Triggers settings    │           │ PapermillRunner                  │
│ Data Browser context menu     │           │   subprocess in pipeline's venv  │
└───────────────────────────────┘           │   inject env vars                │
                                            │   harvest scrapbook              │
                                            └──────────────────────────────────┘
                                                          │
                                                          ▼ reads/writes via Tiled API key
                                                ┌──────────────────────┐
                                                │ Tiled                │
                                                │   input run          │
                                                │   output runs        │
                                                │   executed-notebook  │
                                                │   (file path entry)  │
                                                └──────────────────────┘
                                                          ▲
                                                          │ executed.ipynb on disk
                                                ┌──────────────────────┐
                                                │ /data/lightfall-pipelines│
                                                │   /runs/<input_uid>  │
                                                │   /<job_id>.ipynb    │
                                                └──────────────────────┘
```

### NATS topic routing

Per-host, mirroring the exporter:

- `lightfall.pipeline.<hostname>` — request/reply, job submission
- `lightfall.pipeline.<hostname>.ping` — request/reply, health
- `lightfall.pipeline.<hostname>.list` — request/reply, returns discovered pipelines + parameter schemas
- `lightfall.pipeline.<hostname>.refresh` — request/reply, re-scan entry points (after a `pip install`)
- `lightfall.pipeline.<hostname>.progress` — pub/sub, progress events per job

There is **no** auto-trigger NATS topic. Triggers fire from inside Lightfall via `BaseEngine.subscribe()` and submit jobs through the normal request path.

### On-demand spawn

Same as exporter (`src/lightfall/exporter/cli.py`):

1. Lightfall pings `lightfall.pipeline.<host>.ping` with a 1s timeout.
2. On reply → service running, submit the job.
3. On timeout → `subprocess.Popen(["lightfall-pipelines", "--nats", url])`.
4. Retry ping with bounded backoff; surface a toast if startup fails.

Phase 1 is on-demand only. Phase 2+ ships a `lightfall-pipelines.service` systemd unit so triggers can fire when Lightfall is not running.

## Pipeline plugin model

### `PipelinePlugin` base class

Lives in `lightfall_pipelines.plugin`. Parallels `AgentPlugin` / `EnginePlugin` in shape:

```python
from lightfall_pipelines.plugin import PipelinePlugin
from importlib.resources import files

class ReduceSaxsPipeline(PipelinePlugin):
    name = "reduce_saxs"
    display_name = "Reduce SAXS"
    description = "Azimuthal integration + flat-field correction"

    # Notebook source (packaged resource, not filesystem path)
    notebook = files("als_saxs_pipelines") / "reduce.ipynb"

    # Parameter schema (used to build the UI parameter form)
    parameters_schema = {
        "roi_x": {"type": "array<int>", "default": [0, 1024], "ui": "range"},
        "subtract_dark": {"type": "bool", "default": True},
    }

    # Tags + access policy for output runs
    output_tags = ["saxs", "reduced"]
    inherit_input_access_blob = True       # default

    # Storage policy for the executed notebook
    store_executed_notebook = True          # default

    # Optional input-run gating (rejected at submit time if mismatch)
    expects = {
        "plan_name": ["count", "scan"],
        "descriptor_keys": ["pilatus_image"],
    }
```

### Entry-point discovery

The pipeline's package declares its plugin class via standard `[project.entry-points]`:

```toml
[project.entry-points."lightfall_pipelines.pipeline"]
reduce_saxs = "als_saxs_pipelines.reduce:ReduceSaxsPipeline"
```

The executor at startup runs `importlib.metadata.entry_points(group="lightfall_pipelines.pipeline")` to find all installed pipeline plugins. Adding a pipeline = `pip install <package>` in the executor's host env; calling `lightfall.pipeline.<host>.refresh` re-scans.

Discovery is uniform across install mechanisms: pip from PyPI, from a private GitLab Package Registry, from `git+ssh://`, from a local wheel — all just work because entry-point lookup doesn't care how the package got there. The choice of installer (`pip`, `uv pip`, `pdm`, `poetry`, …) is a deployment preference, not an architectural one. Workstation-managed install vs. ops-managed install is also a deployment choice.

### Beamline plugins repository (multi-package monorepo)

A beamline typically has multiple kinds of plugins (panels, agents, pipelines) with different dependencies. To prevent dep leakage (a panel plugin should not pull pyFAI), use a multi-package monorepo with one sub-package per plugin family:

```
als-saxs/                              # one repo, one git history
├── README.md
├── packages/
│   ├── als-saxs-panels/               # dist: als-saxs-panels
│   │   ├── pyproject.toml             #   dependencies = [lightfall, PySide6]
│   │   │   [project.entry-points."lightfall.panels"]
│   │   └── src/als_saxs_panels/
│   ├── als-saxs-pipelines/            # dist: als-saxs-pipelines
│   │   ├── pyproject.toml             #   dependencies = [
│   │   │                              #     "lightfall-pipelines",  # SDK only
│   │   │                              #     "numpy", "scipy", "pyFAI", "scrapbook",
│   │   │                              #   ]
│   │   │   [project.entry-points."lightfall_pipelines.pipeline"]
│   │   │   reduce_saxs = "als_saxs_pipelines.reduce:ReduceSaxsPipeline"
│   │   └── src/als_saxs_pipelines/
│   │       ├── __init__.py
│   │       ├── reduce.py              # ReduceSaxsPipeline class
│   │       └── reduce.ipynb           # packaged resource
│   └── als-saxs-agents/               # dist: als-saxs-agents
│       └── ...
```

Each sub-package is a standard PEP 621 distribution. `pip install ./packages/als-saxs-pipelines` (or installing a built wheel from a registry) works regardless of which packaging tool a developer uses locally — vanilla pip + stdlib `venv`, uv, pdm, poetry, and hatch all work because the wire format (wheels + PEP 621 metadata + entry points) is tool-agnostic.

For dev convenience, a team may optionally add a tool-specific workspace declaration at the repo root (`[tool.uv.workspace]`, pdm workspaces, etc.) for one-command "install all packages editable" — but this is purely a local-dev choice and doesn't affect distribution or executor consumption.

For tiny beamlines with only one or two pipelines and no other plugin types, a single-package layout with `[project.optional-dependencies]` is also acceptable.

## Environment management

### Per-pipeline-package venv

Each installed pipeline package gets its own venv at:

```
~/.cache/lightfall-pipelines/envs/<package_name>@<package_version>/
```

Built lazily on first job that names a pipeline in that package. Subsequent jobs reuse. Different package versions get different venvs — upgrading `als-saxs-pipelines` from 0.4.1 to 0.4.2 builds a fresh venv and leaves the old one cached.

Build step uses standard-library tooling by default:

```sh
python -m venv ~/.cache/lightfall-pipelines/envs/<pkg>@<version>
~/.cache/lightfall-pipelines/envs/<pkg>@<version>/bin/pip install <pkg>==<version>
```

If `uv` is available on PATH, the executor prefers `uv venv` + `uv pip install` purely as a speed optimization (typically ~10× faster builds). Functional behavior is identical either way — sites with policies against uv (locked-down workstations, restricted HPC environments) get the same result with stdlib `venv` + pip. The choice is auto-detected; no configuration.

Reproducibility comes from the executor's install pin (`pkg==X.Y.Z`) plus whatever the dist itself ships (a `requirements.txt`, a `uv.lock`, a `pdm.lock`, …, all consumed via their respective installers if present). Lock files are nice-to-have, not required.

A kernel spec is registered against the venv's python as `lightfall-pipelines:<pkg>@<version>`; Papermill is invoked with that kernel name. Kernels are pinned per-pipeline-package, not leaked into the user's global Jupyter config.

### Escape hatch: pre-prepared interpreter

For facility-managed envs (NSLS-II shared-NFS conda envs, beamline-curated venvs), the plugin can override env management entirely:

```python
class FacilityEnvPipeline(PipelinePlugin):
    python_executable = "/nsls2/conda/envs/2024-3.0-py311-tiled/bin/python"
```

When this is set, the executor does no venv management — it just spawns Papermill against the named interpreter. The author owns dep correctness.

### Cache cleanup

No automatic GC. Envs accumulate intentionally (rebuild cost is high; cached venvs are not a leak). A `lightfall-pipelines cleanup --unused` subcommand removes envs for which no plugin is currently discovered.

## Auth: job-scoped Tiled API keys

### Mint flow (per job)

1. User triggers a pipeline (manually or via a configured Trigger).
2. `PipelineClient.mint_job_key()` calls als-tiled's user-scoped API-key endpoint (per Stage 0), passing the user's current Keycloak bearer + a TTL covering expected job duration (default 24h).
3. Tiled returns an API key bound to the user's identity. Lightfall embeds it in the NATS job message.
4. Executor receives the job, exports `TILED_API_KEY=<key>` + `TILED_URL=<url>` into the kernel subprocess env. The bootstrap parameter cell (see Notebook authoring contract) makes them available to the notebook.
5. Notebook reads / writes Tiled with the API key for the entire job.
6. On job completion (success or failure), `PipelineClient` calls Tiled's `DELETE /api/v1/auth/apikey/{prefix}` to revoke. Best-effort; the TTL is the backstop.

The key never travels through the executor's logs, the executed notebook (env vars are not serialized into nbformat), or Lightfall's persistent state.

### Generalization: `lightfall.auth.mint_job_key()`

The mint helper lives in `lightfall.auth.mint_job_key(lifetime, scopes, note)` — not in `lightfall.pipelines` — because the same primitive solves the same problem for tsuchinoko, future remote-executor offloads, and any other Lightfall-dispatched headless workload.

### Access-blob inheritance for output runs

Per-entry authz is live in ALS Tiled (`feedback_tiled_access_blob_path` memory). Pipeline output runs must inherit the input run's `access_blob` / `tiled_access_tags` so the original user retains access to derived data. Mechanism:

- Lightfall fetches the input run's `access_blob` at submit time, includes it in the job message.
- Executor exports `Lightfall_INPUT_ACCESS_BLOB` (JSON) in the kernel env.
- The `lightfall_pipelines.notebook.TiledWriter` wrapper passes the blob into every write, merging the plugin's `output_tags` into the inherited tag list.
- Notebook authors using the wrapper get correct authz inheritance without thinking about it. Those bypassing the wrapper own the correctness themselves.

### Auto-trigger identity

Manual and auto-triggered jobs both use the user's current session at submit time. For automatic triggers (`RunEndTrigger`, etc.), the trigger fires inside Lightfall — which still has the user's session in hand — and the mint happens then, with a TTL covering the expected pipeline runtime.

If the user logs out / closes Lightfall before the executor finishes, the API key keeps working until its TTL expires (that's the whole point of moving off the bearer). If Lightfall isn't running at all when a binding *would* have fired, the trigger doesn't fire at all (Phase 1 limit — see Non-Goals).

## Trigger model

### `TriggerManager` (Lightfall-side, single instance)

Owns the user's configured triggers, persisted in Lightfall's settings backend (no YAML file in a registry, no defaults shipped — each beamline configures its own). Subscribes to `BaseEngine.subscribe()` (`src/lightfall/acquire/engine/base.py:396`) so triggers operate against any engine (Bluesky, mock, future).

Each configured trigger is a record:

```
{
  type: run_start | run_end,
  filter: { plan_name?: str | [str], tags_includes?: [str], start_doc_match?: {...} },
  pipeline: "reduce_saxs",
  parameter_overrides: { ... },
}
```

`ManualTrigger` isn't configured — it's instantiated per-click by the Data Browser context menu.

### `Trigger` base class

```python
class Trigger(ABC):
    @abstractmethod
    def attach(self, manager: TriggerManager) -> None: ...
    @abstractmethod
    def detach(self) -> None: ...
```

Subclasses:

- `RunStartTrigger` — engine-subscribed; fires on `'start'` doc matching `filter`.
- `RunEndTrigger` — engine-subscribed; fires on `'stop'` doc matching `filter` (pulls the matching `'start'` doc by `run_start` ref).
- `ManualTrigger` — no engine hook; fires from Data Browser invocations with explicit `(pipeline, run_uid, parameters)`.

The trigger framework is reusable beyond pipelines — anything that wants "fire X on run-end" (auto-export, auto-logbook entry) can register a Trigger subclass against the same manager. Phase 1 only ships pipeline-targeted triggers.

### Filter predicates (Phase 1, fixed set)

- `plan_name: str | list[str]` — exact match or any-of
- `tags_includes: str | list[str]` — at least one of these in the start doc's `tags` field
- `start_doc_match: dict[str, Any]` — exact-equality match on top-level start-doc keys

Free-form expressions (jq-style) are out of scope; revisit only if a real binding can't be expressed in this set.

### Loop safety

Phase 1 triggers hook `BaseEngine.subscribe()`, which only emits docs for runs *executed by the engine*. Pipeline-derived runs are written directly to Tiled by the notebook — they never pass through any engine — so no start/stop doc is emitted to Lightfall's subscribers about them. **Therefore no loop is possible in Phase 1; no sentinel tag or filter exclusion is needed.**

(If Phase 2+ adds a "Tiled-poll" trigger that watches Tiled directly for new entries, the loop concern returns and the design will need a sentinel like `metadata.start.lightfall_pipeline_output = true` plus a filter that excludes it. Phase 1 doesn't need this.)

### Configuration UI: Pipeline Triggers settings panel

New settings panel (`lightfall.ui.panels.pipeline_triggers`) with a table of configured triggers + add/edit/delete dialogs. Each trigger's row shows: type, filter summary, target pipeline, last fired timestamp. Persisted via the existing settings backend.

## Wire formats

### Job message (`lightfall.pipeline.<host>` request)

```json
{
  "job_id": "uuid4",
  "tiled_url": "https://bcgtiled.als.lbl.gov/api/v1",
  "api_key": "<job-scoped key minted by Lightfall>",
  "api_key_expires_at": "2026-05-16T18:00:00Z",
  "input_run_uid": "abcdef…",
  "input_access_blob": { ... },
  "pipeline": "reduce_saxs",
  "parameters": {
    "roi_x": [0, 1024],
    "subtract_dark": true
  },
  "user_id": "rpandolfi",
  "requested_by": "lightfall@bcg-workstation-3",
  "submitted_at": "2026-05-15T20:14:00Z"
}
```

- `pipeline` matches a discovered `PipelinePlugin.name`.
- `parameters` is unioned with manifest defaults; manifest declares the schema.
- `input_access_blob` is opaque — opaque JSON passed through to the TiledWriter wrapper.

### Reply (synchronous, ≤100ms after submit)

```json
{ "job_id": "...", "status": "queued", "position": 0 }
```

or `{ "error": "<message>", "code": "unknown_pipeline" | "auth_invalid" | "input_run_unreachable" | "schema_mismatch" }`.

**Idempotency.** The executor maintains a per-process LRU set of recent `job_id`s (size ~1024). A redelivered or duplicate `job_id` replies `{status: "already_processed", job_id, current_status}` instead of re-running. Baked in; no separate protocol.

### Progress events (`lightfall.pipeline.<host>.progress` pub)

```json
{
  "job_id": "...",
  "status": "queued" | "env_building" | "running" | "completed" | "failed",
  "detail": "Building env als-saxs-pipelines@0.4.1 (first use)…",
  "input_run_uid": "abcdef…",
  "output_run_uids": ["xyz…"],
  "executed_notebook_path": "/data/lightfall-pipelines/runs/abcdef.../<job_id>.ipynb",
  "error": null | "<traceback excerpt>",
  "ts": "2026-05-15T20:14:42Z"
}
```

Lightfall's `PipelineClient` accumulates these into the Pipeline Jobs panel.

## Notebook authoring contract

### Bootstrap (auto-injected parameter cell)

Before user cells run, the executor injects a Papermill parameter cell:

```python
# Auto-injected by lightfall-pipelines
import os, json
from lightfall_pipelines.notebook import TiledWriter, get_input_run, get_provenance

TILED_URL = os.environ["TILED_URL"]
TILED_API_KEY = os.environ["TILED_API_KEY"]
Lightfall_INPUT_RUN_UID = os.environ["Lightfall_INPUT_RUN_UID"]
Lightfall_INPUT_ACCESS_BLOB = json.loads(os.environ["Lightfall_INPUT_ACCESS_BLOB"])
Lightfall_PIPELINE_PROVENANCE = json.loads(os.environ["Lightfall_PIPELINE_PROVENANCE"])

# Convenience: open the input run client
input_run = get_input_run()  # configured tiled client + Lightfall_INPUT_RUN_UID

# … user-declared parameters injected here by Papermill …
```

The bootstrap params are env-var-sourced (not Papermill-parameter-sourced) so the secret API key doesn't end up serialized into the executed notebook.

### `lightfall_pipelines.notebook.TiledWriter` wrapper

Ships in the `lightfall-pipelines` SDK. Wraps `bluesky.callbacks.tiled_writer.TiledWriter` and:

- Reads `Lightfall_INPUT_RUN_UID` / `Lightfall_INPUT_ACCESS_BLOB` / `Lightfall_PIPELINE_PROVENANCE` from env.
- Auto-stamps `metadata.start.parent_run_uid = Lightfall_INPUT_RUN_UID` on every run written.
- Auto-stamps `metadata.start.pipeline_provenance = Lightfall_PIPELINE_PROVENANCE` (`{pipeline_name, pipeline_package, pipeline_package_version, python_executable, env_hash}`).
- Auto-merges `Lightfall_INPUT_ACCESS_BLOB.tiled_access_tags + plugin.output_tags` into the write's tags so access inheritance works.
- Honors `bluesky.TiledWriter`'s `batch_size` default (10000) — never set 1 (per `feedback_tiled_writer_batch_size` memory).

Usage in a notebook is one line:

```python
tw = TiledWriter(client)   # all the stamping happens automatically
# subscribe RE.subscribe(tw) or feed documents directly
```

Notebook authors who bypass the wrapper own the stamping themselves; convention is documented but not enforced.

*Cross-project reuse:* tsuchinoko's adaptive-experiment service already needs the same derived-run stamping. The shared helper should live in a place both projects can install (e.g., the same `lightfall-pipelines` SDK package, or factored further). Existing tsuchinoko pattern lives on the `Lightfall-refactor` branch at `~/PycharmProjects/tsuchinoko-phase1` — inspect before final shaping.

### Output discovery (scrapbook + UID-delta fallback)

A notebook may optionally signal its outputs via scrapbook:

```python
import scrapbook as sb
sb.glue("output_run_uids", [u1, u2])
sb.glue("figures", fig, encoder="display")   # optional rendering artifact
sb.glue("metrics", {"chi2": 1.04})
```

The executor reads these after execution via `sb.read_notebook(executed).scraps`. Missing `output_run_uids` is **not** an error.

**Fallback.** The bootstrap snapshots the set of run UIDs visible under the user's catalog at job start. After execution, if no scrapbook `output_run_uids` is present, the executor diffs the post-run set and reports the delta. This is best-effort (a concurrent write by another process could be misattributed) but means a fully unmodified notebook still produces input→output linkage in the Pipeline Jobs panel. Explicit `sb.glue` always wins.

## Executed notebook storage

Following the AreaDetector pattern: **file lives on disk, path is registered in Tiled, metadata references the path**. Tiled's database holds the pointer, not the blob.

- Executor writes the executed notebook to a configurable filesystem path: default `/data/lightfall-pipelines/runs/<input_run_uid>/<job_id>.ipynb`. Overridable via `--notebook-store <path>`.
- A Tiled entry is registered on the *output run* with:

```json
{
  "executed_notebook": {
    "path": "/data/lightfall-pipelines/runs/<input_run_uid>/<job_id>.ipynb",
    "size_bytes": 824132,
    "sha256": "..."
  }
}
```

- The Pipeline Jobs panel's "Open executed notebook" resolves `executed_notebook.path` and opens it via the configured Jupyter/VS Code launcher.
- Per-plugin opt-out: `PipelinePlugin.store_executed_notebook = False`.
- No automatic GC in Phase 1. Notebooks are small (100 KB–few MB), valuable for provenance; if disk pressure ever matters, ops uses `find … -mtime +N -delete`.

The `--notebook-store` directory must be reachable from any workstation that wants to open the executed notebook (NFS, SMB, or single-machine). On split-host deployments this is the same shared storage that detector data lands on.

## Output rendering (Visualization panel)

Output runs ride through Lightfall's existing **Visualization panel** (`src/lightfall/visualization`, `BaseVisualization` ABC). Two pathways, both flowing through the same panel:

1. **Procedural (free).** A pipeline's output runs are themselves `BlueskyRun` entries in Tiled. Existing visualization widgets (`plot_1d`, `image_stack`, `heatmap`, `scatter`, `table`, etc., registered via `VisualizationRegistry`) score `can_handle(run)` on them like any other run. Selecting an output UID in the Tiled browser auto-picks the best widget. No notebook effort required.
2. **Pre-prepared (notebook-glued artifacts).** When the notebook glues scrapbook artifacts (`figures`, `html_report`), the executor harvests them and writes them as side-car Tiled entries under the output run with `metadata.source = "pipeline_artifact"`. Phase 1.5 adds a `ScrapbookViz(BaseVisualization)` widget that scores 100 for runs carrying such children, rendering them in a tabbed layout. Until that widget lands, image artifacts already render via the existing `image_stack` widget.

The notebook author chooses their effort level: write data and walk away (procedural) or glue exactly the figure they want shown (pre-prepared).

## UI surface (Phase 1)

### Data Browser context menu: "Run pipeline…"

Right-click a run → opens a dialog with:

1. `QComboBox` of available pipelines from `lightfall.pipeline.<host>.list`.
2. Auto-generated parameter form from the selected plugin's `parameters_schema` (reuse `lightfall.plugins.agents.panel_builder` patterns where applicable).
3. "Submit" mints the API key, sends the job, switches focus to the Pipeline Jobs panel.

### Pipeline Jobs dock panel

New `BasePanel`:

- Top: queue depth, executor host, status indicator.
- Table rows: `job_id`, pipeline, input UID, status, started, duration, output count.
- Row actions: "Open executed notebook" (resolves the disk path from the registered Tiled entry), "Show outputs" (jumps to output UIDs in the Tiled browser).
- Cancellation is Phase 2+ — add a `lightfall.pipeline.<host>.cancel` request later.

### Pipeline Triggers settings panel

Manages configured triggers. Table + add/edit/delete dialogs. Persists via Lightfall's settings backend.

### `PipelineClient` signals (in-process)

Consumed by the Jobs panel: `sigJobQueued`, `sigJobProgress`, `sigJobCompleted`, `sigJobFailed`. Owns the API-key mint and revoke calls.

## Data flow: one job

```
1. User right-clicks run UID=R in Data Browser, selects "Run pipeline… → reduce_saxs"
2. Lightfall PipelineClient:
     a. fetch R's access_blob from Tiled
     b. POST to als-tiled API-key-mint endpoint → key K (24h TTL)
     c. ping lightfall-pipelines (spawn if absent)
     d. publish job message to lightfall.pipeline.<host>
3. Executor PipelineService:
     a. validate job (pipeline plugin discovered, schema match, R reachable)
     b. enqueue, reply { status: queued }
     c. dequeue → resolve plugin's package → check env cache
        - cache miss → progress: env_building → venv + pip install <pkg>==<ver>
        - cache hit → progress: running
     d. spawn subprocess (venv's python, kernel_name=lightfall-pipelines:<pkg>@<ver>)
     e. invoke Papermill in-memory with input nb + bootstrap params + user params
4. Notebook (in subprocess):
     a. read input run via TILED_API_KEY
     b. process
     c. write derived run(s) via lightfall_pipelines.notebook.TiledWriter (auto-stamps provenance + access)
     d. (optional) sb.glue("output_run_uids", [U1, U2]); glue figures/metrics
5. Executor PapermillRunner:
     a. on subprocess exit 0:
        - harvest scrapbook from in-memory NotebookNode
        - if no output_run_uids, use UID-delta fallback
        - write executed.ipynb to /data/lightfall-pipelines/runs/R/<job_id>.ipynb
        - register Tiled entry on each output run with executed_notebook pointer
        - publish progress: completed with output_run_uids
     b. on exit != 0: write executed.ipynb (containing traceback) to disk same way; publish progress: failed with traceback excerpt
6. Lightfall PipelineClient:
     a. update Pipeline Jobs panel
     b. revoke API key K (DELETE on Tiled)
```

## Error handling

| Failure mode                                            | Where caught          | User-visible result                                                       |
| ------------------------------------------------------- | --------------------- | ------------------------------------------------------------------------- |
| Unknown `pipeline` in job (not entry-point-discovered)  | Service validate      | NATS reply `{error, code: unknown_pipeline}`                              |
| API-key mint fails (Tiled 5xx)                          | Lightfall client          | Toast "Could not authorize pipeline job"; job not submitted               |
| Env build fails (`pip install …` non-zero)              | Executor env step     | Progress: failed, detail = last 50 lines of build log                     |
| Plugin's `expects` mismatch with input run              | Service validate      | NATS reply `{error, code: schema_mismatch}` with detail                   |
| Notebook raises exception                               | Papermill subprocess  | Executed notebook records traceback; executor publishes failed with excerpt |
| Subprocess timeout (`PipelinePlugin.timeout_seconds`)   | Executor watchdog     | SIGTERM → SIGKILL; failed with `code: timeout`                            |
| Scrapbook missing `output_run_uids`                     | Executor harvest      | UID-delta fallback runs; if delta empty, `output_run_uids: []` (not error) |
| Tiled-side authz failure on output write                | Tiled                 | Notebook errors; traceback propagates via Papermill                       |
| API key revoke fails on completion                      | Lightfall cleanup         | Log; key still expires at TTL                                             |
| Executor crash mid-job                                  | Lightfall watcher         | After `2 × ping_interval` of no progress + no ping → mark `lost`; user can resubmit |
| Duplicate `job_id` (redelivery)                         | Service               | Reply `{status: already_processed, current_status}`; no re-run            |
| Trigger fires when user is logged out                   | TriggerManager        | Trigger silently skipped; telemetry log entry                             |

## Testing strategy

### Unit (executor)

- `tests/pipelines/test_service.py` — job parsing, queue behavior, scrapbook harvest, error mapping, idempotency dedup
- `tests/pipelines/test_env_cache.py` — `(pkg, version)` hashing, lazy build mocking, cache hit / miss
- `tests/pipelines/test_papermill_runner.py` — bootstrap injection, env var forwarding, timeout, exit-code handling against `tests/fixtures/pipelines/echo/` (a tiny fixture plugin package)
- `tests/pipelines/test_plugin_discovery.py` — entry-point loading, schema validation, `expects` matching

### Unit (Lightfall-side)

- `tests/pipelines/test_pipeline_client.py` — mint, submit, revoke, signal emission
- `tests/pipelines/test_trigger_manager.py` — engine subscription, filter predicate evaluation, RunStart/RunEnd/Manual subclass behavior
- `tests/pipelines/test_tiled_writer_wrapper.py` — provenance + parent_run_uid stamping, access-blob merge, env-var sourcing

### Integration

- `tests/pipelines/test_e2e.py` — runs against a real Tiled (`bcgtiled` test instance per `MEMORY.md::reference_als_tiled_deploy`) plus a local NATS:
  1. Author a run via `bluesky.TiledWriter`
  2. `pip install` a fixture pipeline package into a temp venv
  3. Spawn `lightfall-pipelines` subprocess pointed at that venv
  4. Submit a job via NATS
  5. Assert progress events arrive in order
  6. Assert output runs exist in Tiled with inherited access_blob + parent_run_uid + pipeline_provenance
  7. Assert executed-notebook file exists on disk + Tiled entry references it
- One e2e test exercises auto-trigger via a synthetic plan + `RunEndTrigger`.

### Pipeline-side contract tests (in beamline plugins repo)

Each beamline's plugin package ships a `tests/test_pipelines.py` that, for each pipeline, runs Papermill against a fixture input run and asserts: notebook executes, expected outputs land in Tiled, provenance correct. Stays in the beamline repo so scientists own their tests alongside their pipelines.

## Related work: Raydata (NSLS-II)

Wijesinghe, Barbour, Wiegart, Rakitin et al., *"Bluesky and Raydata: An Integrated Platform for Adaptive Experiment Orchestration"* (SCW 2024, doi:10.1109/SCW63240.2024.00271).

Where it agrees with this design:

- Papermill as the execution primitive.
- Per-analysis Python envs.
- Manual + automatic triggers.
- Tiled as the data plane.
- "Notebooks unmodified, papermill executes them as-is" stance.
- Metadata-driven workflow selection (we cover this via trigger filters).

Where this design deliberately diverges:

| Concern              | Raydata                                | This design                                              |
| -------------------- | -------------------------------------- | -------------------------------------------------------- |
| IPC                  | Sirepo internal broker                 | Lightfall's existing NATS                                    |
| GUI                  | Browser (Sirepo)                       | Qt desktop (Lightfall's existing panels)                     |
| RunEngine access     | Via Queueserver/ZeroMQ                 | Direct (`BaseEngine.subscribe()`)                        |
| Output linkage       | Filesystem artifacts only              | Explicit `parent_run_uid` graph in Tiled (auto-stamped) |
| Long-job creds       | Not addressed                          | Job-scoped Tiled API key                                 |
| Access inheritance   | Not addressed                          | Default; merged from input run                           |
| Pipeline distribution| "Provided as-is" (filesystem)          | pip-installable plugin packages + entry points           |
| Env mgmt             | Conda from NFS                         | Per-package venvs cached by `(pkg, version)`             |

## Phase 2+ (designed-for)

The Phase 1 wire format admits these without protocol changes:

- **Online during acquisition.** New trigger type (`RunEventTrigger`?) submits the same job with `parameters.partial = true, parameters.descriptor_offset = N`; notebook reads up-to-N events from Tiled. Executor / scrapbook flow unchanged.
- **Bulk reprocessing.** Iterate UIDs and submit jobs through `PipelineClient`. No executor change.
- **Persistent kernels.** A plugin flag `kernel: persistent` switches the runner from Papermill to direct `jupyter_client` with kernel reuse. Same job message, same harvest.
- **Cancellation.** Add a `lightfall.pipeline.<host>.cancel` request taking a `job_id`; executor SIGTERMs the subprocess.
- **`ScrapbookViz` widget (Phase 1.5).** `BaseVisualization` subclass rendering pipeline artifacts in a tabbed layout.
- **Remote executor offload.** Replace `PapermillRunner` with `SlurmRunner` / `NerscRunner`; wire format unchanged.
- **Systemd unit for the executor** so triggers can fire without Lightfall running. Pairs with a fallback identity (service-account API key, output tagged `auto-triggered-by:<service>`).
- **Tiled-poll trigger type.** Watches Tiled directly for new entries; would reintroduce the loop-guard requirement (sentinel metadata flag + filter exclusion).

## Open questions

The big architectural decisions are settled. Remaining items, mostly implementation choices:

1. **Filter predicate evaluation surface.** The fixed-set Phase 1 filter (`plan_name`, `tags_includes`, `start_doc_match`) is small enough that a hand-written evaluator is fine; revisit if it grows.
2. **Tiled API key mint endpoint shape on als-tiled.** Path, method, request/response body — finalize during Stage 0 in als-tiled. Likely `POST /api/v1/auth/apikey/new` with `{lifetime, scopes, note}`.
3. **`lightfall_pipelines` SDK package boundary.** Single dist with `[project.optional-dependencies]` for executor vs. SDK consumers, or split into multiple dists (`lightfall-pipelines-sdk` for pipeline authors, `lightfall-pipelines` for the executor)? Lean single-dist with extras for Phase 1.
4. **Notebook-store directory permission model.** Who owns `/data/lightfall-pipelines/runs/`? Per-user subdirs, or shared with group-write? Probably ops-decided per deployment; document, don't enforce.
5. **Tsuchinoko shared `TiledWriter` factoring.** After inspecting the `Lightfall-refactor` branch, decide whether the wrapper goes in `lightfall-pipelines` (and tsuchinoko depends on it) or in a more neutral location (`bluesky-utils`-style package). Defer until comparison is done.

## Implementation skeleton

Staged so each merges independently and the executor is usable from the CLI before Lightfall UI lands.

**Stage 0 (als-tiled):**

1. Add user-scoped API-key mint endpoint to als-tiled (Keycloak bearer → user-scoped key with TTL). Land + deploy first; everything else is gated on this.

**Stage 1 (Lightfall core shared primitives):**

2. `lightfall.auth.mint_job_key(lifetime, scopes, note)` + tests (also used by tsuchinoko).
3. `lightfall.acquire.triggers.{base, manager, run_start, run_end, manual}` + tests (general trigger framework; pipeline-specific routing in Stage 4).

**Stage 2 (executor + SDK):**

4. `lightfall_pipeline` SDK package: `PipelinePlugin` base class, `TiledWriter` wrapper, `notebook` helpers, parameter schema types + tests.
5. `lightfall_pipelines.executor` (the headless service): `PipelineService`, `PapermillRunner` (in-memory execution + scrapbook harvest + disk-write of executed notebook + Tiled-pointer registration), `EnvCache` (stdlib `venv` + `pip`; auto-prefers `uv` if available) + tests.
6. `lightfall-pipelines` console script + meta endpoints (list, refresh, ping) + tests.

**Stage 3 (Lightfall-side integration):**

7. `lightfall.pipelines.PipelineClient` + tests.
8. Data Browser context-menu integration + parameter dialog.
9. Pipeline Jobs dock panel + tests.
10. Pipeline Triggers settings panel + tests.

**Stage 4 (end-to-end):**

11. End-to-end integration test against `bcgtiled` + local NATS.
12. Reference beamline plugins repo (`als-saxs` monorepo with one working `als-saxs-pipelines` package).
13. Tsuchinoko `Lightfall-refactor` branch comparison; factor shared `TiledWriter` helper if profitable.

Stage 0 is a precursor for Stages 2–4 (mint endpoint must exist). Stage 1 has no dependencies. Stage 2 depends on Stage 1's `mint_job_key`. Stage 3 depends on Stage 2's wire format. Stage 4 ties everything.
