# Visualization

The visualization widget contract in
`lightfall.visualization.base_visualization`. Visualizations are widgets
that read from a Tiled `BlueskyRun` entry; the Visualization panel scores
the available widget classes against an incoming run and instantiates the
best match.

```python
from lightfall.visualization.base_visualization import BaseVisualization
```

## BaseVisualization

`BaseVisualization` extends `QWidget` and defines a seven-method abstract
contract. The controller (`VisualizationPanel`) orchestrates the selection
flow in this order:

1. `can_handle(run)` — score every candidate class
2. `set_run(run)` — bind the winning instance to the run
3. `get_streams()` — populate the stream combo
4. `set_stream(name)` — display (auto-picks the best field)
5. `get_fields()` — populate the field combo
6. `set_field(name)` — user override of the field
7. `refresh()` — polled on a timer for live runs

Subclasses must also define class-level metadata:

| Attribute | Description |
|-----------|-------------|
| `viz_name` | Unique identifier (e.g. `"image_stack"`). |
| `viz_display_name` | UI label (e.g. `"Image Stack"`). |
| `viz_icon` | Icon name (default `"chart-line"`). |

### The contract

| Method | Contract |
|--------|----------|
| `can_handle(run) -> int` | **Static method.** Score 0–100 for how well this visualization handles the given run. The panel picks the highest scorer. |
| `set_run(run)` | Bind the Tiled `BlueskyRun` entry: cache the reference and read start metadata. |
| `get_streams() -> list[str]` | Stream names, sorted by this visualization's preference. |
| `set_stream(stream_name)` | Select a stream: read its metadata, auto-pick the best field, render. |
| `get_fields() -> list[str]` | Field names for the current stream, sorted by preference. |
| `set_field(field_name)` | Switch to a different field within the current stream. |
| `refresh()` | Poll for new data during a live run. Must be a no-op for completed runs. |

`__init__` stores `self._run`, `self._stream_name`, and `self._field_name`
(empty until set); call `super().__init__(parent)` from subclasses.

> 🖼️ **Image placeholder** — *Screenshot: the Visualization panel during a live scan, with the stream and field combo boxes and an active plot widget.*

## Built-in visualizations

The Visualization panel currently selects from a fixed set of built-in
widget classes (`lightfall.visualization.widgets`): image stack, 1-D plot,
heatmap, scatter, table, and the adaptive-experiment heatmap and plot.

## Plugin visualizations: current status

Infrastructure for plugin-provided visualizations exists but is not yet
wired end to end on this branch:

- `VisualizationRegistry`
  (`lightfall.visualization.registry.VisualizationRegistry`) is a
  thread-safe singleton with `register_visualization()`,
  `get_visualization()`, `get_all_visualizations()`, and introspection.
- `PluginLoader` contains a registration branch for a `"visualization"`
  plugin type that registers instances with this registry.

However, there is no `VisualizationPlugin` base class, the
`"visualization"` type is not registered during application startup (so
manifest entries of that type are skipped), and `VisualizationPanel`
selects only from the built-in widget list — it does not consult
`VisualizationRegistry`. Custom visualizations therefore cannot yet be
added by a plugin; subclassing `BaseVisualization` is the contract that
panel wiring will build on.

## Class reference

```{eval-rst}
.. autoclass:: lightfall.visualization.base_visualization.BaseVisualization
   :members:
   :show-inheritance:
   :member-order: bysource
```

```{eval-rst}
.. autoclass:: lightfall.visualization.registry.VisualizationRegistry
   :members:
   :show-inheritance:
```
