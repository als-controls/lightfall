# Bluesky RunEngine Queue Management Panel

## Overview

The `QueuePanel` provides a comprehensive interface for managing the Bluesky RunEngine queue with full CRUD operations, drag-and-drop reordering, and execution history tracking.

## Panel Structure

```
┌─────────────────────────────────────────────────────────┐
│  QueuePanel                                             │
├─────────────────────────────────────────────────────────┤
│  ┌─────────────────────────────────────────────────┐    │
│  │ ▶ RUNNING: count(det, num=10)                   │    │
│  │   Elapsed: 00:45  |  Point 7/10  |  Priority: 5 │    │
│  └─────────────────────────────────────────────────┘    │
├─────────────────────────────────────────────────────────┤
│  [ Queue ]  [ Recent ]                                  │
├─────────────────────────────────────────────────────────┤
│  (Queue tab: drag-reorderable pending plans)            │
│  (Recent tab: last 100 completed/failed/cancelled)      │
└─────────────────────────────────────────────────────────┘
```

## Features

### Queue Tab
- View all pending procedures with columns: Position, Plan, Parameters, Priority, Submitted
- Drag-and-drop reordering (automatically adjusts priority values)
- Double-click to edit a procedure
- Context menu: Edit, Duplicate, Remove
- Clear Queue button to remove all pending procedures

### Recent Tab
- View last 100 completed procedures (FIFO)
- Columns: Plan, Parameters, Status, Duration, Completed
- Status filter checkboxes: Completed, Failed, Cancelled
- Color-coded status (red for failed, yellow for cancelled)
- Context menu: Retry (failed only), Add to Queue

### Running Header
- Shows currently executing procedure
- Live elapsed time counter
- Progress bar when point count is known
- Priority display

## Implementation Files

| File | Purpose |
|------|---------|
| `lucid/acquire/engine/base.py` | Queue management methods in BaseEngine |
| `lucid/acquire/engine/bluesky.py` | BlueskyEngine queue integration |
| `lucid/ui/widgets/queue_view.py` | QueueModel, RecentModel, and views |
| `lucid/ui/widgets/plan_edit_dialog.py` | Dialog for editing queue items |
| `lucid/ui/panels/queue.py` | Main QueuePanel implementation |
| `lucid/ui/panels/plugins/queue_plugin.py` | Panel plugin registration |

## Engine API Additions

### New Signal
- `sigQueueChanged`: Emitted when queue items are added, removed, or reordered

### PrioritizedProcedure Fields
- `id`: Unique UUID for each procedure
- `submitted_at`: Timestamp when submitted
- `name`: Auto-detected or user-provided name

### New Methods
- `get_queue_items() -> list[PrioritizedProcedure]`: Get copy of queue
- `get_current_procedure() -> PrioritizedProcedure | None`: Get running procedure
- `get_procedure_by_id(id) -> PrioritizedProcedure | None`: Find by ID
- `remove_from_queue(id) -> bool`: Remove specific procedure
- `update_priority(id, priority) -> bool`: Change procedure priority

## MCP Tool Integration

The panel exposes actions for Claude via the introspection API:

```python
def get_introspection_data() -> dict:
    """Returns queue state, current procedure, pending procedures list."""

def action_clear_queue() -> int:
    """Remove all pending procedures."""

def action_remove_from_queue(procedure_id: str) -> bool:
    """Remove a specific procedure."""

def action_update_priority(procedure_id: str, new_priority: int) -> bool:
    """Change procedure priority."""
```

## Future Enhancements

### Queue Server Support
This design is local-queue-first. To add `bluesky-queueserver` support:
1. Create `QueueServerBackend` implementing same interface
2. Add backend selector in preferences
3. `QueueModel` connects to whichever backend is active

### Full Parameter Editing
Currently only priority can be changed after queuing. Full parameter editing would require:
1. Storing the plan function reference separately
2. Recreating the generator with new parameters
3. Replacing the old queue item with the new one

### Retry Implementation
Retry for failed plans would require:
1. Storing the plan function reference and original parameters
2. Recreating the generator on retry
3. Submitting to the queue with original or modified parameters
