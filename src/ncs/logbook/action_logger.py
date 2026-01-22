"""
Device action logging service for automatic logbook entries.

Captures device actions (parameter changes, starts, stops) from control widgets
and formats them as protected markdown entries for the logbook.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any, ClassVar

from loguru import logger
from PySide6.QtCore import QObject, QTimer, Signal

if TYPE_CHECKING:
    from ncs.ui.widgets.base_control import BaseControlWidget


@dataclass
class DeviceAction:
    """A single device action record.

    Attributes:
        device_name: Name of the device that was operated.
        action_type: Type of action ("set", "start", "stop", "move").
        old_value: Value before the action (if applicable).
        new_value: Value after the action (if applicable).
        timestamp: When the action occurred.
        unit: Optional unit string for the values.
    """

    device_name: str
    action_type: str
    old_value: Any = None
    new_value: Any = None
    timestamp: datetime = field(default_factory=datetime.now)
    unit: str = ""

    def format_change(self) -> str:
        """Format the value change as a string."""
        if self.old_value is None and self.new_value is None:
            return ""

        old_str = self._format_value(self.old_value)
        new_str = self._format_value(self.new_value)

        if self.old_value is None:
            return f"→ {new_str}"
        if self.new_value is None:
            return f"{old_str} →"

        unit_suffix = f" {self.unit}" if self.unit else ""
        return f"{old_str} → {new_str}{unit_suffix}"

    def _format_value(self, value: Any) -> str:
        """Format a single value for display."""
        if value is None:
            return "—"
        if isinstance(value, float):
            return f"{value:.4g}"
        return str(value)


@dataclass
class ActionGroup:
    """A group of consecutive device actions.

    Actions within a time window are grouped together and displayed
    as a collapsible section in the logbook.

    Attributes:
        id: Unique identifier for this group.
        actions: List of actions in the group.
        collapsed: Whether the group is displayed collapsed.
    """

    id: str = field(default_factory=lambda: str(uuid.uuid4())[:8])
    actions: list[DeviceAction] = field(default_factory=list)
    collapsed: bool = True

    @property
    def start_time(self) -> datetime | None:
        """Get the timestamp of the first action."""
        return self.actions[0].timestamp if self.actions else None

    @property
    def end_time(self) -> datetime | None:
        """Get the timestamp of the last action."""
        return self.actions[-1].timestamp if self.actions else None

    @property
    def count(self) -> int:
        """Get the number of actions in the group."""
        return len(self.actions)

    def add_action(self, action: DeviceAction) -> None:
        """Add an action to the group."""
        self.actions.append(action)

    def time_range_str(self) -> str:
        """Get formatted time range string."""
        if not self.actions:
            return ""
        start = self.start_time
        end = self.end_time
        if start and end:
            start_str = start.strftime("%H:%M:%S")
            end_str = end.strftime("%H:%M:%S")
            if start_str == end_str:
                return start_str
            return f"{start_str} - {end_str}"
        return ""


class DeviceActionLogger(QObject):
    """
    Singleton service that captures device actions and logs them.

    Listens to control widget signals and formats actions as protected
    markdown for insertion into the logbook.

    Signals:
        action_recorded(DeviceAction): Emitted for each action.
        group_updated(ActionGroup): Emitted when a group is updated.
        group_closed(ActionGroup): Emitted when a group is finalized.

    Example:
        >>> logger = DeviceActionLogger.get_instance()
        >>> logger.action_recorded.connect(on_action)
        >>> logger.connect_to_control_widget(motor_widget)
    """

    _instance: ClassVar[DeviceActionLogger | None] = None

    # Signals
    action_recorded = Signal(object)  # DeviceAction
    group_updated = Signal(object)  # ActionGroup
    group_closed = Signal(object)  # ActionGroup

    # Default collapse window (5 minutes)
    DEFAULT_COLLAPSE_WINDOW_SECONDS = 300

    def __init__(self, parent: QObject | None = None) -> None:
        """Initialize the action logger.

        Args:
            parent: Optional Qt parent object.
        """
        super().__init__(parent)
        self._current_group: ActionGroup | None = None
        self._collapse_window = timedelta(seconds=self.DEFAULT_COLLAPSE_WINDOW_SECONDS)
        self._connected_widgets: list[BaseControlWidget] = []

        # Timer to auto-close groups after window expires
        self._close_timer = QTimer(self)
        self._close_timer.setSingleShot(True)
        self._close_timer.timeout.connect(self._on_close_timer)

        # Track pending moves (motion_started but not finished)
        self._pending_moves: dict[str, dict[str, Any]] = {}

    @classmethod
    def get_instance(cls) -> DeviceActionLogger:
        """Get the singleton instance."""
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    @classmethod
    def reset_instance(cls) -> None:
        """Reset the singleton instance (for testing)."""
        if cls._instance is not None:
            cls._instance.deleteLater()
        cls._instance = None

    def set_collapse_window(self, seconds: int) -> None:
        """Set the collapse window duration.

        Args:
            seconds: Window duration in seconds.
        """
        self._collapse_window = timedelta(seconds=seconds)
        logger.debug(f"Action collapse window set to {seconds}s")

    def connect_to_control_widget(self, widget: BaseControlWidget) -> None:
        """Connect to a control widget's signals.

        Args:
            widget: The control widget to connect to.
        """
        if widget in self._connected_widgets:
            return

        widget.motion_started.connect(self._on_motion_started)
        widget.motion_finished.connect(self._on_motion_finished)
        self._connected_widgets.append(widget)
        logger.debug(f"Connected to control widget: {widget.__class__.__name__}")

    def disconnect_from_control_widget(self, widget: BaseControlWidget) -> None:
        """Disconnect from a control widget's signals.

        Args:
            widget: The control widget to disconnect from.
        """
        if widget not in self._connected_widgets:
            return

        try:
            widget.motion_started.disconnect(self._on_motion_started)
            widget.motion_finished.disconnect(self._on_motion_finished)
        except RuntimeError:
            pass  # Already disconnected
        self._connected_widgets.remove(widget)

    def record_action(
        self,
        device_name: str,
        action_type: str,
        old_value: Any = None,
        new_value: Any = None,
        unit: str = "",
    ) -> DeviceAction:
        """Record a device action.

        Args:
            device_name: Name of the device.
            action_type: Type of action.
            old_value: Value before action.
            new_value: Value after action.
            unit: Optional unit string.

        Returns:
            The created DeviceAction.
        """
        action = DeviceAction(
            device_name=device_name,
            action_type=action_type,
            old_value=old_value,
            new_value=new_value,
            unit=unit,
        )

        self._add_action_to_group(action)
        self.action_recorded.emit(action)

        logger.info(
            f"Recorded action: {device_name} {action_type} {action.format_change()}"
        )
        return action

    def close_current_group(self) -> ActionGroup | None:
        """Close the current action group.

        Called when user adds manual content or window expires.

        Returns:
            The closed group, or None if no group was active.
        """
        if self._current_group is None:
            return None

        group = self._current_group
        self._current_group = None
        self._close_timer.stop()

        self.group_closed.emit(group)
        logger.debug(f"Closed action group {group.id} with {group.count} actions")
        return group

    def _add_action_to_group(self, action: DeviceAction) -> None:
        """Add an action to the current group or start a new one."""
        if self._should_start_new_group(action):
            self.close_current_group()
            self._current_group = ActionGroup()
            logger.debug(f"Started new action group: {self._current_group.id}")

        if self._current_group is not None:
            self._current_group.add_action(action)
            self.group_updated.emit(self._current_group)

            # Reset close timer
            self._close_timer.start(int(self._collapse_window.total_seconds() * 1000))

    def _should_start_new_group(self, action: DeviceAction) -> bool:
        """Check if a new group should be started for this action."""
        if self._current_group is None:
            return True

        # Check if outside collapse window
        if self._current_group.end_time:
            elapsed = action.timestamp - self._current_group.end_time
            if elapsed > self._collapse_window:
                return True

        return False

    def _on_close_timer(self) -> None:
        """Handle group close timer expiry."""
        self.close_current_group()

    def _on_motion_started(self, device_name: str) -> None:
        """Handle motion started signal from control widget."""
        # Store pending move to capture old value
        self._pending_moves[device_name] = {
            "start_time": datetime.now(),
            "old_value": None,  # Could be populated if we track device state
        }
        logger.debug(f"Motion started: {device_name}")

    def _on_motion_finished(self, device_name: str) -> None:
        """Handle motion finished signal from control widget."""
        pending = self._pending_moves.pop(device_name, None)
        old_value = pending.get("old_value") if pending else None

        # Record the move action
        self.record_action(
            device_name=device_name,
            action_type="move",
            old_value=old_value,
            new_value=None,  # Could be populated with final position
        )

    def format_action_markdown(self, action: DeviceAction) -> str:
        """Format a single action as markdown.

        Args:
            action: The action to format.

        Returns:
            Markdown string for the action.
        """
        time_str = action.timestamp.strftime("%H:%M:%S")
        change = action.format_change()
        if change:
            return f"**{action.device_name}** {action.action_type}: {change}"
        return f"**{action.device_name}** {action.action_type}"

    def format_group_markdown(self, group: ActionGroup) -> str:
        """Format an action group as protected markdown.

        Args:
            group: The action group to format.

        Returns:
            Protected markdown string for the group.
        """
        if group.count == 0:
            return ""

        region_id = f"action-{group.id}"
        start_ts = group.start_time.isoformat() if group.start_time else ""
        end_ts = group.end_time.isoformat() if group.end_time else ""

        lines = [
            f"<!-- PROTECTED:{region_id} -->",
            f"<!-- ACTION_GROUP:count={group.count}:start={start_ts}:end={end_ts}:collapsed={'true' if group.collapsed else 'false'} -->",
        ]

        if group.count == 1:
            # Single action - simple format
            action = group.actions[0]
            time_str = action.timestamp.strftime("%H:%M:%S")
            action_text = self.format_action_markdown(action)
            lines.append(f"**[Device Action]** {time_str} - {action_text}")
        else:
            # Multiple actions - table format with details wrapper
            time_range = group.time_range_str()
            lines.append(f"<details>")
            lines.append(
                f"<summary>**Device Actions** ({group.count} actions, {time_range})</summary>"
            )
            lines.append("")
            lines.append("| Time | Device | Action | Change |")
            lines.append("|------|--------|--------|--------|")
            for action in group.actions:
                time_str = action.timestamp.strftime("%H:%M:%S")
                change = action.format_change() or "—"
                lines.append(
                    f"| {time_str} | {action.device_name} | {action.action_type} | {change} |"
                )
            lines.append("")
            lines.append("</details>")

        lines.append(f"<!-- /PROTECTED:{region_id} -->")

        return "\n".join(lines)

    @property
    def current_group(self) -> ActionGroup | None:
        """Get the current active action group."""
        return self._current_group

    @property
    def has_active_group(self) -> bool:
        """Check if there's an active action group."""
        return self._current_group is not None
