# Getting Started

Lightfall is a control environment for synchrotron beamlines: device control,
Bluesky plan execution, data browsing, an electronic logbook, and an embedded
Claude assistant in a single desktop application. This page gets it installed
and running, and introduces the layout of the main window.

A fresh install needs **no hardware and no facility services** — it starts with
a set of simulated devices, so everything in [Your First Session](first-session.md)
works on a laptop.

## Requirements

- Python 3.11 or newer
- Windows, macOS, or Linux

## Installation

```bash
pip install lightfall
```

Installing into a virtual environment is recommended:

```bash
python -m venv .venv
.venv\Scripts\activate      # Windows
source .venv/bin/activate   # macOS / Linux
pip install lightfall
```

### Development install

```bash
git clone https://github.com/als-controls/lightfall.git
cd lightfall
python -m venv .venv
.venv\Scripts\activate      # Windows
source .venv/bin/activate   # macOS / Linux
pip install -e ".[dev]"
```

## Launching

```bash
lightfall
```

On first launch you are greeted by the login dialog. Sign in, or click
**Continue as Guest** to look around with read-only access — see
[Logging In](login.md) for the full set of options.

> 🖼️ **Image placeholder** — *Screenshot: the login dialog with the Lightfall logo, "Login with Keycloak" and "Continue as Guest" buttons*

## What works out of the box

- **Simulated devices.** The Mock device backend is enabled by default and
  provides four motors (`motor`, `motor1`, `motor2`, `motor3`), point
  detectors with Gaussian responses (`det`, `det1`, `det2`, `noisy_det`), and
  simulated sensors (`temperature`, `pressure`, `ring_current`). Real scans
  run against them.
- **Plan execution.** The Bluesky RunEngine and the built-in plan library work
  immediately.
- **The logbook.** Entries are stored in a local SQLite database
  (offline-first); a logbook server is only needed for sync across machines.

Two things require configuration:

- **The Claude assistant** needs credentials: set the `ANTHROPIC_API_KEY`
  environment variable, or use an existing Claude Code OAuth login. See
  [Claude Assistant](claude-assistant.md).
- **The Data Browser and Visualization panels** read runs from a
  [Tiled](https://blueskyproject.io/tiled/) data server, configured under
  **File → Settings → Tiled Data Catalog**. Without one, scans still run —
  there is just no catalog to browse. For evaluation, a throwaway local
  server is two commands; [Your First Session](first-session.md) walks
  through it.

Connecting to real hardware (EPICS/BCS devices, Keycloak sign-on, a shared
logbook server) is a deployment task; see the
[Developer Guide](../developer-guide/index.md).

## Application layout

```text
┌──────────────────────────────────────────────────────────────────┐
│  Menu Bar   (File · View · User · Help)                          │
├──────────────────────────────────────────────────────────────────┤
│  Toolbar    (RunEngine state + pause/stop/abort, profile avatar) │
├───┬──────────────────────────────────────────────────────────────┤
│ ▣ │                                                              │
│ ▣ │  ← upper icons: left-docked panels                           │
│ ▣ │              Logbook (center, always open)                   │
│   │              + any panels you open from the sidebar          │
│ ▣ │  ← lower icons: bottom-docked panels                         │
│ ▣ │                                                              │
├───┴──────────────────────────────────────────────────────────────┤
│  Status Bar (connection, auth, beam status, plugin widgets)      │
└──────────────────────────────────────────────────────────────────┘
```

> 🖼️ **Image placeholder** — *Screenshot: main window with the icon sidebar on the left, the Logbook in the center, and the status bar annotated*

The key elements:

- **Icon sidebar** (left edge): a vertical icon strip, one icon per panel,
  in the style of VS Code. Click an icon to toggle its panel; drag icons to
  reorder. The strip has two sections: icons in the **upper section** open
  panels docked on the left (Bluesky, Devices, Data Browser, …); icons in
  the **lower section** open panels docked along the bottom (Claude
  Assistant, Visualization, Logging, …).
- **Logbook** sits in the center and cannot be closed — it is the running
  record of your session.
- **RunEngine control** in the toolbar shows the engine state and provides
  pause/resume, stop, and abort buttons whenever a plan is active.
- **Status bar** shows connection state, authentication info, and beam/source
  status; plugins can add their own widgets here.
- **Profile avatar** (top-right) shows the signed-in user.

Panels can also be opened from **View → Panels**, grouped by category, and
rearranged by dragging their title bars — see [Using Panels](panels.md). Your
layout is saved automatically between sessions.

## The built-in tour

**Help → Welcome Tutorial** starts an interactive overlay tour that walks
through the status bar, the sidebar, and each major panel, ending with an
example prompt in the Claude Assistant. It is the fastest way to learn where
things are.

> 🖼️ **Image placeholder** — *Screenshot: the welcome tutorial overlay spotlighting the icon sidebar with its callout box*

## Keyboard shortcuts

| Shortcut | Action |
|----------|--------|
| `Ctrl+,` | Open Settings |
| `Ctrl+Q` | Quit application |

## Next steps

- [Logging In](login.md) — authentication options and roles
- [Your First Session](first-session.md) — the hands-on walkthrough
- [Running Plans](running-plans.md) — data acquisition in depth
