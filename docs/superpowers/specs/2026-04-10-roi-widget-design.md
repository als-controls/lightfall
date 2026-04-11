# Interactive ROI Widget Design

## Overview

Replace the text-input ROI fields in the NXsas export dialog with an interactive PyQtGraph ImageView + RectROI widget. The widget loads a sample frame from the first selected run when the user selects NXsas export type.

## Goals

- Visual ROI selection on actual image data
- Contrast adjustment via ImageView's built-in histogram
- Load image lazily (only when NXsas selected)
- Clear error messaging when image data is unavailable

## Non-Goals

- ROI selection on multiple runs (first run's frame is the preview)
- Multi-ROI selection
- ROI presets or saved ROI configurations

## NXsas Parameter Widget

Replace `_create_nxsas_params` in `ExportDialog` with a new widget containing:

- **`pyqtgraph.ImageView`** (~400x400) — displays a sample frame with histogram/level controls
- **`pyqtgraph.RectROI`** — draggable/resizable rectangle overlay, initialized to full frame
- **Status label** — shows "Loading...", "Drag to select ROI", or error text
- **Read-only coordinate display** — shows current ROI as "X: 10, Y: 20, W: 50, H: 40", updated live as the user drags

The dialog grows vertically to accommodate the image view. `setMinimumWidth` increased from 500 to 600.

## Image Loading

Triggered when user selects NXsas from the export type dropdown. Does NOT load on dialog open or for NoOp exports.

Runs in a `QThreadFuture`:

1. Get the first selected run's `_client_key`
2. Access the run via `TiledService._client[client_key]`
3. Get the primary stream
4. Find the first field where `data_keys[field]["shape"]` has ndim >= 2
5. Read a single frame: frame 0 for 2D data, middle frame (`n // 2`) for 3D
6. Return the 2D numpy array

**On success:**
- Display frame in ImageView via `setImage()`
- Add RectROI covering full frame, snapped to pixel boundaries
- Connect ROI `sigRegionChanged` to update coordinate display
- Enable Export button
- Status: "Drag to select ROI"

**On failure (no image field found):**
- Status label: "No image data found in selected run — NXsas requires image data"
- Disable Export button
- ImageView left empty

**On error (Tiled connection failure, etc.):**
- Status label: "Failed to load preview: {error}"
- Disable Export button

## Export Type Switching

- **Switch to NXsas:** trigger image load, show ROI widget
- **Switch away from NXsas:** clear ImageView, remove ROI, re-enable Export button, show empty NoOp widget
- **Re-selecting NXsas:** reload the image (don't cache — simple for v1)

## ROI → Export Params

`_get_roi_params()` reads from the `RectROI` object:

```python
pos = roi.pos()    # (x, y) float
size = roi.size()  # (w, h) float
return {
    "x": int(pos[0]),
    "y": int(pos[1]),
    "width": int(size[0]),
    "height": int(size[1]),
}
```

If the ROI covers the full frame (pos == (0,0) and size == frame shape), returns `None` — no cropping, full frame export.

## Export Button State

The Export button state depends on the selected export type:

- **NoOp:** enabled whenever output directory is non-empty
- **NXsas loading:** disabled while loading image
- **NXsas loaded:** enabled (ROI is always valid — initialized to full frame)
- **NXsas error:** disabled (no image data available)

## No Exporter Changes

The exporter already receives ROI as `{x, y, width, height}` in params and applies it. No changes needed.

## Testing

- **Image loading function:** extract as a standalone function that takes a tiled client and run key, returns a 2D numpy array. Unit test with mock tiled client.
- **ROI params extraction:** unit test that `_get_roi_params` returns correct dict from known ROI position/size, and returns None for full-frame.
- **Widget behavior:** manual testing (Qt widget testing for interactive ROI is brittle)
