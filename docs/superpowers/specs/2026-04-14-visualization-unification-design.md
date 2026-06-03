# Visualization Unification: Tiled-Only Data Path

## Problem

Lightfall's visualization system has three code paths for feeding data to widgets:

1. **Eager live** — Engine → MultiStreamBuffer → `_on_new_point()` per event
2. **Eager historical** — `_load_historical_data()` reads buffer on widget init
3. **Lazy tiled** — `set_array_source()` / `set_data()` with ArrayClient

Path 3 is the real path. Paths 1 and 2 are dead weight from before the tiled integration. The TiledWriter already writes live scan data to tiled in real-time, so even live runs are accessible via tiled. The buffer layer is redundant.

Additionally, the current architecture splits responsibilities awkwardly:
- `VisualizationPlugin` handles scoring (`can_handle`) and widget creation
- `BaseVisualizationWidget` handles buffer wiring and abstract `_on_new_point`
- `DocumentProcessor` extracts `DataCharacteristics` from Bluesky documents
- `VisualizationPanel` orchestrates setup via `open_tiled_run` with 13 parameters
- `TiledBrowserPanel._setup_visualization` does the actual tiled data extraction

## Design

### New Base Class: `BaseVisualization`

Replace `BaseVisualizationWidget` + `VisualizationPlugin` with a single ABC. Each visualization widget IS its own plugin — no separate plugin/widget split.

```python
class BaseVisualization(QWidget, ABC):

    @staticmethod
    @abstractmethod
    def can_handle(run) -> int:
        """Score 0-100 for how well this viz handles the given run.

        Has access to:
        - run.metadata["start"] — plan_name, motors, hints, shape
        - run[stream].metadata["data_keys"] — field names, shapes, dtypes
        - run[stream].metadata["hints"] — hinted fields

        Scoring guidelines:
            0:     Cannot handle
            1-39:  Fallback only
            40-59: Adequate
            60-79: Good match
            80-100: Optimal
        """

    @abstractmethod
    def set_run(self, run) -> None:
        """Set the BlueskyRun tiled entry.

        Cache run reference and read start metadata.
        Does not display anything yet — waits for set_stream.
        """

    @abstractmethod
    def get_streams(self) -> list[str]:
        """Return stream names sorted by this viz's preference.

        Example: ImageStack returns ["primary", "dark", ...]
        putting the stream most likely to have images first.
        """

    @abstractmethod
    def set_stream(self, stream_name: str) -> None:
        """Select which stream to display.

        Reads stream metadata (data_keys, hints), auto-selects
        the best field via get_fields()[0], and renders current data.
        """

    @abstractmethod
    def get_fields(self) -> list[str]:
        """Return field names for current stream, sorted by preference.

        Example: ImageStack returns 2D array fields first.
        Example: Plot1D returns hinted scalar fields first.
        """

    @abstractmethod
    def set_field(self, field_name: str) -> None:
        """Switch which field to display within the current stream."""

    @abstractmethod
    def refresh(self) -> None:
        """Poll for new data. Called on a timer (~2s) for live runs.

        The widget checks if frame count / row count has grown
        and fetches new data as needed. No-op for completed runs.
        """
```

Seven methods. No buffer, no DocumentProcessor, no VisualizationSpec, no DataCharacteristics.

### Controller: `VisualizationPanel`

Simplified orchestration:

```
User double-clicks run in Data Browser
    → VisualizationPanel.open_run(entry)
        1. Score all registered widgets via can_handle(entry)
        2. Create winning widget
        3. widget.set_run(entry)
        4. Populate stream combo from widget.get_streams(), select first
        5. widget.set_stream(streams[0])
        6. Populate field combo from widget.get_fields(), select first
        7. If run is live (no stop doc), start refresh timer

User changes stream combo → widget.set_stream(name)
    → Repopulate field combo from widget.get_fields()

User changes field combo → widget.set_field(name)

Refresh timer fires → widget.refresh()
```

The panel's `open_run` method takes a single argument: the tiled BlueskyRun entry. No more 13-parameter `open_tiled_run`.

### Widget Registration

Keep the existing `VisualizationRegistry` but register `BaseVisualization` subclasses directly instead of separate plugin objects. Each subclass provides class-level metadata:

```python
class ImageStackVisualization(BaseVisualization):
    viz_name = "image_stack"
    viz_display_name = "Image Stack"
    viz_icon = "images"
```

### What Each Widget Does Internally

**ImageStackVisualization:**
- `set_stream`: Read `data_keys`, find 2D+ array fields. Use `LazyImageView` with `stream[field]` as ArrayClient. Fetch only the displayed frame.
- `get_fields`: Return fields sorted by: hinted 2D arrays first, then other 2D arrays, then everything else.
- `refresh`: Check `stream[field].shape[0]` for new frames. Update timeline, optionally jump to latest.

**Plot1DVisualization:**
- `set_stream`: Read all scalar data eagerly (small). Plot hinted fields vs first motor.
- `get_fields`: Return hinted scalar fields first, then all scalars.
- `refresh`: Re-read scalar arrays, append new points.

**HeatmapVisualization:**
- `set_stream`: Read scalar data. Build 2D grid from start doc shape/extents.
- `refresh`: Re-read, fill grid with new points.

**TableVisualization:**
- `set_stream`: Read all fields as table. Display in QTableView.
- `refresh`: Re-read, append new rows.

**ScatterVisualization:**
- `set_stream`: Read X, Y, optional Z scalar fields.
- `refresh`: Re-read, add new points.

**VolumeVisualization:**
- `set_stream`: Like ImageStack but with 3D navigation.
- `refresh`: Check for new slices.

### `_setup_visualization` Moves to VisualizationPanel

The `TiledBrowserPanel._setup_visualization` static method (which extracts metadata and resolves ArrayClients) is no longer needed. The widget itself reads from the tiled entry directly in `set_stream`. The browser panel just calls `viz_panel.open_run(entry)`.

### Live Run Detection

A run is live if `entry.metadata.get("stop") is None`. The panel starts a ~2s QTimer that calls `widget.refresh()`. When the stop document appears (checked during refresh via `entry.metadata`), the timer stops.

Note: `entry.metadata` must be re-fetched to detect the stop document. Tiled caches metadata aggressively, so the widget or panel should call something like `entry.refresh()` or re-fetch the entry from the client periodically.

## Code to Remove

### Delete entirely:
| File | What |
|------|------|
| `acquire/buffer.py` | `MultiStreamBuffer` class (only used for viz) |
| `visualization/processor.py` | `DocumentProcessor` (replaced by direct metadata reads) |
| `visualization/base.py` | `BaseVisualizationWidget` (replaced by `BaseVisualization`) |
| `plugins/visualization_plugin.py` | `VisualizationPlugin` (merged into `BaseVisualization`) |

### Remove from `visualization/spec.py`:
- `VisualizationSpec` dataclass — widgets configure themselves from metadata
- `DataCharacteristics` dataclass — `can_handle` reads the run directly

Keep `FieldType` and `FieldInfo` if widgets find them useful as internal helpers, but they're no longer part of the public interface.

### Remove from each widget (`image_sequence.py`, `plot.py`, `volume.py`, `heatmap.py`, `scatter.py`, `table.py`):
- `_on_new_point()` method
- `_load_historical_data()` method (image_sequence only)
- `_update_image_stack()` method (image_sequence only)
- `self._images` in-memory list (image_sequence only)
- `set_data()` bulk-load method (all scalar widgets)
- `set_array_source()` method (image_sequence only)
- Constructor parameters: `spec`, `buffer`
- All `VisualizationPlugin` subclasses at bottom of each file

### Remove from `visualization_panel.py`:
- `set_engine()` and buffer wiring
- `_on_document()` callback
- `DocumentProcessor` usage
- `open_tiled_run()` 13-parameter method (replaced by `open_run(entry)`)
- `_setup_visualization` call chain from `TiledBrowserPanel`
- `_on_characteristics_ready` / `_select_visualization` (replaced by `can_handle` loop)

### Remove from `tiled_browser_panel.py`:
- `_setup_visualization` static method
- `_on_visualization_ready` callback
- Background thread for viz setup (widget reads tiled directly now)

### Simplify in `tiled_browser_panel.py`:
- Double-click handler just calls `viz_panel.open_run(entry)` directly

### Remove from `acquire/__init__.py`:
- `MultiStreamBuffer` export

## Migration Strategy

1. Create `BaseVisualization` ABC in `visualization/base.py` (replaces old content)
2. Port `ImageStackVisualization` first (most complex, validates the interface)
3. Port remaining widgets
4. Update `VisualizationPanel` controller
5. Simplify `TiledBrowserPanel` double-click to just pass the entry
6. Delete dead code: buffer, processor, old plugin interfaces, spec types
7. Update widget registration in `builtin_manifest.py`

## Not in Scope

- Changing the tiled server or bluesky_tiled_plugins (upstream)
- Changing how TiledWriter writes data
- Adding new visualization types
- Theater mode / fit panel changes (they wire to the widget, not the data path)
