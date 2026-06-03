# Theater Mode — Widget Expansion Overlay

**Date:** 2026-04-10
**Status:** Draft

## Overview

Theater mode allows any QWidget to temporarily expand into a fullscreen-like overlay
on top of the main window. The primary use case is expanding PyQtGraph plots and
ImageViews for detailed inspection, then collapsing back to their normal layout
position.

The feature is generic — it works with any QWidget, not just plots.

## Design

### Components

Three classes in a new `lightfall/ui/theater/` package:

1. **`TheaterProxy(QStackedWidget)`** — Wraps a target widget. Provides the hover
   expand button and manages the widget handoff to/from the overlay.
2. **`TheaterOverlay(QWidget)`** — Singleton overlay, child of `NCSMainWindow`.
   Paints a dimmed backdrop and hosts the expanded widget.
3. **`TheaterManager`** — Module-level singleton. Coordinates proxies and the
   overlay. Provides the convenience `install()` API.

### TheaterProxy

A `QStackedWidget` with two pages:

- **Page 0:** The target widget (normal display)
- **Page 1:** A placeholder `QLabel` (centered text "Expanded", muted style) shown
  while the widget is on the overlay

**Hover button:** A `QToolButton` with an expand icon, positioned absolutely in the
top-right corner of the proxy. Opacity ~0.7. Visible only when the mouse is inside
the proxy (`enterEvent`/`leaveEvent`). Repositioned on `resizeEvent`. Clicking it
emits the `expand_requested` signal.

**Signals:**

- `expand_requested(TheaterProxy)` — emitted when the user clicks the expand button

**Key methods:**

- `__init__(self, widget: QWidget)` — accepts the target widget, sets up the stacked
  pages and hover button, registers with the `TheaterManager`
- `take_widget() -> QWidget` — switches to page 1 (placeholder), returns the target
- `return_widget(widget: QWidget)` — inserts widget back as page 0, switches to page 0

**Direct usage (no layout surgery):**

```python
proxy = TheaterProxy(my_plot)
layout.addWidget(proxy)
```

### TheaterOverlay

A single instance per main window, created lazily on first activation.

**Visual structure:**

- Fills the entire `NCSMainWindow` client area
- Background: semi-transparent black (`rgba(0, 0, 0, 150)`) via `paintEvent`
- Content area: 20px margin on all sides, holds the expanded widget
- Collapse button: `QToolButton` with collapse icon, top-right of content area

**Resize tracking:** Installs an event filter on the main window. On `Resize` events,
updates its own geometry to match the main window's client area.

**Key methods:**

- `activate(proxy: TheaterProxy)` — takes the widget from the proxy, shows the
  overlay with animation, raises to top, grabs focus
- `deactivate()` — animates out, returns the widget to the active proxy, hides

**Dismissal triggers:**

- `Escape` key (`keyPressEvent`)
- Collapse button click
- Click on dimmed backdrop outside the content area (`mousePressEvent` with
  hit-testing against the content rect)

**Edge cases:**

- Main window resize while active — overlay and content area resize with it
- Panel closed while widget is in theater — `deactivate()` first, then allow close
- Only one widget can be in theater mode at a time (`_active_proxy` tracking)

### TheaterManager

Module-level singleton accessed as `theater_manager`.

**Responsibilities:**

- Lazily creates the `TheaterOverlay` on first activation (main window reference
  via `QApplication.activeWindow()` or passed explicitly)
- Tracks all installed proxies by widget identity
- Connects each proxy's `expand_requested` signal to `activate(proxy)`

**Public API:**

- `install(widget: QWidget) -> TheaterProxy` — convenience method that:
  1. Finds the widget's current parent layout
  2. Finds the widget's index in that layout
  3. Creates a `TheaterProxy` wrapping the widget
  4. Replaces the widget in the layout at the same index with the proxy
- `uninstall(widget: QWidget)` — returns the widget to its layout position, removes
  the proxy
- `activate(proxy: TheaterProxy)` — delegates to the overlay
- `deactivate()` — delegates to the overlay

### Animation

**Activation (open):**

1. Capture the widget's current geometry relative to the main window
2. Show the overlay with backdrop opacity at 0
3. `QParallelAnimationGroup` with two `QPropertyAnimation`s:
   - Backdrop opacity: 0 → 150 (~200ms, `QEasingCurve.OutCubic`)
   - Content container geometry: widget's original rect → final expanded rect
     (~300ms, `QEasingCurve.OutCubic`)

**Deactivation (close):**

- Reverse: widget geometry shrinks back toward its original position, backdrop fades
  out
- On animation `finished`, reparent the widget back into the proxy and hide the
  overlay

The overlay exposes a `backdrop_opacity` property (PySide6 `Property(int)`) so
`QPropertyAnimation` can drive it. The geometry animation targets the content
container widget directly.

### Integration

Single-line opt-in for existing widgets:

```python
from lightfall.ui.theater import theater_manager

# In a panel or widget setup:
plot = pg.PlotWidget()
layout.addWidget(plot)
theater_manager.install(plot)
```

Or direct proxy construction:

```python
from lightfall.ui.theater import TheaterProxy

proxy = TheaterProxy(my_image_view)
layout.addWidget(proxy)
```

No subclassing or protocol implementation required. Any `QWidget` works.

## File Structure

```
lightfall/ui/theater/
├── __init__.py          # exports TheaterProxy, TheaterOverlay, theater_manager
├── proxy.py             # TheaterProxy
├── overlay.py           # TheaterOverlay
└── manager.py           # TheaterManager
```

## Testing Strategy

- **Unit tests for TheaterProxy:** verify stacked widget page switching, take/return
  widget, hover button visibility on enter/leave events
- **Unit tests for TheaterManager:** verify install/uninstall layout surgery, proxy
  tracking, activate/deactivate delegation
- **Integration tests:** verify full cycle — install, click expand, verify overlay
  visible, press Escape, verify widget restored to original layout position
- **Edge case tests:** window resize during theater, uninstall during theater,
  multiple proxies with single active constraint
