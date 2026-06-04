# Lightfall

<!-- badges:start -->
[![CI](https://github.com/als-controls/lightfall/actions/workflows/ci.yml/badge.svg)](https://github.com/als-controls/lightfall/actions/workflows/ci.yml)
[![Docs](https://github.com/als-controls/lightfall/actions/workflows/docs.yml/badge.svg)](https://als-controls.github.io/lightfall/)
[![Python](https://img.shields.io/badge/python-3.11%20%7C%203.12-blue.svg)](https://github.com/als-controls/lightfall/blob/master/pyproject.toml)
[![License: BSD-3-Clause](https://img.shields.io/badge/license-BSD--3--Clause-blue.svg)](LICENSE.md)
[![DOI](https://zenodo.org/badge/1259553556.svg)](https://zenodo.org/badge/latestdoi/1259553556)
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

### Development Installation

```bash
# Clone the repository
git clone https://github.com/als-computing/ncs.git
cd ncs

# Create and activate virtual environment
python -m venv .venv
.venv\Scripts\activate  # Windows
# or
source .venv/bin/activate  # Unix

# Install in development mode with dev dependencies
pip install -e ".[dev]"
```

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
ncs/
├── src/ncs/
│   ├── acquire/      # Data acquisition engine
│   ├── auth/         # Authentication & authorization
│   ├── config/       # Configuration management
│   ├── core/         # Core application classes
│   ├── devices/      # Device catalog & backends
│   ├── plugins/      # Plugin system
│   ├── services/     # Application services (Tiled, etc.)
│   ├── ui/           # User interface
│   │   ├── panels/   # Panel implementations
│   │   ├── widgets/  # Reusable widgets
│   │   ├── preferences/  # Settings UI
│   │   └── theme/    # Theming system
│   └── utils/        # Utilities
└── tests/            # Test suite
```

