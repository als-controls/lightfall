# Using Panels

Lightfall's interface is built from dockable panels. Panels can be opened and
closed independently, rearranged, stacked into tabs, or floated as separate
windows. This page explains the docking system and describes each built-in
panel.

## Managing panels

### Opening panels

- **Click a sidebar icon.** Each panel has an icon in the vertical strip on
  the left edge of the window; click to toggle the panel, hover for its name.
  Icons in the strip's upper section open panels docked on the left; icons in
  the lower section open panels docked along the bottom. Icons can be dragged
  to reorder, including between sections.
- **View → Panels.** All panels available to your role, grouped by category.

Panels you lack permission for (based on your [role](login.md)) do not appear
in either place.

> 🖼️ **Image placeholder** — *Screenshot: the icon sidebar with a tooltip visible, next to the View → Panels menu open*

### Rearranging panels

- **Move**: drag a panel's title bar to a new dock position
- **Tab**: drop a panel onto another to stack them as tabs
- **Float**: drag a panel out of the main window
- **Resize**: drag the borders between panels

Your layout (dock positions, open panels, window geometry) is saved
automatically when you quit and restored on the next launch.

## Panel reference

| Panel | Category | Default location | Notes |
|-------|----------|------------------|-------|
| Logbook | Core | Center | Always open, not closable |
| Entries | Core | Left | Logbook entry list |
| Bluesky | Acquisition | Left | Plan selection and execution |
| Devices | Core | Left | Device tree and controllers |
| Data Browser | Data | Left | Tiled catalog search |
| Documents | Acquisition | Left | Raw Bluesky document stream |
| Claude Assistant | Tools | Bottom | Embedded agent chat |
| IPython Console | Tools | Bottom | Interactive Python REPL |
| Visualization | Acquisition | Bottom | Plots of run data |
| Logging | System | Bottom | Application log viewer |
| Synoptic | Core | Bottom | 2D beamline layout view |
| Queue | Acquisition | Bottom | RunEngine queue and history |
| Threads | Developer | Bottom | Background thread monitor |
| Data Movement | Monitoring | — | Shussebora transfer daemon status |
| Pipeline Jobs / Pipeline Triggers | Acquisition | Bottom / Left | Data pipeline monitoring |

Plugins can add further panels; agent-built user panels appear under the
**User** category (see
[Customizing Lightfall with the Agent](agent-customization.md)).

---

### Bluesky

**Purpose:** select, configure, and run data acquisition plans.

The **Plans** tab lists every registered plan with search and category
filtering. Selecting a plan opens a **Config** tab where each parameter gets
an appropriate input — device dropdowns for motors and detectors, numeric
fields with units and limits — plus **Run**, **Reset**, and **Edit** buttons.
Title-bar buttons create a **New Plan**, **Refresh** user plans from disk,
and open the user plans folder.

See [Running Plans](running-plans.md) for the full workflow.

---

### Devices

**Purpose:** browse and control hardware.

Two permanent tabs:

- **Favorites** — devices you have starred (right-click a device in the tree
  to toggle). Favorites persist per user.
- **All** — the full device tree from every enabled backend, with search and
  kind filtering.

Double-clicking a device opens a **controller tab** with live readbacks and
the appropriate control widget — for a motor: setpoint entry with **Go**,
relative-motion tweak buttons, **STOP**, and status flags. Devices without a
matching controller show a read-only message.

---

### Logbook and Entries

**Purpose:** the running record of your experiment.

The **Logbook** panel (center, always open) shows the current entry as a
sequence of fragments — your Markdown notes, images, and automatic records of
plans and device changes. The **Entries** panel is the sidebar list for
switching between entries, creating new ones (**＋ New Entry**), and sorting.

See [Using the Logbook](logbook.md).

---

### Claude Assistant

**Purpose:** the embedded agent — help, natural-language control, and
building new panels and plans.

Chat interface with a **Send** button and a broom button to reset the
conversation. Requires Claude credentials (see
[Claude Assistant](claude-assistant.md)).

---

### Data Browser

**Purpose:** find runs in the Tiled data catalog.

Filter by date range, plan name, and exit status; results are paginated.
Double-click a run to open it in the Visualization panel. Right-click for
*Copy UUID*, *Copy Scan ID*, *Show Visualization*, *Show Documents*, *Run
pipeline...*, and *Export*.

**Requires** a Tiled server configured in **File → Settings → Tiled Data
Catalog**; the status line at the bottom of the panel shows the connection
state.

---

### Visualization

**Purpose:** plot run data, live or post-hoc.

Given a run (from a Data Browser double-click, or from the assistant),
the panel picks the best matching visualization — 1D line plot, heatmap,
image stack, scatter, or table — and provides stream/field/type selectors,
an optional fitting side panel, and data export. Runs that are still
acquiring refresh automatically every two seconds.

---

### Documents

**Purpose:** inspect the raw Bluesky document stream (start, descriptor,
event, stop) during acquisition. Useful for debugging metadata and data-flow
issues.

---

### IPython Console

**Purpose:** an interactive Python REPL inside the application, with
application objects pre-loaded for scripting and debugging.

---

### Logging

**Purpose:** live application log with level filtering (DEBUG through ERROR)
and search. The first place to look when something misbehaves.

---

### Synoptic

**Purpose:** a 2D schematic of the beamline hardware layout. Selecting a
device in the Devices panel highlights it here.

---

### Queue

**Purpose:** view and manage the RunEngine's pending plan queue and recent
execution history.

---

### Threads

**Purpose:** monitor background threads and async tasks. A developer tool for
diagnosing hangs.
