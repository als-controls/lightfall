# Lightfall

<!-- badges:start -->
[![PyPI version](https://img.shields.io/pypi/v/lightfall.svg)](https://pypi.org/project/lightfall/)
[![Python versions](https://img.shields.io/pypi/pyversions/lightfall.svg)](https://pypi.org/project/lightfall/)
[![CI](https://github.com/als-controls/lightfall/actions/workflows/ci.yml/badge.svg)](https://github.com/als-controls/lightfall/actions/workflows/ci.yml)
[![Docs](https://github.com/als-controls/lightfall/actions/workflows/docs.yml/badge.svg)](https://als-controls.github.io/lightfall/)
[![License: BSD-3-Clause](https://img.shields.io/badge/license-BSD--3--Clause-blue.svg)](LICENSE.md)
<!-- badges:end -->

A modern, unified control system for synchrotron lightsource facilities that provides facility-wide consistency with beamline-specific customization.

## Overview

Lightfall is designed for the Advanced Light Source (ALS) facility, providing:

- **Unified Interface**: Consistent look-and-feel across beamlines with skinnable themes
- **API-First Architecture**: Modular, extensible design enabling automation and integration
- **LLM/AI Integration**: Claude-powered chatbot for natural language control and assistance
- **FAIR-Compliant Data Management**: Integration with Tiled for data cataloging and access
- **Secure Remote Operations**: Full remote operation capability with fine-grained access control

## Features

### User Interface
- Progressive disclosure: user panels with expert panels behind authorization
- Scripting panel with Jupyter-lab style interface
- LLM panel for controlling panels via natural language
- GUI Builder for drag-and-drop interface creation
- Persistent user preferences saved across sessions

### Device Management
- Centralized device catalog with lifecycle tracking
- Real-time monitoring via EPICS Channel Access
- Version-controlled device configurations
- Rich high-level controls integrated into workflows

### Data Acquisition
- Bluesky-based acquisition engine
- Flexible signal/stream selection with real-time visualization
- Interactive, pausable, restartable acquisition
- Automatic data persistence to Tiled catalog

### Data Browser
- Browse and search data stored in Tiled server
- Filter by date range, plan type, exit status
- Pagination for large datasets
- Click/double-click signals for integration with analysis tools

## Installation

```bash
pip install lightfall
```

### Development Installation

```bash
# Clone the repository
git clone https://github.com/als-controls/lightfall.git
cd lightfall

# Create and activate virtual environment
python -m venv .venv
.venv\Scripts\activate  # Windows
# or
source .venv/bin/activate  # Unix

# Install in development mode with dev dependencies
pip install -e ".[dev]"
```

Lightfall depends on [`lightfall-utils`](https://git.als.lbl.gov/ncs/lightfall-utils) via a `uv` path source (see `[tool.uv.sources]` in `pyproject.toml`), so development requires a sibling checkout at `../lightfall-utils` and `uv`:

```bash
git clone https://github.com/als-controls/lightfall.git
git clone <lightfall-utils-repo-url> ../lightfall-utils   # sibling checkout
cd lightfall
uv sync --extra dev
```

Plain `pip install -e ".[dev]"` won't resolve `lightfall-utils` until it's published to an index (pending LBNL software disclosure) — use `uv` for local development in the meantime.

### Running the Application

```bash
lightfall
```

## Configuration

Lightfall uses a layered configuration system:

1. **System defaults** - Built-in defaults
2. **Site configuration** - Facility-wide settings
3. **User preferences** - Personal customizations

Configuration files are stored in:
- Windows: `%APPDATA%\lightfall\`
- Linux/Mac: `~/.config/lightfall/`

## Architecture

Lightfall is built on:

- **PySide6** - Qt for Python GUI framework
- **Bluesky** - Data acquisition framework
- **Ophyd** - Device abstraction layer
- **Tiled** - Data catalog and access
- **EPICS** - Control system communication

### Plugin System

Lightfall supports plugins for:
- **Panels** - Custom UI panels
- **Settings** - Preference pages
- **Status Bar** - Status indicators
- **Engines** - Acquisition backends
- **Plans** - Scan procedures

## Development

### Running Tests

```bash
pytest
```

### Code Quality

```bash
# Linting
ruff check src tests

# Type checking
mypy src
```

## Project Structure

```
lightfall/
├── src/lightfall/
│   ├── acquire/        # Data acquisition engine (Bluesky RunEngine, plans)
│   ├── auth/           # Authentication & authorization
│   ├── claude/         # Embedded Claude agent integration
│   ├── config/         # Configuration management
│   ├── core/           # Core application classes
│   ├── devices/        # Device catalog & backends
│   ├── epics/          # EPICS (caproto) widgets & helpers
│   ├── exporter/       # Data exporter CLI
│   ├── ipc/            # Inter-process communication (NATS)
│   ├── logbook/        # Logbook client
│   ├── pipelines/      # Pipeline client
│   ├── plugins/        # Plugin system
│   ├── services/       # Application services (Tiled, CA tunnel, etc.)
│   ├── settings/       # User-portable settings client
│   ├── ui/             # User interface
│   │   ├── panels/     # Panel implementations
│   │   ├── widgets/    # Reusable widgets
│   │   ├── preferences/  # Settings UI
│   │   └── theme/      # Theming system
│   ├── utils/          # Utilities
│   └── visualization/  # Plotting & live visualization
└── tests/              # Test suite
```

