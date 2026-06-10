# Your First Session

This is a hands-on walkthrough of a complete session: launch Lightfall, log
in, look around, move a motor, run a scan, watch the data come in, and find
the run afterwards. Everything here uses the **simulated devices** that ship
enabled by default, so you can follow along on any machine —
no beamline, no hardware.

If you have not installed Lightfall yet, do
[Getting Started](getting-started.md) first.

## 1. Launch and log in

```bash
lightfall
```

The login dialog appears. For this walkthrough, sign in with a **local
development account** rather than as a guest — guests cannot run plans:

1. Click **Use local account instead** at the bottom of the dialog.
2. Enter username `user`, password `user`.
3. Click **Login**.

(The other built-in accounts — `operator`/`operator`, `staff`/`staff`,
`admin`/`admin` — work too. See [Logging In](login.md).)

> 🖼️ **Image placeholder** — *Screenshot: login dialog with the local account form open, username "user" filled in*

The main window opens with the **Logbook** in the center, an **icon
sidebar** along the left edge, the **RunEngine control** in the toolbar, and
a **status bar** at the bottom showing your user and connection state.

> 🖼️ **Image placeholder** — *Screenshot: main window immediately after login, with sidebar, logbook, toolbar, and status bar visible*

```{tip}
**Help → Welcome Tutorial** runs an interactive spotlight tour of all of
these elements. Worth two minutes if this is genuinely your first session.
```

## 2. Find your way around the sidebar

Each icon in the sidebar toggles one panel. Hover an icon for its name.
Icons in the upper section open panels docked on the left; icons in the
lower section open panels docked along the bottom. The ones this walkthrough
uses:

- **Bluesky** (upper section) — select and run plans
- **Devices** (upper section) — browse and control hardware
- **Data Browser** (upper section) — search collected runs in the Tiled
  catalog
- **Visualization** (lower section) — plot run data, live or after the fact

Click an icon once to open its panel, again to hide it. Panels can be dragged
by their title bars to rearrange, floated as separate windows, or stacked
into tabs — the layout is saved automatically when you quit.

## 3. Open the Devices panel and move a motor

Click the **Devices** icon in the left sidebar. The panel has two permanent
tabs: **Favorites** and **All**. Switch to **All** for the full device tree —
with the mock backend you will see simulated motors (`motor`, `motor1`,
`motor2`, `motor3`), detectors (`det`, `det1`, `det2`, `noisy_det`), and
sensors (`temperature`, `pressure`, `ring_current`). The search box filters
the tree by name.

> 🖼️ **Image placeholder** — *Screenshot: Devices panel, All tab, device tree showing the simulated motors and detectors*

**Double-click `motor`** in the tree. A controller opens in a new tab inside
the panel, showing:

- **Current** — the live position readback
- **Setpoint** — type a target position and click **Go** (or press Enter)
- **Relative Motion** — ◀ / ▶ tweak buttons that step by the size in the
  middle field
- **STOP** — halts the motor immediately
- Status flags (Done, limit switches, Home)

Type `5.0` into the Setpoint field and click **Go**. The Current readback
updates to 5.0 — the simulated motor moves instantly. Try the tweak buttons
to step it back down.

> 🖼️ **Image placeholder** — *Screenshot: motor controller tab with readback, setpoint + Go, tweak buttons, and STOP button*

Notice the Logbook: moving a device writes an automatic **device change**
fragment (`motor: 0.0 → 5.0`) into the current entry. The session is
documenting itself — more on this in [Using the Logbook](logbook.md).

## 4. Run a count

Click the **Bluesky** icon in the left sidebar. The **Plans** tab lists the
available plans with a search box and category filter.

Select **Count**. A configuration tab opens with the plan's parameters:

1. **detectors** — select `det`
2. **num** — number of readings, leave at `1` or set a few
3. **delay** — seconds between readings, leave at `0`

Click **Run**. A **Sample Metadata** dialog appears — you can enter a sample
name and extra metadata to record with the run, or just confirm it. The plan
is then submitted to the RunEngine; watch the **RunEngine control** in the
toolbar switch out of idle while it executes. A toast notification reports
success or failure when it finishes, and the Logbook gains a plan fragment
recording the run and its exit status.

> 🖼️ **Image placeholder** — *Screenshot: Bluesky panel with the Count plan configured and the Run button highlighted*

## 5. Run a scan

Back in the **Plans** tab, select **1D Scan**. Configure it:

1. **detectors** — `det` (the simulated detector with a Gaussian response
   centered at `motor = 0`)
2. **motor** — `motor`
3. **start** — `-5`
4. **stop** — `5`
5. **num** — `21`

Click **Run**. While the scan steps the motor across the Gaussian peak:

- The RunEngine control in the toolbar shows the running state and offers
  **Pause** — try it: the scan stops at the next safe checkpoint, the button
  becomes **Resume**, and **Stop**/**Abort** become available. Resume to let
  it finish.
- The Logbook's plan fragment updates with the exit status when the run
  completes.

> 🖼️ **Image placeholder** — *Screenshot: RunEngine control in the toolbar mid-scan, showing the running state with pause/stop/abort buttons*

```{note}
The remaining two steps — watching the plot and browsing past runs — read
data back from a **Tiled** catalog, configured under **File → Settings →
Tiled Data Catalog**. If no Tiled server is configured, the Data Browser
shows *Disconnected*; the scan itself still runs fine. Setting up a Tiled
server is covered in the [Developer Guide](../developer-guide/index.md).
```

## 6. Watch the data in the Visualization panel

Open the **Data Browser** from the sidebar and click the refresh button in
its title bar. Your runs appear in the table with their plan name, scan ID,
and exit status.

**Double-click the scan you just ran.** The **Visualization** panel opens
with the run plotted — for a 1D scan over the Gaussian detector you get a
line plot of `det` versus `motor` with a clean peak at zero. The toolbar lets
you switch streams, fields, and visualization types, and **Export** saves the
data out. If you double-click a run that is *still acquiring*, the plot
refreshes every couple of seconds as new points land.

> 🖼️ **Image placeholder** — *Screenshot: Visualization panel showing the Gaussian peak from the 1D scan of det vs motor*

## 7. Find the run again later

The Data Browser is also your way back to old data:

- **Filter** by date range, plan name, or exit status using the filter bar.
- **Right-click a run** for the context menu: *Copy UUID*, *Copy Scan ID*,
  *Show Visualization*, *Show Documents* (the raw Bluesky documents), and
  *Export*.

> 🖼️ **Image placeholder** — *Screenshot: Data Browser with the filter bar and a run's right-click context menu open*

## 8. Wrap up

Look at the Logbook one more time before you quit: the whole session — every
motor move, the count, the scan with its exit status — is already written
down, interleaved with any notes you typed. Click **＋ Add note** to add your
own commentary; press `Ctrl+V` with a screenshot in the clipboard to attach
an image.

Where to go from here:

- [Running Plans](running-plans.md) — every plan, plus writing your own
- [Claude Assistant](claude-assistant.md) — do all of the above by asking
  in plain language
- [Customizing Lightfall with the Agent](agent-customization.md) — have the
  assistant build you a custom panel
