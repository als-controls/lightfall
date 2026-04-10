# Data Export System Design

## Overview

Add batch data export to LUCID, allowing users to select runs in the Data Browser and export them via a headless exporter tool. The exporter runs as an independent process communicating over NATS IPC, keeping heavy processing out of LUCID's UI thread and enabling multi-workstation deployment.

## Goals

- Export selected runs from the Data Browser in batch
- Support NXsas (HDF5 with ROI cropping) and NoOp (raw file copy) converters
- Run export processing in a separate process to avoid impacting LUCID's UI performance
- Handle multi-workstation environments where each machine has different local disks
- Report progress via toast notifications

## Non-Goals (v1)

- Pluggable converter system (fixed set of converters for now)
- Remote host targeting (export always targets a local exporter)
- Dedicated exports panel or detailed progress UI
- Exporter GUI (all parameter collection happens in LUCID)
- Auto-shutdown or lifecycle management of the exporter process

## Architecture

### Components

1. **Data Browser (modified)** — Multi-select + Export button in LUCID's tiled browser panel
2. **Export Dialog (new)** — LUCID-side dialog for choosing export type, output directory, and type-specific parameters
3. **Exporter Service (new)** — Headless CLI tool that subscribes to NATS, receives jobs, processes exports, publishes progress

### Communication

All communication uses LUCID's existing NATS-based IPC infrastructure.

```
LUCID (workstation B)                    Exporter (workstation B)
┌─────────────────┐                      ┌─────────────────────┐
│ Data Browser     │                      │ lucid-exporter CLI  │
│  - multi-select  │   NATS request       │                     │
│  - export button │ ──────────────────── │ Subscribe:          │
│                  │   lucid.export.hostB  │  lucid.export.hostB │
│ Export Dialog    │                      │                     │
│  - type selector │   NATS reply         │ Job Queue           │
│  - dir picker    │ ◄────────────────── │  - sequential       │
│  - ROI widget    │   {status: queued}   │  - per-run dispatch │
│                  │                      │                     │
│ Toast Display    │   NATS pub/sub       │ Converters          │
│  - progress      │ ◄────────────────── │  - NXsas            │
│  - completion    │   lucid.export.      │  - NoOp             │
│  - errors        │     hostB.progress   │                     │
└─────────────────┘                      └─────────────────────┘
```

### Routing: Topic-Per-Host

Each exporter subscribes to `lucid.export.<hostname>`. LUCID publishes to its own hostname's topic. This naturally handles multiple workstations on the same NATS bus — each machine's exporter only receives jobs intended for that machine's local disk.

Targeting a remote host's exporter (e.g., exporting to tsuru's disk from another workstation) is trivially possible by publishing to a different hostname's topic, but is not exposed in the UI for v1.

### On-Demand Spawning

When the user initiates an export, LUCID:

1. Publishes a ping request to `lucid.export.<hostname>.ping` (1s timeout)
2. If a reply is received, the exporter is already running — proceed to send the job
3. If no reply, spawn a local exporter: `subprocess.Popen(["lucid-exporter", "--nats", nats_url])`
4. Retry ping with backoff until the exporter responds (fail after reasonable timeout with error toast)

LUCID does not track or manage the exporter's lifecycle beyond spawning it. The exporter runs independently until killed.

## Data Browser Changes

### Multi-Select

Change selection mode from `QAbstractItemView.SingleSelection` to `QAbstractItemView.ExtendedSelection`. This enables standard shift-click (range) and ctrl-click (toggle) selection.

The existing `record_clicked` / `record_double_clicked` signals remain unchanged — double-click still opens a single run in visualization.

A new internal method `_get_selected_records() -> list[TiledRecord]` gathers all currently selected records from the selection model.

### Export Button

Added to the toolbar area near the existing filter/refresh controls. Enabled only when one or more runs are selected. Clicking opens the export configuration dialog.

## Export Dialog

A modal `QDialog` in LUCID with the following controls:

1. **Export type** — `QComboBox` with available converter types (NXsas, NoOp)
2. **Output directory** — `QLineEdit` + browse button (`QFileDialog.getExistingDirectory`)
3. **Type-specific parameters area** — a stacked widget that shows different parameter widgets depending on the selected export type:
   - **NoOp:** No additional parameters
   - **NXsas:** ROI selection widget — `pyqtgraph.ImageView` displaying a sample image from the first selected run, with a `pyqtgraph.RectROI` overlay. Outputs `{x, y, width, height}`.
4. **Export / Cancel buttons**

The dialog is responsible for:
- Loading a sample image from Tiled for ROI selection (via `QThreadFuture` to avoid blocking)
- Validating inputs (directory exists and is writable, ROI within bounds)
- Assembling the complete job message
- Triggering the IPC send

Location: `src/lucid/ui/dialogs/export_dialog.py`

## Exporter Service

### Entry Point

```toml
# pyproject.toml
[project.scripts]
lucid = "lucid.main:cli"
lucid-exporter = "lucid.exporter.cli:main"
```

CLI usage:
```
lucid-exporter --nats nats://localhost:4222
```

### Code Location

```
src/lucid/exporter/
├── __init__.py
├── cli.py              # argparse, NATS connection, main loop
├── service.py          # NATS subscription, job queue, dispatch
├── converters/
│   ├── __init__.py
│   ├── base.py         # Converter ABC
│   ├── noop.py         # Copy raw files
│   └── nxsas.py        # NXsas HDF5 with ROI cropping
└── tiled_utils.py      # Connect to Tiled, fetch run data
```

### Dependencies

The exporter has no Qt or GUI dependencies. Its requirements:
- `nats-py` — NATS client (already a LUCID dependency)
- `tiled[client]` — data access (already a LUCID dependency)
- `h5py` — HDF5 writing for NXsas
- `numpy` — array operations

All are already LUCID dependencies or trivial additions.

### Job Message Format

Received via NATS request on `lucid.export.<hostname>`:

```json
{
    "job_id": "uuid",
    "tiled_url": "https://tiled.example.com",
    "auth_token": "eyJ...",
    "run_uids": ["abc123", "def456", "ghi789"],
    "export_type": "nxsas",
    "params": {
        "output_dir": "/data/exports/2026-04-10/",
        "roi": {"x": 10, "y": 20, "width": 100, "height": 100}
    }
}
```

### Job Acknowledgment

Immediate reply to the NATS request:

```json
{
    "job_id": "uuid",
    "status": "queued"
}
```

### Progress Events

Published to `lucid.export.<hostname>.progress`:

```json
{
    "job_id": "uuid",
    "status": "processing",
    "current_run": 2,
    "total_runs": 5,
    "detail": "Exporting run def456..."
}
```

Status values: `queued` | `processing` | `completed` | `failed`

On failure, `detail` contains the error message.

### Runtime Model

The exporter runs a plain `asyncio` event loop (no Qt). NATS subscriptions and job processing are all async coroutines. Converter work that is CPU-bound or uses synchronous libraries (h5py, numpy) runs in a thread executor via `asyncio.to_thread()`.

### Internal Processing

1. Job arrives via NATS request, is acknowledged immediately and added to an `asyncio.Queue`
2. A worker coroutine pulls jobs from the queue sequentially
3. For each job:
   a. Connect to Tiled using provided URL + auth token
   b. For each run UID in the batch:
      - Fetch run data from Tiled
      - Dispatch to the appropriate converter (`nxsas` or `noop`)
      - Publish progress event after each run completes
   c. Publish final `completed` or `failed` event
4. Tiled connection is per-job (no persistent connection)

### Ping Endpoint

Subscribes to `lucid.export.<hostname>.ping`. Replies with:

```json
{
    "hostname": "workstation-b",
    "status": "ready",
    "queue_depth": 0
}
```

### Converters

**Base converter interface:**
```python
class Converter(ABC):
    @abstractmethod
    async def export(self, tiled_client, run_uid: str, params: dict, progress_cb) -> None:
        """Export a single run. Raises on failure."""
```

**NoOp converter:**
- Accesses the run's raw data arrays from Tiled
- Writes them to `output_dir/<run_uid>/` preserving the Tiled structure as individual files (numpy `.npy` or original format)

**NXsas converter:**
- Accesses the run's image data from Tiled
- Applies ROI cropping using `params["roi"]`
- Writes NXsas-compliant HDF5 file to `output_dir/<run_uid>.h5`
- Follows NeXus NXsas schema for SAXS data

## LUCID-Side IPC Integration

### Sending a Job

After the export dialog collects all parameters:

1. Assemble job message with: generated UUID, Tiled URL from `TiledService`, current auth token from `SessionManager`, selected run UIDs, export type, parameters
2. Ping the local exporter (1s timeout)
3. If no response, spawn via `subprocess.Popen(["lucid-exporter", "--nats", nats_url])`
4. Retry ping with backoff (max ~5 retries, ~5s total)
5. Send job via NATS request to `lucid.export.<hostname>`
6. On acknowledgment, subscribe to progress events filtered by job ID
7. Show "Export queued" toast

### Progress Display

Subscribe to `lucid.export.<hostname>.progress`, filter by `job_id`:

| Status | Toast |
|--------|-------|
| `queued` | "Export queued (N runs)" |
| `processing` | "Exporting run M/N..." |
| `completed` | "Export complete — N runs to /path/" |
| `failed` | Error toast with detail |

## Testing Strategy

- **Converters:** Unit tests with mock Tiled data — verify correct file output, ROI cropping, NXsas schema compliance
- **Service:** Integration tests with embedded NATS server — verify job queue, progress events, ping/reply
- **Export dialog:** Manual testing (Qt dialog testing is brittle and low-value for v1)
- **End-to-end:** Manual — select runs, export, verify files on disk
