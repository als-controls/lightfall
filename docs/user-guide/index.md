# User Guide

This guide is for beamline scientists running experiments with Lightfall. It
covers everything you do through the application window: logging in, moving
devices, running plans, watching data come in, keeping records in the logbook,
and putting the embedded Claude assistant to work — including having it build
new panels for you.

No Python or Bluesky experience is required for most of it. Where a page does
involve writing code (user plans, for example), it says so up front.

## Suggested reading order

1. **[Getting Started](getting-started.md)** — install Lightfall, launch it,
   and learn the layout of the main window.
2. **[Logging In](login.md)** — authentication options, user roles, and what
   guest access can and cannot do.
3. **[Your First Session](first-session.md)** — a hands-on walkthrough with
   simulated devices: move a motor, run a scan, watch the plot, find the run
   afterwards. Start here if you learn by doing.
4. **[Using Panels](panels.md)** — a reference for every panel and how the
   docking system works.
5. **[Running Plans](running-plans.md)** — plan selection, parameters,
   pause/resume/abort, and writing your own user plans.
6. **[Claude Assistant](claude-assistant.md)** — the embedded agent: asking
   questions, controlling the application in plain language, and approving
   tool use.
7. **[Customizing Lightfall with the Agent](agent-customization.md)** — have
   the assistant build or modify a panel for you, with every change committed
   to git.
8. **[Using the Logbook](logbook.md)** — automatic run and device records,
   manual notes, images, and offline-first sync.
9. **[Preferences](preferences.md)** — every settings page, from themes to
   device backends.

```{toctree}
:maxdepth: 2

getting-started
login
first-session
panels
running-plans
claude-assistant
agent-customization
logbook
preferences
```
