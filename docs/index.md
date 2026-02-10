# LUCID Documentation

```{image} _static/logo.png
:alt: LUCID Logo
:width: 200px
:align: center
```

**LUCID** (Lightsource Unified Control Interface Dashboard) is a next-generation control system that brings together hardware control, data acquisition, and AI assistance in a single, unified interface. Built for the Advanced Light Source facility and designed for the future of synchrotron science.

---

## Why LUCID?

Running experiments at a synchrotron beamline has traditionally meant juggling multiple applications, remembering obscure command sequences, and manually documenting every step. LUCID changes this by providing:

- **One interface for everything** — Device control, data acquisition, experiment logging, and data browsing in coordinated panels
- **AI that understands your beamline** — Ask Claude to open panels, explain procedures, or help troubleshoot errors using natural language
- **Extensibility without limits** — A plugin system with 9 distinct extension points lets facilities and beamline scientists customize every aspect
- **Data you can trust** — FAIR-compliant data management with automatic cataloging, metadata capture, and provenance tracking

---

## Core Integrations

LUCID builds on proven scientific computing infrastructure:

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

LUCID includes an integrated AI assistant powered by Claude. This is not a simple chatbot — Claude has access to MCP (Model Context Protocol) tools that let it actually *do* things:

- **Open and arrange panels** — "Show me the Devices panel"
- **Query device status** — "What's the current position of the sample stage?"
- **Explain procedures** — "How do I run a 2D grid scan?"
- **Understand errors** — "Why did my scan stop?"

The assistant is fully extensible. Beamline scientists can add custom skills and tools that give Claude domain-specific knowledge and capabilities.

---

## Plugin Architecture

LUCID's plugin system is designed for real extensibility, not just theming. Nine distinct plugin types cover the full spectrum of customization:

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

Get started with LUCID for your experiments:

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

Extend LUCID with custom functionality:

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

LUCID is built on a modern, maintainable stack:

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
