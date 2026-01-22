"""Bluesky panel for plan selection and execution.

Provides an interface for:
- Browsing and selecting Bluesky plans
- Configuring plan parameters
- Executing plans on the RunEngine
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, ClassVar

from loguru import logger
from PySide6.QtCore import Qt, Signal, Slot
from PySide6.QtWidgets import (
    QSplitter,
    QWidget,
)

from ncs.ui.panels.base import BasePanel, PanelMetadata
from ncs.ui.widgets.plan_config import PlanConfigWidget
from ncs.ui.widgets.plan_selector import PlanSelectorWidget

if TYPE_CHECKING:
    from ncs.acquire.plans import PlanInfo

from ncs.acquire import QRunEngine, get_run_engine
from ncs.acquire.plans import PlanRegistry, get_registry


class BlueskyPanel(BasePanel):
    """Panel for Bluesky plan selection and execution.

    The BlueskyPanel provides an interface for selecting and running Bluesky scans:

    - Plan selector with category filtering and search
    - Plan configuration with dynamic parameter UI

    RunEngine control and document viewing are handled by:
    - RunEngineControlWidget in the main toolbar
    - DocumentsPanel as a separate panel

    Signals:
        plan_started(str): Emitted when a plan starts (plan name).
        plan_finished(str, str): Emitted when a plan finishes (name, exit_status).

    Example:
        >>> from ncs.acquire import get_run_engine
        >>> from ncs.acquire.plans import get_registry
        >>> panel = BlueskyPanel()
        >>> panel.set_run_engine(get_run_engine())
        >>> panel.set_registry(get_registry())
    """

    panel_metadata: ClassVar[PanelMetadata] = PanelMetadata(
        id="ncs.panels.bluesky",
        name="Bluesky",
        description="Select and execute Bluesky plans",
        icon="play",
        category="Acquisition",
        singleton=True,
        closable=True,
        keywords=["scan", "plan", "bluesky", "runengine", "acquisition"],
    )

    plan_started = Signal(str)  # plan name
    plan_finished = Signal(str, str)  # plan name, exit_status

    def __init__(self, parent: QWidget | None = None) -> None:
        """Initialize the Bluesky panel.

        Args:
            parent: Parent widget.
        """
        self._re: QRunEngine | None = None
        self._registry: PlanRegistry | None = None
        self._current_plan_name: str = ""
        super().__init__(parent)

    def _setup_ui(self) -> None:
        """Set up the panel UI."""
        # Plan selector at top
        self._plan_selector = PlanSelectorWidget()
        self._plan_selector.plan_selected.connect(self._on_plan_selected)

        # Plan configuration below
        self._plan_config = PlanConfigWidget()
        self._plan_config.run_requested.connect(self._on_run_requested)

        # Vertical splitter for selector and config
        splitter = QSplitter(Qt.Orientation.Vertical)
        splitter.addWidget(self._plan_selector)
        splitter.addWidget(self._plan_config)
        splitter.setSizes([300, 200])

        self._layout.addWidget(splitter)

        # Auto-configure with RunEngine and PlanRegistry singletons
        self._auto_configure()

    def _auto_configure(self) -> None:
        """Auto-configure with RunEngine and PlanRegistry singletons."""
        try:
            re = get_run_engine()
            self.set_run_engine(re)
        except Exception as e:
            logger.debug("Could not auto-configure RunEngine: {}", e)

        try:
            registry = get_registry()
            self.set_registry(registry)
        except Exception as e:
            logger.debug("Could not auto-configure PlanRegistry: {}", e)

    def set_run_engine(self, re: QRunEngine) -> None:
        """Connect to a QRunEngine instance.

        Args:
            re: The QRunEngine to use for plan execution.
        """
        self._re = re

        # Connect signals for tracking plan execution
        re.sigStart.connect(self._on_run_start)
        re.sigFinish.connect(self._on_run_finish)
        re.sigDocumentYield.connect(self._on_document)

        logger.info("BlueskyPanel connected to RunEngine")

    def set_registry(self, registry: PlanRegistry) -> None:
        """Set the plan registry.

        Args:
            registry: PlanRegistry with available plans.
        """
        self._registry = registry
        self._plan_selector.set_registry(registry)
        logger.info(f"BlueskyPanel loaded {len(registry)} plans")

    # === Introspection API for MCP tools ===

    def get_introspection_data(self) -> dict[str, Any]:
        """Get introspection data for Claude MCP tools.

        Returns:
            Dictionary with panel state and capabilities.
        """
        data = {
            "panel_id": self.panel_metadata.id,
            "panel_name": self.panel_metadata.name,
            "has_run_engine": self._re is not None,
            "has_registry": self._registry is not None,
            "current_plan": self._current_plan_name or None,
        }

        if self._re:
            data["run_engine_state"] = self._re.state
            data["queue_size"] = self._re.queue_size

        if self._registry:
            data["plan_count"] = len(self._registry)
            data["plan_categories"] = self._registry.get_categories()
            data["plan_names"] = self._registry.plan_names

        return data

    def get_available_actions(self) -> list[dict[str, str]]:
        """Get list of actions that can be performed on this panel.

        Returns:
            List of action descriptions for MCP tools.
        """
        return [
            {
                "action": "select_plan",
                "description": "Select a plan by name",
                "params": "plan_name: str",
            },
            {
                "action": "run_plan",
                "description": "Run the currently configured plan",
                "params": "None",
            },
        ]

    def select_plan(self, plan_name: str) -> bool:
        """Select a plan by name (for MCP tools).

        Args:
            plan_name: Name of the plan to select.

        Returns:
            True if plan was found and selected.
        """
        if self._registry is None:
            return False

        plan_info = self._registry.get_plan(plan_name)
        if plan_info:
            self._plan_config.set_plan(plan_info)
            self._current_plan_name = plan_name
            return True
        return False

    # === Slots ===

    @Slot(object)
    def _on_plan_selected(self, plan_info: PlanInfo) -> None:
        """Handle plan selection from selector.

        Args:
            plan_info: Selected plan.
        """
        self._plan_config.set_plan(plan_info)
        self._current_plan_name = plan_info.name
        logger.debug(f"Plan selected: {plan_info.name}")

    @Slot(object, dict)
    def _on_run_requested(self, plan_info: PlanInfo, kwargs: dict) -> None:
        """Handle run request from config widget.

        Args:
            plan_info: Plan to run.
            kwargs: Parameter values.
        """
        if self._re is None:
            logger.error("No RunEngine configured")
            return

        try:
            # TODO: Resolve device names to actual devices from catalog
            # For now, just pass kwargs directly
            plan = plan_info.func(**kwargs)

            # Submit to RunEngine
            self._re(plan)
            self._current_plan_name = plan_info.name

            logger.info(f"Submitted plan: {plan_info.name}")
        except Exception as e:
            logger.error(f"Failed to run plan {plan_info.name}: {e}")

    @Slot()
    def _on_run_start(self) -> None:
        """Handle run start from RunEngine."""
        self.plan_started.emit(self._current_plan_name)

    @Slot()
    def _on_run_finish(self) -> None:
        """Handle run finish from RunEngine."""
        # Get exit status from last stop document if available
        exit_status = "unknown"
        self.plan_finished.emit(self._current_plan_name, exit_status)

    @Slot(str, dict)
    def _on_document(self, name: str, doc: dict) -> None:
        """Handle document from RunEngine.

        Args:
            name: Document type.
            doc: Document data.
        """
        if name == "start":
            plan_name = doc.get("plan_name", "")
            if plan_name:
                self._current_plan_name = plan_name
        elif name == "stop":
            exit_status = doc.get("exit_status", "unknown")
            self.plan_finished.emit(self._current_plan_name, exit_status)
