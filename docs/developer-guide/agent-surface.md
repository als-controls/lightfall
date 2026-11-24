# Agent UI Surface

`lightfall.ui.surface` lets an agent see and operate Lightfall's real windows by role and
name, the way a screen reader does. The assistant uses it through three tools on its `qt`
tool server, and it is the foundation for natural-language user stories and agent
evaluations (the Stories work).

| Tool | What it does |
|---|---|
| `ui_snapshot` | An outline of what is on screen: roles, names, values, states, with `[refs]`. Panels other than the focused one are collapsed to a count; `scope=<panel, dialog or ref>` shows one part in full. |
| `ui_act` | Real mouse and keyboard input (`QTest`): `click`, `double_click`, `right_click`, `check`, `uncheck`, `fill`, `select`, `press`, `scroll_to`, `open_panel`, `close`. Returns what changed. |
| `ui_wait` | Waits for `idle`, an `element` to appear or disappear, a `toast`, or `run_finished`. A timeout is `met: false`, not an error. |
| `screenshot` | Unchanged, plus an optional `scope` to capture one panel or dialog. |

`ui_snapshot` and `ui_wait` are auto-approved; `ui_act` goes through the normal approval
flow. The operator assistant (`lightfall`) is told only about the read tools;
`lightfall-dev` documents `ui_act`.

## What a snapshot looks like

```text
window "Lightfall" [w1]
  dock "Plans" [p1]
    list "Plans" [e3] 30 items, showing 1-12
      item "grid_scan" [e5] selected
    field "Start" [e16] = "0.00"
    field "Points" [e17] = "11"
    button "Run" [e18]
    button "Stop" [e19] disabled
  dock "Devices" [p2] collapsed, 3 controls; ui_snapshot scope=p2 to expand
dialog "Confirm plan" [d1] modal
  button "Submit" [e20] default
toast "Saved" [t1] = "entry 12"
```

- Lists, tables and trees are read from their models (12 rows at a time), never from
  cell widgets.
- Only the current page of a tab widget is shown; the other tabs are listed.
- Refs are valid until the next snapshot. Numbers are never reused, so an old ref fails
  with "stale" instead of naming a different widget.
- Targets are strict: two widgets with the same role and name in scope is an error that
  lists both, and the agent narrows with `within`.

## How a widget gets its name

| Widget | Name, in order |
|---|---|
| Buttons, toggles, check boxes | visible text, `accessibleName`, tooltip |
| Windows, dialogs, docks | window title, `accessibleName` |
| Group boxes | title |
| Labels | text |
| Fields, combos, lists, sliders, everything else | `accessibleName`, the `QFormLayout` row label, a buddy label (`QLabel.setBuddy`), placeholder text, tooltip |

`objectName` is never a name. The snapshot shows it only as a hint on unnamed widgets.

## Conventions for new UI

1. **Icon-only buttons get `setAccessibleName`.** A tooltip is a fallback, not a name.
2. **Fields sit in a `QFormLayout`, or have a buddy label.** `label.setBuddy(field)` also
   gives the label's mnemonic to the field.
3. **Docks, dialogs and group boxes have titles.**
4. **Views that list things get an accessible name** ("Plans", "Devices"), since a view
   has no visible text of its own.
5. **Custom-painted widgets implement `AgentDescribable`:**

   ```python
   def agent_role(self) -> str:
       return "plot"

   def agent_describe(self) -> dict:
       # Small, JSON-able, and what a person would read off the widget.
       return {"opaque": True, "series": [...], "x_label": "sample_lift"}
   ```

   `"opaque": True` stops the snapshot from walking the widget's internals.
   `BaseVisualization` already does this for every plot and image visualization.

## Auditing

```bash
python -m lightfall.ui.surface audit [--panel NAME ...] [--skip NAME ...] [--out report.md]
```

This builds every registered panel offscreen (built-in panels and installed plugins) and
prints a Markdown table of:

- **unnamed** interactive widgets;
- **ambiguous** same-name pairs in one panel;
- **undescribed** custom-painted widgets.

Each row names the Lightfall class to fix. The panels run in a child process, because
panels built outside the app can crash during teardown after the report is complete. The
IPython console is skipped by default.

## Threading

- The tool handlers run on the agent's loop and move blocking work to a thread.
- `act()` and `wait()` must run **off** the GUI thread. They marshal each step with
  `run_on_main_thread`.
- `act()` posts its input with a zero-delay timer and polls for the UI to settle. So a
  click whose slot opens `QDialog.exec()` returns with the dialog listed under `opened`,
  and the agent can operate that dialog next, instead of hanging.
- `act_sync()` is the GUI-thread variant for tests and tools that know no modal loop will
  start.
