# Cross-Panel Patterns

Recipes for panels that reach beyond their own widgets — driving the Claude
Assistant, opening sibling panels, or reacting to application-wide events.
These complement the single-panel API in
[`panel_design.md`](panel_design.md); read that first.

## Dispatching a prompt to Claude

When a panel wants a button that kicks off an AI-mediated procedure (run a
skill, summarize state, draft a logbook note), it dispatches a prompt to the
singleton Claude Assistant panel rather than talking to the model directly.

**Recommended:** drop in a `SkillTriggerButton`. It encapsulates the whole
get-or-open / busy-guard / send flow, shows status, and emits `dispatched`
when the prompt goes through.

```python
from lightfall.ui.widgets.skill_trigger_button import SkillTriggerButton

btn = SkillTriggerButton(
    skill_name="Beam Alignment",
    prompt="Run the beam alignment skill: align the beam on the sample.",
)
self._layout.addWidget(btn)
```

For the manual pattern and the full agent-signal contract
(`message_received`, `query_completed`, `is_busy`), and for guidance on how to
phrase the trigger prompt, see **triggering-the-claude-assistant** in
[`panel_design.md`](panel_design.md#triggering-the-claude-assistant).

## Opening another panel from a button

There are two distinct operations; pick the one that matches your intent.

- **Get a reference to a singleton panel** (to call an `action_*` method or
  read its state) with `PanelRegistry.get_instance().create(panel_id)`. This
  gets-or-creates the instance; it's what the Claude bridge above uses. It does
  not guarantee the panel is docked or visible.
- **Show the panel to the user** (dock it and add its sidebar button) with the
  main window's `add_panel(panel_id)`. It instantiates a deferred panel if
  needed and is a no-op if the panel is already open. From inside a docked
  panel, `self.window()` returns the `NCSMainWindow`.

```python
from lightfall.ui.panels.registry import PanelRegistry

def _on_open_devices(self) -> None:
    # Just need to call a method on the Device panel? Get-or-create it:
    panel = PanelRegistry.get_instance().create("lightfall.panels.device")
    if panel is not None:
        panel.action_refresh()

    # Want the user to actually see it? Route through the main window so it
    # gets docked with a sidebar button (no-op if already open).
    window = self.window()
    if hasattr(window, "add_panel"):
        window.add_panel("lightfall.panels.device")
```

## Reacting to a global event (device connect, plan complete)

Application-wide singletons expose Qt signals you can subscribe to. Because
they're Qt signals, emissions from worker threads are marshalled to your
slot's (GUI) thread automatically — no manual thread hop needed.

`DeviceCatalog` announces device lifecycle; the RunEngine announces
acquisition lifecycle:

```python
from lightfall.devices import DeviceCatalog
from lightfall.acquire import get_run_engine

def _wire_global_events(self) -> None:
    catalog = DeviceCatalog.get_instance()
    catalog.device_connected.connect(self._on_device_connected)  # emits device_id

    engine = get_run_engine()
    engine.sigFinish.connect(self._on_plan_complete)        # plan finished
    engine.sigStateChanged.connect(self._on_engine_state)   # emits state str

@Slot(str)
def _on_device_connected(self, device_id: str) -> None:
    logger.info("Device {} connected — refreshing", device_id)
    self._refresh()

@Slot()
def _on_plan_complete(self) -> None:
    self._status_label.setText("Plan complete")
```

Disconnect in `_on_closing()` (or hold the connection and let the singleton
outlive the panel) so a closed panel's slot isn't invoked against a dead
C++ wrapper.
