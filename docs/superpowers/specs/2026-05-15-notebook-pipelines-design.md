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

The executor reads them post-execution via `sb.read_notebook(executed_nb_path).scraps`. Missing `output_run_uids` is **not** an error — a pipeline that doesn't produce new Tiled runs (e.g., a QC notebook that only emits a report) is valid; the executed notebook itself is the artifact.

A notebook may also `sb.glue("metrics", {…})` for arbitrary scalar metrics surfaced in the Jobs panel.

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

## Phase 2+ (designed-for)

The Phase 1 wire format is shaped so the following don't need protocol changes:

- **Online during acquisition.** A new subscriber listens for `run.event_page` (or sampled equivalent) and submits the same job with `parameters.partial = true, parameters.descriptor_offset = N`. Notebook reads up-to-N events from Tiled. Executor / API-key / scrapbook flow unchanged.
- **Bulk reprocessing.** A new dialog ("Reprocess all runs from yesterday…") iterates UIDs and submits jobs. No executor change.
- **Persistent kernels.** A manifest flag `kernel: persistent` switches the runner from Papermill to direct `jupyter_client` with kernel reuse across jobs. Same job message, same scrapbook harvest.
- **Bindings UI.** Replace `bindings.yaml` with a settings panel writing the same YAML.
- **Cancellation.** Add a `lucid.pipeline.<host>.cancel` request taking a `job_id`; executor SIGTERMs the subprocess and marks failed.

## Open questions

1. **Where does the `RunStopNATSEmitter` live?** The auto-trigger needs a NATS `run.stopped` event. The existing IPC service catalog (`src/lucid/ipc/service.py:215`) doesn't yet publish bluesky stop docs. Two options: (a) a small RunEngine subscriber callback in `lucid.acquire.runengine` that emits one NATS event per stop doc (cleanest); (b) extend the existing logbook plugin's hook surface. Phase 1 should add (a) — it's a useful primitive beyond pipelines.

2. **Does ALS Tiled allow user-scoped API key creation today?** Tiled's API key endpoint exists and is enabled by default, but the per-deployment policy may restrict who can mint and with what TTL. Needs a one-shot probe against `bcgtiled` before committing — if disabled, fall back to NATS-brokered token (worse but works).

3. **Conda vs uv as default env resolver.** Both are supported via file extension, but the manifest could grow a `resolver:` field if one ends up clearly preferred. Defer until 2-3 real pipelines exist.

4. **Should the executed notebook itself be stored in Tiled?** As a `dataframe`-type entry tagged with the input run? It's the natural audit artifact. Phase 1 stores it on the executor's local disk under `/var/lib/lucid-pipelines/runs/<job_id>.ipynb`. Promoting to Tiled (Phase 1.5) is a small addition.

5. **Per-pipeline resource limits.** A misbehaving notebook can fill memory or saturate I/O. Phase 1 enforces `timeout_seconds` only; cgroup-style limits deferred.

6. **Notebook output run linkage.** Should we require the notebook to write `metadata.start.parent_run_uid = LUCID_INPUT_RUN_UID` on each output, or have the bootstrap inject it into a context manager? Convention vs. enforcement — lean toward bootstrap-injected helper if/when the optional `lucid_pipeline` module materializes.

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
