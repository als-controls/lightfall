# Architecture

Lightfall is **API-first**: every user-facing surface — panels, devices, and plans — has a programmatic representation that any client can discover and invoke. The GUI, a script connected over NATS, and the embedded Claude agent are peers against that surface; none has privileged access the others lack. Capabilities are added as [plugins](plugins/index.md) that register against this surface rather than against the renderer, which is why a newly installed plugin becomes addressable by the agent and by external clients without additional wiring.

This page walks the stack layer by layer. All class and module names below are verified against `src/lightfall/`.

## Layered overview

```
┌────────────────────────────────────────────────────────────────────┐
│ Qt shell (PySide6)                                                 │
│   LFApplication · ServiceRegistry · LFMainWindow · ThemeManager    │
├────────────────────────────────────────────────────────────────────┤
│ Panel + plugin system                                              │
│   PluginLoader · PluginRegistry · PanelRegistry · BasePanel        │
│   8 plugin types (settings, panel, plan, engine, theme,            │
│   statusbar, controller, agent)                                    │
├────────────────────────────────────────────────────────────────────┤
│ Acquisition (Bluesky + Ophyd)                                      │
│   BaseEngine / BlueskyEngine / MockEngine · PlanRegistry           │
├────────────────────────────────────────────────────────────────────┤
│ Devices                                                            │
│   DeviceCatalog · backends: MockBackend, HappiBackend, BCSBackend  │
│   EPICS Channel Access via ophyd/caproto · CATunnelService         │
├──────────────────────┬─────────────────────┬───────────────────────┤
│ Data (Tiled)         │ IPC (NATS)          │ Auth (Keycloak)       │
│   TiledService       │   IPCService        │   SessionManager      │
│   access stamping    │   TrustManager      │   per-service keys    │
├──────────────────────┴─────────────────────┴───────────────────────┤
│ Embedded agent (Claude Agent SDK)                                  │
│   QtClaudeAgent · AgentRegistry · per-plugin MCP servers           │
└────────────────────────────────────────────────────────────────────┘
```

## Qt shell

`lightfall.core.application.LFApplication` is the application singleton. It owns the Qt event loop, coordinates the initialization sequence (configuration → services → plugins → UI), and manages lifecycle states (`UNINITIALIZED` → `INITIALIZING` → `READY` → `RUNNING` → `SHUTTING_DOWN` → `TERMINATED`).

Two pieces of infrastructure live here:

- **`ServiceRegistry`** (`lightfall.core.services`) — a lazy dependency-injection container. Services such as `ConfigManager`, `TrustManager`, and `IPCService` are registered with factory functions and constructed on first access; tests can reset and substitute them.
- **Layered configuration** (`lightfall.config.manager.ConfigManager`) — YAML configuration merged from bundled package defaults, a system-wide directory (`/etc/ncs` or `%PROGRAMDATA%\ncs`), a per-user directory (`~/.config/ncs` or `%APPDATA%\ncs`), and a mutable runtime session layer, validated against a Pydantic schema (`lightfall.config.schema.LFConfig`). Authentication provider settings (`auth.provider.*`) live here. Day-to-day user preferences use a separate mechanism, `lightfall.ui.preferences.manager.PreferencesManager`, which backs the Preferences dialog.

## Panel and plugin system

The window is composed of dockable **panels**. Each panel is a `BasePanel` subclass (`lightfall.ui.panels.base`) carrying `PanelMetadata` (id, name, category, docking preferences) and an introspection contract: panels report their state and expose named **actions** that can be invoked programmatically. `PanelRegistry` (`lightfall.ui.panels.registry`) tracks the available panels and filters them by the current user's permissions.

Panels — like everything else — arrive through the **plugin system** (`lightfall.plugins`): `PluginLoader` discovers `PluginManifest` objects (the built-in manifest plus any registered under the `lightfall.plugins` entry-point group), and `PluginRegistry` tracks loaded plugins by type and name. Eight plugin types exist; see the [Plugin Type Reference](plugins/plugin-types/index.md).

User-authored plugin files placed under `~/lightfall/plugins/` are loaded by `UserPluginService` and auto-registered via `PluginType.__init_subclass__`. Every change to these files — whether written by a person or by the embedded agent — is committed by a `GitTracker` to a git repository at `~/lightfall/`, so customization history is ordinary git history.

## Acquisition: Bluesky and Ophyd

Plan execution goes through an engine abstraction in `lightfall.acquire.engine`:

- `BaseEngine` defines the contract: submit plans, pause/resume/stop/abort, signals for documents (`sigOutput`), state changes, completion, and exceptions.
- `BlueskyEngine` wraps a Bluesky `RunEngine` running on a background thread, emitting documents to Qt as they are produced.
- `MockEngine` provides the same interface without hardware, for development and tests.
- `get_engine()` returns the configured engine instance.

Plans are registered in a `PlanRegistry` (`lightfall.acquire.plans.registry`) with parameter signatures, so both the GUI's plan panel and the agent's plan tools can enumerate them and validate parameters. Plans are ordinary Bluesky generator functions; `PlanPlugin` instances contribute new ones.

## Devices

`DeviceCatalog` (`lightfall.devices.catalog`) is the single registry of devices. It is populated by pluggable backends in `lightfall.devices.backends`:

- **`MockBackend`** — simulated motors and detectors (Gaussian-response point detectors, temperature/pressure signals) so a fresh install works with no hardware.
- **`HappiBackend`** — loads device definitions from a [happi](https://github.com/pcdshub/happi) database and instantiates Ophyd devices from them.
- **`BCSBackend`** — bridges to the ALS Beamline Control System over its TCP protocol, so beamlines running LabVIEW-based BCS can expose their devices without re-describing them.

Backends are selected and configured in Preferences (see [Deployment](deployment.md)). Devices ultimately speak EPICS Channel Access through Ophyd (caproto); for off-site work, `CATunnelService` bridges CA's UDP discovery phase through an SSH tunnel — see [Remote EPICS Access](remote-epics-access.md).

## Data: Tiled

`TiledService` (`lightfall.services.tiled_service`) manages the connection to a [Tiled](https://blueskyproject.io/tiled/) data server: URL and authentication mode come from preferences, and connection state is surfaced in the status bar. Acquired runs are written to Tiled by a `TiledWriter` subscribed to the engine's document stream; the data-browser panel and visualization widgets read back through the same catalog.

Data access is enforced **per entry**. At run start, the access stamper (`lightfall.services.access_stamper`) reads the operator's identity from the Keycloak session and the active ESAF (from alshub or an admin override) and emits `tiled_access_tags` in the Bluesky start document. Tiled stores those tags in its `access_blob` column and filters every read against the requesting user. Authorization for data access derives from the same Keycloak session that authorizes control operations, so a single identity governs both motor actuation and data retrieval.

## IPC: NATS

The runtime participates in a beamline-wide NATS message bus through `IPCService` (`lightfall.ipc.service`). External services — autonomous engines, live-analysis processes, external agents — address the same plans, logbook, and agent the GUI uses, via request/reply actions (`commands.plan.run`, `commands.logbook.add`, `commands.agent.message`, …) and published events (`runs.new`, `runs.complete`, `state.engine`). Actions and events are self-describing through `meta.actions` / `meta.events` discovery endpoints. A `TrustManager` gates unknown clients behind a user-facing trust dialog; approved clients receive a Tiled API key over the same handshake.

See [IPC Architecture](ipc-architecture.md) for internals and the [IPC Client Integration Guide](ipc-client-guide.md) for writing clients.

## Authentication: Keycloak and auth v2

`SessionManager` (`lightfall.auth.session`) holds the current `Session` and `User`. Providers in `lightfall.auth.providers` implement the actual flows:

- **`KeycloakAuthProvider`** — OIDC against a facility Keycloak; opens a browser for login, maps Keycloak groups/roles to application roles.
- **`LocalAuthProvider`** and a PAM provider — development and single-machine fallbacks. Guest login is available for evaluation.

Under the **auth-v2** model, the short-lived Keycloak access token is used once at login to **mint per-service API keys** (`lightfall.auth.service_key.mint_service_key`) against each Lightfall-protected service that implements the Tiled-shape `/api/v1/auth/apikey` contract — the Tiled server today, the logbook server next. Keys are user-scoped, carry a TTL (one week by default), and outlive the Keycloak token, so a running session does not depend on token refresh. The same keys are what `IPCService` hands to trusted external clients.

Authorization checks use `lightfall.auth.policy.Permission` (e.g. `DEVICE_CONTROL`); both GUI controls and agent tools check the same permissions before acting.

## Embedded agent

`QtClaudeAgent` (`lightfall.claude.agent`) embeds a Claude Agent SDK session inside the Qt process. At construction it assembles the session from two sources:

1. **A fixed set of generic Qt tools** (`lightfall.claude.tools`): `screenshot`, `get_widget_tree`, `find_widget`, `click_widget`, `type_text`, `show_controller`, and `get_recent_logs`.
2. **The enabled `AgentPlugin` instances** from `AgentRegistry`: each contributes a skill prompt (materialized as a `SKILL.md`) and/or an in-process MCP server of domain tools. Built-in agent plugins cover panels (`lightfall_list_panels`, `lightfall_open_panel`, `lightfall_invoke_panel_action`, …), devices, plans, the engine, the IPython console, and panel/plan authoring expertise.

The addressability story is deliberately simple: the agent's tools are a **fixed, generic set**, and their *results* come from the same registries the GUI renders — `PanelRegistry`, `DeviceCatalog`, `PlanRegistry`, and panel introspection data. Tool schemas are not generated at runtime. When a plugin adds a new panel or plan, the existing `list_*` tools report it and the existing `open_panel` / `run_plan` / `invoke_panel_action` tools can act on it, because those tools query the registries rather than hard-coding what exists.

Agent-driven customization goes through the same user-plugin path as manual customization: panels the agent writes land as files under `~/lightfall/plugins/`, are hot-loaded into the running session, and are committed to the `~/lightfall/` git repository by the `GitTracker`. See [AgentPlugin](plugins/plugin-types/agent.md) for the plugin authoring reference.

## Supporting infrastructure

### Logbook

The logbook is a service the GUI, the agent, and IPC clients all publish to — not merely a panel. `LogbookClient` (`lightfall.logbook.client`) is **local-first**: entries land in a local SQLite store and sync to the facility logbook server when one is configured (`logbook_url` preference; `logbook_offline_only` disables sync). Entries are created automatically on run start, completion, and error through acquisition event listeners, and on device actuation through an action logger; manual entries and viewport screenshots attach to the same record.

### Error tracking

Lightfall can report errors to any Sentry-compatible server (the ALS runs a self-hosted GlitchTip) via `sentry-sdk` (`lightfall.utils.sentry`). Telemetry is **opt-in**: it activates only when a DSN is explicitly configured through the `SENTRY_DSN` environment variable or the `telemetry_dsn` preference; with no DSN configured, all reporting no-ops. The integration hooks Loguru so anything logged at `ERROR` or above is captured, a `before_send` hook scrubs sensitive data, and attached user context is limited to the username — never tokens.

## Technical foundation

- **PySide6** — Qt for Python; all UI
- **Bluesky / Ophyd** — plan execution and device abstraction
- **Tiled** — data catalog and access
- **NATS** (`nats-py`) — inter-process messaging
- **Pydantic** — validated, layered configuration
- **PyQtGraph** — real-time plotting (see [development notes](pyqtgraph-notes.md))
- **Loguru** — structured logging, feeding the in-app logging panel, the agent's `get_recent_logs` tool, and optional Sentry reporting
- **asyncio + QThread** — NATS I/O and the Bluesky RunEngine run on background threads; results are marshalled to the Qt main thread
