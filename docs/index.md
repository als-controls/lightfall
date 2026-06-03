# Lightfall Documentation

```{image} _static/logo.png
:alt: Lightfall Logo
:width: 200px
:align: center
```

**Lightfall** is a next-generation control system that brings together hardware control, data acquisition, and AI assistance in a single, unified interface. Built for the Advanced Light Source facility and designed for the future of synchrotron science.

---

## Why Lightfall?

Running experiments at a synchrotron beamline has traditionally meant juggling multiple applications, remembering obscure command sequences, and manually documenting every step. Lightfall changes this by providing:

- **One interface for everything** — Device control, data acquisition, experiment logging, and data browsing in coordinated panels
- **AI that understands your beamline** — Ask Claude to open panels, explain procedures, or help troubleshoot errors using natural language
- **Extensibility without limits** — A plugin system with 9 distinct extension points lets facilities and beamline scientists customize every aspect
- **Data you can trust** — FAIR-compliant data management with automatic cataloging, metadata capture, and provenance tracking

---

## Core Integrations

Lightfall builds on proven scientific computing infrastructure:

| Integration | Purpose |
|-------------|---------|
| **Bluesky** | Data acquisition engine — scans, plans, and document-based data flow |
| **Ophyd** | Device abstraction — motors, detectors, and custom hardware |
| **EPICS** | Control system communication via Channel Access |
| **Tiled** | Data catalog and access — browse, search, and retrieve experiment data |
| **Keycloak** | Authentication and authorization — SSO with role-based access control |
| **Claude (MCP)** | AI assistant with tool-use capabilities for natural language control |

---

## The Claude Assistant

Lightfall includes an integrated AI assistant powered by Claude. This is not a simple chatbot — Claude has access to MCP (Model Context Protocol) tools that let it actually *do* things:

- **Open and arrange panels** — "Show me the Devices panel"
- **Query device status** — "What's the current position of the sample stage?"
- **Explain procedures** — "How do I run a 2D grid scan?"
- **Understand errors** — "Why did my scan stop?"

The assistant is fully extensible. Beamline scientists can add custom skills and tools that give Claude domain-specific knowledge and capabilities.

---

## Architecture

Lightfall is **API-first**: every user-facing surface — panels, devices, and plans — has a programmatic representation that any client can discover and invoke through one uniform interface. The GUI, a script, and the embedded Claude agent are peers against that surface; none has privileged access the others lack. This uniform addressability is what lets a single agent act on panels, devices, and plans without bespoke per-type adapters.

The runtime also participates in a beamline-wide [NATS](ipc-architecture.md) message bus, so external services — autonomous engines, live-analysis processes, and external agents — address the same surface as the GUI. Capabilities are added as [plugins](plugins/index.md) that register against this surface rather than against the renderer, which is why a newly installed plugin becomes agent-addressable automatically. The **Technical Foundation** section below summarizes the underlying stack.

---

## Plugin Architecture

Lightfall's plugin system is designed for real extensibility, not just theming. Nine distinct plugin types cover the full spectrum of customization:

| Plugin Type | What You Can Add |
|-------------|------------------|
| **Panel** | New dockable UI panels |
| **Settings** | Preferences pages |
| **Plan** | Bluesky scan procedures |
| **Engine** | Execution backends |
| **Theme** | Color schemes |
| **StatusBar** | Status indicators |
| **Controller** | Device control widgets |
| **MCP Tool** | Claude assistant capabilities |
| **Skill** | Claude domain expertise |

Plugins are distributed as Python packages and discovered automatically via entry points. No core code modification required.

---

## User Guide

Get started with Lightfall for your experiments:

- [Getting Started](user/getting-started.md) — Overview and first steps
- [Logging In](user/login.md) — Authentication and access
- [Running Plans](user/running-plans.md) — Execute data acquisition plans
- [Using Panels](user/panels.md) — Work with the main application panels
- [Claude Assistant](user/claude-assistant.md) — AI-powered help and control
- [Preferences](user/preferences.md) — Customize your experience

```{toctree}
:maxdepth: 2
:caption: User Guide
:hidden:

user/getting-started
user/login
user/running-plans
user/panels
user/claude-assistant
user/preferences
```

## Developer Guide

Extend Lightfall with custom functionality:

- [Plugin System Overview](plugins/index.md) — Architecture and concepts
- [Plugin Quickstart](plugins/quickstart.md) — Create your first plugin
- [Creating Plugins](plugins/creating-plugins.md) — Step-by-step guide
- [Plugin Types](plugins/plugin-types/index.md) — All 9 plugin types

```{toctree}
:maxdepth: 2
:caption: Developer Guide
:hidden:

plugins/index
```

## API Reference

- [Plugin Types](api/plugins.md) — Base classes and infrastructure

```{toctree}
:maxdepth: 2
:caption: API Reference
:hidden:

api/index
```

---

## Technical Foundation

Lightfall is built on a modern, maintainable stack:

- **PySide6** — Qt for Python with native look and feel
- **Pydantic** — Type-safe configuration with validation
- **PyQtGraph** — High-performance real-time plotting
- **Loguru** — Structured, human-readable logging
- **asyncio + QThread** — Responsive UI with background processing

The architecture emphasizes:

- **Layered configuration** — Defaults → Site → Beamline → User → Session
- **Service registry** — Dependency injection for testability
- **Document-based data flow** — Bluesky documents from acquisition through storage
- **Progressive disclosure** — Simple defaults with expert options behind authorization

---

## Supporting Infrastructure

Beyond control and acquisition, Lightfall integrates the operational services a beamline depends on day to day.

### Logbook

The logbook is a service the GUI and the embedded agent both publish to, not merely a panel. Entries are created **automatically** on run start and completion — and on error events — through acquisition triggers, and **manually** by staff. Manual entries capture the run UID, the user's identity, and relevant panel state; screenshots from the Lightfall viewport can be attached directly.

### Error tracking

Lightfall reports errors to a self-hosted, Sentry-compatible **Glitchtip** instance via `sentry-sdk`. The integration hooks Loguru, so anything logged at `ERROR` or above is captured automatically and tagged with the release version and environment. A `before_send` hook scrubs sensitive data before transmission, and the attached user context is limited to the Keycloak username — never the raw token.

### Authorization

Data access is enforced **per entry** through Tiled's `access_blob`. Acquisition emits `tiled_access_tags` in the Bluesky start document; Tiled populates the `nodes.access_blob` JSONB column from those tags and enforces row-level access at read time. Authorization derives from the same Keycloak session that authorizes control operations, so a single identity governs both motor actuation and data retrieval.

---

## Deployment

Lightfall is in testing at the **COSMIC-Scattering** beamline at the ALS — a production-class deployment scoped to a single beamline to keep the blast radius small while the platform matures.

Most of the stack runs **once per facility**: Keycloak SSO, the Lightfall logbook server, the Tiled server, error tracking, and git hosting for plugin repositories. Each beamline runs its **own** Lightfall GUI process, NATS broker, EPICS IOCs, and plugin repository, plus an optional autonomous engine such as Tsuchinoko.

Lightfall **coexists** with existing LabVIEW infrastructure: both address the same EPICS process variables over Channel Access, so a beamline can adopt Lightfall incrementally and migrate when it is ready rather than on a forced schedule.
