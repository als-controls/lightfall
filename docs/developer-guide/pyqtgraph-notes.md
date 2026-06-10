# PyQtGraph Development Notes

This document captures non-obvious patterns and gotchas when working with PyQtGraph in this codebase.

## Coordinate Systems

PyQtGraph has multiple coordinate systems that interact in non-obvious ways:

### The Three Coordinate Systems

1. **Qt Scene Coordinates** - Pixel coordinates in the underlying `QGraphicsScene`
2. **View/Data Coordinates** - The actual data values shown on the plot axes
3. **Item Local Coordinates** - Relative to an item's position (origin at item center)

### Key Insight: Items in PlotItem Use Data Coordinates

When you add a `GraphicsObject` to a `PlotItem`, the item's `pos()` is in **data coordinates**, not Qt scene coordinates. This has important implications:

```python
# Setting item position - these are DATA coordinates (e.g., meters)
item.setPos(1.5, 0.3)  # Places item at x=1.5m, y=0.3m in the plot

# Getting click position from mouse event
pos = event.scenePos()                      # Qt scene coords (pixels)
view_pos = plot.vb.mapSceneToView(pos)      # Data coords (meters)
```

### Custom GraphicsObject Pattern

When creating custom `pg.GraphicsObject` subclasses for use in a `PlotItem`:

```python
class MyItem(pg.GraphicsObject):
    def __init__(self):
        super().__init__()
        self._local_bounds = QRectF()  # Centered at origin

    def _update_geometry(self):
        self.prepareGeometryChange()

        # Set position in DATA coordinates
        self.setPos(data_x, data_y)

        # Bounds are LOCAL (centered at origin)
        half_w, half_h = self.width / 2, self.height / 2
        self._local_bounds = QRectF(-half_w, -half_h, self.width, self.height)

    def boundingRect(self) -> QRectF:
        # MUST return LOCAL coordinates (centered at origin)
        return self._local_bounds

    def paint(self, painter, option, widget):
        # Paint in LOCAL coordinates (centered at origin)
        painter.drawRect(self._local_bounds)

    def contains_point(self, data_point: QPointF) -> bool:
        # Convert DATA coordinates to LOCAL coordinates
        # (NOT mapFromScene - that expects Qt scene coords!)
        item_pos = self.pos()
        local_x = data_point.x() - item_pos.x()
        local_y = data_point.y() - item_pos.y()
        return self._local_bounds.contains(QPointF(local_x, local_y))
```

### Common Mistakes

1. **Using scene coords in boundingRect()** - Causes rendering glitches (clipping, caching issues)
2. **Using `mapFromScene()` with data coords** - `mapFromScene` expects Qt scene pixels, not data values
3. **Non-cosmetic pens with data-unit widths** - Line widths in data units become huge or invisible

## Pen Width: Cosmetic vs Non-Cosmetic

```python
# NON-COSMETIC (default): width is in DATA units
pen = QPen(color, 0.1)  # 0.1 meters wide - scales with zoom!

# COSMETIC: width is in PIXELS
pen = QPen(color, 2.0)
pen.setCosmetic(True)   # Always 2 pixels wide regardless of zoom
```

**Use cosmetic pens for:**
- Selection highlights
- Gizmos/handles
- UI elements that should stay readable at any zoom

**Use non-cosmetic pens for:**
- Data that has physical width (beams, apertures)
- When you want width to scale with zoom

## Mouse Event Handling in PlotItem

```python
def setup_mouse_handling(self):
    # Connect to scene's click signal
    self._plot.scene().sigMouseClicked.connect(self._on_click)

def _on_click(self, event):
    # Step 1: Get Qt scene position
    scene_pos = event.scenePos()

    # Step 2: Convert to data coordinates
    data_pos = self._plot.vb.mapSceneToView(scene_pos)
    point = QPointF(data_pos.x(), data_pos.y())

    # Step 3: Hit-test items using data coordinates
    for item in self._items.values():
        if item.contains_point(point):  # Pass DATA coords
            # Found hit
            break
```

## GraphicsLayoutWidget Structure

```
GraphicsLayoutWidget
└── GraphicsLayout (central item)
    └── PlotItem (added via addPlot())
        ├── ViewBox (vb) - handles pan/zoom, coordinate transforms
        ├── AxisItem (left, bottom, etc.)
        └── Your GraphicsObjects (in ViewBox coordinate space)
```

## Debugging Tips

1. **Print coordinate values** to verify which system you're in:
   ```python
   print(f"Scene pos: {event.scenePos()}")
   print(f"Data pos: {self._plot.vb.mapSceneToView(event.scenePos())}")
   print(f"Item pos: {item.pos()}")
   ```

2. **Check boundingRect visually** by temporarily drawing it:
   ```python
   def paint(self, painter, option, widget):
       painter.setPen(QPen(Qt.red, 1))
       painter.drawRect(self.boundingRect())  # Debug: show bounds
       # ... rest of painting
   ```

3. **Verify item is in scene**:
   ```python
   print(f"Item scene: {item.scene()}")  # Should not be None
   ```
