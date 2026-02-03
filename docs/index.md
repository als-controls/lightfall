# LUCID Documentation

```{image} _static/logo.png
:alt: LUCID Logo
:width: 200px
:align: center
```

LUCID (Lightsource Unified Control Interface Dashboard) is a modern control system for scientific data acquisition and hardware controls at the ALS (Advanced Light Source) facility.

## User Guide

Learn how to use LUCID for your experiments and data acquisition.

- [Getting Started](user/getting-started.md) - Overview and first steps
- [Logging In](user/login.md) - Authentication and access
- [Running Plans](user/running-plans.md) - Execute data acquisition plans
- [Using Panels](user/panels.md) - Work with the main application panels
- [Claude Assistant](user/claude-assistant.md) - AI-powered help and control
- [Preferences](user/preferences.md) - Customize your experience

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

Extend LUCID with custom functionality through the plugin system.

- [Plugin System Overview](plugins/index.md) - Architecture and concepts
- [Plugin Quickstart](plugins/quickstart.md) - Create your first plugin
- [Creating Plugins](plugins/creating-plugins.md) - Step-by-step guide
- [Plugin Types](plugins/plugin-types/index.md) - All 9 plugin types

```{toctree}
:maxdepth: 2
:caption: Developer Guide
:hidden:

plugins/index
```

## API Reference

Auto-generated documentation from source code.

- [Plugin Types](api/plugins.md) - Base classes and infrastructure

```{toctree}
:maxdepth: 2
:caption: API Reference
:hidden:

api/index
```

## Key Features

- **Modern Qt Interface**: PySide6-based UI with theming and dockable panels
- **Bluesky Integration**: Native support for data acquisition plans
- **LLM Integration**: Claude assistant with MCP tools for natural language control
- **Role-Based Access**: Fine-grained permissions with Keycloak authentication
- **Extensible Architecture**: Plugin system for custom functionality
