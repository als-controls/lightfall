# Lightfall

```{image} _static/logo.png
:alt: Lightfall Logo
:width: 200px
:align: center
```

**Lightfall is a control environment for your beamline.** It puts live device
control, Bluesky plan execution, data browsing, an electronic logbook, and an
embedded AI agent in one application — one window, one login, for the things a
beamline shift actually consists of.

> 🖼️ **Image placeholder** — *Screenshot: main Lightfall window with device control, plan execution, live visualization, and logbook panels docked*

## What is it?

- **Device control** — live panels for motors, detectors, and signals, built on
  Ophyd and EPICS Channel Access
- **Plan execution** — run Bluesky scans interactively, with live plotting;
  pause, resume, or abort mid-run
- **Data browsing** — search and review collected runs through a Tiled catalog,
  filtered by date, plan type, or exit status
- **Electronic logbook** — entries are created automatically as runs start,
  finish, or fail, and as devices are actuated; manual notes and viewport
  screenshots attach to the same record
- **An embedded AI agent** — a Claude-based assistant that can operate the
  instrument (move motors, run plans, inspect data and panels) and **build new
  UI panels and scan plans when you describe them in plain language**

The last item is the unusual one. The agent doesn't just answer questions about
the software — it modifies it. Ask for "a panel with the sample stage readbacks
and a jog button for theta" and it writes the panel, loads it into your running
session, and commits the code to your beamline's git repository.

> 🖼️ **Image placeholder** — *Screenshot: the agent chat mid-conversation, with a freshly built custom panel appearing in the workspace*

## Why you might care

**Customization happens during your shift, not after a developer queue.**
Beamline software requests usually mean filing a ticket and waiting. Here the
agent makes the change while you watch, and every change is a git commit —
reviewable, revertible, and persistent across sessions. Throwaway one-experiment
panels are cheap; the good ones graduate into your beamline's plugin repository.

**Adaptive experiments instead of exhaustive rasters.** Lightfall connects to
[Tsuchinoko](https://github.com/lbl-camera/tsuchinoko) over its message bus, and
the agent walks you through designing a gpCAM-driven adaptive scan — the
Gaussian-process engine decides where to measure next, concentrating beamtime
where the data is interesting. Live visualization shows the surrogate model and
the measured points as the experiment runs.

> 🖼️ **Image placeholder** — *Screenshot: adaptive-scan visualization showing the gpCAM surrogate heatmap with measured points overlaid*

**One login for control and data.** The same sign-on that authorizes motor moves
authorizes data access. Runs you acquire are cataloged to Tiled automatically,
tagged with your identity, and access is enforced per run — no second account,
no shared group password.

**The experiment documents itself.** Run starts, completions, and errors land in
the logbook without anyone remembering to write them down; your annotations and
screenshots interleave with the automatic record.

> 🖼️ **Image placeholder** — *Screenshot: logbook panel showing automatic run entries interleaved with a manual annotation and an attached screenshot*

## Try it — no hardware required

```bash
pip install lightfall
lightfall
```

Requires Python 3.11 or newer. On first launch, click **Continue as Guest** to
look around, or sign in with the built-in demo account to run scans: click
**Use local account instead**, then username `user`, password `user` (guest
access is read-only). A fresh install starts with a set of **simulated
devices** — motors and point detectors with Gaussian responses, plus
temperature and pressure signals — so you can open the device panels, run real
Bluesky scans, and explore the logbook without touching a beamline.

Three things to know about the out-of-the-box experience:

- **The agent needs Claude credentials.** Set the `ANTHROPIC_API_KEY`
  environment variable, or authenticate a Claude subscription with the Claude
  Code CLI (`claude auth login`). Everything else works without it.
- **Plots and the Data Browser read from a Tiled catalog.** A throwaway local
  server is two commands — [Your First Session](user-guide/first-session.md)
  walks through it.
- **Facility services are optional.** A production Tiled server, Keycloak
  single sign-on, the logbook server, and real EPICS devices are deployment
  steps, not prerequisites — the [Developer Guide](developer-guide/index.md)
  covers wiring them up.

## Status

Lightfall is in testing at the **COSMIC-Scattering** beamline at the Advanced
Light Source, where it has run since early 2026. The software is alpha: APIs are
still settling, and you should expect rough edges. It coexists with existing
control systems — both can address the same EPICS process variables — so trying
it does not mean replacing anything.

Lightfall is open source under the BSD-3-Clause license, developed by the ALS
Controls Team at Lawrence Berkeley National Laboratory.

## Where next

- **[User Guide](user-guide/index.md)** — [getting started](user-guide/getting-started.md),
  [your first session](user-guide/first-session.md),
  [running plans](user-guide/running-plans.md),
  [the Claude assistant](user-guide/claude-assistant.md), and
  [the logbook](user-guide/logbook.md)
- **[Developer Guide](developer-guide/index.md)** — architecture, the plugin
  system, messaging, and deployment
- **[API Reference](api/index.md)** — formal reference for the programmatic
  surface

```{toctree}
:maxdepth: 2
:hidden:

user-guide/index
developer-guide/index
api/index
```
