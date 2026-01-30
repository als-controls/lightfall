# LUCID Documentation

LUCID (Lightsource Unified Control Interface Dashboard) is a modern control system for scientific data acquisition and hardware controls at the ALS (Advanced Light Source) facility.

## Getting Started

- [Plugin System Overview](plugins/index.md) - Extend LUCID with custom functionality
- [Plugin Quickstart](plugins/quickstart.md) - Create your first plugin in 5 minutes

## Developer Guides

```{toctree}
:maxdepth: 2
:caption: Contents

plugins/index
```

## Key Features

- **Modular Plugin Architecture**: Extend functionality through 9 plugin types
- **Qt-based UI**: Modern PySide6 interface with theming support
- **Bluesky Integration**: Native support for Bluesky data acquisition plans
- **LLM Integration**: Claude assistant with MCP tools for natural language control

## Plugin Types at a Glance

| Plugin Type | Purpose |
|-------------|---------|
| `settings` | Add preferences pages to the Settings dialog |
| `panel` | Add new panels to the main application |
| `plan` | Register Bluesky plans for data acquisition |
| `engine` | Provide execution backends |
| `theme` | Define application color themes |
| `statusbar` | Add status indicators to the status bar |
| `controller` | Provide device-specific control widgets |
| `mcp_tool` | Add tools for the Claude assistant |
| `skill` | Add domain expertise to the Claude assistant |

See the [Plugin System Overview](plugins/index.md) for details on each type.
