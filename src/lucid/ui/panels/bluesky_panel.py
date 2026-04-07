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
from PySide6.QtGui import QAction
from PySide6.QtWidgets import (
    QDialog,
    QSplitter,
    QToolBar,
    QWidget,
)

from lucid.ui.panels.base import BasePanel, PanelMetadata
from lucid.ui.widgets.plan_config import PlanConfigWidget
from lucid.ui.widgets.plan_selector import PlanSelectorWidget

if TYPE_CHECKING:
    from lucid.acquire.engine import Engine
    from lucid.acquire.plans import PlanInfo

from lucid.acquire import get_engine
from lucid.acquire.plans import PlanRegistry, get_registry
from lucid.devices import DeviceCatalog


class BlueskyPanel(BasePanel):
    """Panel for Bluesky plan selection and execution.

    The BlueskyPanel provides an interface for selecting and running Bluesky scans:

    - Plan selector with category filtering and search
    - Plan configuration with dynamic parameter UI

    Engine control and document viewing are handled by:
    - RunEngineControlWidget in the main toolbar
    - DocumentsPanel as a separate panel

    Signals:
        plan_started(str): Emitted when a plan starts (plan name).
        plan_finished(str, str): Emitted when a plan finishes (name, exit_status).

    Example:
        >>> from lucid.acquire import get_engine
        >>> from lucid.acquire.plans import get_registry
        >>> panel = BlueskyPanel()
        >>> panel.set_engine(get_engine())
        >>> panel.set_registry(get_registry())
    """

    panel_metadata: ClassVar[PanelMetadata] = PanelMetadata(
        id="lucid.panels.bluesky",
        name="Bluesky",
        description="Select and execute Bluesky plans",
        icon="play",
        category="Acquisition",
        singleton=True,
        closable=True,
        keywords=["scan", "plan", "bluesky", "runengine", "acquisition"],
        # Docking preferences - primary tool in left sidebar
        default_area="left",
        sidebar_group="top",
        auto_hide=True,
        sidebar_order=0,
    )

    plan_started = Signal(str)  # plan name
    plan_finished = Signal(str, str)  # plan name, exit_status

    def __init__(self, parent: QWidget | None = None) -> None:
        """Initialize the Bluesky panel.

        Args:
            parent: Parent widget.
        """
        self._engine: Engine | None = None
        self._registry: PlanRegistry | None = None
        self._current_plan_name: str = ""
        super().__init__(parent)

    def _setup_ui(self) -> None:
        """Set up the panel UI."""
        # Toolbar for plan actions
        self._toolbar = QToolBar()
        self._toolbar.setMovable(False)
        self._setup_toolbar()
        self._layout.addWidget(self._toolbar)

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

    def _setup_toolbar(self) -> None:
        """Set up the toolbar with plan actions."""
        # Create New Plan
        self._create_plan_action = QAction("New Plan", self)
        self._create_plan_action.setToolTip("Create a new user plan (opens in editor)")
        self._create_plan_action.triggered.connect(self._on_create_plan)
        self._toolbar.addAction(self._create_plan_action)

        # Refresh Plans
        self._refresh_action = QAction("Refresh", self)
        self._refresh_action.setToolTip("Reload user plans from disk")
        self._refresh_action.triggered.connect(self._on_refresh_plans)
        self._toolbar.addAction(self._refresh_action)

        # Open Folder
        self._open_folder_action = QAction("Open Folder", self)
        self._open_folder_action.setToolTip("Open user plans folder in file explorer")
        self._open_folder_action.triggered.connect(self._on_open_plans_folder)
        self._toolbar.addAction(self._open_folder_action)

    @Slot()
    def _on_create_plan(self) -> None:
        """Handle Create Plan action."""
        from lucid.ui.dialogs import CreatePlanDialog
        from lucid.ui.toast import ToastManager

        dialog = CreatePlanDialog(self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            name = dialog.get_plan_name()
            desc = dialog.get_description()

            try:
                from lucid.acquire.plans import UserPlanService

                service = UserPlanService.get_instance()
                file_path = service.create_new_plan(name, desc)

                # Open in external editor
                from lucid.ui.preferences.manager import PreferencesManager
                from lucid.utils.editor_launcher import (
                    CodeEditor,
                    get_editor_from_string,
                    open_in_editor,
                )

                prefs = PreferencesManager.get_instance()
                editor_str = prefs.get("code_editor", CodeEditor.VSCODE.value)
                editor = get_editor_from_string(editor_str)
                if editor:
                    open_in_editor(str(file_path), line=1, editor=editor)

                ToastManager.get_instance().success(
                    "Plan Created", f"Created {name}.py"
                )
            except ValueError as e:
                ToastManager.get_instance().error("Creation Failed", str(e))
            except Exception as e:
                logger.error("Failed to create plan: {}", e)
                ToastManager.get_instance().error("Creation Failed", str(e))

    @Slot()
    def _on_refresh_plans(self) -> None:
        """Handle Refresh Plans action."""
        from lucid.ui.toast import ToastManager

        try:
            from lucid.acquire.plans import UserPlanService

            service = UserPlanService.get_instance()
            service.refresh_plans()
            ToastManager.get_instance().info("Plans Refreshed", "User plans reloaded")
        except Exception as e:
            logger.error("Failed to refresh plans: {}", e)
            ToastManager.get_instance().error("Refresh Failed", str(e))

    @Slot()
    def _on_open_plans_folder(self) -> None:
        """Handle Open Folder action."""
        try:
            from lucid.acquire.plans import UserPlanService

            service = UserPlanService.get_instance()
            service.open_plans_folder()
        except Exception as e:
            logger.error("Failed to open plans folder: {}", e)

    def _auto_configure(self) -> None:
        """Auto-configure with Engine, PlanRegistry, and DeviceCatalog singletons."""
        try:
            engine = get_engine()
            self.set_engine(engine)
        except Exception as e:
            logger.debug("Could not auto-configure Engine: {}", e)

        try:
            registry = get_registry()
            self.set_registry(registry)
        except Exception as e:
            logger.debug("Could not auto-configure PlanRegistry: {}", e)

        try:
            catalog = DeviceCatalog.get_instance()
            self.set_catalog(catalog)
        except Exception as e:
            logger.debug("Could not auto-configure DeviceCatalog: {}", e)

    def set_catalog(self, catalog: DeviceCatalog) -> None:
        """Set the device catalog for plan parameter device selection.

        Args:
            catalog: DeviceCatalog with available devices.
        """
        self._plan_config.set_catalog(catalog)
        logger.info("BlueskyPanel connected to DeviceCatalog")

    def set_engine(self, engine: Engine) -> None:
        """Connect to an Engine instance.

        Args:
            engine: The Engine to use for plan execution.
        """
        self._engine = engine

        # Connect signals for tracking plan execution
        engine.sigStart.connect(self._on_run_start)
        engine.sigFinish.connect(self._on_run_finish)
        engine.sigOutput.connect(self._on_document)

        logger.info("BlueskyPanel connected to Engine")

    def set_run_engine(self, re: Engine) -> None:
        """Connect to an Engine instance.

        Deprecated: Use set_engine() instead.

        Args:
            re: The Engine to use for plan execution.
        """
        self.set_engine(re)

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
            "has_engine": self._engine is not None,
            "has_registry": self._registry is not None,
            "current_plan": self._current_plan_name or None,
        }

        if self._engine:
            data["engine_state"] = self._engine.state_name
            data["queue_size"] = self._engine.queue_size

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
        if self._engine is None:
            logger.error("No Engine configured")
            return

        try:
            # Resolve device names to actual ophyd devices
            resolved_kwargs = self._resolve_device_kwargs(plan_info, kwargs)

            plan = plan_info.func(**resolved_kwargs)

            # Submit to Engine
            self._engine(plan)
            self._current_plan_name = plan_info.name

            logger.info(f"Submitted plan: {plan_info.name}")
        except Exception as e:
            logger.error(f"Failed to run plan {plan_info.name}: {e}")

    def _resolve_device_kwargs(
        self, plan_info: PlanInfo, kwargs: dict
    ) -> dict:
        """Resolve device names to actual ophyd device objects.

        Checks the pyqtgraph parameter type (``"device"``) to identify
        device parameters, then resolves name strings via the catalog.
        Multi-select parameters stay as lists; single-select are unwrapped.

        Args:
            plan_info: Plan info with parameter metadata.
            kwargs: Parameter values (device names as strings/lists).

        Returns:
            kwargs with device names replaced by ophyd device objects.
        """
        catalog = DeviceCatalog.get_instance()
        resolved = {}

        # Use the live pyqtgraph parameter tree to determine types
        root_param = self._plan_config._root_param

        for key, value in kwargs.items():
            child = root_param.child(key) if root_param else None

            is_device = child is not None and child.opts.get("type") == "device"
            if not is_device:
                resolved[key] = value
                continue

            multi_select = child.opts.get("multi_select", True)

            if isinstance(value, list):
                devices = []
                for name in value:
                    device = self._resolve_single_device(catalog, name)
                    if device is not None:
                        devices.append(device)
                    else:
                        logger.warning(
                            f"Could not resolve device '{name}' for "
                            f"parameter '{key}' — not connected?"
                        )

                if multi_select:
                    resolved[key] = devices
                elif len(devices) == 1:
                    resolved[key] = devices[0]
                elif not devices:
                    raise ValueError(
                        f"Device parameter '{key}': no devices could be "
                        f"resolved from {value}"
                    )
                else:
                    resolved[key] = devices
            elif isinstance(value, str):
                device = self._resolve_single_device(catalog, value)
                if device is not None:
                    resolved[key] = device
                else:
                    raise ValueError(
                        f"Device parameter '{key}': could not resolve "
                        f"'{value}' — not connected?"
                    )
            else:
                resolved[key] = value

        return resolved

    @staticmethod
    def _resolve_single_device(catalog: DeviceCatalog, name: str) -> Any:
        """Resolve a device name to an ophyd object.

        First checks if the device already has an ophyd instance.
        If not, attempts to instantiate it via the backend.
        """
        device_info = catalog.get_device_by_name(name)
        if device_info is None:
            return None

        # Already instantiated?
        if device_info.ophyd_device is not None:
            return device_info.ophyd_device

        # Try to connect on demand
        logger.info(f"Device '{name}' not connected — requesting connection")
        try:
            catalog.request_device_connection(device_info.id)
        except Exception as e:
            logger.warning(f"Failed to connect '{name}': {e}")

        return device_info.ophyd_device

    @Slot()
    def _on_run_start(self) -> None:
        """Handle run start from Engine."""
        self.plan_started.emit(self._current_plan_name)

    @Slot()
    def _on_run_finish(self) -> None:
        """Handle run finish from Engine."""
        # Get exit status from last stop document if available
        exit_status = "unknown"
        self.plan_finished.emit(self._current_plan_name, exit_status)

    @Slot(str, dict)
    def _on_document(self, name: str, doc: dict) -> None:
        """Handle document from Engine.

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
