"""Plan selector widget for browsing and selecting Bluesky plans.

Provides a UI for browsing registered plans with category filtering
and search functionality, showing display names and category icons.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from loguru import logger
from PySide6.QtCore import QModelIndex, QSortFilterProxyModel, Qt, Signal, Slot
from PySide6.QtGui import QColor, QIcon, QPainter, QPixmap, QStandardItem, QStandardItemModel
from PySide6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListView,
    QSplitter,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from lightfall.ui.theme import scaled_px

if TYPE_CHECKING:
    from lightfall.acquire.plans import PlanInfo, PlanRegistry


def create_plan_icon(color: str, letter: str, size: int = 16) -> QIcon:
    """Create a simple colored icon with a letter for plan categories.

    Args:
        color: Hex color string for the background.
        letter: Single letter to display.
        size: Icon size in pixels.

    Returns:
        QIcon with colored circle and letter.
    """
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.GlobalColor.transparent)

    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)

    # Draw colored circle
    painter.setBrush(QColor(color))
    painter.setPen(Qt.PenStyle.NoPen)
    painter.drawEllipse(1, 1, size - 2, size - 2)

    # Draw letter
    painter.setPen(QColor("white"))
    font = painter.font()
    font.setBold(True)
    font.setPixelSize(scaled_px(10))
    painter.setFont(font)
    painter.drawText(pixmap.rect(), Qt.AlignmentFlag.AlignCenter, letter)

    painter.end()

    return QIcon(pixmap)


class PlanListModel(QStandardItemModel):
    """Qt model for displaying plans from a registry.

    Each item stores a reference to its PlanInfo in UserRole.
    Uses display names and category icons for better user experience.
    """

    PLAN_INFO_ROLE = Qt.ItemDataRole.UserRole + 1

    def __init__(self, registry: PlanRegistry | None = None, parent=None) -> None:
        """Initialize the model.

        Args:
            registry: Optional plan registry to load from.
            parent: Optional parent.
        """
        super().__init__(parent)
        self._registry = registry
        self._icon_cache: dict[str, QIcon] = {}
        if registry:
            self._load_plans()

    def set_registry(self, registry: PlanRegistry) -> None:
        """Set the plan registry and reload plans.

        Args:
            registry: The plan registry.
        """
        self._registry = registry
        self._load_plans()

    def _get_icon(self, plan_info: PlanInfo) -> QIcon:
        """Get or create icon for a plan.

        Args:
            plan_info: Plan to get icon for.

        Returns:
            QIcon for the plan's category.
        """
        color, letter = plan_info.get_icon()
        cache_key = f"{color}:{letter}"

        if cache_key not in self._icon_cache:
            self._icon_cache[cache_key] = create_plan_icon(color, letter)

        return self._icon_cache[cache_key]

    def _load_plans(self) -> None:
        """Load plans from the registry into the model."""
        self.clear()
        if self._registry is None:
            return

        for plan_info in self._registry.list_plans():
            # Use display name instead of internal name
            display_name = plan_info.get_display_name()
            item = QStandardItem(display_name)
            item.setData(plan_info, self.PLAN_INFO_ROLE)
            item.setToolTip(f"{plan_info.name}\n{plan_info.description or ''}")
            item.setEditable(False)
            # Set category icon
            item.setIcon(self._get_icon(plan_info))
            self.appendRow(item)

        logger.debug(f"Loaded {self.rowCount()} plans into model")

    def get_plan_info(self, index: QModelIndex) -> PlanInfo | None:
        """Get PlanInfo for an index.

        Args:
            index: Model index.

        Returns:
            PlanInfo or None.
        """
        if not index.isValid():
            return None
        return index.data(self.PLAN_INFO_ROLE)


class PlanFilterProxyModel(QSortFilterProxyModel):
    """Proxy model for filtering plans by category and search text."""

    def __init__(self, parent=None) -> None:
        """Initialize the proxy model."""
        super().__init__(parent)
        self._category_filter: str | None = None
        self._search_text: str = ""
        self.setFilterCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)

    def set_category_filter(self, category: str | None) -> None:
        """Set category filter.

        Args:
            category: Category to filter by, or None for all.
        """
        self._category_filter = category
        self.invalidateFilter()

    def set_search_text(self, text: str) -> None:
        """Set search text filter.

        Args:
            text: Search text.
        """
        self._search_text = text.lower()
        self.invalidateFilter()

    def filterAcceptsRow(self, source_row: int, source_parent: QModelIndex) -> bool:
        """Check if a row passes the filters.

        Args:
            source_row: Row in source model.
            source_parent: Parent index.

        Returns:
            True if row should be shown.
        """
        source_model = self.sourceModel()
        if source_model is None:
            return True

        index = source_model.index(source_row, 0, source_parent)
        plan_info = index.data(PlanListModel.PLAN_INFO_ROLE)

        if plan_info is None:
            return False

        # Category filter
        if self._category_filter and plan_info.category != self._category_filter:
            return False

        # Search filter - also search in display name
        if self._search_text:
            name_match = self._search_text in plan_info.name.lower()
            display_match = self._search_text in plan_info.get_display_name().lower()
            desc_match = self._search_text in plan_info.description.lower()
            if not (name_match or display_match or desc_match):
                return False

        return True


class PlanSelectorWidget(QWidget):
    """Widget to browse and select plans from a registry.

    Provides:
    - Category filter dropdown
    - Search box for filtering by name/description
    - List view of matching plans
    - Details panel showing selected plan info

    Signals:
        plan_selected(PlanInfo): Emitted when user selects a plan.

    Example:
        >>> from lightfall.acquire.plans import get_registry
        >>> registry = get_registry()
        >>> selector = PlanSelectorWidget()
        >>> selector.set_registry(registry)
        >>> selector.plan_selected.connect(on_plan_selected)
    """

    plan_selected = Signal(object)  # PlanInfo

    def __init__(self, parent: QWidget | None = None) -> None:
        """Initialize the selector widget.

        Args:
            parent: Optional parent widget.
        """
        super().__init__(parent)
        self._registry: PlanRegistry | None = None
        self._setup_ui()

    def _setup_ui(self) -> None:
        """Set up the user interface."""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)

        # Filter controls
        filter_layout = QHBoxLayout()

        # Category filter
        filter_layout.addWidget(QLabel("Category:"))
        self._category_combo = QComboBox()
        self._category_combo.addItem("All", None)
        self._category_combo.currentIndexChanged.connect(self._on_category_changed)
        filter_layout.addWidget(self._category_combo)

        filter_layout.addSpacing(12)

        # Search box
        filter_layout.addWidget(QLabel("Search:"))
        self._search_box = QLineEdit()
        self._search_box.setPlaceholderText("Filter plans...")
        self._search_box.setClearButtonEnabled(True)
        self._search_box.textChanged.connect(self._on_search_changed)
        filter_layout.addWidget(self._search_box, 1)

        layout.addLayout(filter_layout)

        # Splitter for list and details
        splitter = QSplitter(Qt.Orientation.Horizontal)

        # Plan list
        self._list_view = QListView()
        self._list_view.setSelectionBehavior(QListView.SelectionBehavior.SelectRows)
        self._list_view.setAlternatingRowColors(True)

        self._source_model = PlanListModel()
        self._proxy_model = PlanFilterProxyModel()
        self._proxy_model.setSourceModel(self._source_model)
        self._list_view.setModel(self._proxy_model)

        self._list_view.clicked.connect(self._on_item_clicked)
        self._list_view.doubleClicked.connect(self._on_item_double_clicked)
        self._list_view.selectionModel().currentChanged.connect(self._on_selection_changed)

        splitter.addWidget(self._list_view)

        # Details panel
        self._details_panel = QTextEdit()
        self._details_panel.setReadOnly(True)
        self._details_panel.setPlaceholderText("Select a plan to view details")
        splitter.addWidget(self._details_panel)

        splitter.setSizes([200, 300])
        layout.addWidget(splitter)

    def set_registry(self, registry: PlanRegistry) -> None:
        """Set the plan registry.

        Args:
            registry: The plan registry to display.
        """
        self._registry = registry
        self._source_model.set_registry(registry)
        self._update_categories()
        logger.debug(f"Set registry with {len(registry)} plans")

        # Connect to user plan service for live updates
        try:
            from lightfall.acquire.plans import UserPlanService

            service = UserPlanService.get_instance()
            service.plans_refreshed.connect(self._reload_plans)
        except Exception:
            pass

    def _reload_plans(self) -> None:
        """Reload plans from registry (called when user plans change)."""
        if self._registry is not None:
            self._source_model.set_registry(self._registry)
            self._update_categories()

    def _update_categories(self) -> None:
        """Update the category dropdown from the registry."""
        self._category_combo.blockSignals(True)
        self._category_combo.clear()
        self._category_combo.addItem("All", None)

        if self._registry:
            for category in self._registry.get_categories():
                self._category_combo.addItem(category.capitalize(), category)

        self._category_combo.blockSignals(False)

    def _format_plan_details(self, plan_info: PlanInfo) -> str:
        """Format plan details as HTML.

        Args:
            plan_info: Plan to format.

        Returns:
            HTML string.
        """
        display_name = plan_info.get_display_name()
        lines = [
            f"<h3>{display_name}</h3>",
            f"<p><b>Name:</b> {plan_info.name}</p>",
            f"<p><b>Category:</b> {plan_info.category}</p>",
        ]

        if plan_info.description:
            lines.append(f"<p>{plan_info.description}</p>")

        # Parameters
        if plan_info.parameters:
            lines.append("<h4>Parameters</h4>")
            lines.append("<ul>")
            for param in plan_info.parameters:
                required = "(required)" if param.required else "(optional)"
                type_str = param.type_name
                default_str = ""
                if not param.required:
                    default_str = f" = {param.default!r}"
                lines.append(
                    f"<li><b>{param.name}</b>: {type_str}{default_str} {required}"
                )
                if param.description:
                    lines.append(f"<br/><i>{param.description}</i>")
                lines.append("</li>")
            lines.append("</ul>")

        # Examples
        if plan_info.examples:
            lines.append("<h4>Examples</h4>")
            for example in plan_info.examples:
                lines.append(f"<pre>{example}</pre>")

        return "\n".join(lines)

    def get_selected_plan(self) -> PlanInfo | None:
        """Get the currently selected plan.

        Returns:
            Selected PlanInfo or None.
        """
        indexes = self._list_view.selectedIndexes()
        if not indexes:
            return None
        proxy_index = indexes[0]
        source_index = self._proxy_model.mapToSource(proxy_index)
        return self._source_model.get_plan_info(source_index)

    # === Slots ===

    @Slot(int)
    def _on_category_changed(self, index: int) -> None:
        """Handle category filter change.

        Args:
            index: Combo box index.
        """
        category = self._category_combo.currentData()
        self._proxy_model.set_category_filter(category)

    @Slot(str)
    def _on_search_changed(self, text: str) -> None:
        """Handle search text change.

        Args:
            text: Search text.
        """
        self._proxy_model.set_search_text(text)

    @Slot(QModelIndex)
    def _on_item_clicked(self, index: QModelIndex) -> None:
        """Handle item click.

        Args:
            index: Clicked index.
        """
        source_index = self._proxy_model.mapToSource(index)
        plan_info = self._source_model.get_plan_info(source_index)
        if plan_info:
            self._details_panel.setHtml(self._format_plan_details(plan_info))

    @Slot(QModelIndex)
    def _on_item_double_clicked(self, index: QModelIndex) -> None:
        """Handle item double-click (selection).

        Args:
            index: Double-clicked index.
        """
        source_index = self._proxy_model.mapToSource(index)
        plan_info = self._source_model.get_plan_info(source_index)
        if plan_info:
            self.plan_selected.emit(plan_info)
            logger.info(f"Selected plan: {plan_info.name}")

    @Slot(QModelIndex, QModelIndex)
    def _on_selection_changed(self, current: QModelIndex, previous: QModelIndex) -> None:
        """Handle selection change.

        Args:
            current: New selection.
            previous: Previous selection.
        """
        if not current.isValid():
            self._details_panel.clear()
            return

        source_index = self._proxy_model.mapToSource(current)
        plan_info = self._source_model.get_plan_info(source_index)
        if plan_info:
            self._details_panel.setHtml(self._format_plan_details(plan_info))
