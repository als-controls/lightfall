# Notebook Pipelines Design

## Overview

Add post-acquisition (and eventually online) data-processing pipelines to LUCID, implemented as parameterized Jupyter notebooks executed by a separate headless service. LUCID dispatches jobs over NATS carrying a Tiled run UID and the credentials needed for the notebook to read inputs and write outputs back to Tiled. The notebook is the contract: scientists author and version notebooks in a per-beamline git repo, declare their own Python environments, and emit output run UIDs via Papermill's scrapbook so LUCID can link inputs to derived data.

The architectural shape directly mirrors the existing `lucid.exporter` service (`src/lucid/exporter/`): per-host NATS topic, request/reply for job submission and health, pub/sub for progress events, on-demand process spawn. A scientist's mental model is "I cloned the reduction notebook, tweaked two cells, and it ran on the new scan" — Papermill makes that the literal implementation. Netflix's notebook-as-job pattern is the prior art; Prefect (heavier orchestration) and Marimo (reactive exploration) target adjacent problems we don't have.

## Goals

- Execute user-authored notebooks against a Tiled run, parameterized by a manifest
- Per-pipeline Python environments — pipelines do not share a global env
- Notebook writes derived data back to Tiled and emits the new run UIDs
- Output runs inherit the input run's `access_blob`/`tiled_access_tags` so the original user retains access
- Token strategy that survives access-token expiry without an executor-side refresh loop
- Pipeline definitions live in a per-beamline git repo so they are reviewable, versioned, and reproducible
- Reuse LUCID's existing IPC, ping/spawn, and progress-event idioms

## Non-Goals (Phase 1)

- Online / during-acquisition triggers (designed-for, not built — see Phase 2+)
- Bulk-reprocess UI (deferred)
- Bindings UI for automatic-on-completion (YAML config only in Phase 1)
- Persistent-kernel pipelines for cross-batch state (Phase 2+)
- Container-per-pipeline isolation (Phase 2+ if needed)
- A `lucid_pipeline` helper module — the scrapbook contract is the only required surface; helpers come if needed
- Remote-host targeting from the UI (executor is local per workstation; cross-host routing possible but unexposed)

## Architecture

### Components

1. **`lucid.pipelines.PipelineClient`** (new, in-process) — LUCID-side client that mints a job-scoped Tiled API key, builds the job message, submits over NATS, and tracks progress.
2. **UI surface** (new, in LUCID) — context-menu action on a run, pipeline picker / parameter dialog, "Pipeline Jobs" dock panel.
3. **`lucid-pipelines` service** (new, headless console script) — subscribes to NATS, manages the registry git clone, manages per-pipeline env cache, dispatches Papermill runs in subprocesses, harvests scrapbook outputs, publishes progress.
4. **Pipeline registry repo** (external, per-beamline) — `pipelines.yaml` + `pipelines/*.ipynb` + `envs/*.yml` (or `requirements.txt`).
5. **`AutoTriggerSubscriber`** (new, runs inside the executor) — for Phase 1's automatic-on-completion, a NATS subscriber listens for Bluesky run-stop events and resubmits a job using bindings from `bindings.yaml`.

### Communication

```
LUCID                                   lucid-pipelines (per host)
┌──────────────────────┐                ┌─────────────────────────────┐
│ PipelineClient       │                │ PipelineService             │
│  - mint API key      │  NATS req      │   subscribe                 │
│  - submit job        │ ────────────►  │     lucid.pipeline.<host>   │
│                      │                │                             │
│                      │  NATS reply    │ Job Queue                   │
│                      │ ◄────────────  │   - sequential per-host MVP │
│                      │ {status:queued}│                             │
│ Pipeline Jobs panel  │                │ PapermillRunner             │
│  - queue depth       │  NATS pub/sub  │   - subprocess in pipe env  │
│  - progress          │ ◄────────────  │   - inject env vars         │
│  - completion        │  lucid.pipeline│   - harvest scrapbook       │
│                      │   .<host>.progr│                             │
│ Run-context menu     │                │ AutoTriggerSubscriber       │
│  "Run pipeline…"     │                │   - subscribe Bluesky stop  │
└──────────────────────┘                │   - resolve binding         │
                                        │   - resubmit job            │
                                        └─────────────────────────────┘
                                                       │
                                                       ▼ writes
                                              ┌──────────────────┐
                                              │ Tiled            │
                                              │  input run       │
                                              │  output runs     │
                                              └──────────────────┘
```

### Topic routing

Per-host, mirroring exporter:

- `lucid.pipeline.<hostname>` — request/reply, job submission
- `lucid.pipeline.<hostname>.ping` — request/reply, health
- `lucid.pipeline.<hostname>.list` — request/reply, returns available pipelines + parameter schemas from the loaded manifest (used by the LUCID picker dialog)
- `lucid.pipeline.<hostname>.refresh` — request/reply, `git pull` + revalidate manifest
- `lucid.pipeline.<hostname>.progress` — pub/sub, progress events

Auto-trigger subscription (Phase 1 dispatcher):

- `lucid.run.stopped` — pub/sub from `lucid.acquire.runengine` (or wherever bluesky stop docs are published). If a topic doesn't already exist, this design adds a tiny `RunStopNATSEmitter` callback to the RunEngine on the LUCID side, gated on a setting. Output payload: `{run_uid, plan_name, plan_args, tags, host, user_id}`.

### On-demand spawn

Same as exporter (`src/lucid/exporter/cli.py`, `src/lucid/ui/dialogs/export_dialog.py:94`):

1. LUCID pings `lucid.pipeline.<host>.ping` with a 1s timeout.
2. On reply → service is running, submit the job.
3. On timeout → `subprocess.Popen(["lucid-pipelines", "--nats", url, "--registry", git_url])`.
4. Retry ping with bounded backoff; surface a toast if startup fails.

The executor lives independently after spawn and is not lifecycle-managed by LUCID.

## Wire formats

### Job message (`lucid.pipeline.<host>` request)

```json
{
  "job_id": "uuid4",
  "tiled_url": "https://bcgtiled.als.lbl.gov/api/v1",
  "api_key": "<job-scoped key minted by LUCID>",
  "api_key_expires_at": "2026-05-16T18:00:00Z",
  "input_run_uid": "abcdef…",
  "pipeline": "reduce_saxs",
  "parameters": {
    "roi_x": [0, 1024],
    "subtract_dark": true
  },
  "user_id": "rpandolfi",
  "requested_by": "lucid@bcg-workstation-3",
  "submitted_at": "2026-05-15T20:14:00Z"
}
```

- `pipeline` matches a `name` in the registry's `pipelines.yaml`.
- `parameters` is unioned with manifest defaults; manifest declares the schema (see Registry).
- `api_key` is consumed *only* by the executor; never written to disk in the executed notebook (see Auth).
- `user_id` is metadata for audit / `derived-by` tagging; authorization is enforced via `api_key` scopes.

### Reply (synchronous, ≤100ms after submit)

```json
{ "job_id": "…", "status": "queued", "position": 0 }
```

or `{ "error": "<message>", "code": "unknown_pipeline" | "auth_invalid" | "registry_unavailable" }`.

### Progress events (`lucid.pipeline.<host>.progress` pub)

```json
{
  "job_id": "…",
  "status": "queued" | "env_building" | "running" | "completed" | "failed",
  "detail": "Building env saxs (first use)…",
  "input_run_uid": "abcdef…",
  "output_run_uids": ["xyz…"],         // only on completed
  "executed_notebook_path": "/var/lib/lucid-pipelines/runs/<job_id>.ipynb",
  "error": null | "<traceback excerpt>",
  "ts": "2026-05-15T20:14:42Z"
}
```

LUCID's `PipelineClient` accumulates these into the Pipeline Jobs panel.

### Scrapbook output contract

The notebook signals its outputs by glueing two names:

```python
import scrapbook as sb

# … notebook writes runs to Tiled via TiledWriter …
sb.glue("output_run_uids", [new_uid_1, new_uid_2])
sb.glue("output_files", ["/data/derived/foo.h5"])  # optional
```

The executor reads them post-execution via `sb.read_notebook(executed_nb_path).scraps`. Missing `output_run_uids` is **not** an error.

**Fallback for notebooks that opt out of scrapbook entirely.** The bootstrap cell (see Auth) records the user's current Tiled write head — specifically, the set of start-UIDs visible under the user's catalog at job start. After the kernel exits, if no `output_run_uids` scrap is present, the executor diffs the post-run set and reports the delta as `output_run_uids`. This is best-effort (a concurrent write by another process could be misattributed) but lets a fully unmodified notebook still produce input→output linkage in the Pipeline Jobs panel. Explicit `sb.glue` always wins.

**Pre-prepared renderings.** Beyond UIDs, the notebook may glue arbitrary visualization artifacts:

```python
sb.glue("figures", fig, encoder="display")       # matplotlib Figure
sb.glue("html_report", html_string, encoder="html")
sb.glue("metrics", {"chi2": 1.04, "n_peaks": 7})
```

These are harvested by the executor and made available to LUCID's Visualization panel — see §UI surface → Rendering pipeline outputs.

## Pipeline registry

### Repository layout

```
beamline-saxs-pipelines/        # one git repo per beamline
├── pipelines.yaml              # manifest, root of truth
├── bindings.yaml               # auto-trigger bindings (Phase 1: YAML only)
├── pipelines/
│   ├── reduce_saxs.ipynb
│   ├── reduce_gisaxs.ipynb
│   └── waxs_qc.ipynb
├── envs/
│   ├── saxs.yml                # conda env spec
│   └── waxs.yml
└── README.md
```

### `pipelines.yaml`

```yaml
schema_version: 1
pipelines:
  - name: reduce_saxs
    description: "Azimuthal integration + flat-field correction for SAXS runs"
    notebook: pipelines/reduce_saxs.ipynb
    env: envs/saxs.yml            # or "envs/saxs/requirements.txt"
    timeout_seconds: 900
    parameters:
      - name: roi_x
        type: array<int>          # JSON schema-ish
        default: [0, 1024]
        ui: range_slider          # hint for parameter form
      - name: subtract_dark
        type: bool
        default: true
    expects:
      input_run:
        plan_name: ["count", "scan"]   # optional gating; executor 400s on mismatch
        descriptor_keys: ["pilatus_image"]
    outputs:
      tagged: ["saxs", "reduced"]      # added to output runs' access_tags
```

The executor validates the manifest against a JSON schema on startup; failures are logged and the pipeline marked unavailable.

### `bindings.yaml`

```yaml
schema_version: 1
auto:
  - on: run.stopped
    when:
      plan_name: count
      tags_includes: saxs
    submit:
      pipeline: reduce_saxs
      parameters:
        subtract_dark: true
```

Phase 1 supports `plan_name` and `tags_includes` predicates only; richer matching (jq-style expressions) deferred.

### Registry refresh

- Executor `--registry <git-url>` clones into `~/.cache/lucid-pipelines/registry/<sha-of-url>/` on startup.
- A `lucid.pipeline.<host>.refresh` request triggers `git pull` + manifest re-validation.
- For Phase 1, only manual refresh; auto-poll deferred.

## Environment management

### Spec

Each pipeline declares its env via `env:` pointing at either a conda environment YAML or a `requirements.txt` (uv-compatible). The executor picks the resolver by file extension.

### Cache

```
~/.cache/lucid-pipelines/envs/<env-hash>/
  bin/python            # or Scripts/python.exe on Windows
  lib/…
```

`env-hash = sha256(canonicalized env-spec file contents)`. Identical spec across pipelines → shared env directory.

### Build lifecycle

1. Job arrives → executor resolves `env` from manifest.
2. If `<env-hash>` not in cache: publish `status: env_building` progress event; invoke `conda env create` or `uv pip install` into a fresh path.
3. On success: kernel spec is registered (`<env-path>/bin/jupyter kernelspec install` against a project-local data dir) and the env is reused.
4. On failure: job fails with `error.code = "env_build_failed"` and the build log is attached as `detail`.

The first job per env is slow (one-time per env); subsequent jobs reuse the cache.

### Kernel selection

Papermill is invoked with `kernel_name = lucid-pipeline-<env-hash>`, registered against the env's Python. This pins the kernel to the per-pipeline env without leaking onto the user's global Jupyter config.

## Auth: job-scoped Tiled API keys

### Token strategy

LUCID's Keycloak access token TTL is short (~15min) and refresh has a single owner (`SessionManager`, per `docs/superpowers/specs/2026-04-09-token-refresh-redesign.md`). Forwarding a bearer token into a multi-hour pipeline does not work; brokering refreshes over NATS works but couples job lifetime to LUCID's process. **Solution: mint a job-scoped Tiled API key per job.**

### Flow

1. User submits a pipeline.
2. `PipelineClient.mint_job_key()` calls Tiled's `POST /api/v1/auth/apikey/new`:
   ```json
   {
     "lifetime": 86400,
     "scopes": ["read:metadata", "read:data", "write:metadata", "write:data"],
     "note": "lucid pipeline job <job_id>"
   }
   ```
   The user's current bearer token is used to authorize the mint call.
3. The minted API key (a long-lived secret bound to the user's Tiled identity) is embedded in the NATS job message.
4. Executor receives the job and sets `TILED_API_KEY=<key>` and `TILED_URL=<url>` in the kernel subprocess env.
5. Notebook code uses standard Tiled client construction — `from_uri(TILED_URL, api_key=TILED_API_KEY)`.
6. On job completion (success or failure), `PipelineClient` calls `DELETE /api/v1/auth/apikey/{prefix}` to revoke the key. Best-effort; expiry is the backstop.

### Generalization

This token-minting helper (`lucid.auth.mint_job_key(lifetime, scopes, note)`) is **factored out so tsuchinoko can reuse it for the same problem**. Putting it under `lucid.auth` rather than `lucid.pipelines` is intentional — it's a generic "headless workload needs a Tiled credential" primitive.

### Auto-trigger identity

The flow above is for *manual* submissions where a live user session mints the key. **Automatic-on-completion triggers fire without a user-attended request**, so the executor's `AutoTriggerSubscriber` cannot mint on the user's behalf at fire time. Two practical options:

- **(a) Plan-time pre-mint (preferred).** When the user starts a plan from LUCID, if any binding matches the plan, LUCID mints a longer-lived API key (matching the binding's `expected_max_runtime`) and stashes it in the run's start-doc metadata under `lucid.pipeline_credential_id` (key prefix, not the secret). The actual key is published on `lucid.run.stopped` alongside the UID — only consumed by the local executor, never persisted. If the user is logged out at run-start, the binding is silently skipped (telemetry-logged).
- **(b) Executor service account.** Executor is configured with a long-lived service-account API key at startup. Auto-triggered output runs are tagged `auto-triggered-by:<service-account>` plus `derived-for:<user>` from the start doc. Simpler, but the original user does not own the output by Tiled authz — they read it only because the access_blob is inherited.

Phase 1 ships (a) for the default and falls back to (b) only if explicitly configured. This keeps user attribution in the common case.

### Access blob inheritance

The notebook is responsible for writing runs with the correct `access_blob`. To keep the default ergonomic and safe:

- The job message carries the input run's `access_blob` (LUCID fetches it from Tiled at submit time).
- The executor exposes it as the env var `LUCID_INPUT_ACCESS_BLOB` (JSON).
- Convention (documented, not enforced): notebooks pass this blob into their `TiledWriter` calls via `metadata={"access_blob": …, "tiled_access_tags": …}` so outputs inherit the input's audience.
- Manifest's `outputs.tagged` list is appended to the inherited tags by the *executor's* generated bootstrap (see Notebook bootstrap, below).

### Notebook bootstrap

Before user cells run, the executor injects a parameters cell that contains:

```python
# Auto-injected by lucid-pipelines
import os, json
TILED_URL = os.environ["TILED_URL"]
TILED_API_KEY = os.environ["TILED_API_KEY"]
LUCID_INPUT_RUN_UID = os.environ["LUCID_INPUT_RUN_UID"]
LUCID_INPUT_ACCESS_BLOB = json.loads(os.environ["LUCID_INPUT_ACCESS_BLOB"])
LUCID_OUTPUT_TAGS = json.loads(os.environ["LUCID_OUTPUT_TAGS"])

# … user-declared parameters injected by papermill …
```

This is Papermill's standard parameter-injection mechanism. The executed notebook becomes the audit artifact; secrets read from env vars are *not* serialized into the notebook (Papermill only serializes the parameters cell, not env values).

## UI surface (Phase 1)

### Run-context "Run pipeline…"

In the Tiled browser, right-click on a run → "Run pipeline…":

1. Opens a small dialog with a `QComboBox` of available pipelines from the executor's manifest (fetched via `lucid.pipeline.<host>.list` request).
2. Below: an auto-generated parameter form built from the manifest schema (existing `panel_builder` in `lucid.plugins.agents.panel_builder` is a precedent for schema→widget if we can reuse it; if not, simple per-type widgets).
3. "Submit" mints the API key, sends the job, and switches focus to the Pipeline Jobs panel.

### Pipeline Jobs panel

A new dockable `BasePanel` (mirrors `lucid/ui/panels/`):

- Top: queue depth, executor host, status indicator.
- Table rows: job_id, pipeline, input UID, status, started, duration, output count.
- Row actions: "Open executed notebook" (launches whatever Jupyter / VS Code is configured), "Show outputs" (jumps to outputs in the Tiled browser), "Cancel" (Phase 2).

### `PipelineClient` (in-process)

Exposes signals consumed by the panel: `sigJobQueued`, `sigJobProgress`, `sigJobCompleted`, `sigJobFailed`. Owns the API-key mint and revoke calls.

### Rendering pipeline outputs

Output rendering reuses LUCID's existing **Visualization panel** (`lucid.visualization`, `BaseVisualization` ABC). Two rendering pathways, both flowing through that same panel:

1. **Procedural rendering (free, no new code).** A pipeline's output runs are themselves `BlueskyRun` entries in Tiled. The existing visualization widgets (`plot_1d`, `image_stack`, `heatmap`, `scatter`, `table`, etc., each registered via `VisualizationRegistry`) score `can_handle(run)` on them just like any other run. Selecting an output UID in the Tiled browser or via the Jobs panel's "Show outputs" action opens the Visualization panel which auto-picks the best widget. This is the default — no work required from the pipeline author.

2. **Pre-prepared rendering (notebook-glued artifacts).** When a notebook glues scrapbook artifacts (`figures`, `html_report`, etc.) the executor harvests them and attaches them to the output Tiled run as side-car entries (e.g., the figure goes in as an `image`-typed child node with `metadata.source = "pipeline_artifact"`, the HTML report as an `html`-typed entry). Phase 1.5 adds a thin `ScrapbookViz(BaseVisualization)` widget whose `can_handle()` returns 100 for runs carrying `pipeline_artifact` children, rendering them in a tabbed layout. Until that widget lands, scrapbook artifacts are still visible via the existing `image_stack`/`table` widgets that already handle image and tabular children.

The key property: pipeline outputs are first-class Tiled entries, so they live in the same browser, the same access-control machinery, and the same visualization pipeline as any other beamline data. The notebook author chooses how much rendering effort to invest — pure procedural (write data, walk away) or pre-prepared (glue exactly the figure you want shown).

## Data flow: one job

```
1. User right-clicks run UID=R in Tiled browser, selects "Run pipeline… → reduce_saxs"
2. LUCID PipelineClient:
     a. fetch R's access_blob from Tiled
     b. POST /api/v1/auth/apikey/new → API key K (24h TTL)
     c. ping lucid-pipelines (spawn if absent)
     d. publish job message to lucid.pipeline.<host>
3. Executor PipelineService:
     a. validate job (pipeline exists, schema match, R is reachable)
     b. enqueue, reply {status: queued}
     c. dequeue → resolve env spec → check env cache
        - cache miss → build env, progress: env_building → env_ready
        - cache hit → progress: running
     d. spawn subprocess (env's python, kernel_name=lucid-pipeline-<hash>)
     e. invoke papermill: input nb path, output nb path, parameters + bootstrap params
4. Notebook (in subprocess):
     a. read input run from Tiled via TILED_API_KEY
     b. process
     c. write derived run(s) with inherited access_blob + manifest tags
     d. sb.glue("output_run_uids", [U1, U2])
5. Executor PapermillRunner:
     a. on subprocess exit 0: parse scrapbook → publish progress: completed with output_run_uids
     b. on exit != 0: publish progress: failed with last 100 lines of stderr
6. LUCID PipelineClient:
     a. update Pipeline Jobs panel
     b. revoke API key K
     c. (optional) toast notification
```

## Error handling

| Failure mode | Where caught | User-visible result |
|---|---|---|
| Manifest invalid on startup | Executor startup | Pipeline disabled; log + meta endpoint marks it unavailable |
| Unknown `pipeline` in job | Service validate | NATS reply `{error, code: unknown_pipeline}` |
| API key mint fails (Tiled 5xx) | LUCID client | Toast: "Could not authorize pipeline job"; job not submitted |
| Env build fails | Executor env step | Progress: failed, detail = last 50 lines of build log; partial cache cleaned up |
| Notebook raises exception | Papermill subprocess | Output `.ipynb` records the traceback; executor publishes failed with traceback excerpt |
| Subprocess timeout (`timeout_seconds`) | Executor watchdog | SIGTERM → SIGKILL; failed with `code: timeout` |
| Scrapbook missing `output_run_uids` | Executor harvest | Not an error — completed with `output_run_uids: []` (the executed notebook itself is the artifact) |
| Notebook writes to Tiled with wrong access_blob | Tiled write helper | Tiled-side authz catches it; notebook errors propagate via Papermill |
| Executor crash mid-job | LUCID watcher | After `2 × ping_interval` of no progress and no ping, mark job `lost`; user can resubmit |
| API key revoke fails | LUCID cleanup | Log; key still expires at TTL |
| Auto-trigger fires but no plan-time key (user logged out at plan start) | RunStopNATSEmitter | Binding skipped; telemetry log entry; falls back to service-account key only if configured |
| Plan-time pre-minted key expired before run finished | AutoTriggerSubscriber | Job fails with `auth_expired`; user resubmits manually (which re-mints) |

## Testing strategy

### Unit (executor, ~80% of new code)

- `tests/pipelines/test_service.py` — job parsing, queue behavior, scrapbook harvest, error mapping
- `tests/pipelines/test_env_cache.py` — hash stability, lazy build mocking, cache hit / miss
- `tests/pipelines/test_papermill_runner.py` — bootstrap injection, env var forwarding, timeout, exit-code handling (use a tiny in-tree `tests/fixtures/notebooks/echo.ipynb`)
- `tests/pipelines/test_manifest.py` — schema validation, parameter merge

### Integration

- `tests/pipelines/test_e2e.py` — runs against a real Tiled (the `bcgtiled` test instance per `MEMORY.md::reference_als_tiled_deploy`) plus a real local NATS:
  1. Author a run via TiledWriter
  2. Spawn `lucid-pipelines` subprocess against the test fixture registry
  3. Submit a job via NATS
  4. Assert progress events arrive in order
  5. Assert output runs exist in Tiled with inherited access_blob
- One end-to-end test exercises the auto-trigger path with a synthetic `run.stopped` event.

### Notebook contract tests (in registry repo, not LUCID)

Each registry repo ships a tiny `tests/test_pipelines.py` that, for each pipeline, runs Papermill with a fixture input run and asserts:
- Output notebook executes without error
- Required scrapbook keys present
- Output runs visible to the fixture user

This stays in the registry repo so beamline scientists own their tests alongside their notebooks.

### CI

- LUCID CI: unit + integration with a NATS container and a Tiled container.
- Registry CI (per beamline repo): notebook contract tests via Papermill in a matrix of declared envs.

## Related work: Raydata (NSLS-II)

Wijesinghe, Barbour, Wiegart, Rakitin et al., *"Bluesky and Raydata: An Integrated Platform for Adaptive Experiment Orchestration"* (SCW 2024, doi:10.1109/SCW63240.2024.00271) describes the closest sibling system. Architectural shape:

- **Backbone:** Sirepo (RadiaSoft's Tornado-based web gateway) hosts `raydata_scan_monitor`, a containerized service that polls Tiled.
- **Execution:** Papermill on parameterized notebooks; Conda envs per analysis, mounted from facility-wide NFS.
- **Triggers:** Tiled-poll automatic + manual via Sirepo web GUI; CHX uses metadata-based dynamic workflow selection, CSX uses static.
- **Feedback loop:** Raydata → Bluesky Queueserver via ZeroMQ for adaptive plan management (paper notes this is in active development).
- **UI:** browser-based; auto-renders artifacts (figures, JSON, logs) the notebook drops on disk.

Where this design **deliberately diverges** from Raydata:

| Concern | Raydata | This design | Reason |
|---|---|---|---|
| IPC | Sirepo's internal broker | LUCID's existing NATS | Reuses `lucid.exporter`'s production pattern; LUCID is NATS-native |
| GUI | Browser (Sirepo) | Qt desktop (LUCID's existing panels) | LUCID's product bet is workstation-resident; no second GUI |
| RunEngine access | Via Queueserver/ZeroMQ | Direct (LUCID owns RE) | Adaptive loops are a function call away |
| Output linkage | Filesystem artifacts only | Explicit input→output run graph in Tiled (scrapbook) | Enables Tiled-browser "show derived data" navigation |
| Long-job credentials | Not addressed in paper | Job-scoped Tiled API key | Solves the access-token TTL problem; reusable by tsuchinoko |
| Access-blob inheritance | Not addressed | Default behavior | Per-entry authz is enforced in ALS Tiled |
| Pipeline registry | "Provided as-is" (filesystem) | Per-beamline git repo + CI | Reviewability, versioning, reproducibility |

Where Raydata **influences** this design:

- The "notebooks unmodified, papermill executes them as-is" stance is reinforced; the scrapbook contract is correspondingly soft (missing keys are not errors, with a UID-delta fallback).
- The auto-artifact rendering UX is good — adopted as the Phase 1.5 `ScrapbookViz` plan, routed through LUCID's existing Visualization panel rather than a new web UI.
- Metadata-driven workflow selection (CHX style) is supported via the `when:` clause in `bindings.yaml`.

Why not just use Raydata: the entire Sirepo + browser-UI stack would have to be installed, integrated, and maintained alongside LUCID; the credential, access-inheritance, and Tiled-linkage gaps would still need solving; and the GUI duplication would confuse beamline scientists who already have LUCID open.

## Phase 2+ (designed-for)

The Phase 1 wire format is shaped so the following don't need protocol changes:

- **Online during acquisition.** A new subscriber listens for `run.event_page` (or sampled equivalent) and submits the same job with `parameters.partial = true, parameters.descriptor_offset = N`. Notebook reads up-to-N events from Tiled. Executor / API-key / scrapbook flow unchanged.
- **Bulk reprocessing.** A new dialog ("Reprocess all runs from yesterday…") iterates UIDs and submits jobs. No executor change.
- **Persistent kernels.** A manifest flag `kernel: persistent` switches the runner from Papermill to direct `jupyter_client` with kernel reuse across jobs. Same job message, same scrapbook harvest.
- **Bindings UI.** Replace `bindings.yaml` with a settings panel writing the same YAML.
- **Cancellation.** Add a `lucid.pipeline.<host>.cancel` request taking a `job_id`; executor SIGTERMs the subprocess and marks failed.
- **`ScrapbookViz` widget (Phase 1.5).** Dedicated `BaseVisualization` subclass that renders pipeline artifacts (figures, HTML, metrics) attached to output runs in a tabbed layout. Until then, scrapbook image artifacts already render via the existing `image_stack` widget.
- **Remote executor offload.** Replace `PapermillRunner` with a `SlurmRunner` / `NerscRunner` etc. — wire format is unchanged; only the runner's "where to spawn the subprocess" changes. Equivalent of Sirepo's NERSC dispatch.

## Open questions

1. **Where does the `RunStopNATSEmitter` live?** The auto-trigger needs a NATS `run.stopped` event. The existing IPC service catalog (`src/lucid/ipc/service.py:215`) doesn't yet publish bluesky stop docs. Two options: (a) a small RunEngine subscriber callback in `lucid.acquire.runengine` that emits one NATS event per stop doc (cleanest); (b) extend the existing logbook plugin's hook surface. Phase 1 should add (a) — it's a useful primitive beyond pipelines.
> I don't think we need any extra stop notification. Tiled provides this once you have the run id. Please correct me if I missed context. Make note that Tiled's documentation is in a poor state. It LOOKS good, but in largely incomplete or inaccurate, missing critically helpful tools. For triggering jobs on run completion, that should be done with a runengine subscriber in lucid that submits the jobs, but the job submission should contain the run id, not the stop doc id.
! Agreed — that's strictly better. The dispatcher chain collapses from "RunEngine → NATS stop event → executor-side AutoTriggerSubscriber → match binding → submit job" to just "RunEngine → LUCID-side BindingDispatcher → match binding → submit job via the normal `lucid.pipeline.<host>` request." Three consequences I'll fold into the spec: (i) `AutoTriggerSubscriber` is **removed from the executor**; the binding dispatcher lives in LUCID as a RunEngine subscriber alongside `RunEngine.subscribe('stop', …)`. (ii) `bindings.yaml` now lives on the LUCID side (per-workstation user config or a settings panel later), not in the registry. The executor stays a pure job runner. (iii) Job payload carries the run uid only; the dispatcher pulls anything else it needs from Tiled. Adding the "Tiled docs are aspirational" caveat to the spec since it'll bite the implementer.
$ Note: I said RunEngine here, but we should implement things through the abstraction layer around RunEngine so that it works with other potential executors. Also, given that we'll have a few pipeline triggering modes, we should have some component to manager the triggers, and a trigger base class to normalize their api.
! Agreed — `BaseEngine` (`src/lucid/acquire/engine/base.py:45`) is the correct surface, not `RE` directly. It already exposes `subscribe(callback)` accepting `(name, doc)` callbacks (`base.py:396`), so the dispatcher hooks the engine wrapper and is automatically engine-agnostic (works against `bluesky.py`, `mock.py`, future engines). Concrete proposal that ties this together with your Q8 trigger list:

```python
class Trigger(ABC):
    """Base class for pipeline triggers. Lifecycle owned by TriggerManager."""
    @abstractmethod
    def attach(self, manager: TriggerManager) -> None: ...
    @abstractmethod
    def detach(self) -> None: ...

class RunStartTrigger(Trigger):
    """Engine-subscribed; fires on 'start' docs matching `filter`."""

class RunEndTrigger(Trigger):
    """Engine-subscribed; fires on 'stop' docs matching `filter`."""

class ManualTrigger(Trigger):
    """No engine hook; fires from Data Browser context menu invocations."""

class TriggerManager:
    """Single LUCID-side owner. Holds the configured Trigger instances,
    routes their `fire(run_uid, params)` calls to PipelineClient.submit()."""
```

Reusable beyond pipelines — anything that wants "fire X on run-end" (e.g., auto-export, auto-logbook entry) can register a Trigger subclass. Same loop-guard rule applies: every Trigger's filter excludes `metadata.start.lucid_pipeline_output = true` by default.

2. **Does ALS Tiled allow user-scoped API key creation today?** Tiled's API key endpoint exists and is enabled by default, but the per-deployment policy may restrict who can mint and with what TTL. Needs a one-shot probe against `bcgtiled` before committing — if disabled, fall back to NATS-brokered token (worse but works).
> I suspect we have to build this in to als-tiled. It is a big benefit to LUCID too, since that's the main reason we are on the referesh token route currently (to be able to talk to als-tiled). Haven a 24-hour api key with tiled would be great for lucid too.
! Confirmed — this is a **precursor dependency**. The als-tiled side needs an API-key mint endpoint that accepts a Keycloak access token, mints a TTL-bounded user-scoped key, returns it. Once that exists, LUCID itself can move off the access-token treadmill for its own data-browser calls (huge win), and notebook pipelines fall out for free. I'll mark this as Stage 0 in the implementation skeleton: ship the als-tiled change, then everything downstream simplifies. Worth coordinating with whoever owns als-tiled — looks like a small endpoint addition rather than a Tiled-core change.
$ we own als-tiled. You should have notes about it. 
! Right — your team owns als-tiled (deployed at bcgtiled:/opt/als-tiled per `MEMORY.md::reference_als_tiled_deploy`; update via `git reset --hard origin/master + pip install + restart`). Reframing: adding the user-scoped API-key mint endpoint is an **in-team** change, not external coordination. Should be a focused PR on the als-tiled repo (handful of files: a new FastAPI route that accepts the Keycloak bearer, calls Tiled's internal key-creation primitive with a user-derived `principal_id` + TTL, returns the key). I don't have the als-tiled source path in memory — point me at the repo URL when convenient and I'll scope the change as part of Stage 0 in the implementation plan.
% https://git.als.lbl.gov/ncs/als-tiled
clone here: C:\Users\rp\PycharmProjects\als-tiled
! Noted. als-tiled origin: `https://git.als.lbl.gov/ncs/als-tiled`; local clone: `C:\Users\rp\PycharmProjects\als-tiled`. Stage 0 of the implementation plan scopes the user-scoped API-key mint endpoint there. I'll save this to memory so it persists.

3. **Conda vs uv as default env resolver.** Both are supported via file extension, but the manifest could grow a `resolver:` field if one ends up clearly preferred. Defer until 2-3 real pipelines exist.
> I DO NOT like conda. I think uv is good path, but we can also leave it open with a path to set the python executable so that pre- or externally-prepared envs can be used.
! uv is the default; conda support is dropped from the design. Three env-spec flavors the manifest accepts:
  - `env: pyproject.toml` (preferred — `uv sync` against an inline `[project]` or `[tool.uv]` table; locked via `uv.lock` in the registry)
  - `env: requirements.txt` (acceptable — `uv pip install -r`)
  - `python: /path/to/python` (escape hatch — use an externally-prepared interpreter; executor does no env management, just spawns papermill against that python)
The "python:" path is the right answer for facility-managed envs (NSLS-II-style shared NFS conda envs, or a beamline-curated venv on local disk). No conda code in our codebase.
$ Its worth thinking about the structure of a lucid plugin repo that contains pipelines. If the pipelines have their own dependency definitions how is that repo structured? The plugin manifest points to a papermill notebook, but that notebook has sidecar files with the dependencies? how are they associated? Does papermill have an ordained way for associated env requirements with a notebook?
! Papermill has **no** env-association mechanism — it just executes a `.ipynb` against whichever Jupyter kernel you pick. Kernel-to-env association is registered separately via `jupyter kernelspec`. So associating env to notebook is *our* problem.

The cleanest answer with the plugin-registry model: **the pipeline package IS the env**. `pyproject.toml`'s `[project.dependencies]` declares deps; the entry point declares the plugin class; the class points at a notebook bundled as a package resource. One `pip install <package>` materializes notebook + deps together; the executor's per-pipeline venv = the venv created for that specific package install. No sidecar manifest, no `pipelines.yaml`, no associating-by-convention.

```
als-saxs-reduce/
├── pyproject.toml
│    [project]
│    name = "als-saxs-reduce"
│    version = "0.4.1"
│    dependencies = [
│      "lucid-pipelines",          # base classes + bootstrap helpers
│      "numpy>=1.26",
│      "scipy",
│      "pyFAI",
│      "tiled[client]",
│      "scrapbook",
│    ]
│    [project.entry-points."lucid_pipelines.pipeline"]
│    reduce_saxs = "als_saxs_reduce:ReduceSaxsPipeline"
├── src/als_saxs_reduce/
│   ├── __init__.py        # class ReduceSaxsPipeline(PipelinePlugin):
│   │                      #     notebook = "notebook.ipynb"  # importlib.resources
│   │                      #     parameters_schema = {...}
│   │                      #     output_tags = ["saxs", "reduced"]
│   └── notebook.ipynb     # the actual pipeline body
└── uv.lock                # locked deps for reproducibility
```

The executor at startup:
1. For each entry point in `lucid_pipelines.pipeline`, load the class.
2. To run a pipeline, ensure a venv exists at `~/.cache/lucid-pipelines/envs/<pkg>@<version>/`; if not, `uv venv` + `uv pip install <pkg>==<version>` (using `uv.lock` from the installed dist for reproducibility).
3. Papermill is invoked with `kernel_name="lucid-pipelines:<pkg>@<version>"`, registered against that venv's python.
4. Notebook loaded via `importlib.resources.files("als_saxs_reduce") / "notebook.ipynb"` — the notebook is a packaged resource, not a filesystem path.

Multi-notebook beamline packages just declare multiple entry points + multiple classes sharing the same package's deps. That's the right granularity unit (one venv per pipeline package, not per notebook) — keeps env-build cost amortized across a beamline's pipelines.

Open detail: do we want users to be able to **add** a pipeline by just `pip install`-ing it into the executor's discovery scope? Two paths: (a) executor calls `uv pip install <pkg>` on demand via a `lucid-pipelines install <pkg>` CLI subcommand (or LUCID-side action), keeping pipelines in a managed area; (b) ops installs pipelines into the executor's base env via standard pip workflows, no LUCID-side install action. I'd lean (a) for the workstation-managed case but it's a minor detail.
$ This is good, however we probably want to isolate the pipeline deps from the other deps in a beamline plugins repo. Should pipeline deps be an optional deps section, or can we separate them out as distinct pacakges somehow that are individual installable, or do we need two repos? As for install mechanisms, any of them should just work since the discover mechansim is entrypoints. Which gets used is up to the operator and depends on the host env.
! Good catch — a beamline plugins repo realistically holds more than pipelines (panel plugins, agent plugins, settings plugins). If a panel plugin sits in the same dist as a pipeline, the panel pulls pyFAI / scipy / etc. into LUCID's runtime every time someone installs it. Bad.

Three structurings worth considering, in order of preference:

**1. uv workspace monorepo with separate sub-packages per plugin family (recommended).** One git repo, one workspace, N independently installable distributions:

```
als-saxs/                          # one repo
├── pyproject.toml                 # workspace root
│   [tool.uv.workspace]
│   members = ["packages/*"]
├── packages/
│   ├── als-saxs-panels/           # dist: als-saxs-panels
│   │   ├── pyproject.toml         #   deps: [lucid, PySide6]
│   │   │   [project.entry-points."lucid.panels"]
│   │   └── src/...
│   ├── als-saxs-pipelines/        # dist: als-saxs-pipelines
│   │   ├── pyproject.toml         #   deps: [lucid-pipelines, numpy, pyFAI, scrapbook]
│   │   │   [project.entry-points."lucid_pipelines.pipeline"]
│   │   │   reduce_saxs = "als_saxs_pipelines.reduce:ReduceSaxsPipeline"
│   │   └── src/als_saxs_pipelines/
│   │       ├── reduce.py
│   │       └── reduce.ipynb
│   └── als-saxs-agents/           # dist: als-saxs-agents
│       └── ...
└── README.md
```

Each sub-package is its own PEP 621 distribution with its own `[project.dependencies]`. The executor installs `als-saxs-pipelines` into a per-pipeline-package venv (per Q3 original); LUCID installs `als-saxs-panels` into its own runtime. No dependency leakage. uv workspaces are first-class — `uv sync` resolves all members; `uv build --package als-saxs-pipelines` produces the single wheel. Scientists author in one repo, ops install whichever subset is needed.

**2. Optional dependencies (`[project.optional-dependencies]`) on a single dist.** `pip install als-saxs[pipelines]` pulls pipeline deps; `pip install als-saxs[panels]` pulls panel deps. Simpler than (1) — no workspace tooling — but lumps everything into one distribution version. Cross-family changes ripple. Workable, but (1) scales better as the plugin set grows.

**3. Separate repos.** Cleanest separation, highest coordination cost. Skip unless a beamline has a genuine reason.

On install mechanism: agreed — entry-point discovery doesn't care how the package got there. `uv pip install als-saxs-pipelines` (PyPI/Artifactory/git+ssh URL) all work. The `lucid-pipelines install` subcommand becomes an optional convenience for workstation-managed deployments but isn't required.

I'll write the spec assuming option (1) and treat it as the canonical pattern for "a beamline's plugins repository," with a footnote on (2) for tiny beamlines that only have one or two pipelines.

4. **Should the executed notebook itself be stored in Tiled?** As a `dataframe`-type entry tagged with the input run? It's the natural audit artifact. Phase 1 stores it on the executor's local disk under `/var/lib/lucid-pipelines/runs/<job_id>.ipynb`. Promoting to Tiled (Phase 1.5) is a small addition.
> Sounds like a good thing to default to, but might want to allow it to be disabled
! Resolved: Phase 1 stores executed notebooks **to Tiled by default**, as `awkward`/`json`-typed entries (the JSON serialization of `nbformat`) with `metadata.pipeline_provenance` linking back to the input run + pipeline + git SHA. Manifest opt-out: `store_executed_notebook: false`. This collapses the local-disk-accumulation worry (see Q11). The Pipeline Jobs panel's "Open executed notebook" action fetches from Tiled.

5. **Per-pipeline resource limits.** A misbehaving notebook can fill memory or saturate I/O. Phase 1 enforces `timeout_seconds` only; cgroup-style limits deferred.
> the trust model should give full freedom to notebooks. Its something beamline scientists need to be responsible for ensuring quality.
! Resolved — out of scope. Spec will be edited to drop the resource-limit framing entirely; even `timeout_seconds` becomes optional rather than required. Quality of beamline notebooks is the beamline's responsibility.

6. **Notebook output run linkage.** Should we require the notebook to write `metadata.start.parent_run_uid = LUCID_INPUT_RUN_UID` on each output, or have the bootstrap inject it into a context manager? Convention vs. enforcement — lean toward bootstrap-injected helper if/when the optional `lucid_pipeline` module materializes.
> Need this explained more
! Sorry — re-stating the concrete question:

When a pipeline writes a derived run to Tiled, that derived run should "know" which input run it came from. Otherwise the Tiled-browser navigation "show derived data from run X" can't work, and provenance is broken. The mechanism is: derived runs carry `metadata.start.parent_run_uid = <input_uid>` (and probably also the `pipeline_provenance` blob from Q7).

The question is **who stamps that metadata**:

  (a) Convention — the notebook author writes it explicitly:
```python
tw = TiledWriter(client, metadata={"parent_run_uid": LUCID_INPUT_RUN_UID, "pipeline_provenance": LUCID_PIPELINE_PROVENANCE})
```
Pro: zero LUCID code; con: every notebook author must remember.

  (b) Enforcement via a thin wrapper the bootstrap exposes:
```python
# notebook imports
from lucid_pipeline import TiledWriter  # wraps bluesky.TiledWriter, auto-stamps metadata
tw = TiledWriter(client)  # parent_run_uid + provenance stamped automatically
```
Pro: can't forget; con: tiny LUCID-shaped dependency for notebook authors (the `lucid_pipeline` module we said was non-goal earlier).

Given your Q7 answer ("aim for enforcement"), I'd flip the earlier non-goal and **ship a minimal `lucid_pipeline.TiledWriter` wrapper** in Phase 1 — it's ~20 lines and removes a whole class of provenance bugs. Resolves Q6 + Q7 together.
$ agreed, let's go with b. However, we should compare with how we already did this in Tsuchinoko
! Tried to look — I grepped `~/PycharmProjects/tsuchinoko/` for `parent_run_uid` / `input_run_uid` / `derived` / Tiled writers, came up empty in the source tree. The repo is currently on branch `test/gpcam-coverage` and has a `LUCID-refactor` branch (worktree) plus several others; the NATS/Tiled rescope work referenced in memory (`project_tsuchinoko_rescope` — Phases 1–4a done) is presumably on a branch I haven't checked out. Could you point me at the branch/path where the derived-run-stamping lives? I'll compare and either reuse the pattern or factor it into the new `lucid_pipeline.TiledWriter` so both projects share one helper.
% sounds like the lucid-refactor is probably it. Make a note about its location so we don't lose it
! Saved to memory: tsuchinoko's `LUCID-refactor` branch holds the NATS/Tiled rescope work (`~/PycharmProjects/tsuchinoko`, branch `LUCID-refactor`). I'll inspect it next, find the derived-run write surface, and factor whatever's there into `lucid_pipeline.TiledWriter` so the helper is shared between LUCID-dispatched pipelines and tsuchinoko's adaptive-experiment derived data.

7. **Pipeline versioning in output metadata.** Provenance: "what code produced this output run, six months from now?" The executor already knows the registry's git SHA + the notebook path + the manifest entry. Proposal: bootstrap injects `LUCID_PIPELINE_PROVENANCE = {repo_url, repo_sha, notebook_path, pipeline_name, manifest_version, env_hash}` as an env var; output runs are expected to land `metadata.start.pipeline_provenance = LUCID_PIPELINE_PROVENANCE` either via notebook convention or via a TiledWriter wrapper the bootstrap provides. **Open:** convention only, or do we ship a thin write helper to enforce it? Cheap to add now, miserable to backfill across an already-churned registry.
! Resolved: enforce via the `lucid_pipeline.TiledWriter` wrapper (same one as Q6). Provenance keys: `pipeline_name`, `pipeline_package` (Python dist name), `pipeline_package_version` (PEP 440 from importlib.metadata), `python_executable`, `env_hash` if uv-resolved, `parent_run_uid`. With the plugin-registry model (Q9), "git SHA" is replaced by package version — semantically equivalent for provenance, more standard tooling.

8. **Auto-trigger loop guards.** If `outputs.tagged` includes a tag that also appears in `bindings.yaml`'s `when.tags_includes`, the binding refires on its own output → unbounded recursion. **Proposal:** the executor stamps every output run with a reserved tag `pipeline_output` (or `lucid.pipeline_output`); `bindings.yaml` `when:` matchers implicitly exclude runs carrying that tag unless they explicitly set `include_pipeline_outputs: true`. Trivial to implement; catastrophic if missed. Worth promoting from "open question" to a hard requirement before any binding ships.
> Please explain what you're asking more.
! Concrete failure scenario:

1. User has a binding (in `bindings.yaml`): *"when a run finishes carrying tag `saxs`, run pipeline `reduce_saxs`."*
2. User starts a SAXS plan. The plan's runs are tagged `saxs`. Plan finishes.
3. Binding fires → `reduce_saxs` runs against the raw run.
4. `reduce_saxs` writes a derived run to Tiled. Derived run inherits `access_tags` from the input (per the access-blob inheritance design) **plus** anything in the manifest's `outputs.tagged`. End result: the derived run also carries `saxs`.
5. The BindingDispatcher (now in LUCID, per Q1's answer) sees a new run finish with tag `saxs` and fires `reduce_saxs` again — against the derived run this time.
6. Step 4 repeats. Step 5 repeats. Runs multiply each iteration. Disk fills overnight.

This is a real footgun specifically because tag inheritance is the right default — we *want* derived runs to be visible to the same audience as the input. But "tagged for access" overloaded onto "tagged for triggering" causes the loop.

Fix (small, hard requirement before any binding ships):
  - Every pipeline-emitted output run is automatically stamped with a reserved sentinel: `metadata.start.lucid_pipeline_output = true` (boolean, not a tag).
  - The BindingDispatcher's match function **filters out any run with `lucid_pipeline_output = true`** before evaluating `when` clauses.
  - To explicitly chain pipelines (rare but legitimate), a binding sets `match_pipeline_outputs: true` in its config.

The sentinel is a metadata flag, not a tag, so it can't bleed into access decisions or be accidentally consumed by other matchers. Promote from open question to hard requirement?
$ I don't like bindings.yaml. Let's skip that. Pipeline configuration should be done in LUCID. Let's plan to support these trigger mechanism:
- Trigger at run start initiated by lucid
- Trigger at run end intiated by lucid
- Trigger manually: triggered from Data Browser
not sure how that reframes your question.
! Big reframe, all in a good direction. Combining with your $-note on Q1:

- **`bindings.yaml` is dead.** Pipeline triggers are configured in a LUCID settings panel; persisted in LUCID's settings backend (same one as user prefs); not a file in the registry or any package.
- **Three trigger types** map directly onto `Trigger` subclasses: `RunStartTrigger`, `RunEndTrigger`, `ManualTrigger`. The `ManualTrigger` is what the Data Browser context menu instantiates per click — it's not really "configured" so much as "invoked" — but uniform with the other two.
- **A "PipelineTrigger configuration"** in the settings panel is: `{type: run_start | run_end, filter: <match expression>, pipeline: <name>, parameter_overrides: {...}}`. Each beamline configures its own; no defaults shipped.
- **Loop-guard reframed:** trigger filters exclude `metadata.start.lucid_pipeline_output = true` by default. Same sentinel mechanism, same hard requirement, the configuration just lives in the LUCID UI now.

Reframed question — does that match your intent? In particular: is the filter expression a free-form structure (jq-ish), or a small fixed set (plan_name, tags, has_descriptor_key)? I'd lean small fixed set for Phase 1; jq-ish later if there's demand. Confirm and I'll write it that way.
% Sounds ok, but we shouldn't need to worry about what you called loop-guard. our trigger mechanisms would never be triggered by a pipeline-derived run; those don't come through the runengine. 
! You're right and I was wrong — conceding cleanly. The Trigger subclasses hook `BaseEngine.subscribe()`, which only emits docs for runs the engine *executes*. Pipeline-derived runs are written directly to Tiled by the notebook via `bluesky.TiledWriter`; they never pass through any engine; no start/stop doc is ever emitted to LUCID's subscribers about them. Therefore no loop is possible. The sentinel tag (`lucid_pipeline_output`) is unnecessary in Phase 1.

I'll drop the sentinel requirement and rewrite the auto-trigger section accordingly. One footnote I'll keep: *if* Phase 2+ adds a "Tiled-poll" trigger that watches Tiled directly for new entries (independent of any engine), the loop concern returns. At that point we revisit. But Phase 1 RunStart/RunEnd/Manual triggers are loop-safe by construction.

Also lock in: filter expressivity is a small fixed set in Phase 1 (`plan_name`, `tags_includes`, `has_descriptor_key`) since you didn't push back on that.

9. **Registry git credentials.** `lucid-pipelines --registry <git-url>` does `git clone` / `git pull`. For ALS GitLab the repo is almost certainly private (`git.als.lbl.gov/ncs/beamline-saxs-pipelines` style). Options: (a) `--registry-token` flag reading a GitLab deploy token (Maintainer-scoped, per `TOOLS.md` patterns); (b) `--registry-ssh-key` flag; (c) host's existing `~/.gitconfig` / `~/.ssh/` (requires the executor to run as a user with credentials configured). **Open:** pick one as default and document the rest as alternatives. (a) is most portable; (c) is most ops-friendly on a managed workstation. Real Phase 1 blocker — needed before the first deployment.
> I have some questions about how this is envisioned. What I had in mind is lucid-pipelines having its own registry of pipeline plugins (like lucid plugins) keyed by name. Lucid asks lucid-pipelines to run a pipeline by name. No cloning needed, since lucid-pipelines knows about it only by nature of the plugin registry. Let me know if I've missed something here.
! You haven't missed anything — your plugin-registry model is genuinely better and **I'm convinced we should switch to it**. To make sure we agree on what it means:

**What I had been describing** (git-clone model): one `beamline-saxs-pipelines` git repo per beamline, structured as a tree of `.ipynb` files plus a `pipelines.yaml` manifest at the root. Executor clones at startup, pulls on refresh. Pipelines are *content in a repo*.

**What you're proposing** (plugin model, matching LUCID's `AgentPlugin` etc.): each pipeline is its own pip-installable Python package that exposes a `lucid_pipelines.pipeline` entry point. Executor discovers via `importlib.metadata.entry_points(group="lucid_pipelines.pipeline")` at startup. Pipelines are *Python packages*.

Concretely, a pipeline package looks like:

```
als-saxs-reduce/
├── pyproject.toml          # declares lucid_pipelines.pipeline entry point
├── src/als_saxs_reduce/
│   ├── __init__.py         # defines class ReduceSaxsPipeline(PipelinePlugin):
│   ├── manifest.py         # name, description, params schema, output_tags
│   ├── notebook.ipynb      # the actual pipeline (loaded via importlib.resources)
│   └── …
└── uv.lock or requirements.txt
```

`pyproject.toml`:
```toml
[project.entry-points."lucid_pipelines.pipeline"]
reduce_saxs = "als_saxs_reduce:ReduceSaxsPipeline"
```

The `PipelinePlugin` base class (parallel to `AgentPlugin`) declares `name`, `description`, `notebook_resource()`, `parameters_schema()`, `output_tags`, `store_executed_notebook: bool`.

Why this is better:
- **Uniform with LUCID's existing plugin model** — scientists who already understand `AgentPlugin` get pipelines for free; one mental model.
- **Standard packaging tooling** — `pip install als-saxs-reduce` (or `uv pip install`); no `git clone` step, no credential ceremony beyond pip's existing index auth (PyPI, GitLab Package Registry, git+ssh URLs all just work).
- **Per-pipeline envs become "pip install the package"** — `uv venv` + `uv pip install als-saxs-reduce` is the env-resolve step. The pipeline's own dependencies are declared in its `pyproject.toml` as normal.
- **Version pinning is PEP 440**, not a git SHA — `als-saxs-reduce==0.4.1` for prod, `als-saxs-reduce==0.5.0rc1` for dev, etc. Better provenance shape (see Q7).
- **The "registry git credentials" problem disappears** — it's just `pip install` auth, which Linux workstations already have configured.

Consequences for the spec — this is a substantive rewrite of multiple sections:
- §Pipeline registry → rewrite around entry-point discovery. The git-repo / `pipelines.yaml` layout goes away.
- §Environment management → rewrite around `uv pip install <pipeline-pkg>`. The cache key becomes `(pkg_name, version)` not `sha256(env-spec)`.
- §Auth → "registry credentials" subsection deleted, "pip index auth" added (boring).
- §UI surface → `lucid.pipeline.<host>.list` returns entry-point-discovered pipelines.
- §Wire formats → `pipeline` field in the job message stays a string name, same as before — externally unchanged.
- §Implementation skeleton → reorder around plugin class + entry-point hookup + a reference pipeline package.

A `lucid-pipelines install <pkg-spec>` subcommand handles the "add a new pipeline" workflow (just calls `uv pip install` into the executor's per-pipeline env area). A `lucid-pipelines list` reflects what's discoverable.

Open detail to confirm: **does `bindings.yaml` still live as a YAML file on the LUCID workstation** (per Q1, where the dispatcher moved to LUCID), or do pipeline packages themselves declare default bindings via their entry point? Leaning workstation YAML — bindings are inherently user/site-specific (different beamlines bind the same `reduce_saxs` to different plans), so they shouldn't live inside the pipeline package.

If you confirm the plugin-registry direction, I'll do the rewrite in a follow-up commit.
$ Confirming the plugin-registry direction. Related notes on bindings.yaml already stated above, as well as uncertainty about how the rest of this integrated with package structure. Your rewrite will likely resolve my uncertainties
! Confirmed. Once you've signed off on the remaining open `!` follow-ups (Q3 install-CLI yes/no, Q8 filter expressivity, Q11 storage approach below, Q2 als-tiled repo path, Q6 tsuchinoko branch), I'll do the consolidating rewrite as one clean commit that supersedes the current scaffolding — new §Pipeline plugins, new §Environment (per-package venv), new §Triggers + §TriggerManager, deleted §Pipeline registry git stuff, slimmer §Auth without registry-creds, updated §Implementation skeleton.

10. **Executor lifecycle: systemd unit or on-demand only?** The exporter spawns on-demand from LUCID via `subprocess.Popen` and that's fine for manual triggers, but auto-trigger semantics break if "user closed LUCID, plan still running on queueserver, plan finishes, auto-trigger fires" — no LUCID, no executor. **Options:** (a) Phase 1 accepts "no LUCID open = no auto-trigger" as a limit (consistent with the auto-trigger-identity design, since the plan-time pre-mint also requires live LUCID); (b) Phase 1 ships a `lucid-pipelines.service` systemd unit + an autostart pattern, decoupling executor lifetime from LUCID. Leaning (a) for Phase 1 since the auth model already requires LUCID at plan-start; revisit when fallback-to-service-account is enabled.
> We're not baking in queueserver. a for phase 1, b planned.
! Resolved. Phase 1 = on-demand only; (b) planned for a later phase. Also dropping the stray queueserver mention — none of the design *requires* queueserver, it appeared only in the Raydata comparison as their architecture and I'll keep it confined there.


11. **Output cleanup / garbage collection.** Executed notebooks accumulate in `/var/lib/lucid-pipelines/runs/<job_id>.ipynb`; env cache in `~/.cache/lucid-pipelines/envs/<hash>/` is potentially gigabytes per env. Phase 1 proposal: `--retention-days N` flag (default 30) prunes executed notebooks older than N days; envs are never auto-pruned (rebuild cost is high); a `lucid-pipelines cleanup` subcommand removes envs with no manifest entry pointing at them. Defer specifics or pin now?
> why would they accumulate? we can run them without making copies? please educate me on how this integrates with the plan.
! Fair question. They accumulate because **papermill writes a new `.ipynb` per job by design** — that output file is where the executed cell outputs live (figures, prints, scrapbook scraps, tracebacks). Papermill's job is essentially "read input.ipynb + parameters, execute against a kernel, write output.ipynb with results inline." The result lives *in* a notebook file because nbformat is its serialization format.

You can avoid persisting to local disk by running papermill against an in-memory `nbformat.NotebookNode` and harvesting the result object without writing it. But you do need to **keep the result somewhere** to read the scrapbook scraps (post-execution) and to surface it as an audit artifact.

Given your Q4 answer (executed notebook → Tiled by default), the cleanest path is: **don't write to local disk at all**. Papermill executes in-memory, executor harvests scrapbook from the in-memory `NotebookNode`, then writes the executed notebook directly to Tiled as a JSON entry tagged with `pipeline_provenance`. Local disk is only used in an explicit `--debug` mode for development.

With that, the accumulation worry collapses:
- **Executed notebooks**: never on local disk; live in Tiled with the same lifecycle as any other Tiled entry.
- **Env cache** (`~/.cache/lucid-pipelines/envs/`): per-pipeline-version, persistent on purpose (rebuild = slow). One env per `(pipeline_package, version)` tuple per the Q9 plugin model. When you upgrade `als-saxs-reduce` from 0.4.1 to 0.4.2, the old env stays cached until manually cleaned with `lucid-pipelines cleanup --unused`. That's a real cache, not a leak.

So **Q11 is resolved as "no Phase 1 GC needed"** — executed notebooks ride Tiled's retention; env cache is intentional. Add a `cleanup --unused` subcommand for ops convenience.
$ I see, papermill operates by emitting .ipynb as artifacts. I want to be careful about how we use tiled with this. The analogy I'm familiar with is when acquiring data with an AreaDetector the data is written to file and that file path (typically with some data movement in the middle) gets registered in tiled. My understanding is that Tiled's datastore is database-backed, and you wouldn't want to inject large data blobs directly? I could be wrong, though.
! Your intuition is correct enough that we should follow it. Tiled's `nodes` table holds metadata; `data_sources` rows reference assets stored elsewhere (filesystem-backed adapters for arrays/dataframes/files). The bluesky pattern you describe — AreaDetector frames written to disk, paths registered in Tiled — is the same mechanism. Large blobs DON'T go straight into the DB; they're file-backed, Tiled tracks the file's existence + metadata.

For executed notebooks specifically:
- Size: typically 100KB–few MB (notebook source is small; bloat comes from inline base64 PNGs which scientists often produce). Not detector-scale, but not metadata-scale either.
- Right pattern: executor writes `executed.ipynb` to a configured filesystem path (Tiled-backed storage area, mirroring detector data conventions), then registers a Tiled entry that **references** the path — same shape as bluesky_tiled_plugins does for streamed array data.

Revised Q11 proposal (supersedes my earlier "store as JSON in Tiled"):

```
Executor configuration: --notebook-store /data/lucid-pipelines/runs/
Per job: write to /data/lucid-pipelines/runs/<input_run_uid>/<job_id>.ipynb
Tiled entry on the *output run* carries:
   metadata.start.executed_notebook = {
     "path": "/data/lucid-pipelines/runs/<input_run_uid>/<job_id>.ipynb",
     "size_bytes": 824132,
     "sha256": "...",
   }
```

The Pipeline Jobs panel's "Open executed notebook" resolves `executed_notebook.path` and opens it in whatever Jupyter / VS Code is configured. Tiled doesn't store the blob; it stores the pointer.

This brings back the local-disk accumulation that Q11 was originally about. Reconciled answer: notebooks accumulate in a **configurable directory** (default outside `/tmp`, somewhere ops-managed like `/data/lucid-pipelines/runs/`), with a `--retention-days` flag (default ∞ for Phase 1 — they're small + valuable; revisit only if it bites). No GC machinery in code; ops can `find … -mtime +N -delete` if they want.

Caveats: this requires the executor's `--notebook-store` path to be reachable from the user's workstation when they click "Open executed notebook" (NFS, SMB, or local in single-machine deployments). If that's a blocker for a beamline, manifest opt-out `store_executed_notebook: false` is still available.

Confirm this storage model and I'll write it that way.
! yeah, there are complications here. Notebooks WILL accumulate, and we'd need to setup data-movement for the pipelines to get the files off the beamline workstation. Let's acknowledge this problem but keep it out of scope. I think this is leading me to consider a data movement supporting NATS-aware piece in this ecosystem. This will break retrieving the .ipynb file from tiled, since tiled never receives it. However, we already HAVE it. We'll just store the path to file for now and not expect tiled to "fill" it.

12. **Job idempotency / NATS redelivery.** If a NATS message is redelivered (JetStream replay, transient network glitch) the executor would run the same `job_id` twice. Proposal: per-process LRU set of recent `job_id`s (size ~1024); second arrival replies `{status: already_processed, job_id, current_status}` instead of re-running. Cheap; should we just bake it in or call it explicitly?
> sounds good; bake it in.
! Baked in. Spec will fold this into §Wire formats reply behavior, no separate section needed.

13. **System-lib pinning (deferred from Phase 1).** Conda env captures Python deps; system-side state (HDF5 ABI, CUDA toolkit, GPU drivers) is workstation-pinned and varies across hosts. A pipeline that requires CUDA 12.4 cannot be expressed in a conda env alone. Raydata accepts this constraint. Phase 1 should explicitly document "the manifest declares Python deps only; system-lib portability is the registry author's responsibility, fix is containerization (Phase 2+)."
> managing system-libs is out of scope
! Confirmed — dropping this from the open-questions list; a single line in §Non-Goals will suffice.

14. **Network policy from the kernel.** Some facilities prohibit pipeline kernels reaching the public internet (e.g., calling out to PyPI for a forgotten import). Not something the design can enforce — falls to the deployment / firewall layer. **Action:** add a runbook note, no spec change.
> Seem like nothing to be done here. There are other solutions to this (local hardware, tunnels, etc.)
! Agreed — dropping from open questions entirely.

## Implementation skeleton (for the plan)

The implementation plan that follows from this spec is expected to be staged roughly as:

1. `lucid.auth.mint_job_key()` + tests (also used by tsuchinoko)
2. `RunStopNATSEmitter` + tests
3. `lucid.pipelines.{service, runner, env_cache, manifest, bindings}` + unit tests
4. `lucid-pipelines` CLI entrypoint
5. `PipelineClient` + Pipeline Jobs panel + run-context menu integration in LUCID
6. End-to-end integration test against bcgtiled
7. A reference registry repo (`als-saxs-pipelines`) with one working notebook (`reduce_saxs`) as a smoke test

Each stage merges independently; the executor is usable from the CLI before the LUCID UI lands.

Other notes:
- consider using the same plugin model (entrypoint + manifest) as lucid for pipeline registry
! Agreed and absorbed into Q9 above. The next revision of this spec — assuming you confirm — restructures around `PipelinePlugin` + `entry_points(group="lucid_pipelines.pipeline")`, mirroring LUCID's own plugin model (`AgentPlugin`, `EnginePlugin`, etc.). The git-repo / `pipelines.yaml` model is replaced by per-pipeline pip-installable packages. `bindings.yaml` stays as user/workstation config (LUCID-side, per Q1).
