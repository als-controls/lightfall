# Deployment

How Lightfall is deployed at a facility and at a beamline: which services run once per facility, which run per beamline, and exactly what must be configured today. Lightfall is in testing at the **COSMIC-Scattering** beamline at the ALS — a production-class deployment scoped to a single beamline while the platform matures.

```{note}
A turnkey beamline profile (one file that captures a beamline's full configuration) and a provisioning runbook are on the roadmap. Today, deployment means standing up the services below and setting preferences in each Lightfall installation by hand. This page documents what exists now.
```

## Topology: facility vs. beamline

Most of the stack runs **once per facility** and is shared by every beamline:

| Service | Role | Notes |
|---------|------|-------|
| **Keycloak** | Single sign-on; identity for both control and data access | Lightfall authenticates via OIDC and maps Keycloak groups/roles to application roles |
| **Tiled server** | Data catalog; per-entry access enforcement | Must implement the auth-v2 API-key contract (`/api/v1/auth/apikey`); the ALS deployment is `als-tiled` |
| **Logbook server** | Facility logbook backend | Lightfall's `LogbookClient` is local-first; entries sync to this server when configured |
| **Error tracking** | Sentry-compatible server (the ALS runs self-hosted GlitchTip) | Optional; telemetry is disabled unless a DSN is configured |
| **Git hosting** | Repositories for beamline plugin packages | Any git forge; plugin packages are ordinary Python packages |

Each beamline runs its **own**:

| Component | Role | Notes |
|-----------|------|-------|
| **Lightfall GUI** | The application itself, one process per operator console | `pip install lightfall`; Python 3.11+ |
| **NATS subjects** | The beamline's slice of the message bus | The broker itself can be shared facility-wide; beamlines are separated by topic prefix (e.g. `als.7011`) |
| **EPICS IOCs / devices** | The actual hardware layer | Lightfall coexists with existing control systems — both address the same EPICS process variables over Channel Access, so adoption can be incremental |
| **Beamline plugin repository** | Beamline-specific panels, plans, settings, themes | Installed into the Lightfall environment as a Python package; see [External Packages](plugins/external-packages.md) |
| **Autonomous engine** (optional) | e.g. [Tsuchinoko](https://github.com/lbl-camera/tsuchinoko) for adaptive experiments | Connects over NATS like any external client |

## What must be configured today

### Preferences (per installation)

User-facing settings live in `PreferencesManager` and are edited in the Preferences dialog (**File → Preferences**). The keys below are the deployment-relevant ones, verified against `src/lightfall/ui/preferences/`:

**IPC / NATS** (`ipc_settings.py`):

| Key | Default | Description |
|-----|---------|-------------|
| `ipc_nats_url` | `nats://bcgnats.als.private.lbl.gov:4222` | NATS broker URL; an empty string disables IPC entirely |
| `ipc_topic_prefix` | `als.7011` | Prefix prepended to every published/subscribed subject — set this per beamline |
| `ipc_display_name` | *(empty)* | Human-readable name for this instance, shown to discovery clients |

**Tiled** (`tiled_settings.py`):

| Key | Default | Description |
|-----|---------|-------------|
| `tiled_enabled` | `true` | Enable the Tiled connection |
| `tiled_url` | `http://bcgtiled.dhcp.lbl.gov:8000/` | Tiled server URL |
| `tiled_auth_mode` | `keycloak` | `keycloak` (per-service API key minted at login) or explicit API key |
| `tiled_api_key` | *(empty)* | Static API key, used when not authenticating via Keycloak |
| `tiled_beamline` | *(empty)* | Beamline identifier used in access tags and ESAF lookup |
| `tiled_alshub_url` | `https://bcgmds01.als.lbl.gov` | alshub API used to resolve the active ESAF for access stamping |

**Logbook** (`logbook_settings.py`):

| Key | Default | Description |
|-----|---------|-------------|
| `logbook_enabled` | `true` | Enable the logbook subsystem |
| `logbook_url` | `http://bcglightfalllogbook.dhcp.lbl.gov:8000` | Facility logbook server; entries are stored locally and synced here |
| `logbook_offline_only` | `false` | Keep entries local-only (no server sync) |

**Devices** (`device_settings.py`) — backends can be enabled independently:

| Key | Default | Description |
|-----|---------|-------------|
| `device_mock_enabled` | `true`* | Simulated devices (the out-of-the-box experience) |
| `device_mock_include_noisy` | `true` | Include noisy variants of the simulated signals |
| `device_bcs_enabled` | `false`* | ALS Beamline Control System backend |
| `device_bcs_host` / `device_bcs_port` | `localhost` / `5577` | BCS server address |
| `device_bcs_beamline` | *(empty)* | BCS beamline identifier |
| `device_bcs_timeout_ms` | `5000` | BCS request timeout |
| `device_happi_enabled` | `false` | happi database backend |
| `device_happi_path` | *(empty)* | Path to the happi database |
| `device_happi_beamline` | *(empty)* | Filter to one beamline's devices |
| `device_happi_instantiate` | `false` | Instantiate Ophyd devices on load |

\* The legacy single-valued `device_backend` key (`"mock"` / `"bcs"` / `"happi"`) is still written for compatibility and seeds the per-backend defaults.

Connection behavior is tunable via `device_instantiate_mode` (default `background`), `device_connection_timeout` (default `5.0` s), and `device_connect_on_startup` (default `true`). For off-site use, the same page configures the CA tunnel (`Enable CA tunnel for remote access` plus a gateway address, default `localhost:5099`) — see [Remote EPICS Access](remote-epics-access.md).

### Authentication (YAML config)

The auth provider is configured in the layered YAML configuration (`application.yaml`), not in Preferences. Files merge in priority order: bundled package defaults → system-wide (`/etc/ncs/` or `%PROGRAMDATA%\ncs\`) → per-user (`~/.config/ncs/` or `%APPDATA%\ncs\`) → runtime session overrides.

```yaml
auth:
  provider:
    type: keycloak        # local | keycloak | pam
    server_url: https://keycloak.example.org
    realm: alsncs
    client_id: LUCID
    redirect_uri: http://localhost:8089/callback
```

Field names and defaults are defined in `lightfall.config.schema.AuthProviderConfig`. The `NCS_AUTH` environment variable overrides the provider type at launch (e.g. `NCS_AUTH=local` for development). With no Keycloak configured, local auth and guest login keep the application usable.

### Telemetry (opt-in)

Error reporting is **disabled unless a DSN is explicitly configured** — there is no default endpoint. Provide one via either:

- the `SENTRY_DSN` environment variable (wins), or
- the `telemetry_dsn` preference.

Any Sentry-compatible server works. With no DSN set, all reporting functions no-op.

### Agent credentials

The embedded Claude agent needs Anthropic credentials on each machine: set the `ANTHROPIC_API_KEY` environment variable, or authenticate a Claude subscription with `claude login`. Everything else in the application works without it.

## A minimal single-machine deployment

For evaluation, none of the facility services are required: install, launch, continue as guest, and use the mock device backend. Adding services is incremental from there —

1. **Tiled** — point `tiled_url` at a Tiled server; runs are cataloged and browseable.
2. **NATS** — point `ipc_nats_url` at a broker and choose a `ipc_topic_prefix`; external clients can now submit plans and watch runs.
3. **Keycloak** — configure `auth.provider` in YAML; logins now mint per-service API keys, and runs are access-stamped per user and ESAF.
4. **Logbook server** — point `logbook_url` at the facility logbook; local entries begin syncing.
5. **Telemetry** — set a DSN once an error-tracking server exists.

Preference changes for the IPC service take effect on the next restart; the service is not reconfigured at runtime.
