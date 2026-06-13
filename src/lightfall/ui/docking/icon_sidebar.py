"""IconStripSidebar - Custom icon strip sidebar for panel navigation.

Provides a VS Code/PyCharm-like icon strip that controls docked panels.
Icons remain visible regardless of whether panels are shown (pinned).

Architecture:
    +------+
    | [B]  |  <- Top icons (dock panels to left)
    | [D]  |
    |      |
    |      |  <- Stretch spacer
    |      |
    | [C]  |  <- Bottom icons (dock panels to bottom)
    | [L]  |
    +------+
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import qtawesome as qta
from PySide6.QtCore import QPoint, QSize, Qt, Signal
from PySide6.QtGui import QIcon, QMouseEvent
from PySide6.QtWidgets import QFrame, QMenu, QToolButton, QVBoxLayout, QWidget

from lightfall.utils.logging import logger

if TYPE_CHECKING:
    pass


class IconStripButton(QToolButton):
    """A toggle button for the icon strip sidebar.

    Supports drag-and-drop reordering via mouse events with threshold detection.
    """

    # Signals for drag operations
    drag_started = Signal(str, QPoint)  # panel_id, global_pos
    drag_moved = Signal(str, QPoint)  # panel_id, global_pos
    drag_finished = Signal(str, QPoint)  # panel_id, global_pos

    # Minimum movement before drag starts
    DRAG_THRESHOLD = 8

    # Design sizes at the reference 10pt font; scaled via ThemeManager.scale_px
    # so the strip tracks the Appearance > Font Size setting.
    BASE_ICON_PX = 17
    BASE_BOX_PX = 26

    def __init__(
        self,
        panel_id: str,
        icon: QIcon,
        tooltip: str,
        parent: QWidget | None = None,
    ) -> None:
        """Initialize the icon strip button.

        Args:
            panel_id: The panel ID this button controls.
            icon: The button icon.
            tooltip: Tooltip text shown on hover.
            parent: Optional parent widget.
        """
        super().__init__(parent)
        self.panel_id = panel_id
        self.setIcon(icon)
        self.setToolTip(tooltip)
        self.setCheckable(True)
        self.setAutoRaise(True)
        self.apply_font_scale()

        # Drag state
        self._drag_start_pos: QPoint | None = None
        self._is_dragging: bool = False

    def apply_font_scale(self) -> None:
        """Size the icon and button box relative to the base font.

        Called on construction and whenever the base font size changes so the
        strip stays proportional to the rest of the UI.
        """
        from lightfall.ui.theme import ThemeManager

        scale = ThemeManager.get_instance().scale_px
        icon = scale(self.BASE_ICON_PX)
        box = scale(self.BASE_BOX_PX)
        self.setIconSize(QSize(icon, icon))
        self.setFixedSize(box, box)

    def mousePressEvent(self, event: QMouseEvent) -> None:
        """Handle mouse press - start potential drag."""
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_start_pos = event.globalPosition().toPoint()
            self._is_dragging = False
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        """Handle mouse move - detect drag threshold and emit signals."""
        if self._drag_start_pos is not None:
            global_pos = event.globalPosition().toPoint()
            delta = global_pos - self._drag_start_pos

            if not self._is_dragging:
                # Check if we've moved enough to start dragging
                if delta.manhattanLength() >= self.DRAG_THRESHOLD:
                    self._is_dragging = True
                    self.drag_started.emit(self.panel_id, global_pos)
            else:
                # Already dragging, emit move signal
                self.drag_moved.emit(self.panel_id, global_pos)

        # Don't call super during drag to prevent hover effects on other buttons
        if not self._is_dragging:
            super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        """Handle mouse release - finish drag or toggle."""
        if event.button() == Qt.MouseButton.LeftButton:
            if self._is_dragging:
                # Emit drag finished
                global_pos = event.globalPosition().toPoint()
                self.drag_finished.emit(self.panel_id, global_pos)
                self._is_dragging = False
                self._drag_start_pos = None
                # Don't call super - prevent toggle on drag release
                return

            self._drag_start_pos = None

        super().mouseReleaseEvent(event)


class DropIndicator(QFrame):
    """Visual indicator showing where a dragged button will be dropped."""

    def __init__(self, parent: QWidget | None = None) -> None:
        """Initialize the drop indicator.

        Args:
            parent: Parent widget.
        """
        super().__init__(parent)
        self.setObjectName("IconStripDropIndicator")
        self.setFixedHeight(2)
        self.hide()


class IconStripSidebar(QFrame):
    """Custom icon strip sidebar that controls docked panels.

    Emits panel_toggled signal when icons are clicked. The main window
    or docking manager handles showing/hiding the actual panels.

    Icons are split into top (left-docking panels) and bottom
    (bottom-docking panels) sections with a stretch spacer between.

    Supports drag-and-drop reordering of icons within and between sections.
    """

    panel_toggled = Signal(str, bool)  # panel_id, should_show
    panel_section_changed = Signal(str, str)  # panel_id, new_section ("top" or "bottom")
    panel_remove_requested = Signal(str)  # panel_id

    def __init__(self, parent: QWidget | None = None) -> None:
        """Initialize the sidebar.

        Args:
            parent: Optional parent widget.
        """
        super().__init__(parent)
        self._buttons: dict[str, IconStripButton] = {}
        self._button_sections: dict[str, str] = {}  # panel_id -> "top" or "bottom"
        self._button_orders: dict[str, int] = {}    # panel_id -> sidebar_order
        self._button_icons: dict[str, str] = {}     # panel_id -> icon_name (for theme updates)
        self._stretch_index: int = -1  # Index of stretch item in layout

        # Drag state
        self._drop_indicator: DropIndicator | None = None
        self._dragging_panel_id: str | None = None
        self._drag_start_index: int = -1

        self._setup_ui()

    # Strip width at the reference 10pt font; scaled with the base font size.
    BASE_WIDTH_PX = 34

    def _setup_ui(self) -> None:
        """Setup the sidebar UI."""
        self.setFrameStyle(QFrame.Shape.NoFrame)
        self.setObjectName("IconStripSidebar")

        from lightfall.ui.theme import ThemeManager

        theme_mgr = ThemeManager.get_instance()
        self.setFixedWidth(theme_mgr.scale_px(self.BASE_WIDTH_PX))
        # Font-size changes emit theme_changed; icon/box/strip pixel sizes are
        # imperative (no stylesheet cascade), so rescale them here.
        theme_mgr.theme_changed.connect(self._apply_font_scale)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 8, 4, 8)
        layout.setSpacing(3)
        layout.setAlignment(Qt.AlignmentFlag.AlignTop)

        # Create drop indicator (hidden by default)
        self._drop_indicator = DropIndicator(self)

    def _apply_font_scale(self, *_args: object) -> None:
        """Rescale the strip width and every button to the base font size."""
        from lightfall.ui.theme import ThemeManager

        self.setFixedWidth(ThemeManager.get_instance().scale_px(self.BASE_WIDTH_PX))
        for button in self._buttons.values():
            button.apply_font_scale()

    def _create_button(
        self, panel_id: str, icon_name: str, tooltip: str
    ) -> IconStripButton:
        """Create a fully-wired IconStripButton (shared by add/insert).

        Args:
            panel_id: Unique panel identifier.
            icon_name: QtAwesome icon name (e.g., "fa5s.bolt") or path.
            tooltip: Tooltip text shown on hover.

        Returns:
            The created button (not yet added to the layout).
        """
        try:
            from lightfall.ui.theme import ThemeManager

            icon_color = ThemeManager.get_instance().colors.text
        except Exception:
            icon_color = "#cccccc"

        icon = self._resolve_icon(icon_name, icon_color)
        button = IconStripButton(panel_id, icon, tooltip, self)
        button.toggled.connect(
            lambda checked: self._on_button_toggled(panel_id, checked)
        )
        button.drag_started.connect(self._on_button_drag_started)
        button.drag_moved.connect(self._on_button_drag_moved)
        button.drag_finished.connect(self._on_button_drag_finished)
        button.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        button.customContextMenuRequested.connect(
            lambda pos, pid=panel_id: self._show_button_context_menu(pid, pos)
        )
        self._buttons[panel_id] = button
        self._button_icons[panel_id] = icon_name
        return button

    def _show_button_context_menu(self, panel_id: str, pos: QPoint) -> None:
        """Show the right-click menu for a sidebar button.

        Args:
            panel_id: Panel identifier.
            pos: Local position of the right-click event.
        """
        button = self._buttons.get(panel_id)
        if button is None:
            return
        menu = QMenu(self)
        remove_action = menu.addAction("Remove from Sidebar")
        chosen = self._exec_context_menu(menu, button.mapToGlobal(pos))
        if chosen is remove_action:
            self.panel_remove_requested.emit(panel_id)

    def _exec_context_menu(self, menu: QMenu, global_pos: QPoint):
        """Execute the context menu at a global position.

        Extracted for testability — tests monkeypatch this method.

        Args:
            menu: The QMenu to execute.
            global_pos: Global screen position.

        Returns:
            The triggered QAction, or None if dismissed.
        """
        return menu.exec(global_pos)

    def add_panel_button(
        self,
        panel_id: str,
        icon_name: str,
        tooltip: str,
    ) -> IconStripButton:
        """Add a button for a panel.

        Args:
            panel_id: Unique panel identifier.
            icon_name: QtAwesome icon name (e.g., "fa5s.bolt") or path.
            tooltip: Tooltip text shown on hover.

        Returns:
            The created button.
        """
        button = self._create_button(panel_id, icon_name, tooltip)

        # Determine which section to add to based on whether stretch exists
        if self._stretch_index >= 0:
            # Stretch exists, add to bottom section
            self._button_sections[panel_id] = "bottom"
        else:
            # No stretch yet, add to top section
            self._button_sections[panel_id] = "top"

        self.layout().addWidget(button)

        logger.debug("Added sidebar button for panel: {} (section: {})", panel_id, self._button_sections[panel_id])
        return button

    def insert_panel_button_sorted(
        self,
        panel_id: str,
        icon_name: str,
        tooltip: str,
        sidebar_order: int,
        section: str,
    ) -> IconStripButton:
        """Insert a button at the correct sorted position within a section.

        Unlike add_panel_button(), this inserts based on sidebar_order to maintain
        sorted order within the section. Used when panels are registered at runtime.

        Args:
            panel_id: Unique panel identifier.
            icon_name: QtAwesome icon name (e.g., "fa5s.bolt") or path.
            tooltip: Tooltip text shown on hover.
            sidebar_order: Order value for sorting (lower = higher position).
            section: Target section ("top" for left-docking, "bottom" for bottom-docking).

        Returns:
            The created button.
        """
        button = self._create_button(panel_id, icon_name, tooltip)
        self._button_orders[panel_id] = sidebar_order
        self._button_sections[panel_id] = section

        # Find the correct insert position within the section
        insert_index = self._find_sorted_insert_index(sidebar_order, section)

        # Insert at the computed position
        layout = self.layout()
        layout.insertWidget(insert_index, button)

        # Adjust stretch index if we inserted before it
        if self._stretch_index >= 0 and insert_index <= self._stretch_index:
            self._stretch_index += 1

        logger.debug(
            "Inserted sidebar button for panel: {} at index {} (section: {}, order: {})",
            panel_id, insert_index, section, sidebar_order
        )
        return button

    def _find_sorted_insert_index(self, sidebar_order: int, section: str) -> int:
        """Find the correct layout index to insert a button based on order.

        Args:
            sidebar_order: Order value for the new button.
            section: Target section ("top" or "bottom").

        Returns:
            Layout index for insertion.
        """
        layout = self.layout()

        # Determine section boundaries
        if section == "top":
            start_index = 0
            end_index = self._stretch_index if self._stretch_index >= 0 else layout.count()
        else:  # bottom
            start_index = (self._stretch_index + 1) if self._stretch_index >= 0 else layout.count()
            end_index = layout.count()

        # Find position within section by comparing sidebar_order
        for i in range(start_index, end_index):
            item = layout.itemAt(i)
            if item is None:
                continue

            widget = item.widget()
            if not isinstance(widget, IconStripButton):
                continue

            existing_order = self._button_orders.get(widget.panel_id, 0)
            if sidebar_order < existing_order:
                return i

        # Insert at end of section
        return end_index

    def remove_panel_button(self, panel_id: str) -> bool:
        """Remove a panel button from the sidebar.

        Args:
            panel_id: Panel identifier.

        Returns:
            True if the button was removed.
        """
        button = self._buttons.pop(panel_id, None)
        if button is None:
            return False

        # Get button index before removal
        layout = self.layout()
        button_index = layout.indexOf(button)

        # Remove from layout
        layout.removeWidget(button)
        button.deleteLater()

        # Clean up tracking dicts
        self._button_sections.pop(panel_id, None)
        self._button_orders.pop(panel_id, None)
        self._button_icons.pop(panel_id, None)

        # Adjust stretch index if button was before it
        if self._stretch_index >= 0 and button_index < self._stretch_index:
            self._stretch_index -= 1

        logger.debug("Removed sidebar button for panel: {}", panel_id)
        return True

    def _resolve_icon(self, icon_name: str, color: str) -> QIcon:
        """Resolve an icon name to a QIcon.

        Args:
            icon_name: QtAwesome icon name or path.
            color: Icon color.

        Returns:
            QIcon instance.
        """
        if not icon_name:
            return QIcon()

        try:
            # Support both "bolt" and "fa5s.bolt" formats
            if "." not in icon_name:
                for prefix in ["fa5s", "fa5", "mdi", "mdi6", "ri"]:
                    try:
                        return qta.icon(f"{prefix}.{icon_name}", color=color)
                    except Exception:
                        continue
            else:
                return qta.icon(icon_name, color=color)
        except Exception:
            pass

        return QIcon(icon_name)

    def update_button_icon(self, panel_id: str, icon_name: str = "", color: str = "") -> None:
        """Update a button's icon and/or color at runtime.

        Args:
            panel_id: Panel identifier.
            icon_name: New qtawesome icon name. Empty string keeps current icon.
            color: Icon color as hex string. Empty string uses theme default.
        """
        button = self._buttons.get(panel_id)
        if button is None:
            return

        # Update stored icon name if a new one is provided
        if icon_name:
            self._button_icons[panel_id] = icon_name
        else:
            icon_name = self._button_icons.get(panel_id, "")

        if not icon_name:
            return

        # Resolve color: use provided color or fall back to theme default
        if not color:
            try:
                from lightfall.ui.theme import ThemeManager
                theme_mgr = ThemeManager.get_instance()
                color = theme_mgr.colors.text
            except Exception:
                color = "#cccccc"

        icon = self._resolve_icon(icon_name, color)
        button.setIcon(icon)

    def _on_button_toggled(self, panel_id: str, checked: bool) -> None:
        """Handle button toggle.

        Args:
            panel_id: The panel that was toggled.
            checked: Whether the button is now checked.
        """
        self.panel_toggled.emit(panel_id, checked)

    def set_panel_active(self, panel_id: str, active: bool) -> None:
        """Set the active state of a panel button.

        Call this when panel visibility changes externally (e.g., closed
        via X button) to keep the sidebar in sync.

        Args:
            panel_id: Panel identifier.
            active: Whether the panel is active/visible.
        """
        if panel_id in self._buttons:
            button = self._buttons[panel_id]
            button.blockSignals(True)
            button.setChecked(active)
            button.blockSignals(False)

    def move_panel_to_section(self, panel_id: str, section: str) -> bool:
        """Move a panel button to a different section.

        Args:
            panel_id: Panel identifier.
            section: Target section ("top" for left-docking, "bottom" for bottom-docking).

        Returns:
            True if the button was moved.
        """
        if panel_id not in self._buttons:
            return False

        current_section = self._button_sections.get(panel_id)
        if current_section == section:
            return False  # Already in correct section

        if self._stretch_index < 0:
            return False  # No stretch, can't determine sections

        button = self._buttons[panel_id]
        layout = self.layout()

        # Find current button index
        current_index = layout.indexOf(button)
        if current_index < 0:
            return False

        # Remove button from current position
        layout.removeWidget(button)

        # Adjust stretch index if button was before it
        if current_index < self._stretch_index:
            self._stretch_index -= 1

        # Insert at new position
        if section == "top":
            # Insert just before the stretch
            layout.insertWidget(self._stretch_index, button)
            # Stretch moves down by 1
            self._stretch_index += 1
        else:  # bottom
            # Append to end
            layout.addWidget(button)

        self._button_sections[panel_id] = section
        logger.debug("Moved sidebar button {} to section {}", panel_id, section)
        return True

    def get_panel_section(self, panel_id: str) -> str | None:
        """Get the current section of a panel button.

        Args:
            panel_id: Panel identifier.

        Returns:
            Section name ("top" or "bottom") or None if not found.
        """
        return self._button_sections.get(panel_id)

    def ordered_panel_ids(self) -> list[str]:
        """Panel ids in visual order: top section top-to-bottom, then bottom.

        Reflects the current layout order, including any drag-and-drop
        reordering the user has done.

        Returns:
            List of panel IDs in visual layout order.
        """
        ids: list[str] = []
        layout = self.layout()
        for i in range(layout.count()):
            item = layout.itemAt(i)
            widget = item.widget() if item is not None else None
            if isinstance(widget, IconStripButton):
                ids.append(widget.panel_id)
        return ids

    def add_stretch(self) -> None:
        """Add stretch to push subsequent buttons to bottom."""
        layout = self.layout()
        layout.addStretch()
        self._stretch_index = layout.count() - 1

    def add_separator(self) -> None:
        """Add a visual separator line."""
        separator = QFrame()
        separator.setFrameShape(QFrame.Shape.HLine)
        separator.setFixedHeight(1)
        separator.setObjectName("IconStripSeparator")
        self.layout().addWidget(separator)

    def update_theme(self) -> None:
        """Update button icons when theme changes."""
        # TODO: Re-resolve icons with new theme color
        # We'd need to store icon_name per button to do this properly
        logger.debug("IconStripSidebar theme updated")

    # ─────────────────────────────────────────────────────────────────────────
    # Drag-and-drop reordering
    # ─────────────────────────────────────────────────────────────────────────

    def _on_button_drag_started(self, panel_id: str, global_pos: QPoint) -> None:
        """Handle the start of a button drag operation.

        Args:
            panel_id: The panel ID being dragged.
            global_pos: Global cursor position.
        """
        if panel_id not in self._buttons:
            return

        self._dragging_panel_id = panel_id
        button = self._buttons[panel_id]
        layout = self.layout()
        self._drag_start_index = layout.indexOf(button)

        # Make the button semi-transparent during drag
        button.setWindowOpacity(0.5)
        button.setStyleSheet("opacity: 0.5;")

        # Set drag cursor
        self.setCursor(Qt.CursorShape.ClosedHandCursor)

        # Show drop indicator
        self._update_drop_indicator(global_pos)

        logger.debug("Started dragging sidebar button: {}", panel_id)

    def _on_button_drag_moved(self, panel_id: str, global_pos: QPoint) -> None:
        """Handle button drag movement.

        Args:
            panel_id: The panel ID being dragged.
            global_pos: Global cursor position.
        """
        if self._dragging_panel_id != panel_id:
            return

        self._update_drop_indicator(global_pos)

    def _on_button_drag_finished(self, panel_id: str, global_pos: QPoint) -> None:
        """Handle the end of a button drag operation.

        Args:
            panel_id: The panel ID being dragged.
            global_pos: Global cursor position.
        """
        if self._dragging_panel_id != panel_id:
            return

        # Get drop position
        target_index, target_section = self._get_drop_position(global_pos)

        # Perform the reorder if position changed
        if target_index >= 0:
            self._reorder_button(panel_id, target_index, target_section)

        # Reset visual state
        button = self._buttons.get(panel_id)
        if button:
            button.setStyleSheet("")

        # Hide drop indicator
        if self._drop_indicator:
            self._drop_indicator.hide()

        # Reset cursor
        self.unsetCursor()

        # Clear drag state
        self._dragging_panel_id = None
        self._drag_start_index = -1

        logger.debug("Finished dragging sidebar button: {} to index {}", panel_id, target_index)

    def _update_drop_indicator(self, global_pos: QPoint) -> None:
        """Update the drop indicator position based on cursor.

        Args:
            global_pos: Global cursor position.
        """
        if not self._drop_indicator or not self._dragging_panel_id:
            return

        target_index, _ = self._get_drop_position(global_pos)
        if target_index < 0:
            self._drop_indicator.hide()
            return

        # Get Y position for the indicator
        indicator_y = self._get_indicator_y_position(target_index)

        # Position the indicator
        layout = self.layout()
        margins = layout.contentsMargins()
        self._drop_indicator.setGeometry(
            margins.left(),
            indicator_y,
            self.width() - margins.left() - margins.right(),
            2
        )
        self._drop_indicator.show()
        self._drop_indicator.raise_()

    def _get_drop_position(self, global_pos: QPoint) -> tuple[int, str]:
        """Calculate target index and section from cursor position.

        Args:
            global_pos: Global cursor position.

        Returns:
            Tuple of (target_index, target_section). Index is -1 if invalid.
        """
        local_pos = self.mapFromGlobal(global_pos)
        cursor_y = local_pos.y()
        layout = self.layout()

        # Find which button the cursor is over and determine insert position
        best_index = -1
        best_section = "top"

        # Iterate through layout items to find insertion point
        for i in range(layout.count()):
            item = layout.itemAt(i)
            if item is None:
                continue

            widget = item.widget()

            # Skip the stretch spacer (it's a spacer item, not a widget)
            if widget is None:
                # This is the stretch - cursor below this means bottom section
                if i == self._stretch_index:
                    spacer_geo = item.geometry()
                    if cursor_y >= spacer_geo.top():
                        best_section = "bottom"
                continue

            # Skip the drop indicator itself
            if widget is self._drop_indicator:
                continue

            # Get widget geometry
            widget_center_y = widget.geometry().center().y()

            # If cursor is above the center of this widget, insert before it
            if cursor_y < widget_center_y:
                best_index = i
                # Determine section based on whether we're before or after stretch
                if self._stretch_index >= 0:
                    best_section = "top" if i < self._stretch_index else "bottom"
                break
            else:
                # Cursor is below center - tentatively insert after this widget
                best_index = i + 1
                if self._stretch_index >= 0:
                    best_section = "top" if i < self._stretch_index else "bottom"

        # Handle edge cases
        if best_index < 0:
            # Cursor is above all items - insert at top
            best_index = 0
            best_section = "top"
        elif best_index > layout.count():
            best_index = layout.count()

        # Don't allow dropping at the stretch position itself
        if self._stretch_index >= 0 and best_index == self._stretch_index:
            # Snap to either top section end or bottom section start
            if best_section == "bottom":
                best_index = self._stretch_index + 1
            else:
                best_index = self._stretch_index

        return best_index, best_section

    def _get_indicator_y_position(self, target_index: int) -> int:
        """Convert a layout index to a Y pixel position for the indicator.

        Args:
            target_index: The target layout index.

        Returns:
            Y position in widget coordinates.
        """
        layout = self.layout()
        margins = layout.contentsMargins()

        if target_index <= 0:
            return margins.top()

        if target_index >= layout.count():
            # After last item
            last_item = layout.itemAt(layout.count() - 1)
            if last_item:
                widget = last_item.widget()
                if widget:
                    return widget.geometry().bottom() + 1
                return last_item.geometry().bottom() + 1
            return margins.top()

        # Get the item at target index
        item = layout.itemAt(target_index)
        if item:
            widget = item.widget()
            if widget:
                return widget.geometry().top() - 1
            return item.geometry().top() - 1

        return margins.top()

    def _reorder_button(self, panel_id: str, target_index: int, target_section: str) -> None:
        """Reorder a button to a new position.

        Args:
            panel_id: The panel ID to move.
            target_index: Target layout index.
            target_section: Target section ("top" or "bottom").
        """
        if panel_id not in self._buttons:
            return

        button = self._buttons[panel_id]
        layout = self.layout()
        current_index = layout.indexOf(button)

        if current_index < 0:
            return

        # Track old section for change detection
        old_section = self._button_sections.get(panel_id)

        # If same position, nothing to do
        if current_index == target_index or current_index + 1 == target_index:
            # Update section if it changed
            if old_section != target_section:
                self._button_sections[panel_id] = target_section
                # Emit signal for DockingManager to move the panel
                self.panel_section_changed.emit(panel_id, target_section)
            return

        # Remove from current position
        layout.removeWidget(button)

        # Adjust indices after removal
        adjusted_target = target_index
        if current_index < target_index:
            adjusted_target -= 1

        # Adjust stretch index if needed
        if current_index < self._stretch_index:
            self._stretch_index -= 1

        # Insert at new position
        layout.insertWidget(adjusted_target, button)

        # Adjust stretch index if button was inserted before it
        if adjusted_target <= self._stretch_index:
            self._stretch_index += 1

        # Update section tracking
        self._button_sections[panel_id] = target_section

        # Emit signal if section changed (for DockingManager to move the panel)
        if old_section != target_section:
            self.panel_section_changed.emit(panel_id, target_section)

        logger.debug(
            "Reordered button {} from {} to {} (section: {})",
            panel_id, current_index, adjusted_target, target_section
        )
