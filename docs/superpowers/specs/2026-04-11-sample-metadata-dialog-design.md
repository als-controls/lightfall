# Sample Metadata Dialog

## Summary

When a plan is submitted to the RunEngine, a dialog prompts the user for a sample name and optional arbitrary metadata. The dialog validates against duplicate sample names in Tiled and allows the user to force submission if desired. The mechanism is engine-level middleware (pre-submit hooks) so any caller gets the dialog unless they explicitly opt out.

## Decisions

- **Sample name key**: Flat `sample_name` in the start document (not nested `sample.name`). Flat structures parse and filter cleaner when translating to other data models.
- **Duplicate check**: Targeted Tiled query on accept (not prefetch-all). Too many names to fetch upfront.
- **Dialog scope**: All plans from BlueskyPanel show the dialog. Programmatic callers (dark collection, etc.) pass `skip_pre_submit=True` to bypass.
- **Persistence**: Full QSettings persistence of the ScalableGroup state (field definitions + values) across sessions, like Xi-CAM. Sample name value is not persisted (forces intentional entry each time).
- **Force behavior**: No metadata marker when forcing a duplicate name. The force button is a nudge to describe things better, not a data concern.
- **Reserved fields**: `uid`, `time`, `scan_id`, `plan_name`, `plan_type`, `plan_args`, `plan_pattern_args`, `plan_pattern_module`, `num_points`, `num_intervals`, `hints`, `detectors`, `motors`, `sample_name`.
- **User/ESAF association**: Out of scope, future work.

## Component 1: Pre-submit hook system in BaseEngine

`BaseEngine` gains a pre-submit callable registry. These callables run before a plan enters the queue, on the calling thread (main/UI thread for interactive submissions).

### API

```python
class BaseEngine(QObject):
    def register_pre_submit(self, callable_: Callable[[str, dict], dict | None]) -> None:
        """Register a callable invoked before each plan submission.

        The callable receives (plan_name, kwargs) and returns:
        - dict: updated kwargs (merged into submission)
        - None: cancel the submission

        Callables run on the calling thread in registration order.
        """

    def unregister_pre_submit(self, callable_: Callable[[str, dict], dict | None]) -> None:
        """Remove a pre-submit callable."""
```

### Submit flow

```
submit(procedure, priority=1, name="", skip_pre_submit=False, **kwargs)
  |
  +-- skip_pre_submit=True --> straight to queue
  |
  +-- skip_pre_submit=False --> iterate _pre_submit_callables
       |
       each callable: (plan_name, kwargs) -> dict | None
       |
       +-- returns dict --> merge into kwargs, continue to next callable
       +-- returns None --> submission cancelled, submit() returns None
```

- `submit()` return type: `str | None` (procedure ID or None if cancelled)
- `__call__` gains `skip_pre_submit` kwarg, passed through to `submit()`
- Pre-submit callables stored as `list[Callable]` (ordered, not a set) to preserve registration order

### Relationship to existing kwargs_callables

- Pre-submit callables: run in `submit()` on the calling thread, before queuing. Can show UI, can cancel.
- kwargs_callables: run in `_execute_plan()` on the background thread, just before `RE(plan, **kwargs)`. System-injected metadata only.

Both contribute to the final start document with no conflict.

## Component 2: SampleMetadataDialog

New file: `lucid/ui/dialogs/sample_metadata_dialog.py`

### Layout

```
+-- Sample Metadata --------------------------+
|                                             |
|  Sample Name: [___________________________] |
|  (!) "my_sample" already exists in Tiled    |
|                                             |
|  +-- Additional Metadata -----------------+ |
|  |  temperature    | 25.0                 | |
|  |  polarization   | "LCP"               | |
|  |  [+ Add: str v]                        | |
|  +----------------------------------------+ |
|                                             |
|                       [Cancel] [Run/Force]  |
+---------------------------------------------+
```

### Behavior

- **Sample name**: QLineEdit, required (non-empty). Not persisted across sessions.
- **Additional metadata**: pyqtgraph `ScalableGroup` with add/remove typed fields (str, float, int). Fields are renamable and removable. Persisted via QSettings.
- **Duplicate check**: On accept, queries Tiled via `client.search(Key("start.sample_name") == name)`. If results non-empty, shows inline warning and changes "Run" button to "Force". Clicking Force submits without re-checking.
- **Tiled not connected**: Duplicate check skipped. "Run" button stays as "Run". Not an error.
- **Reserved name validation**: On accept, checks arbitrary field names against reserved set. Shows error if collision found.
- **Return**: `get_metadata() -> dict` returns `{"sample_name": "...", ...arbitrary fields...}`.

### Persistence

Uses `QSettings` key `lucid.dialogs.sample_metadata.v1` to save/restore the ScalableGroup state (field definitions and values). Restored on dialog construction.

### Subclasses LucidDialog

Inherits from `lucid.ui.dialogs.base.LucidDialog` for consistent window icon behavior.

## Component 3: Integration & wiring

### Registration

A pre-submit callable is registered on the BlueskyEngine during app startup. The callable:

1. Instantiates `SampleMetadataDialog` with reserved field names
2. Calls `dialog.exec()` (blocks on main thread)
3. Returns `dialog.get_metadata()` if accepted, `None` if rejected

Registration happens in `BlueskyPanel._auto_configure` (or equivalent setup path) after the engine is available — the same place that already connects engine signals. This keeps the UI-aware callable registration in the UI layer, not in the engine itself.

### BlueskyPanel

No changes to `_on_run_requested`. The engine's `submit()` handles the dialog before queuing.

### Programmatic callers

Pass `skip_pre_submit=True`:
```python
engine.submit(plan, skip_pre_submit=True, sample_name="dark")
```

### Tiled browser compatibility

The existing `_entry_to_record` in `tiled_browser_panel.py` already reads `sample_name` as a flat key (line 714 fallback). No changes needed for display.

## Files to create/modify

| File | Action | Description |
|------|--------|-------------|
| `lucid/acquire/engine/base.py` | Modify | Add pre-submit callable registry and `skip_pre_submit` to submit/__call__ |
| `lucid/ui/dialogs/sample_metadata_dialog.py` | Create | SampleMetadataDialog with ScalableGroup, duplicate check, force button |
| `lucid/ui/dialogs/__init__.py` | Modify | Export SampleMetadataDialog |
| App startup / engine wiring | Modify | Register the sample metadata pre-submit callable on the engine |
