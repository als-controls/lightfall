# Using the Logbook

The logbook is the running record of your session, and most of it writes
itself: every plan you run and every device you move lands in the current
entry automatically, interleaved with whatever notes and images you add. It
is also **offline-first** — everything is stored locally and synced to a
logbook server in the background when one is configured.

The **Logbook** panel sits in the center of the window and cannot be closed.
The **Entries** panel (left sidebar) lists your entries for navigating
between them.

> 🖼️ **Image placeholder** — *Screenshot: Logbook panel showing an entry with a mix of automatic plan/device fragments and a manual note, Entries list visible at left*

## Entries and fragments

An **entry** is a titled, taggable document — think "today's shift" or "the
alignment campaign". Each entry is a sequence of **fragments**, of three
kinds:

- **Text** — your notes, written in Markdown. Double-click a text fragment
  to edit it in place; click away to render it.
- **Readonly** — automatic records injected by the system (described below).
  These are protected: they cannot be edited, so the experimental record
  stays trustworthy.
- **Image** — screenshots and pictures.

Hovering over any fragment shows a small button bar in its corner: **edit**
(text fragments), **copy content**, **ask Claude about this** (sends the
fragment to the assistant — handy for "why did this scan fail?"), and
**delete**.

### Working with entries

- **＋ New Entry** in the Entries panel creates a fresh entry and selects it.
- Click an entry in the list to switch to it; the sort dropdown reorders the
  list.
- The title is editable inline at the top of the entry view ("Untitled
  entry" until you name it); the **+ tag** button adds tag chips.
- **＋ Add note** at the top of the entry view appends a text fragment.

The *currently selected* entry is where automatic fragments go — switching
entries redirects the live record.

## Automatic fragments

Lightfall listens to the acquisition system and the device layer and writes
the record for you:

- **Runs** (`bluesky_plan` fragments, blue accent): when a plan starts, a
  fragment appears with the plan name, its parameters, and the short run ID.
  When the run finishes, *the same fragment* is updated with the exit status
  (`success`, `abort`, `fail`) and event counts — one fragment per run, with
  its complete story.
- **Device changes** (`device_change` fragments, orange accent): actuations
  made through device controller widgets are recorded live as
  `device: old_value → new_value`. (Motor moves *inside* a plan are part of
  the run's own record rather than separate fragments.)
- **Assistant responses** (`claude_response` fragments, purple accent): when
  you use a fragment's **Ask Claude about this** button, the assistant's
  answer is written back into the entry as its own fragment.

> 🖼️ **Image placeholder** — *Screenshot: close-up of a plan fragment showing "Plan: scan_1d (a1b2c3d4) — success" above an orange device-change fragment*

## Images

Two ways to attach images to the current entry:

- **Paste** — press `Ctrl+V` while the entry view has focus and the
  clipboard contains an image (a screenshot, typically).
- **The image button** in the entry toolbar opens a file picker (PNG, JPEG,
  GIF).

Images are stored locally first and uploaded to the logbook server during
sync, like everything else.

## Offline-first sync

All logbook writes go to a local SQLite database first
(`~/.lightfall/logbook.db`), so the logbook works with no network and no
server — notes during a beamline network outage are not lost.

When a logbook server is configured (**File → Settings → Logbook**), a
background sync runs a couple of seconds after each change (and shortly
after startup): pending entries, fragments, and images are pushed, and
remote changes are pulled. Sync behavior worth knowing:

- **A warning banner** appears on the panel when the server is unreachable;
  it clears on the next successful sync. You can keep working — everything
  queues locally.
- **Guest sessions never sync.** Guest notes stay on the machine.
- **The local database is a buffer, not an archive.** Already-synced content
  is purged locally on logout and exit; the server holds the permanent
  record.
- An **offline-only** setting in the Logbook preferences disables sync
  entirely for air-gapped or standalone use.

## Tips

- Let the automatic record do the bookkeeping and spend your typing on
  *interpretation* — why you changed the slit width, what the peak shape
  suggests — interleaved at the moment it happened.
- Paste a screenshot of the Visualization panel next to the run fragment it
  belongs to; image + run record together make results reproducible.
- Use the fragment **Claude** button on a failed run's fragment to start a
  troubleshooting conversation with the context already attached.
