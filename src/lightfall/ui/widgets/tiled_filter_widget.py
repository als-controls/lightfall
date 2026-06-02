"""Filter widget for Tiled data browser.

Provides UI components for filtering Tiled records by date range,
text search, plan name, and exit status.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import TYPE_CHECKING

from PySide6.QtCore import QTimer, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QDateEdit,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

if TYPE_CHECKING:
    pass


@dataclass
class TiledFilters:
    """Container for Tiled query filters.

    Attributes:
        start_date: Filter for runs after this date.
        end_date: Filter for runs before this date.
        text_query: Free text search query.
        plan_name: Filter by specific plan name.
        exit_status: Filter by exit status (success, fail, abort).
    """

    start_date: datetime | None = None
    end_date: datetime | None = None
    text_query: str = ""
    plan_name: str | None = None
    exit_status: str | None = None

    def is_empty(self) -> bool:
        """Check if all filters are empty/default."""
        return (
            self.start_date is None
            and self.end_date is None
            and not self.text_query
            and self.plan_name is None
            and self.exit_status is None
        )

    def to_dict(self) -> dict:
        """Convert to dictionary representation."""
        return {
            "start_date": self.start_date.isoformat() if self.start_date else None,
            "end_date": self.end_date.isoformat() if self.end_date else None,
            "text_query": self.text_query,
            "plan_name": self.plan_name,
            "exit_status": self.exit_status,
        }


class TiledFilterWidget(QWidget):
    """Widget for filtering Tiled records.

    Provides UI controls for:
    - Text search with debouncing
    - Plan name dropdown
    - Exit status dropdown
    - Date range selection

    Signals:
        filters_changed: Emitted when any filter changes, with TiledFilters object.
    """

    filters_changed = Signal(object)  # TiledFilters

    # Debounce delay for text search in milliseconds
    DEBOUNCE_DELAY_MS = 300

    def __init__(self, parent: QWidget | None = None) -> None:
        """Initialize the filter widget.

        Args:
            parent: Parent widget.
        """
        super().__init__(parent)
        self._plan_names: list[str] = []
        self._debounce_timer = QTimer(self)
        self._debounce_timer.setSingleShot(True)
        self._debounce_timer.timeout.connect(self._on_debounce_timeout)
        self._setup_ui()

    def _setup_ui(self) -> None:
        """Setup the filter UI."""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)

        # First row: text search and dropdowns
        row1 = QHBoxLayout()
        row1.setSpacing(8)

        # Text search
        search_label = QLabel("Search:")
        row1.addWidget(search_label)

        self._search_input = QLineEdit()
        self._search_input.setPlaceholderText("Search plans, samples, UIDs...")
        self._search_input.setClearButtonEnabled(True)
        self._search_input.textChanged.connect(self._on_search_text_changed)
        row1.addWidget(self._search_input, stretch=2)

        # Plan name dropdown
        plan_label = QLabel("Plan:")
        row1.addWidget(plan_label)

        self._plan_combo = QComboBox()
        self._plan_combo.setMinimumWidth(100)
        self._plan_combo.addItem("All", None)
        self._plan_combo.currentIndexChanged.connect(self._on_filter_changed)
        row1.addWidget(self._plan_combo)

        # Status dropdown
        status_label = QLabel("Status:")
        row1.addWidget(status_label)

        self._status_combo = QComboBox()
        self._status_combo.setMinimumWidth(80)
        self._status_combo.addItem("All", None)
        self._status_combo.addItem("Success", "success")
        self._status_combo.addItem("Fail", "fail")
        self._status_combo.addItem("Abort", "abort")
        self._status_combo.currentIndexChanged.connect(self._on_filter_changed)
        row1.addWidget(self._status_combo)

        layout.addLayout(row1)

        # Second row: date range and buttons
        row2 = QHBoxLayout()
        row2.setSpacing(8)

        # From date
        from_label = QLabel("From:")
        row2.addWidget(from_label)

        self._from_date = QDateEdit()
        self._from_date.setCalendarPopup(True)
        self._from_date.setDate(datetime.now().date() - timedelta(days=30))
        self._from_date.setSpecialValueText("Any")
        self._from_date.dateChanged.connect(self._on_filter_changed)
        row2.addWidget(self._from_date)

        # To date
        to_label = QLabel("To:")
        row2.addWidget(to_label)

        self._to_date = QDateEdit()
        self._to_date.setCalendarPopup(True)
        self._to_date.setDate(datetime.now().date())
        self._to_date.setSpecialValueText("Any")
        self._to_date.dateChanged.connect(self._on_filter_changed)
        row2.addWidget(self._to_date)

        row2.addStretch()

        # Apply button
        self._apply_btn = QPushButton("Apply")
        self._apply_btn.clicked.connect(self._on_apply_clicked)
        row2.addWidget(self._apply_btn)

        # Clear button
        self._clear_btn = QPushButton("Clear")
        self._clear_btn.clicked.connect(self._on_clear_clicked)
        row2.addWidget(self._clear_btn)

        layout.addLayout(row2)

    def _on_search_text_changed(self, text: str) -> None:
        """Handle search text change with debouncing."""
        self._debounce_timer.start(self.DEBOUNCE_DELAY_MS)

    def _on_debounce_timeout(self) -> None:
        """Handle debounce timer timeout - emit filter change."""
        self._emit_filters()

    def _on_filter_changed(self) -> None:
        """Handle filter dropdown change."""
        # Don't debounce dropdown changes, emit immediately
        self._emit_filters()

    def _on_apply_clicked(self) -> None:
        """Handle apply button click."""
        self._emit_filters()

    def _on_clear_clicked(self) -> None:
        """Handle clear button click."""
        # Block signals while resetting
        self._search_input.blockSignals(True)
        self._plan_combo.blockSignals(True)
        self._status_combo.blockSignals(True)
        self._from_date.blockSignals(True)
        self._to_date.blockSignals(True)

        self._search_input.clear()
        self._plan_combo.setCurrentIndex(0)
        self._status_combo.setCurrentIndex(0)
        self._from_date.setDate(datetime.now().date() - timedelta(days=30))
        self._to_date.setDate(datetime.now().date())

        self._search_input.blockSignals(False)
        self._plan_combo.blockSignals(False)
        self._status_combo.blockSignals(False)
        self._from_date.blockSignals(False)
        self._to_date.blockSignals(False)

        self._emit_filters()

    def _emit_filters(self) -> None:
        """Emit the current filter state."""
        filters = self.get_filters()
        self.filters_changed.emit(filters)

    def get_filters(self) -> TiledFilters:
        """Get the current filter settings.

        Returns:
            TiledFilters object with current settings.
        """
        # Convert QDate to datetime
        from_date = self._from_date.date().toPython()
        to_date = self._to_date.date().toPython()

        # Convert to datetime at start/end of day
        start_dt = datetime.combine(from_date, datetime.min.time())
        end_dt = datetime.combine(to_date, datetime.max.time())

        return TiledFilters(
            start_date=start_dt,
            end_date=end_dt,
            text_query=self._search_input.text().strip(),
            plan_name=self._plan_combo.currentData(),
            exit_status=self._status_combo.currentData(),
        )

    def set_plan_names(self, names: list[str]) -> None:
        """Set the available plan names for the dropdown.

        Args:
            names: List of plan names.
        """
        current = self._plan_combo.currentData()

        self._plan_combo.blockSignals(True)
        self._plan_combo.clear()
        self._plan_combo.addItem("All", None)
        for name in sorted(set(names)):
            if name:
                self._plan_combo.addItem(name, name)

        # Restore selection if possible
        if current:
            idx = self._plan_combo.findData(current)
            if idx >= 0:
                self._plan_combo.setCurrentIndex(idx)

        self._plan_combo.blockSignals(False)
        self._plan_names = list(names)

    def set_date_range(
        self,
        start: datetime | None = None,
        end: datetime | None = None,
    ) -> None:
        """Set the date range.

        Args:
            start: Start date or None for default (30 days ago).
            end: End date or None for default (today).
        """
        self._from_date.blockSignals(True)
        self._to_date.blockSignals(True)

        if start:
            self._from_date.setDate(start.date())
        else:
            self._from_date.setDate(datetime.now().date() - timedelta(days=30))

        if end:
            self._to_date.setDate(end.date())
        else:
            self._to_date.setDate(datetime.now().date())

        self._from_date.blockSignals(False)
        self._to_date.blockSignals(False)

    def set_enabled(self, enabled: bool) -> None:
        """Enable or disable all filter controls.

        Args:
            enabled: Whether controls should be enabled.
        """
        self._search_input.setEnabled(enabled)
        self._plan_combo.setEnabled(enabled)
        self._status_combo.setEnabled(enabled)
        self._from_date.setEnabled(enabled)
        self._to_date.setEnabled(enabled)
        self._apply_btn.setEnabled(enabled)
        self._clear_btn.setEnabled(enabled)
