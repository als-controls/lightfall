# Customizing Lightfall with the Agent

The Claude assistant doesn't just operate Lightfall — it can extend it. Ask
for a panel that doesn't exist and the agent writes it, loads it into your
running session, and commits the code to git. No restart, no developer
queue. This page walks through the workflow and explains what happens behind
each step.

Prerequisite: a working assistant (see
[Claude Assistant](claude-assistant.md) for credentials and approval
basics).

## A worked example

Open the **Claude Assistant** panel and describe what you want. Be concrete
about the devices and the behavior:

> Build me a panel that shows the current positions of motor, motor1, and
> motor2, with a button next to each that moves it back to zero.

What follows:

### 1. The agent drafts the panel

The agent writes a complete Python file: a panel class with the requested
widgets, wired to the device catalog for live readbacks and to the motors
for the move buttons.

### 2. You approve the creation

Before anything is written to disk, an approval prompt appears for the
`lightfall_create_user_plugin` tool. The prompt shows the plugin **name**,
a **description**, and the **full source code** the agent wants to install —
this is your chance to read what it built before it runs. Click **✓ Allow**
to proceed (or **✗ Deny** with feedback, and the agent revises).

> 🖼️ **Image placeholder** — *Screenshot: approval prompt for lightfall_create_user_plugin showing the generated panel source code*

### 3. The plugin is validated, written, loaded, and committed

On approval, Lightfall:

1. **Validates** the code — syntax check, a test execution in an isolated
   namespace, and a check that it actually registers a panel. Invalid code is
   rejected back to the agent with the error, and the agent fixes it.
2. **Writes** the file to `~/lightfall/plugins/<name>.py`.
3. **Loads** it immediately — the panel registers with the panel registry, a
   sidebar icon appears, and the panel shows up under **View → Panels →
   User**.
4. **Commits** the file to the git repository in `~/lightfall/`, with the
   agent's description as the commit message, authored as *Lightfall Agent*.

> 🖼️ **Image placeholder** — *Screenshot: the freshly built motor panel docked in the workspace, with its new icon visible in the sidebar*

### 4. Iterate

Just keep talking:

> Add motor3 to the panel, and show each position with two decimal places.

The agent rewrites the file (same tool, `overwrite=true` — you approve
again), the plugin reloads in place, and a new commit lands. Each round trip
is a few seconds.

## Where everything lives

| What | Where |
|------|-------|
| Agent-built panels and skills | `~/lightfall/plugins/*.py` |
| User plans (yours or agent-written) | `~/lightfall/plans/*.py` |
| Version history of all of it | git repository at `~/lightfall/` |

The git history is the safety net and the institutional memory. Every
creation, edit, and deletion — by the agent *or* by you editing files
externally — becomes a commit. `git log` in `~/lightfall/` shows the whole
story; `git revert` undoes a bad change. Useful one-experiment panels are
cheap to make and discard; the good ones can be promoted into your
beamline's plugin repository.

You can edit the files yourself, too: Lightfall watches the plugins folder,
and saving a change hot-reloads the plugin and commits it (message:
`external edit: <file>`).

## Managing user plugins

The agent manages plugins with four tools (all subject to approval):

| Tool | What it does |
|------|--------------|
| `lightfall_create_user_plugin` | Validate, write to `~/lightfall/plugins/`, load, and commit |
| `lightfall_list_user_plugins` | List loaded user plugins, their registrations, and any load errors |
| `lightfall_reload_plugin` | Unload and reload a plugin from disk after external edits |
| `lightfall_unload_plugin` | Unregister a plugin without deleting its file |

So "what plugins do I have?", "reload my motor panel", and "unload that
broken panel" all work as chat requests.

For quick experiments there is also `lightfall_create_temp_plugin`: the
plugin loads immediately but is written to a temporary directory and
disappears when the application exits — useful for trying an idea before
committing to it.

```{note}
Reloading or unloading a plugin while one of its panels is open can be
unstable — close the panel first if you can. Lightfall shows a warning the
first time a hot-reload happens in a session.
```

## What the agent can author today

Three kinds of artifacts are agent-authorable:

- **Panels** — dock panels built from Qt widgets, registered into the
  sidebar and the View menu (the main use case, shown above).
- **Plans** — Bluesky user plans in `~/lightfall/plans/`, created via the
  separate `lightfall_create_user_plan` tool and runnable from the Bluesky
  panel like any other plan (see [Running Plans](running-plans.md)).
- **Agent skills** — extensions to the assistant itself: a plugin file can
  define new prompt knowledge and tools, which register with the agent the
  same way panels register with the UI.

Other extension points (themes, settings pages, acquisition engines, status
bar widgets) exist in the plugin system but are developer territory — see
the [Developer Guide](../developer-guide/index.md).

## Practical notes

- **Panels persist across restarts.** Files in `~/lightfall/plugins/` are
  loaded at every startup; broken ones are skipped with the error recorded
  (ask the agent to list plugins to see it).
- **Validation is real but not a sandbox.** The plugin code runs with the
  same privileges as Lightfall itself. The approval prompt showing the full
  source is the control point — read it, especially on a machine connected
  to real hardware.
- **Be specific about devices.** Naming the exact devices ("motor1", "det")
  saves a round trip where the agent goes looking for them.
