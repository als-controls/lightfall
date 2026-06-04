# Getting Started

Lightfall is a modern control system for scientific data acquisition and hardware controls. This guide introduces the main concepts and helps you get started with your first experiment.

## Overview

Lightfall provides a unified interface for:

- **Data Acquisition**: Run scans and measurements using Bluesky plans
- **Device Control**: Monitor and control beamline hardware
- **Experiment Documentation**: Automatic logbook entries for your experiments
- **AI Assistance**: Natural language control and help via Claude assistant

## Application Layout

When you launch Lightfall, you'll see a window with several key areas:

```
┌─────────────────────────────────────────────────────────────────┐
│  Menu Bar                                                       │
├─────────────────────────────────────────────────────────────────┤
│  Toolbar (RunEngine controls, quick actions)                    │
├─────────────────────────────────────────────────────────────────┤
│                                                                 │
│   ┌─────────────────────┐    ┌────────────────────────────┐     │
│   │                     │    │                            │     │
│   │  Left Panels        │    │  Right Panels              │     │
│   │  (Claude, Bluesky,  │    │  (Logbook)                 │     │
│   │   Devices - tabbed) │    │                            │     │
│   │                     │    │                            │     │
│   └─────────────────────┘    └────────────────────────────┘     │
│                                                                 │
├─────────────────────────────────────────────────────────────────┤
│  Status Bar (user info, session time, connection status)        │
└─────────────────────────────────────────────────────────────────┘
```

### Main Panels

Lightfall uses a dockable panel system. The default layout includes:

| Panel | Purpose |
|-------|---------|
| **Bluesky** | Select and run data acquisition plans |
| **Devices** | Browse and control hardware devices |
| **Logbook** | View experiment documentation |
| **Claude** | AI assistant for help and automation |

Panels can be rearranged by dragging their title bars, and additional panels are available from the View menu.

## Quick Start Workflow

A typical experiment workflow in Lightfall:

1. **Log In**: Authenticate with your credentials (see [Logging In](login.md))
2. **Check Devices**: Verify your hardware is connected in the Devices panel
3. **Select a Plan**: Choose a data acquisition plan in the Bluesky panel
4. **Configure Parameters**: Set detectors, motors, and scan ranges
5. **Run the Plan**: Click Run to start data acquisition
6. **Review Results**: Check the Logbook for automatic documentation

## Keyboard Shortcuts

| Shortcut | Action |
|----------|--------|
| `Ctrl+,` | Open Preferences |
| `Ctrl+Q` | Quit application |
| `F1` | Context help |

## Next Steps

- [Logging In](login.md) - Set up your authentication
- [Running Plans](running-plans.md) - Learn how to execute data acquisition
- [Using Panels](panels.md) - Explore all available panels
