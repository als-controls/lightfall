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

Publishing is described under "Publishing shared content" below.

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

## Publishing shared content

Open **Panels → Shared content**. The table lists every plan, plugin, agent and
skill Lightfall knows about, with its **Source** (Mine, Built-in, Package,
Facility, Beamline 7.0.1, Branch 7.0.1.1, and so on), its **Revision** for
published items, and a note when it shadows another item. Use the type filter
and the search box to narrow the list. Select a row to enable the buttons:

| Button | Enabled when |
|--------|--------------|
| **Publish…** | The row is yours (source *Mine*) and you are a maintainer or above on at least one scope. |
| **History…** | The row is published content (facility, beamline or branch), a retracted item (see "Retracted items" below), or it is yours (*Mine*) and scoped settings are on. |
| **Retract** | The row is published, not already retracted, and you are a maintainer or above on its scope. |
| **Copy to mine** | The row is published content, or a built-in or package agent or skill. |
| **Open folder** | The item has a file or folder on disk. |

A disabled button explains why in its tooltip, for example *You are not a
maintainer of Branch 7.0.1.1*.

### Roles

Your role is set per scope. **Viewers and operators** can see published content
and copy it. **Maintainers, admins and owners** can publish, retract and restore
on that scope. Publishing never targets your personal settings. The server checks
your role too, so a stale screen can't bypass it.

### Publish

1. Select one of your own items and press **Publish…**.
2. The dialog shows what will be sent: the file count, the size and the start of
   the checksum; hover over that line for the list of files. What you see is
   what is uploaded, even if you edit the item while the dialog is open. Close
   and reopen it to pick up later edits.
3. Choose the **scope**. Only scopes where you are a maintainer or above are
   offered.
4. Write a **message** describing the change. It is required and appears in the
   history.
5. If you are publishing someone else's work, fill in **author**.
6. Press **Publish**. While the upload runs the dialog can't be cancelled (it
   couldn't be undone halfway); you are told when it has been published, even if
   the dialog has closed by then.

Bundles leave out `__pycache__`, `*.pyc`, `.git`, `.DS_Store`, `Thumbs.db` and a
skill's `_drafts` folder. A plan is one `.py` file, an agent is one `.md` file,
and a folder plugin or skill is sent as its whole folder.

**Limits.** A bundle may not exceed a size, an unpacked size or a file count. The
dialog checks them before anything is uploaded and tells you which one is
exceeded. Admins and owners of the scope are exempt. If the server still
refuses, you see its reason in the dialog.

After a successful publish, other consoles receive the change on their next sync.
Plans and plugins load at their next start, while agents and skills refresh live
(see "When changes take effect" below).

### History, restore and retract

**History…** lists the revisions of a published item with who made each one and
the message. Select an older revision and press **Restore** to make it current
again; the revision itself is unchanged, it simply becomes the current one
again. **Retract** removes the item from the scope. Both need maintainer or above
and ask you to confirm. Nothing is lost: a retracted item can be restored from
its history.

Your own copy of an item wins over the published one, so after you publish from
*Mine* the table shows only your row (with a note such as *Replaces Beamline
7.0.1*). Select that row and press **History…**: Lightfall asks which shared
scope to look at (the one your copy replaces is preselected) and opens that
scope's history, where you can restore or retract as usual. This is also how you
bring back an item you retracted while you have your own copy of it: a retracted
item drops out of the table after the next sync, but your row stays. If the item was never published at the scope you picked,
the History window says so.

**Someone else got there first.** Restore and Retract tell the server which
revision the History window showed as current (or that it showed the item as
retracted). If someone published, restored or retracted the item after the
window read its history, the server refuses and the window says *Someone changed
it while you were deciding — look again.* Nothing was changed; close and reopen
History to see what changed before you retry.

### Retracted items

A retracted item no longer reaches anyone, so it drops out of the table, and on
a console that never had it there is no row to open its History from. Tick
**Show retracted** above the table and pick a shared scope of your chain
(most specific first) in the box beside it. The table then also lists the items
retracted at that scope: **Source** is the scope, **Revision** is the last
revision it had, and the note says *Retracted*. Select one and press
**History…** to see its revisions; a maintainer or above can select a revision
and press **Restore** to bring it back. Everyone at that scope receives it again
at their next sync, and it then shows as an ordinary published row. **Retract**
and **Copy to mine** are not offered on a retracted row.

The list is fetched from the logbook server when you tick the box, change the
scope, or the table refreshes. A server too old to list retracted items shows
*This server doesn't list retracted items.* below the table, and no rows are
added. If you have your own copy of the item, you can still reach its History
from your row, as described above.

### Copy to mine

**Copy to mine** copies a published item (or a built-in or package agent or
skill) into `~/lightfall/plans`, `plugins`, `agents` or `skills`, where you can
edit it. If you already have an item with that name, Lightfall asks before
replacing it, and answering **No** leaves yours untouched. Because a local item
wins over published ones, your copy takes over its name straight away.

### Using the assistant

The Claude assistant can do the same through its content tools: list content,
show history, and publish, retract or restore an item. Every publish, retract or
restore **always asks you first**, offering only Allow or Deny; there is no
"always allow". Answering by voice, only the whole word "Allow" allows (a
fragment such as "low" counts as a refusal). Every question names the scope and
the item. A publish question also gives the file count, the
size and the start of the checksum of the bundle that will be sent; a retract or
restore question gives the current revision (and, for restore, the revision that
becomes current). If you deny, close the question or let it time out, nothing is
sent and the assistant is told you declined. For a retract or restore, if the
item's current revision changed while you were deciding, the request is refused
and the assistant must ask again. The revision named in the question is also sent
to the server, which refuses the change if the item moved on in the last moment
(*Someone changed it while you were deciding — look again.*). Revisions made this way are marked as made by
the agent in the history.

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
