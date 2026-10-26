# Developer Guide

Technical and architecture documentation for extending, integrating with, and deploying Lightfall. This page is the map; each section below links to the page that covers it in depth.

## How Lightfall is put together

Start with [Architecture](architecture.md). It covers the layered design — the Qt shell, the panel and plugin system, the acquisition engine over Bluesky/Ophyd, device backends, Tiled data access, NATS messaging, Keycloak authentication, and the embedded agent — and explains the API-first principle that ties them together: the GUI, external clients, and the agent all address the same registries.

## Extending the application

The [plugin system](plugins/index.md) is the supported way to add functionality. Plugins are ordinary Python classes registered through manifests and discovered via entry points — no core code modification required.

- [Plugin System Overview](plugins/index.md) — architecture, loading lifecycle, manifests
- [Quickstart](plugins/quickstart.md) — a working plugin in five minutes
- [Creating Plugins](plugins/creating-plugins.md) — step-by-step guide and common patterns
- [Plugin Type Reference](plugins/plugin-types/index.md) — all eight plugin types, including [AgentPlugin](plugins/plugin-types/agent.md) for extending the embedded Claude agent
- [External Packages](plugins/external-packages.md) — packaging and distributing plugins as standalone Python packages

## Integrating external processes

Lightfall participates in a NATS message bus so that external services — automation scripts, live-analysis processes, autonomous engines such as Tsuchinoko — can submit plans, watch run lifecycle events, write logbook entries, and message the agent.

- [IPC Architecture](ipc-architecture.md) — how `IPCService`, trust management, and the action/event catalogs work inside Lightfall
- [IPC Client Integration Guide](ipc-client-guide.md) — how to write an external client; requires no knowledge of Lightfall internals
- [NATS Use Model](nats-use-model.md) — the reference model for every component on the beamline bus: subject classes, the per-connection capability enforced by the bcgnats auth callout, and what is and isn't protected

## Reaching EPICS from off-site

[Remote EPICS Access](remote-epics-access.md) documents the CA tunnel: how Lightfall bridges Channel Access's UDP discovery phase through an SSH tunnel to a CA Gateway on the beamline network, and how to set up both ends.

## Deploying at a facility

[Deployment](deployment.md) covers the facility-versus-beamline topology — which services run once per facility (Keycloak, Tiled, the logbook server, error tracking) and which run per beamline (the GUI, NATS subjects, IOCs) — plus everything that must be configured today and what is still on the roadmap.

## Working on Lightfall itself

- [PyQtGraph Development Notes](pyqtgraph-notes.md) collects non-obvious coordinate-system and rendering gotchas for contributors working on visualization code.
- [Startup Profiling](startup-profiling.md) documents `LIGHTFALL_STARTUP_PROFILE`, the HTML/JSON/cProfile artifacts written to `~/.lightfall/diagnostics/`, and how to instrument additional startup phases.

```{toctree}
:maxdepth: 2

architecture
plugins/index
ipc-architecture
ipc-client-guide
nats-use-model
remote-epics-access
deployment
pyqtgraph-notes
startup-profiling
```
