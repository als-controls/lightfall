# Scoped Content: Where Plans, Plugins, Agents and Skills Come From

Plans, plugins, agents and skills reach Lightfall from several places. Most of
the time you never need to think about it, but when two items share a name
Lightfall has to pick one, and it tells you when it does. This page explains the
sources, the precedence rules, and the warnings.

## Where content comes from

From least to most specific:

| Source | What it is |
|--------|------------|
| **core** | Built into Lightfall itself. |
| **package** | Installed Python packages (`lightfall-*` plugins) that register plans, plugins, agents or skills. |
| **facility** | Content published for the whole facility. |
| **beamline** | Content published for your beamline, e.g. `beamline:7.0.1`. |
| **branch** | Content published for your branch, e.g. `branch:7.0.1.1`. |
| **local** | Your own files in `~/lightfall/` (`plans/`, `plugins/`, `agents/`, `skills/`). |

Facility, beamline and branch content is *published* centrally and *synced* to
the machine in the background. Once synced it sits in a local store and is
read-only: you can't edit it in place, and it is never committed to your
`~/lightfall` repository.

```{note}
Publishing content to these scopes comes in a later release. Until then the
synced sources are empty and you will only see core, package and local content.
```

## Precedence

**Plans and plugins are code, so core and package always win.** If a facility,
beamline, branch or local plan or plugin has the same name as one from core or a
package, the scoped or local one is **not loaded**. Among the non-package
sources the more specific one wins: local over branch over beamline over
facility, and the less specific ones are not loaded.

**Agents and skills are text, so the most specific wins** (local, then branch,
beamline, facility, package, core). Nothing is refused: a more specific agent or
skill simply shadows the others, and the editor shows what it shadows.

What counts as "the same name":

- **Plans:** the file name without `.py`.
- **Plugins:** the file name or folder name, and, for clashes with packages, the
  tool name or panel id the plugin registers. If a package registers a tool or
  panel that one of your plugins already owns, the whole plugin is unloaded.
- **Agents:** the `name` in the agent's front matter.
- **Skills:** the skill's folder name.

## Folder plugins

A plugin can be a single `.py` file or a folder with an `__init__.py`. Folder
plugins load as a real package, so relative imports between their files work, and
editing any file in the folder reloads the plugin. Names starting with an
underscore are skipped. Having both `x.py` and a folder `x/` is an error and
neither loads. Folder plugins cannot install pip dependencies.

## When changes take effect

- Published **plans and plugins** take effect at the **next start** of Lightfall.
  A retracted or updated one stays as loaded until then.
- Published **agents and skills** update **live** when a sync completes.
- Your own `~/lightfall` files hot-reload as before.
- **Guests** and consoles that are logged out use the machine's last synced copy.

## The warnings

- **"Content not loaded"** (a dialog): a plan or plugin was skipped because
  something with higher precedence already has that name. Each line names the
  item, where it came from, and who kept the name, for example *plan xafs_scan
  from branch:7.0.1.1 was not loaded: package has one with the same name*. For a
  plugin that lost a tool or panel the line names the plugin and the key, for
  example *plugin my_tools (tool fit_tool) from local was not loaded: core has one
  with the same name*. To fix it, rename the skipped item (the dialog ends with
  that reminder). Long lists show the first 20 lines and a count of the rest.
- **"Content shadowed"** (a toast): an agent or skill is replacing a less
  specific one with the same name, for example *agent helper from
  branch:7.0.1.1 shadows the one from package*. This is informational.

Each clash is reported once per run of Lightfall, and no warning appears when
there are none. Warnings found while Lightfall starts wait until you have signed
in and the main window has finished loading its plugins, then appear together.

In the Agents & Skills editor, an agent or skill that shadows others is listed as
*name (shadows …)*, naming the sources it replaces. When an agent asks to load a
synced skill, the approval prompt says where it was published, for example
*published to branch 7.0.1.1*.
